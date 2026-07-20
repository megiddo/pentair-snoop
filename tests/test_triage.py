"""Unit tests for offline capability-session triage (A2.3)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pentairsnoop import triage
from pentairsnoop.cli import build_parser, cmd_extract_capability, cmd_triage_capabilities, main
from pentairsnoop.triage import (
    CapabilityIndexRow,
    extract_frames,
    format_summary_table,
    iter_frames_ndjson,
    load_index,
    summarize_session,
    write_frames_ndjson,
)


def _write_session(tmp_path: Path) -> Path:
    """Build a tiny synthetic capability session for offline triage tests."""
    session = tmp_path / "20260720T120000Z_capability-guided"
    session.mkdir()
    index_lines = [
        {
            "seq": 1,
            "capability_id": "idle_baseline",
            "status": "done",
            "started_at": "2026-07-20T12:00:00Z",
            "ended_at": "2026-07-20T12:01:00Z",
            "frame_seq_start": 1,
            "frame_seq_end": 2,
            "frames": 2,
            "dir": "by_capability/01_idle_baseline",
            "skip_ok": False,
            "attempt": 1,
        },
        {
            "seq": 2,
            "capability_id": "spa_on_off",
            "status": "done",
            "started_at": "2026-07-20T12:01:00Z",
            "ended_at": "2026-07-20T12:02:00Z",
            "frame_seq_start": 3,
            "frame_seq_end": 4,
            "frames": 2,
            "dir": "by_capability/02_spa_on_off",
            "skip_ok": False,
            "attempt": 1,
        },
        {
            "seq": 3,
            "capability_id": "cleaner_on_off",
            "status": "skipped",
            "started_at": "2026-07-20T12:02:00Z",
            "ended_at": "2026-07-20T12:02:01Z",
            "frame_seq_start": None,
            "frame_seq_end": None,
            "frames": 0,
            "dir": "by_capability/03_cleaner_on_off",
            "skip_ok": True,
            "attempt": 1,
        },
        {
            "seq": 4,
            "capability_id": "filter_pump_on_off",
            "status": "not_run",
            "started_at": "",
            "ended_at": "",
            "frames": 0,
            "dir": "",
            "skip_ok": False,
            "attempt": 1,
        },
    ]
    with (session / "index.jsonl").open("w", encoding="utf-8") as fh:
        for obj in index_lines:
            fh.write(json.dumps(obj) + "\n")
        fh.write("\n")  # blank line tolerated

    frames = [
        {"t": "…", "seq": 1, "kind": "a5", "checksum_ok": True, "raw": "aa", "capability_id": "idle_baseline"},
        {"t": "…", "seq": 2, "kind": "a5", "checksum_ok": True, "raw": "bb", "capability_id": "idle_baseline"},
        {"t": "…", "seq": 3, "kind": "a5", "checksum_ok": True, "raw": "cc", "capability_id": "spa_on_off"},
        {"t": "…", "seq": 4, "kind": "a5", "checksum_ok": True, "raw": "dd", "capability_id": "spa_on_off"},
        {"t": "…", "seq": 5, "kind": "a5", "checksum_ok": True, "raw": "ee"},  # unarmed / no id
    ]
    with (session / "frames.ndjson").open("w", encoding="utf-8") as fh:
        for obj in frames:
            fh.write(json.dumps(obj) + "\n")

    # Incomplete dual-write: only idle slice present; spa missing on purpose.
    idle_dir = session / "by_capability" / "01_idle_baseline"
    idle_dir.mkdir(parents=True)
    with (idle_dir / "frames.ndjson").open("w", encoding="utf-8") as fh:
        for obj in frames[:2]:
            fh.write(json.dumps(obj) + "\n")

    return session


def test_load_index_and_summarize(tmp_path: Path) -> None:
    session = _write_session(tmp_path)
    rows = load_index(session)
    assert len(rows) == 4
    assert rows[0].capability_id == "idle_baseline"
    assert rows[0].frames == 2
    assert rows[2].status == "skipped"
    assert rows[3].status == "not_run"
    assert rows[3].frame_seq_start is None

    summary = summarize_session(session)
    assert summary.session_dir == session.resolve()
    assert summary.counts_by_status == {"done": 2, "skipped": 1, "not_run": 1}
    assert summary.total_frames == 4
    assert [r.capability_id for r in summary.done] == ["idle_baseline", "spa_on_off"]
    assert [r.capability_id for r in summary.skipped] == ["cleaner_on_off"]
    assert [r.capability_id for r in summary.not_run] == ["filter_pump_on_off"]


def test_format_summary_table(tmp_path: Path) -> None:
    session = _write_session(tmp_path)
    text = format_summary_table(summarize_session(session))
    assert "idle_baseline" in text
    assert "spa_on_off" in text
    assert "done=2" in text
    assert "skipped=1" in text
    assert "not_run=1" in text


def test_extract_frames_filters_capability_id(tmp_path: Path) -> None:
    session = _write_session(tmp_path)
    spa = extract_frames(session, "spa_on_off")
    assert len(spa) == 2
    assert all(f["capability_id"] == "spa_on_off" for f in spa)
    assert spa[0]["raw"] == "cc"

    idle = extract_frames(session, "idle_baseline")
    assert len(idle) == 2

    missing = extract_frames(session, "filter_pump_on_off")
    assert missing == []


def test_write_frames_ndjson_to_path_and_stream(tmp_path: Path) -> None:
    frames = [{"seq": 1, "capability_id": "spa_on_off"}, {"seq": 2}]
    out = tmp_path / "out" / "spa.ndjson"
    assert write_frames_ndjson(frames, out) == 2
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["capability_id"] == "spa_on_off"

    import io

    buf = io.StringIO()
    assert write_frames_ndjson(frames, buf) == 2
    assert buf.getvalue().count("\n") == 2


def test_capability_slice_path(tmp_path: Path) -> None:
    session = _write_session(tmp_path)
    rows = load_index(session)
    idle_path = triage.capability_slice_path(session, rows[0])
    assert idle_path is not None
    assert idle_path.is_file()
    # spa dual-write missing
    assert triage.capability_slice_path(session, rows[1]) is None
    # not_run has empty dir
    assert triage.capability_slice_path(session, rows[3]) is None


def test_index_missing_and_bad_json(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(FileNotFoundError, match="index.jsonl"):
        load_index(empty)

    session = tmp_path / "bad"
    session.mkdir()
    (session / "index.jsonl").write_text("not-json\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSON"):
        load_index(session)

    (session / "index.jsonl").write_text("[1,2]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="expected JSON object"):
        load_index(session)

    with pytest.raises(FileNotFoundError, match="session directory"):
        summarize_session(tmp_path / "nope")


def test_extract_errors(tmp_path: Path) -> None:
    session = _write_session(tmp_path)
    assert triage.frames_path(session) == session.resolve() / "frames.ndjson"

    with pytest.raises(ValueError, match="non-empty"):
        extract_frames(session, "")

    with pytest.raises(FileNotFoundError, match="frames.ndjson"):
        extract_frames(session, "spa_on_off", frames_file=session / "missing.ndjson")

    blanky = session / "blank_frames.ndjson"
    blanky.write_text(
        "\n"
        + json.dumps({"seq": 1, "capability_id": "spa_on_off"})
        + "\n\n",
        encoding="utf-8",
    )
    assert len(list(iter_frames_ndjson(blanky))) == 1

    bad = session / "bad_frames.ndjson"
    bad.write_text("{\n", encoding="utf-8")
    with pytest.raises(ValueError, match="invalid JSON"):
        list(iter_frames_ndjson(bad))

    bad.write_text("[]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="expected JSON object"):
        list(iter_frames_ndjson(bad))


def test_row_roundtrip() -> None:
    row = CapabilityIndexRow.from_dict(
        {
            "seq": 9,
            "capability_id": "spa_temp_set",
            "status": "done",
            "frames": 3,
            "dir": "by_capability/09_spa_temp_set",
            "extra": "ignored",
        }
    )
    d = row.to_dict()
    assert d["capability_id"] == "spa_temp_set"
    assert d["frames"] == 3
    assert CapabilityIndexRow.from_dict(d).seq == 9


def test_cmd_triage_capabilities(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    session = _write_session(tmp_path)
    assert cmd_triage_capabilities(session, as_json=False) == 0
    out = capsys.readouterr().out
    assert "idle_baseline" in out
    assert "done=2" in out

    assert cmd_triage_capabilities(session, as_json=True) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["counts_by_status"]["done"] == 2
    assert len(payload["capabilities"]) == 4

    assert cmd_triage_capabilities(tmp_path / "missing", as_json=False) == 1


def test_cmd_extract_capability(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    session = _write_session(tmp_path)
    out_path = tmp_path / "spa_slice.ndjson"
    assert (
        cmd_extract_capability(session, "spa_on_off", out=out_path) == 0
    )
    err = capsys.readouterr().err
    assert "wrote 2 frames" in err
    lines = out_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2

    assert cmd_extract_capability(session, "spa_on_off", out=None) == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 2

    assert cmd_extract_capability(tmp_path / "missing", "spa_on_off", out=None) == 1


def test_main_triage_and_extract(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    session = _write_session(tmp_path)
    assert main(["triage-capabilities", str(session)]) == 0
    assert "spa_on_off" in capsys.readouterr().out

    out = tmp_path / "extracted.ndjson"
    assert main(["extract-capability", str(session), "idle_baseline", "-o", str(out)]) == 0
    assert out.is_file()
    assert len(out.read_text(encoding="utf-8").strip().splitlines()) == 2


def test_cli_help_lists_triage_commands() -> None:
    help_text = build_parser().format_help()
    assert "triage-capabilities" in help_text
    assert "extract-capability" in help_text


def test_format_summary_includes_error_status(tmp_path: Path) -> None:
    session = tmp_path / "err_session"
    session.mkdir()
    (session / "index.jsonl").write_text(
        json.dumps(
            {
                "seq": 1,
                "capability_id": "spa_on_off",
                "status": "error",
                "frames": 0,
                "dir": "",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    text = format_summary_table(summarize_session(session))
    assert "error=1" in text


def test_module_pattern_docstring() -> None:
    assert triage.__doc__ is not None
    assert "Pattern:" in triage.__doc__
