"""A2.1 capability-guided session — fixture dry-run + state machine tests."""

from __future__ import annotations

import io
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pentairsnoop.capability_session import (
    STATUS_DONE,
    STATUS_NOT_RUN,
    STATUS_SKIPPED,
    CapabilitySession,
    CapabilitySessionConfig,
    CapabilitySessionWriter,
    ListenOnlyTransport,
    SessionPhase,
    parse_id_list,
    run_capability_session,
)
from pentairsnoop.capture import CaptureWriter
from pentairsnoop.catalog import (
    DEFAULT_CATALOG,
    Capability,
    filter_catalog,
    get_by_id,
    load_catalog,
)
from pentairsnoop.cli import build_parser, cmd_capture_capabilities, main
from pentairsnoop.framer import Frame, FrameKind
from pentairsnoop.transport import HexFileTransport

_INFO = "ffffffffffffffff00ffa50c0f10080d4848555a620400000000000000028a"


class _FixedClock:
    def __init__(self, start: datetime) -> None:
        self._t = start

    def __call__(self) -> datetime:
        return self._t


class _ScriptedInput:
    """Pattern: Stub — scripted stdin lines for interactive dry-runs."""

    def __init__(self, lines: list[str]) -> None:
        self._lines = list(lines)
        self.calls = 0

    def __call__(self, _prompt: str = "") -> str:
        self.calls += 1
        if not self._lines:
            raise EOFError
        return self._lines.pop(0)


class _CountingTransport:
    """Minimal transport that tracks write attempts."""

    def __init__(self, payload: bytes = b"") -> None:
        self._data = payload
        self._pos = 0
        self.writes: list[bytes] = []
        self.opened = False

    def open(self) -> None:
        self.opened = True
        self._pos = 0

    def close(self) -> None:
        self.opened = False

    def read(self, n: int) -> bytes:
        if n <= 0 or self._pos >= len(self._data):
            return b""
        end = min(self._pos + n, len(self._data))
        chunk = self._data[self._pos:end]
        self._pos = end
        return chunk

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    @property
    def remaining(self) -> int:
        return max(0, len(self._data) - self._pos)


def test_parse_id_list() -> None:
    assert parse_id_list(None) is None
    assert parse_id_list("") is None
    assert parse_id_list("  ") is None
    assert parse_id_list("a,b , c") == ["a", "b", "c"]


def test_listen_only_transport_forbids_write() -> None:
    inner = _CountingTransport(b"\xff")
    wrapped = ListenOnlyTransport(inner)  # type: ignore[arg-type]
    wrapped.open()
    assert wrapped.read(1) == b"\xff"
    with pytest.raises(RuntimeError, match="listen-only"):
        wrapped.write(b"\x01")
    assert wrapped.write_attempts == 1
    assert inner.writes == []
    wrapped.close()


def test_capability_session_writer_hybrid_layout(tmp_path: Path) -> None:
    clock = _FixedClock(datetime(2026, 7, 20, 16, 0, 0, tzinfo=timezone.utc))
    caps = [
        get_by_id(DEFAULT_CATALOG, "idle_baseline"),
        get_by_id(DEFAULT_CATALOG, "spa_on_off"),
    ]
    assert caps[0] is not None and caps[1] is not None
    writer = CapabilitySessionWriter(
        tmp_path / "sess",
        label="capability-guided",
        source={"kind": "file", "path": "x.hex"},
        catalog=caps,
        clock=clock,
    )
    writer.open()
    writer.arm(caps[0], seq=1)
    writer.write_raw(bytes.fromhex("ff00ffa5"))
    writer.write_frame(
        Frame(kind=FrameKind.A5, raw=bytes.fromhex(_INFO), checksum_ok=True)
    )
    rel, n, start, end = writer.disarm()
    assert rel == "by_capability/01_idle_baseline"
    assert n == 1
    assert start == 0
    assert end == 0
    writer.close()

    meta = json.loads((tmp_path / "sess" / "meta.json").read_text(encoding="utf-8"))
    assert meta["mode"] == "capability_guided"
    assert meta["listen_only"] is True
    assert meta["capability_format_version"] == 1
    assert meta["slice_policy"] == "hybrid_c"
    assert (tmp_path / "sess" / "catalog_snapshot.json").is_file()
    assert (tmp_path / "sess" / "by_capability" / "01_idle_baseline" / "frames.ndjson").is_file()
    frame = json.loads(
        (tmp_path / "sess" / "frames.ndjson").read_text(encoding="utf-8").splitlines()[0]
    )
    assert frame["capability_id"] == "idle_baseline"


def test_scripted_session_file_fixture(tmp_path: Path) -> None:
    hex_path = tmp_path / "one.hex"
    hex_path.write_text(_INFO + _INFO, encoding="utf-8")
    walk = filter_catalog(
        DEFAULT_CATALOG,
        only=["idle_baseline", "spa_on_off", "cleaner_on_off"],
    )
    out = io.StringIO()
    stdin = _ScriptedInput(["d\n", "s\n", "q\n"])
    dest = tmp_path / "cap-session"
    counts = run_capability_session(
        HexFileTransport(hex_path),
        dest,
        walk,
        label="capability-guided",
        source={"kind": "file", "path": str(hex_path)},
        input_fn=stdin,
        output=out,
        clock=_FixedClock(datetime(2026, 7, 20, 16, 0, 0, tzinfo=timezone.utc)),
    )
    assert counts["done"] >= 1
    assert counts["skipped"] >= 1
    # q on third leaves it skipped (incomplete) or we quit before — with q during
    # recording of third, third is skipped and nothing left for not_run.
    assert counts["done"] + counts["skipped"] + counts["not_run"] == 3

    meta = json.loads((dest / "meta.json").read_text(encoding="utf-8"))
    assert meta["mode"] == "capability_guided"
    assert meta["listen_only"] is True
    assert meta["label"] == "capability-guided"

    index = [
        json.loads(line)
        for line in (dest / "index.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert len(index) == 3
    assert index[0]["capability_id"] == "idle_baseline"
    assert index[0]["status"] == STATUS_DONE
    assert index[1]["capability_id"] == "spa_on_off"
    assert index[1]["status"] == STATUS_SKIPPED

    root_frames = [
        json.loads(line)
        for line in (dest / "frames.ndjson").read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert len(root_frames) >= 1
    assert all(f.get("capability_id") for f in root_frames)
    assert (dest / "raw.ndjson").stat().st_size > 0
    assert (dest / "by_capability" / "01_idle_baseline" / "meta.json").is_file()
    assert "capability session complete" in out.getvalue()


def test_skip_ids_premark_without_prompt(tmp_path: Path) -> None:
    hex_path = tmp_path / "one.hex"
    hex_path.write_text(_INFO, encoding="utf-8")
    walk = filter_catalog(
        DEFAULT_CATALOG,
        only=["idle_baseline", "spa_on_off"],
    )
    stdin = _ScriptedInput(["d\n"])  # only one prompt expected
    dest = tmp_path / "skip-session"
    counts = run_capability_session(
        HexFileTransport(hex_path),
        dest,
        walk,
        skip_ids=["spa_on_off"],
        input_fn=stdin,
        output=io.StringIO(),
    )
    assert counts["done"] == 1
    assert counts["skipped"] == 1
    assert stdin.calls == 1
    index = [
        json.loads(line)
        for line in (dest / "index.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    by_id = {row["capability_id"]: row["status"] for row in index}
    assert by_id["idle_baseline"] == STATUS_DONE
    assert by_id["spa_on_off"] == STATUS_SKIPPED


def test_quit_marks_remaining_not_run(tmp_path: Path) -> None:
    hex_path = tmp_path / "one.hex"
    hex_path.write_text(_INFO, encoding="utf-8")
    walk = filter_catalog(
        DEFAULT_CATALOG,
        only=["idle_baseline", "spa_on_off", "cleaner_on_off"],
    )
    # Quit immediately on first prompt.
    stdin = _ScriptedInput(["q\n"])
    dest = tmp_path / "quit-session"
    counts = run_capability_session(
        HexFileTransport(hex_path),
        dest,
        walk,
        input_fn=stdin,
        output=io.StringIO(),
    )
    assert counts["skipped"] == 1  # current incomplete
    assert counts["not_run"] == 2
    index = [
        json.loads(line)
        for line in (dest / "index.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert index[0]["status"] == STATUS_SKIPPED
    assert index[1]["status"] == STATUS_NOT_RUN
    assert index[2]["status"] == STATUS_NOT_RUN


def test_arm_on_key_variant(tmp_path: Path) -> None:
    hex_path = tmp_path / "one.hex"
    hex_path.write_text(_INFO, encoding="utf-8")
    walk = filter_catalog(DEFAULT_CATALOG, only=["idle_baseline"])
    stdin = _ScriptedInput(["a\n", "d\n"])
    dest = tmp_path / "arm-key"
    counts = run_capability_session(
        HexFileTransport(hex_path),
        dest,
        walk,
        arm_on_prompt=False,
        input_fn=stdin,
        output=io.StringIO(),
    )
    assert counts["done"] == 1
    assert stdin.calls == 2


def test_arm_on_key_done_without_arm(tmp_path: Path) -> None:
    hex_path = tmp_path / "one.hex"
    hex_path.write_text(_INFO, encoding="utf-8")
    walk = filter_catalog(DEFAULT_CATALOG, only=["idle_baseline", "spa_on_off"])
    stdin = _ScriptedInput(["d\n", "s\n"])
    dest = tmp_path / "arm-key-ds"
    counts = run_capability_session(
        HexFileTransport(hex_path),
        dest,
        walk,
        arm_on_prompt=False,
        input_fn=stdin,
        output=io.StringIO(),
    )
    assert counts["done"] == 1
    assert counts["skipped"] == 1


def test_listen_only_no_transport_write(tmp_path: Path) -> None:
    payload = bytes.fromhex(_INFO)
    inner = _CountingTransport(payload)
    walk = filter_catalog(DEFAULT_CATALOG, only=["idle_baseline"])
    writer = CapabilitySessionWriter(
        tmp_path / "lo",
        catalog=walk,
        source={"kind": "mock"},
    )
    config = CapabilitySessionConfig(catalog=walk)
    session = CapabilitySession(
        inner,  # type: ignore[arg-type]
        writer,
        config,
        input_fn=_ScriptedInput(["d\n"]),
        output=io.StringIO(),
    )
    session.run()
    assert inner.writes == []
    assert session.listen_only_transport.write_attempts == 0
    # Explicitly prove write is blocked.
    with pytest.raises(RuntimeError, match="listen-only"):
        session.listen_only_transport.write(b"\x00")


def test_unknown_key_then_done(tmp_path: Path) -> None:
    hex_path = tmp_path / "one.hex"
    hex_path.write_text(_INFO, encoding="utf-8")
    walk = filter_catalog(DEFAULT_CATALOG, only=["idle_baseline"])
    stdin = _ScriptedInput(["x\n", "D\n"])  # case-insensitive d
    dest = tmp_path / "unknown-key"
    counts = run_capability_session(
        HexFileTransport(hex_path),
        dest,
        walk,
        input_fn=stdin,
        output=io.StringIO(),
    )
    assert counts["done"] == 1
    assert stdin.calls == 2


def test_eof_on_stdin_quits(tmp_path: Path) -> None:
    hex_path = tmp_path / "one.hex"
    hex_path.write_text(_INFO, encoding="utf-8")
    walk = filter_catalog(DEFAULT_CATALOG, only=["idle_baseline", "spa_on_off"])
    stdin = _ScriptedInput([])  # immediate EOF
    dest = tmp_path / "eof"
    counts = run_capability_session(
        HexFileTransport(hex_path),
        dest,
        walk,
        input_fn=stdin,
        output=io.StringIO(),
    )
    assert counts["not_run"] + counts["skipped"] == 2


def test_filter_helpers_only_skip_from() -> None:
    only = filter_catalog(
        DEFAULT_CATALOG,
        only=["spa_on_off", "cleaner_on_off"],
    )
    assert [c.id for c in only] == ["spa_on_off", "cleaner_on_off"]
    from_id = filter_catalog(DEFAULT_CATALOG, from_id="spa_temp_set")
    assert from_id[0].id == "spa_temp_set"
    skipped = filter_catalog(DEFAULT_CATALOG, skip_ids=["set_clock"])
    assert "set_clock" not in {c.id for c in skipped}


def test_cli_capture_capabilities_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    hex_path = tmp_path / "one.hex"
    hex_path.write_text(_INFO, encoding="utf-8")
    session = tmp_path / "cli-cap"
    # Drive stdin via input_fn on cmd_ directly for determinism.
    rc = cmd_capture_capabilities(
        tcp=None,
        serial=None,
        file=hex_path,
        out=tmp_path,
        label="capability-guided",
        session_dir=session,
        only="idle_baseline,spa_on_off",
        skip_id=None,
        from_id=None,
        catalog=None,
        arm_on_prompt=True,
        input_fn=_ScriptedInput(["d\n", "s\n"]),
    )
    assert rc == 0
    meta = json.loads((session / "meta.json").read_text(encoding="utf-8"))
    assert meta["mode"] == "capability_guided"
    assert meta["listen_only"] is True
    assert (session / "index.jsonl").is_file()


def test_cli_main_capture_capabilities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hex_path = tmp_path / "one.hex"
    hex_path.write_text(_INFO, encoding="utf-8")
    session = tmp_path / "main-cap"
    keys = _ScriptedInput(["d\n", "q\n"])
    monkeypatch.setattr("builtins.input", keys)
    rc = main(
        [
            "capture-capabilities",
            "--file",
            str(hex_path),
            "--session-dir",
            str(session),
            "--only",
            "idle_baseline,spa_on_off,cleaner_on_off",
        ]
    )
    assert rc == 0
    meta = json.loads((session / "meta.json").read_text(encoding="utf-8"))
    assert meta["mode"] == "capability_guided"
    index = [
        json.loads(line)
        for line in (session / "index.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert index[0]["status"] == STATUS_DONE
    assert any(row["status"] == STATUS_NOT_RUN for row in index)


def test_cli_capture_capabilities_missing_file() -> None:
    assert main(["capture-capabilities", "--file", "/no/such.hex"]) == 1


def test_cli_capture_capabilities_empty_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    hex_path = tmp_path / "one.hex"
    hex_path.write_text(_INFO, encoding="utf-8")
    rc = cmd_capture_capabilities(
        tcp=None,
        serial=None,
        file=hex_path,
        out=tmp_path,
        label="x",
        session_dir=tmp_path / "empty",
        only="not_a_real_id",
        skip_id=None,
        from_id=None,
        catalog=None,
        arm_on_prompt=True,
        input_fn=_ScriptedInput(["q\n"]),
    )
    assert rc == 1
    assert "no capabilities" in capsys.readouterr().err


def test_cli_capture_capabilities_bad_from_id(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    hex_path = tmp_path / "one.hex"
    hex_path.write_text(_INFO, encoding="utf-8")
    rc = cmd_capture_capabilities(
        tcp=None,
        serial=None,
        file=hex_path,
        out=tmp_path,
        label="x",
        session_dir=tmp_path / "bad-from",
        only=None,
        skip_id=None,
        from_id="nope",
        catalog=None,
        arm_on_prompt=True,
    )
    assert rc == 1
    assert "error:" in capsys.readouterr().err


def test_cli_capture_capabilities_no_source(
    capsys: pytest.CaptureFixture[str],
) -> None:
    rc = cmd_capture_capabilities(
        tcp=None,
        serial=None,
        file=None,
        out=Path("/tmp"),
        label="x",
        session_dir=None,
        only=None,
        skip_id=None,
        from_id=None,
        catalog=None,
        arm_on_prompt=True,
    )
    assert rc == 1
    assert "error:" in capsys.readouterr().err


def test_cli_arm_on_key_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hex_path = tmp_path / "one.hex"
    hex_path.write_text(_INFO, encoding="utf-8")
    session = tmp_path / "arm-flag"
    monkeypatch.setattr("builtins.input", _ScriptedInput(["a\n", "d\n"]))
    rc = main(
        [
            "capture-capabilities",
            "--file",
            str(hex_path),
            "--session-dir",
            str(session),
            "--only",
            "idle_baseline",
            "--arm-on-key",
        ]
    )
    assert rc == 0
    assert json.loads((session / "meta.json").read_text(encoding="utf-8"))[
        "mode"
    ] == "capability_guided"


def test_capture_capabilities_help_present() -> None:
    help_text = build_parser().format_help()
    assert "capture-capabilities" in help_text


def test_capture_writer_capability_id_optional(tmp_path: Path) -> None:
    writer = CaptureWriter(tmp_path / "plain")
    writer.open()
    writer.write_frame(
        Frame(kind=FrameKind.A5, raw=bytes.fromhex(_INFO), checksum_ok=True)
    )
    writer.write_frame(
        Frame(kind=FrameKind.A5, raw=bytes.fromhex(_INFO), checksum_ok=True),
        capability_id="spa_on_off",
    )
    writer.close()
    lines = [
        json.loads(line)
        for line in writer.frames_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert "capability_id" not in lines[0]
    assert lines[1]["capability_id"] == "spa_on_off"
    assert writer.frame_seq == 2


def test_session_phase_enum() -> None:
    assert SessionPhase.SESSION_INIT is not SessionPhase.SESSION_END


def test_custom_catalog_json(tmp_path: Path) -> None:
    cat = tmp_path / "mini.json"
    cat.write_text(
        json.dumps(
            [
                {
                    "id": "mini_a",
                    "group": "baseline",
                    "prompt": "Do A",
                    "skip_ok": False,
                },
                {
                    "id": "mini_b",
                    "group": "circuit",
                    "prompt": "Do B",
                    "skip_ok": True,
                },
            ]
        ),
        encoding="utf-8",
    )
    hex_path = tmp_path / "one.hex"
    hex_path.write_text(_INFO, encoding="utf-8")
    session = tmp_path / "custom-cat"
    rc = cmd_capture_capabilities(
        tcp=None,
        serial=None,
        file=hex_path,
        out=tmp_path,
        label="capability-guided",
        session_dir=session,
        only=None,
        skip_id=None,
        from_id=None,
        catalog=cat,
        arm_on_prompt=True,
        input_fn=_ScriptedInput(["d\n", "s\n"]),
    )
    assert rc == 0
    snap = json.loads((session / "catalog_snapshot.json").read_text(encoding="utf-8"))
    assert [c["id"] for c in snap] == ["mini_a", "mini_b"]


def test_parent_samples_smoke(tmp_path: Path, samples_dir: Path) -> None:
    fixture = samples_dir / "status_temps.hex"
    if not fixture.is_file():
        fixture = samples_dir / "log_breakdown" / "001_baseline_filter_on.hex"
    if not fixture.is_file():
        pytest.skip("parent samples hex fixtures not present")
    dest = tmp_path / "parent-hex"
    counts = run_capability_session(
        HexFileTransport(fixture),
        dest,
        filter_catalog(
            DEFAULT_CATALOG,
            only=["idle_baseline", "spa_on_off", "cleaner_on_off"],
        ),
        input_fn=_ScriptedInput(["d\n", "s\n", "q\n"]),
        output=io.StringIO(),
    )
    assert counts["done"] + counts["skipped"] + counts["not_run"] == 3
    assert json.loads((dest / "meta.json").read_text(encoding="utf-8"))[
        "mode"
    ] == "capability_guided"


def test_arm_on_key_quit_before_arm(tmp_path: Path) -> None:
    hex_path = tmp_path / "one.hex"
    hex_path.write_text(_INFO, encoding="utf-8")
    walk = filter_catalog(DEFAULT_CATALOG, only=["idle_baseline", "spa_on_off"])
    counts = run_capability_session(
        HexFileTransport(hex_path),
        tmp_path / "q-before-arm",
        walk,
        arm_on_prompt=False,
        input_fn=_ScriptedInput(["q\n"]),
        output=io.StringIO(),
    )
    assert counts["not_run"] >= 1


def test_writer_idempotent_open_close(tmp_path: Path) -> None:
    caps = [Capability("x", "baseline", "p", False)]
    writer = CapabilitySessionWriter(tmp_path / "idemp", catalog=caps)
    writer.open()
    writer.open()
    writer.close()
    writer.close()
    assert (tmp_path / "idemp" / "meta.json").is_file()


def test_load_catalog_used_by_cli() -> None:
    assert len(load_catalog()) == 17


def test_writer_root_and_active_id(tmp_path: Path) -> None:
    caps = [Capability("x", "baseline", "p", False)]
    writer = CapabilitySessionWriter(tmp_path / "props", catalog=caps)
    writer.open()
    assert writer.root is not None
    assert writer.active_capability_id is None
    writer.arm(caps[0], seq=1)
    assert writer.active_capability_id == "x"
    # Unarmed frame path (no dual-write).
    writer.disarm()
    writer.write_frame(
        Frame(kind=FrameKind.A5, raw=bytes.fromhex(_INFO), checksum_ok=True)
    )
    writer.close()


def test_counts_include_error_status(tmp_path: Path) -> None:
    from pentairsnoop.capability_session import CapabilityAttempt, STATUS_ERROR

    writer = CapabilitySessionWriter(tmp_path / "err", catalog=[])
    writer.open()
    writer.append_index(
        CapabilityAttempt(
            seq=1,
            capability_id="x",
            status=STATUS_ERROR,
            started_at="t",
            ended_at="t",
        )
    )
    assert writer.counts()["error"] == 1
    writer.close()


def test_phase_property_and_blank_key(tmp_path: Path) -> None:
    hex_path = tmp_path / "one.hex"
    hex_path.write_text(_INFO, encoding="utf-8")
    walk = filter_catalog(DEFAULT_CATALOG, only=["idle_baseline"])
    writer = CapabilitySessionWriter(tmp_path / "phase", catalog=walk)
    config = CapabilitySessionConfig(catalog=walk)
    session = CapabilitySession(
        HexFileTransport(hex_path),
        writer,
        config,
        input_fn=_ScriptedInput(["\n", "d\n"]),
        output=io.StringIO(),
    )
    assert session.phase is SessionPhase.SESSION_INIT
    counts = session.run()
    assert counts["done"] == 1
    assert session.phase is SessionPhase.SESSION_END


def test_transport_without_remaining(tmp_path: Path) -> None:
    class _NoRemaining:
        def __init__(self) -> None:
            self._once = True

        def open(self) -> None:
            return None

        def close(self) -> None:
            return None

        def read(self, n: int) -> bytes:
            if self._once:
                self._once = False
                return bytes.fromhex(_INFO)
            return b""

        def write(self, data: bytes) -> None:
            raise AssertionError("no write")

    walk = filter_catalog(DEFAULT_CATALOG, only=["idle_baseline"])
    counts = run_capability_session(
        _NoRemaining(),  # type: ignore[arg-type]
        tmp_path / "no-rem",
        walk,
        input_fn=_ScriptedInput(["d\n"]),
        output=io.StringIO(),
    )
    assert counts["done"] == 1


def test_reader_survives_read_errors(tmp_path: Path) -> None:
    class _Flaky:
        def __init__(self) -> None:
            self.n = 0

        def open(self) -> None:
            return None

        def close(self) -> None:
            raise RuntimeError("close boom")

        def read(self, n: int) -> bytes:
            self.n += 1
            if self.n < 3:
                raise OSError("transient")
            if self.n == 3:
                return bytes.fromhex(_INFO)
            return b""

        def write(self, data: bytes) -> None:
            raise AssertionError("no write")

        @property
        def remaining(self) -> int:
            return 0 if self.n >= 3 else 1

    walk = filter_catalog(DEFAULT_CATALOG, only=["idle_baseline"])
    counts = run_capability_session(
        _Flaky(),  # type: ignore[arg-type]
        tmp_path / "flaky",
        walk,
        input_fn=_ScriptedInput(["d\n"]),
        output=io.StringIO(),
    )
    assert counts["done"] == 1


def test_cli_keyboard_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    hex_path = tmp_path / "one.hex"
    hex_path.write_text(_INFO, encoding="utf-8")

    def boom(*_a: object, **_k: object) -> dict:
        raise KeyboardInterrupt

    monkeypatch.setattr("pentairsnoop.cli.run_capability_session", boom)
    rc = cmd_capture_capabilities(
        tcp=None,
        serial=None,
        file=hex_path,
        out=tmp_path,
        label="x",
        session_dir=tmp_path / "ki",
        only="idle_baseline",
        skip_id=None,
        from_id=None,
        catalog=None,
        arm_on_prompt=True,
    )
    assert rc == 0
    assert "interrupted" in capsys.readouterr().err


def test_stop_reader_flushes_partial_frame(tmp_path: Path) -> None:
    """Incomplete A5 prefix in framer buffer is flushed on stop (may yield 0)."""
    class _PartialThenEmpty:
        def __init__(self) -> None:
            self.calls = 0

        def open(self) -> None:
            return None

        def close(self) -> None:
            return None

        def read(self, n: int) -> bytes:
            self.calls += 1
            if self.calls == 1:
                return b"\xff\x00\xff\xa5"  # incomplete
            return b""

        def write(self, data: bytes) -> None:
            raise AssertionError("no write")

        @property
        def remaining(self) -> int:
            return 0 if self.calls >= 1 else 4

    walk = filter_catalog(DEFAULT_CATALOG, only=["idle_baseline"])
    counts = run_capability_session(
        _PartialThenEmpty(),  # type: ignore[arg-type]
        tmp_path / "partial",
        walk,
        input_fn=_ScriptedInput(["d\n"]),
        output=io.StringIO(),
    )
    assert counts["done"] == 1


def test_ensure_reader_idempotent(tmp_path: Path) -> None:
    hex_path = tmp_path / "one.hex"
    hex_path.write_text(_INFO, encoding="utf-8")
    walk = filter_catalog(DEFAULT_CATALOG, only=["idle_baseline"])
    writer = CapabilitySessionWriter(tmp_path / "ens", catalog=walk)
    session = CapabilitySession(
        HexFileTransport(hex_path),
        writer,
        CapabilitySessionConfig(catalog=walk),
        input_fn=_ScriptedInput(["d\n"]),
        output=io.StringIO(),
    )
    writer.open()
    session._ensure_reader()  # noqa: SLF001
    session._ensure_reader()  # noqa: SLF001 — second call no-op
    session._stop_reader()  # noqa: SLF001
    session._stop_reader()  # noqa: SLF001 — reader already None
    writer.close()
