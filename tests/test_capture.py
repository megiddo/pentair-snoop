"""A4 capture format tests — fixture-based; no invented panel-success corpus."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pentairsnoop.capture import (
    CAPTURE_FORMAT_VERSION,
    CaptureWriter,
    RecordingTransport,
    default_session_dirname,
    describe_source,
    format_timestamp,
    run_capture,
    utc_now,
)
from pentairsnoop.cli import build_parser, cmd_capture, main
from pentairsnoop.framer import Frame, FrameKind
from pentairsnoop.transport import HexFileTransport

# Minimal TempStatus/Info frame (same vector as transport / decode tests).
_INFO = "ffffffffffffffff00ffa50c0f10080d4848555a620400000000000000028a"


class _FixedClock:
    def __init__(self, start: datetime) -> None:
        self._t = start

    def __call__(self) -> datetime:
        return self._t

    def advance_us(self, us: int) -> None:
        from datetime import timedelta

        self._t = self._t + timedelta(microseconds=us)


def test_format_timestamp_zulu() -> None:
    ts = datetime(2026, 7, 20, 15, 30, 45, 123456, tzinfo=timezone.utc)
    assert format_timestamp(ts) == "2026-07-20T15:30:45.123456Z"


def test_format_timestamp_naive_assumes_utc() -> None:
    ts = datetime(2026, 1, 2, 3, 4, 5, 6)
    assert format_timestamp(ts).endswith("Z")
    assert format_timestamp(ts).startswith("2026-01-02T03:04:05")


def test_default_session_dirname() -> None:
    when = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)
    assert default_session_dirname("idle-status", when=when) == (
        "20260720T120000Z_idle-status"
    )
    assert default_session_dirname("a/b c!", when=when).endswith("_a-b-c")


def test_describe_source() -> None:
    assert describe_source(file=Path("x.hex")) == {"kind": "file", "path": "x.hex"}
    assert describe_source(tcp="10.0.0.11:8899") == {
        "kind": "tcp",
        "endpoint": "10.0.0.11:8899",
    }
    assert describe_source(serial="/dev/ttyUSB0") == {
        "kind": "serial",
        "port": "/dev/ttyUSB0",
    }
    assert describe_source() == {"kind": "unknown"}


def test_capture_writer_raw_and_frame_format(tmp_path: Path) -> None:
    clock = _FixedClock(datetime(2026, 7, 20, 10, 0, 0, tzinfo=timezone.utc))
    writer = CaptureWriter(
        tmp_path / "sess",
        label="format-unit",
        source={"kind": "file", "path": "fixture.hex"},
        clock=clock,
    )
    with writer:
        writer.write_raw(bytes.fromhex("ff00ffa5"))
        clock.advance_us(1000)
        writer.write_frame(
            Frame(kind=FrameKind.A5, raw=bytes.fromhex(_INFO), checksum_ok=True)
        )

    raw_lines = writer.raw_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(raw_lines) == 1
    raw_obj = json.loads(raw_lines[0])
    assert raw_obj == {
        "t": "2026-07-20T10:00:00.000000Z",
        "offset": 0,
        "len": 4,
        "hex": "ff00ffa5",
    }

    frame_lines = writer.frames_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(frame_lines) == 1
    frame_obj = json.loads(frame_lines[0])
    assert frame_obj["t"] == "2026-07-20T10:00:00.001000Z"
    assert frame_obj["seq"] == 0
    assert frame_obj["kind"] == "a5"
    assert frame_obj["checksum_ok"] is True
    assert frame_obj["raw"] == _INFO

    meta = json.loads(writer.meta_path.read_text(encoding="utf-8"))
    assert meta["format_version"] == CAPTURE_FORMAT_VERSION
    assert meta["label"] == "format-unit"
    assert meta["raw_chunks"] == 1
    assert meta["frames"] == 1
    assert meta["source"]["kind"] == "file"
    assert meta["started_at"]
    assert meta["ended_at"]
    assert writer.notes_path.is_file()


def test_capture_writer_skips_empty_raw(tmp_path: Path) -> None:
    writer = CaptureWriter(tmp_path / "sess")
    writer.open()
    writer.write_raw(b"")
    writer.close()
    assert writer.raw_path.read_text(encoding="utf-8") == ""
    assert json.loads(writer.meta_path.read_text(encoding="utf-8"))["raw_chunks"] == 0


def test_capture_writer_requires_open(tmp_path: Path) -> None:
    writer = CaptureWriter(tmp_path / "sess")
    with pytest.raises(RuntimeError, match="not open"):
        writer.write_raw(b"\xff")
    with pytest.raises(RuntimeError, match="not open"):
        writer.write_frame(
            Frame(kind=FrameKind.A5, raw=b"\xa5", checksum_ok=False)
        )


def test_recording_transport_tees_reads(tmp_path: Path) -> None:
    hex_path = tmp_path / "one.hex"
    hex_path.write_text(_INFO, encoding="utf-8")
    writer = CaptureWriter(tmp_path / "out", label="tee")
    writer.open()
    try:
        inner = HexFileTransport(hex_path)
        tee = RecordingTransport(inner, writer)
        tee.open()
        chunk = tee.read(8)
        assert chunk == bytes.fromhex(_INFO)[:8]
        rest = tee.read(10_000)
        tee.close()
    finally:
        writer.close()
    raw = [
        json.loads(line)
        for line in writer.raw_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert sum(r["len"] for r in raw) == len(bytes.fromhex(_INFO))
    assert raw[0]["offset"] == 0
    assert raw[1]["offset"] == 8


def test_run_capture_from_hex_fixture(tmp_path: Path, samples_dir: Path) -> None:
    fixture = samples_dir / "log_breakdown" / "001_baseline_filter_on.hex"
    if not fixture.is_file():
        fixture = samples_dir / "status_temps.hex"
    if not fixture.is_file():
        pytest.skip("parent samples hex fixtures not present")
    out = tmp_path / "from-fixture"
    writer = CaptureWriter(
        out,
        label="format-fixture",
        source=describe_source(file=fixture),
    )
    n = run_capture(HexFileTransport(fixture), writer, max_frames=5)
    assert n >= 1
    frames = [
        json.loads(line)
        for line in writer.frames_path.read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert len(frames) == n
    assert all("t" in f and "raw" in f and "kind" in f for f in frames)
    meta = json.loads(writer.meta_path.read_text(encoding="utf-8"))
    assert meta["frames"] == n
    assert meta["raw_chunks"] >= 1
    # Sanity: every framed hex must appear concatenated in raw hex stream.
    raw_hex = "".join(
        json.loads(line)["hex"]
        for line in writer.raw_path.read_text(encoding="utf-8").splitlines()
        if line
    )
    for f in frames:
        assert f["raw"] in raw_hex


def test_run_capture_max_frames_stops_early(tmp_path: Path) -> None:
    """Two back-to-back known frames; max_frames=1 must stop after one."""
    hex_path = tmp_path / "two.hex"
    hex_path.write_text(_INFO + _INFO, encoding="utf-8")
    writer = CaptureWriter(tmp_path / "cap", label="max1")
    n = run_capture(HexFileTransport(hex_path), writer, max_frames=1)
    assert n == 1
    assert json.loads(writer.meta_path.read_text(encoding="utf-8"))["frames"] == 1


def test_format_timestamp_converts_non_utc() -> None:
    from datetime import timedelta

    # Fixed offset east of UTC → convert to Z.
    tz = timezone(timedelta(hours=2))
    ts = datetime(2026, 7, 20, 12, 0, 0, tzinfo=tz)
    assert format_timestamp(ts) == "2026-07-20T10:00:00.000000Z"


def test_default_session_dirname_naive_and_empty_label() -> None:
    when = datetime(2026, 7, 20, 12, 0, 0)  # naive
    name = default_session_dirname("  ", when=when)
    assert name.startswith("20260720T120000Z_")
    assert name.endswith("_session")


def test_capture_writer_idempotent_open_close(tmp_path: Path) -> None:
    notes = tmp_path / "sess" / "NOTES.md"
    writer = CaptureWriter(tmp_path / "sess", label="idem")
    writer.open()
    notes.write_text("# pre-existing\n", encoding="utf-8")
    writer.open()  # second open no-op
    writer.write_raw(b"\xff")
    writer.close()
    writer.close()  # second close no-op
    assert notes.read_text(encoding="utf-8") == "# pre-existing\n"


def test_cli_capture_keyboard_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    hex_path = tmp_path / "one.hex"
    hex_path.write_text(_INFO, encoding="utf-8")

    def boom(*_a: object, **_k: object):
        raise KeyboardInterrupt

    monkeypatch.setattr("pentairsnoop.cli.iter_capture_frames", boom)
    session = tmp_path / "interrupted"
    assert (
        main(
            [
                "capture",
                "--file",
                str(hex_path),
                "--session-dir",
                str(session),
            ]
        )
        == 0
    )
    assert "interrupted" in capsys.readouterr().err
    assert (session / "meta.json").is_file()


def test_iter_capture_frames_yields(tmp_path: Path) -> None:
    from pentairsnoop.capture import iter_capture_frames

    hex_path = tmp_path / "one.hex"
    hex_path.write_text(_INFO, encoding="utf-8")
    writer = CaptureWriter(tmp_path / "it")
    writer.open()
    try:
        frames = list(iter_capture_frames(HexFileTransport(hex_path), writer))
        for fr in frames:
            writer.write_frame(fr)
    finally:
        writer.close()
    assert len(frames) == 1


def test_cli_capture_file_format(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    hex_path = tmp_path / "one.hex"
    hex_path.write_text(_INFO, encoding="utf-8")
    session = tmp_path / "cli-session"
    assert (
        main(
            [
                "capture",
                "--file",
                str(hex_path),
                "--session-dir",
                str(session),
                "--label",
                "format-cli",
                "--max-frames",
                "1",
            ]
        )
        == 0
    )
    err = capsys.readouterr().err
    assert "1 frames" in err
    assert session.is_dir()
    frames = [
        json.loads(line)
        for line in (session / "frames.ndjson").read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert len(frames) == 1
    assert frames[0]["kind"] == "a5"
    meta = json.loads((session / "meta.json").read_text(encoding="utf-8"))
    assert meta["label"] == "format-cli"
    assert meta["source"]["kind"] == "file"


def test_cli_capture_missing_file() -> None:
    assert main(["capture", "--file", "/no/such/file.hex"]) == 1


def test_cli_capture_auto_session_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hex_path = tmp_path / "one.hex"
    hex_path.write_text(_INFO, encoding="utf-8")
    monkeypatch.setattr(
        "pentairsnoop.cli.default_session_dirname",
        lambda label, when=None: f"FIXED_{label}",
    )
    out = tmp_path / "captures"
    assert (
        main(
            [
                "capture",
                "--file",
                str(hex_path),
                "-o",
                str(out),
                "--label",
                "auto",
                "--max-frames",
                "1",
            ]
        )
        == 0
    )
    assert (out / "FIXED_auto" / "meta.json").is_file()


def test_cmd_capture_value_error(capsys: pytest.CaptureFixture[str]) -> None:
    # Force transport builder failure path via empty mutually exclusive args
    # by calling cmd_capture directly with no source.
    rc = cmd_capture(
        tcp=None,
        serial=None,
        file=None,
        out=Path("/tmp"),
        label="x",
        session_dir=None,
        max_frames=1,
    )
    assert rc == 1
    assert "error:" in capsys.readouterr().err


def test_capture_help_present() -> None:
    help_text = build_parser().format_help()
    assert "capture" in help_text
    assert "raw.ndjson" in help_text or "framed" in help_text.lower() or "capture" in help_text


def test_samples_captures_readme_exists() -> None:
    readme = (
        Path(__file__).resolve().parents[1] / "samples" / "captures" / "README.md"
    )
    assert readme.is_file()
    text = readme.read_text(encoding="utf-8")
    assert "pending" in text.lower()
    assert "Do **not** add fabricated" in text or "fabricated" in text.lower()


def test_capture_procedure_doc_exists() -> None:
    doc = Path(__file__).resolve().parents[1] / "docs" / "capture-procedure.md"
    assert doc.is_file()
    text = doc.read_text(encoding="utf-8")
    assert "RS485" in text
    assert "termination" in text.lower() or "terminator" in text.lower()


def test_utc_now_aware() -> None:
    now = utc_now()
    assert now.tzinfo is not None


def test_recording_transport_write_delegates(tmp_path: Path) -> None:
    class _Mem:
        def __init__(self) -> None:
            self.written = b""

        def open(self) -> None:
            return None

        def close(self) -> None:
            return None

        def read(self, n: int) -> bytes:
            return b""[:n]

        def write(self, data: bytes) -> None:
            self.written += data

    inner = _Mem()
    writer = CaptureWriter(tmp_path / "w")
    writer.open()
    try:
        tee = RecordingTransport(inner, writer)  # type: ignore[arg-type]
        tee.write(b"\x01\x02")
        assert inner.written == b"\x01\x02"
        assert tee.inner is inner
    finally:
        writer.close()
