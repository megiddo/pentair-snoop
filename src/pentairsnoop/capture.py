"""Pattern: Writer + Decorator — timestamped raw/framed capture logs."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

from pentairsnoop import __version__
from pentairsnoop.framer import Frame
from pentairsnoop.session import Session
from pentairsnoop.transport import Transport

CAPTURE_FORMAT_VERSION = 1


def utc_now() -> datetime:
    """Return an aware UTC timestamp (injectable for tests)."""
    return datetime.now(timezone.utc)


def format_timestamp(ts: datetime) -> str:
    """ISO-8601 UTC with microseconds; always ``Z`` suffix."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    else:
        ts = ts.astimezone(timezone.utc)
    return ts.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def default_session_dirname(label: str, *, when: datetime | None = None) -> str:
    """Build ``YYYYMMDDTHHMMSSZ_<label>`` session directory name."""
    ts = when if when is not None else utc_now()
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    else:
        ts = ts.astimezone(timezone.utc)
    stamp = ts.strftime("%Y%m%dT%H%M%SZ")
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in label.strip())
    safe = safe.strip("-_") or "session"
    return f"{stamp}_{safe}"


@dataclass
class CaptureMeta:
    """Session metadata written to ``meta.json``."""

    format_version: int = CAPTURE_FORMAT_VERSION
    started_at: str = ""
    ended_at: str = ""
    label: str = ""
    source: dict = field(default_factory=dict)
    tool: str = "pentairsnoop"
    tool_version: str = __version__
    raw_chunks: int = 0
    frames: int = 0
    notes: str = (
        "Annotate after live capture. Do not invent panel-success TX without bus evidence."
    )

    def to_dict(self) -> dict:
        return {
            "format_version": self.format_version,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "label": self.label,
            "source": self.source,
            "tool": self.tool,
            "tool_version": self.tool_version,
            "raw_chunks": self.raw_chunks,
            "frames": self.frames,
            "notes": self.notes,
        }


class CaptureWriter:
    """Pattern: Writer — dual NDJSON streams (raw chunks + framed messages).

    Layout under ``session_dir``::

        meta.json      session metadata
        raw.ndjson     one JSON object per transport read chunk
        frames.ndjson  one JSON object per extracted frame
        NOTES.md       human annotation stub
    """

    def __init__(
        self,
        session_dir: Path,
        *,
        label: str = "",
        source: dict | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.session_dir = Path(session_dir)
        self._clock = clock
        self._raw_fp: TextIO | None = None
        self._frames_fp: TextIO | None = None
        self._byte_offset = 0
        self._frame_seq = 0
        self._open = False
        self.meta = CaptureMeta(
            label=label,
            source=dict(source or {}),
        )

    @property
    def raw_path(self) -> Path:
        return self.session_dir / "raw.ndjson"

    @property
    def frames_path(self) -> Path:
        return self.session_dir / "frames.ndjson"

    @property
    def meta_path(self) -> Path:
        return self.session_dir / "meta.json"

    @property
    def notes_path(self) -> Path:
        return self.session_dir / "NOTES.md"

    def open(self) -> None:
        """Create the session directory and open log files."""
        if self._open:
            return
        self.session_dir.mkdir(parents=True, exist_ok=True)
        started = self._clock()
        self.meta.started_at = format_timestamp(started)
        self._raw_fp = self.raw_path.open("w", encoding="utf-8")
        self._frames_fp = self.frames_path.open("w", encoding="utf-8")
        if not self.notes_path.exists():
            self.notes_path.write_text(
                _notes_template(self.meta.label),
                encoding="utf-8",
            )
        self._open = True

    def close(self) -> None:
        """Flush streams and write ``meta.json`` (idempotent)."""
        if not self._open:
            return
        ended = self._clock()
        self.meta.ended_at = format_timestamp(ended)
        if self._raw_fp is not None:
            self._raw_fp.close()
            self._raw_fp = None
        if self._frames_fp is not None:
            self._frames_fp.close()
            self._frames_fp = None
        self.meta_path.write_text(
            json.dumps(self.meta.to_dict(), indent=2) + "\n",
            encoding="utf-8",
        )
        self._open = False

    def __enter__(self) -> CaptureWriter:
        self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def write_raw(self, data: bytes, *, ts: datetime | None = None) -> None:
        """Append one raw chunk line (skipped if ``data`` is empty)."""
        if not self._open or self._raw_fp is None:
            raise RuntimeError("CaptureWriter is not open")
        if not data:
            return
        when = ts if ts is not None else self._clock()
        rec = {
            "t": format_timestamp(when),
            "offset": self._byte_offset,
            "len": len(data),
            "hex": data.hex(),
        }
        self._raw_fp.write(json.dumps(rec, separators=(",", ":")) + "\n")
        self._raw_fp.flush()
        self._byte_offset += len(data)
        self.meta.raw_chunks += 1

    def write_frame(self, frame: Frame, *, ts: datetime | None = None) -> None:
        """Append one framed message line."""
        if not self._open or self._frames_fp is None:
            raise RuntimeError("CaptureWriter is not open")
        when = ts if ts is not None else self._clock()
        rec = {
            "t": format_timestamp(when),
            "seq": self._frame_seq,
            "kind": frame.kind.value,
            "checksum_ok": frame.checksum_ok,
            "raw": frame.raw.hex(),
        }
        self._frames_fp.write(json.dumps(rec, separators=(",", ":")) + "\n")
        self._frames_fp.flush()
        self._frame_seq += 1
        self.meta.frames += 1


class RecordingTransport:
    """Pattern: Decorator — tee each non-empty ``read`` into a :class:`CaptureWriter`.

    Forwards open/close/write to the inner Strategy transport.
    """

    def __init__(self, inner: Transport, writer: CaptureWriter) -> None:
        self._inner = inner
        self._writer = writer

    @property
    def inner(self) -> Transport:
        return self._inner

    def open(self) -> None:
        self._inner.open()

    def close(self) -> None:
        self._inner.close()

    def read(self, n: int) -> bytes:
        data = self._inner.read(n)
        if data:
            self._writer.write_raw(data)
        return data

    def write(self, data: bytes) -> None:
        self._inner.write(data)


def run_capture(
    transport: Transport,
    writer: CaptureWriter,
    *,
    max_frames: int | None = None,
    chunk_size: int = 256,
) -> int:
    """Record raw+framed logs until EOF or ``max_frames``.

    Uses :class:`RecordingTransport` + :meth:`Session.iter_frames` (persistent
    connection; no open/close per frame).
    """
    writer.open()
    try:
        recorded = RecordingTransport(transport, writer)
        session = Session(recorded)
        count = 0
        for frame in session.iter_frames(chunk_size):
            writer.write_frame(frame)
            count += 1
            if max_frames is not None and count >= max_frames:
                break
        return count
    finally:
        writer.close()


def iter_capture_frames(
    transport: Transport,
    writer: CaptureWriter,
    *,
    chunk_size: int = 256,
    manage_lifecycle: bool = True,
) -> Iterator[Frame]:
    """Yield frames while writing raw+framed capture lines (for CLI interrupt)."""
    recorded = RecordingTransport(transport, writer)
    session = Session(recorded)
    yield from session.iter_frames(chunk_size, manage_lifecycle=manage_lifecycle)


def _notes_template(label: str) -> str:
    title = label or "(unlabeled)"
    return (
        f"# Capture notes — {title}\n\n"
        "## Intent\n\n"
        "- [ ] Idle status baseline\n"
        "- [ ] Circuit toggle (which circuit / from panel or remote)\n"
        "- [ ] Heat / setpoint change\n\n"
        "## Observed (fill after live session)\n\n"
        "- Start conditions:\n"
        "- Actions taken:\n"
        "- Expected bus TX (cmd / addresses) — **only if observed**:\n"
        "- Outcome:\n\n"
        "## Integrity\n\n"
        "Do **not** paste invented “successful panel” TX. Annotate only what the\n"
        "raw/framed logs show on this controller.\n"
    )


def describe_source(
    *,
    tcp: str | None = None,
    serial: str | None = None,
    file: Path | None = None,
) -> dict:
    """Build the ``meta.source`` object for a capture session."""
    if file is not None:
        return {"kind": "file", "path": str(file)}
    if tcp is not None:
        return {"kind": "tcp", "endpoint": tcp}
    if serial is not None:
        return {"kind": "serial", "port": serial}
    return {"kind": "unknown"}
