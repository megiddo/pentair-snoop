"""Pattern: State Machine + Producer-Consumer — capability-guided listen capture.

Interactive walk (main thread / stdin) arms one catalog entry at a time while a
background reader (producer) continuously drains the transport into framed
capture logs (consumer). Hard rule: this path never calls transport ``write``
or craft send.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from pathlib import Path
from typing import TextIO

from pentairsnoop.capture import (
    CAPTURE_FORMAT_VERSION,
    CaptureWriter,
    format_timestamp,
    utc_now,
)
from pentairsnoop.catalog import (
    CAPABILITY_FORMAT_VERSION,
    Capability,
    catalog_to_jsonable,
)
from pentairsnoop.framer import Frame, Framer
from pentairsnoop.transport import Transport

# Status values for index.jsonl (plan §5.4).
STATUS_DONE = "done"
STATUS_SKIPPED = "skipped"
STATUS_NOT_RUN = "not_run"
STATUS_ERROR = "error"


class SessionPhase(Enum):
    """Pattern: State — phases of the capability-guided walk."""

    SESSION_INIT = auto()
    SELECT_NEXT = auto()
    PROMPT_ARM = auto()
    RECORDING = auto()
    FINALIZE = auto()
    SESSION_END = auto()


class ListenOnlyTransport:
    """Pattern: Decorator / Proxy — forbid transport writes (listen-only path).

    Forwards open/close/read to the inner Strategy transport; ``write`` always
    raises so craft/send cannot sneak onto this path.
    """

    def __init__(self, inner: Transport) -> None:
        self._inner = inner
        self.write_attempts = 0

    @property
    def inner(self) -> Transport:
        return self._inner

    def open(self) -> None:
        self._inner.open()

    def close(self) -> None:
        self._inner.close()

    def read(self, n: int) -> bytes:
        return self._inner.read(n)

    def write(self, data: bytes) -> None:
        self.write_attempts += 1
        raise RuntimeError(
            "capability-guided capture is listen-only; transport write forbidden"
        )


@dataclass
class CapabilityAttempt:
    """Pattern: Data Transfer Object — one index.jsonl capability attempt row."""

    seq: int
    capability_id: str
    status: str
    started_at: str
    ended_at: str = ""
    frame_seq_start: int | None = None
    frame_seq_end: int | None = None
    frames: int = 0
    dir: str = ""
    skip_ok: bool = False
    attempt: int = 1

    def to_dict(self) -> dict:
        return {
            "seq": self.seq,
            "capability_id": self.capability_id,
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "frame_seq_start": self.frame_seq_start,
            "frame_seq_end": self.frame_seq_end,
            "frames": self.frames,
            "dir": self.dir,
            "skip_ok": self.skip_ok,
            "attempt": self.attempt,
        }


class CapabilitySessionWriter:
    """Pattern: Writer — hybrid layout C session tree for capability capture.

    Root keeps continuous ``raw.ndjson`` / ``frames.ndjson``; each armed window
    dual-writes frames into ``by_capability/<nn>_<id>/frames.ndjson``.
    """

    def __init__(
        self,
        session_dir: Path,
        *,
        label: str = "capability-guided",
        source: dict | None = None,
        catalog: Sequence[Capability] | None = None,
        clock: Callable[[], datetime] = utc_now,
        notes: str = "Bulk capability capture; no auto TX.",
    ) -> None:
        self.session_dir = Path(session_dir)
        self._clock = clock
        self._catalog = list(catalog or [])
        self._label = label
        self._source = dict(source or {})
        self._notes = notes
        self._root = CaptureWriter(
            self.session_dir,
            label=label,
            source=self._source,
            clock=clock,
        )
        self._lock = threading.RLock()
        self._active_id: str | None = None
        self._cap_frames_fp: TextIO | None = None
        self._cap_dir: Path | None = None
        self._cap_rel: str = ""
        self._cap_frame_count = 0
        self._cap_seq_start: int | None = None
        self._index: list[CapabilityAttempt] = []
        self._open = False

    @property
    def root(self) -> CaptureWriter:
        return self._root

    @property
    def active_capability_id(self) -> str | None:
        with self._lock:
            return self._active_id

    @property
    def index_path(self) -> Path:
        return self.session_dir / "index.jsonl"

    @property
    def catalog_snapshot_path(self) -> Path:
        return self.session_dir / "catalog_snapshot.json"

    @property
    def by_capability_dir(self) -> Path:
        return self.session_dir / "by_capability"

    def open(self) -> None:
        if self._open:
            return
        self._root.open()
        self.by_capability_dir.mkdir(parents=True, exist_ok=True)
        self.catalog_snapshot_path.write_text(
            json.dumps(catalog_to_jsonable(self._catalog), indent=2, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )
        # Rewrite session NOTES for capability mode.
        self._root.notes_path.write_text(
            _session_notes_template(self._label),
            encoding="utf-8",
        )
        self._open = True

    def close(self) -> None:
        if not self._open:
            return
        with self._lock:
            self._close_cap_slice()
        self._write_index()
        self._root.close()
        # CaptureWriter.close already wrote A4 meta; rewrite with capability fields.
        self._write_session_meta(ended=True)
        self._open = False

    def arm(self, capability: Capability, *, seq: int) -> Path:
        """Open a per-capability slice and set the active tag for new frames."""
        with self._lock:
            self._close_cap_slice()
            nn = f"{seq:02d}"
            rel = f"by_capability/{nn}_{capability.id}"
            cap_dir = self.session_dir / "by_capability" / f"{nn}_{capability.id}"
            cap_dir.mkdir(parents=True, exist_ok=True)
            (cap_dir / "meta.json").write_text(
                json.dumps(
                    {
                        "capability_id": capability.id,
                        "seq": seq,
                        "label": capability.id,
                        "parent_label": self._label,
                        "group": capability.group,
                        "skip_ok": capability.skip_ok,
                        "prompt": capability.prompt,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            (cap_dir / "NOTES.md").write_text(
                _cap_notes_template(capability),
                encoding="utf-8",
            )
            self._cap_frames_fp = (cap_dir / "frames.ndjson").open("w", encoding="utf-8")
            self._cap_dir = cap_dir
            self._cap_rel = rel
            self._cap_frame_count = 0
            self._cap_seq_start = self._root.frame_seq
            self._active_id = capability.id
            return cap_dir

    def disarm(self) -> tuple[str, int, int | None, int | None]:
        """Clear active tag and close the per-cap slice.

        Returns ``(rel_dir, frame_count, frame_seq_start, frame_seq_end)``.
        """
        with self._lock:
            rel = self._cap_rel
            count = self._cap_frame_count
            start = self._cap_seq_start
            end: int | None = None
            if count > 0 and start is not None:
                end = start + count - 1
            self._close_cap_slice()
            self._active_id = None
            return rel, count, start, end

    def write_raw(self, data: bytes, *, ts: datetime | None = None) -> None:
        with self._lock:
            self._root.write_raw(data, ts=ts)

    def write_frame(self, frame: Frame, *, ts: datetime | None = None) -> dict:
        with self._lock:
            cap_id = self._active_id
            rec = self._root.write_frame(frame, ts=ts, capability_id=cap_id)
            if cap_id is not None and self._cap_frames_fp is not None:
                self._cap_frames_fp.write(
                    json.dumps(rec, separators=(",", ":")) + "\n"
                )
                self._cap_frames_fp.flush()
                self._cap_frame_count += 1
            return rec

    def append_index(self, attempt: CapabilityAttempt) -> None:
        self._index.append(attempt)

    @property
    def index(self) -> list[CapabilityAttempt]:
        return list(self._index)

    def counts(self) -> dict[str, int]:
        done = sum(1 for a in self._index if a.status == STATUS_DONE)
        skipped = sum(1 for a in self._index if a.status == STATUS_SKIPPED)
        not_run = sum(1 for a in self._index if a.status == STATUS_NOT_RUN)
        error = sum(1 for a in self._index if a.status == STATUS_ERROR)
        return {
            "done": done,
            "skipped": skipped,
            "not_run": not_run,
            "error": error,
        }

    def _close_cap_slice(self) -> None:
        if self._cap_frames_fp is not None:
            self._cap_frames_fp.close()
            self._cap_frames_fp = None
        self._cap_dir = None
        self._cap_rel = ""
        self._cap_frame_count = 0
        self._cap_seq_start = None

    def _write_index(self) -> None:
        lines = [
            json.dumps(a.to_dict(), separators=(",", ":")) for a in self._index
        ]
        self.index_path.write_text(
            ("\n".join(lines) + ("\n" if lines else "")),
            encoding="utf-8",
        )

    def _write_session_meta(self, *, ended: bool) -> None:
        counts = self.counts()
        ended_at = self._root.meta.ended_at
        if ended and not ended_at:
            ended_at = format_timestamp(self._clock())
        meta = {
            "format_version": CAPTURE_FORMAT_VERSION,
            "capability_format_version": CAPABILITY_FORMAT_VERSION,
            "mode": "capability_guided",
            "started_at": self._root.meta.started_at,
            "ended_at": ended_at,
            "label": self._label,
            "source": self._source,
            "tool": self._root.meta.tool,
            "tool_version": self._root.meta.tool_version,
            "listen_only": True,
            "catalog_count": len(self._catalog),
            "capabilities_done": counts["done"],
            "capabilities_skipped": counts["skipped"],
            "capabilities_not_run": counts["not_run"],
            "raw_chunks": self._root.meta.raw_chunks,
            "frames": self._root.meta.frames,
            "notes": self._notes,
            "slice_policy": "hybrid_c",
        }
        self._root.meta_path.write_text(
            json.dumps(meta, indent=2) + "\n",
            encoding="utf-8",
        )


@dataclass
class CapabilitySessionConfig:
    """Pattern: Parameter Object — knobs for a capability-guided session."""

    catalog: list[Capability]
    skip_ids: set[str] = field(default_factory=set)
    arm_on_prompt: bool = True
    chunk_size: int = 256
    reader_idle_sleep: float = 0.01


class CapabilitySession:
    """Pattern: State Machine — prompt → arm → d/s/q → next until SESSION_END.

    Main thread owns stdin; a daemon reader thread owns transport reads so frames
    are not missed while the operator reads the prompt.
    """

    def __init__(
        self,
        transport: Transport,
        writer: CapabilitySessionWriter,
        config: CapabilitySessionConfig,
        *,
        input_fn: Callable[[str], str] | None = None,
        output: TextIO | None = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self._inner_transport = transport
        self._transport = ListenOnlyTransport(transport)
        self._writer = writer
        self._config = config
        self._input = input_fn if input_fn is not None else input
        self._output = output if output is not None else sys.stdout
        self._clock = clock
        self._framer = Framer()
        self._phase = SessionPhase.SESSION_INIT
        self._stop = threading.Event()
        self._reader: threading.Thread | None = None
        self._walk: list[Capability] = list(config.catalog)
        self._cursor = 0
        self._current: Capability | None = None
        self._current_seq = 0
        self._attempt_started_at = ""
        self._quit_requested = False
        self._pending_status: str | None = None

    @property
    def phase(self) -> SessionPhase:
        return self._phase

    @property
    def listen_only_transport(self) -> ListenOnlyTransport:
        return self._transport

    def run(self) -> dict[str, int]:
        """Drive the state machine to SESSION_END; return done/skipped/not_run counts."""
        self._phase = SessionPhase.SESSION_INIT
        self._writer.open()
        # Transport + reader start on first arm so fixture bytes land in an armed
        # window; live sessions still read continuously from the first prompt.
        try:
            while self._phase is not SessionPhase.SESSION_END:
                if self._phase is SessionPhase.SESSION_INIT:
                    self._phase = SessionPhase.SELECT_NEXT
                elif self._phase is SessionPhase.SELECT_NEXT:
                    self._do_select_next()
                elif self._phase is SessionPhase.PROMPT_ARM:
                    self._do_prompt_arm()
                elif self._phase is SessionPhase.RECORDING:
                    self._do_recording()
                elif self._phase is SessionPhase.FINALIZE:
                    self._do_finalize()
                else:
                    break
        finally:
            self._stop_reader()
            # Mark remaining as not_run if quit mid-walk.
            self._mark_remaining_not_run()
            self._writer.close()
            try:
                self._transport.close()
            except Exception:  # noqa: BLE001 — best-effort close
                pass
            self._phase = SessionPhase.SESSION_END
        counts = self._writer.counts()
        self._print_summary(counts)
        return counts

    def _ensure_reader(self) -> None:
        """Open listen-only transport and start background bus reader once."""
        if self._reader is not None:
            return
        self._transport.open()
        self._framer.reset()
        self._start_reader()
        # HexFile (and similar) can drain instantly; wait so frames land while armed
        # before the first key is processed. Live transports have no ``remaining``.
        self._drain_fixture_burst()

    def _drain_fixture_burst(self, timeout: float = 0.5) -> None:
        inner = self._transport.inner
        if not hasattr(inner, "remaining"):
            return
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if getattr(inner, "remaining", 1) == 0:
                time.sleep(0.02)
                return
            time.sleep(0.005)

    def _start_reader(self) -> None:
        self._stop.clear()
        self._reader = threading.Thread(
            target=self._reader_loop,
            name="capability-bus-reader",
            daemon=True,
        )
        self._reader.start()

    def _stop_reader(self) -> None:
        self._stop.set()
        if self._reader is not None:
            self._reader.join(timeout=2.0)
            self._reader = None
        # Flush any trailing framer bytes once.
        for frame in self._framer.feed(b""):
            self._writer.write_frame(frame)

    def _reader_loop(self) -> None:
        while not self._stop.is_set():
            try:
                chunk = self._transport.read(self._config.chunk_size)
            except Exception:  # noqa: BLE001 — keep session alive on transient read errors
                if self._stop.is_set():
                    break
                time.sleep(self._config.reader_idle_sleep)
                continue
            if not chunk:
                # Fixture EOF or idle live bus — poll until session stops.
                time.sleep(self._config.reader_idle_sleep)
                continue
            self._writer.write_raw(chunk)
            for frame in self._framer.feed(chunk):
                self._writer.write_frame(frame)

    def _do_select_next(self) -> None:
        if self._quit_requested or self._cursor >= len(self._walk):
            self._phase = SessionPhase.SESSION_END
            return
        cap = self._walk[self._cursor]
        self._cursor += 1
        self._current = cap
        self._current_seq = self._cursor  # 1-based seq in walk order
        if cap.id in self._config.skip_ids:
            # Pre-mark skip without prompting.
            self._attempt_started_at = format_timestamp(self._clock())
            self._writer.arm(cap, seq=self._current_seq)
            self._ensure_reader()
            self._pending_status = STATUS_SKIPPED
            self._phase = SessionPhase.FINALIZE
            return
        self._phase = SessionPhase.PROMPT_ARM

    def _do_prompt_arm(self) -> None:
        assert self._current is not None
        cap = self._current
        total = len(self._walk)
        self._print(
            f"\n[{self._current_seq}/{total}] {cap.id}\n"
            f"{cap.prompt}\n"
        )
        if self._config.arm_on_prompt:
            self._attempt_started_at = format_timestamp(self._clock())
            self._writer.arm(cap, seq=self._current_seq)
            self._ensure_reader()
            self._print("Recording…  [d=Done / s=Skip / q=Quit]: ")
            self._phase = SessionPhase.RECORDING
            return
        # arm-on-key: wait for 'a' (or d/s/q) before tagging frames.
        self._print("Armed on key…  [a=Arm / d=Done / s=Skip / q=Quit]: ")
        key = self._read_key()
        if key == "q":
            self._quit_requested = True
            self._pending_status = STATUS_NOT_RUN
            self._attempt_started_at = format_timestamp(self._clock())
            self._writer.arm(cap, seq=self._current_seq)
            self._ensure_reader()
            self._phase = SessionPhase.FINALIZE
            return
        if key in ("d", "s"):
            self._attempt_started_at = format_timestamp(self._clock())
            self._writer.arm(cap, seq=self._current_seq)
            self._ensure_reader()
            self._pending_status = STATUS_DONE if key == "d" else STATUS_SKIPPED
            self._phase = SessionPhase.FINALIZE
            return
        # 'a' or anything else that means arm: start recording.
        self._attempt_started_at = format_timestamp(self._clock())
        self._writer.arm(cap, seq=self._current_seq)
        self._ensure_reader()
        self._print("Recording…  [d=Done / s=Skip / q=Quit]: ")
        self._phase = SessionPhase.RECORDING

    def _do_recording(self) -> None:
        key = self._read_key()
        if key == "q":
            # Quit ends the session; current window is incomplete → skipped.
            self._quit_requested = True
            self._pending_status = STATUS_SKIPPED
            self._phase = SessionPhase.FINALIZE
            return
        if key == "d":
            self._pending_status = STATUS_DONE
            self._phase = SessionPhase.FINALIZE
            return
        if key == "s":
            self._pending_status = STATUS_SKIPPED
            self._phase = SessionPhase.FINALIZE
            return
        # Unknown key — stay in RECORDING and re-prompt lightly.
        self._print("  (use d=Done / s=Skip / q=Quit): ")

    def _do_finalize(self) -> None:
        assert self._current is not None
        status = self._pending_status or STATUS_SKIPPED
        rel, frames, seq_start, seq_end = self._writer.disarm()
        ended = format_timestamp(self._clock())
        self._writer.append_index(
            CapabilityAttempt(
                seq=self._current_seq,
                capability_id=self._current.id,
                status=status,
                started_at=self._attempt_started_at or ended,
                ended_at=ended,
                frame_seq_start=seq_start,
                frame_seq_end=seq_end,
                frames=frames,
                dir=rel,
                skip_ok=self._current.skip_ok,
                attempt=1,
            )
        )
        self._print(f"  → {self._current.id}: {status} ({frames} frames)\n")
        self._current = None
        self._pending_status = None
        if self._quit_requested:
            self._phase = SessionPhase.SESSION_END
        else:
            self._phase = SessionPhase.SELECT_NEXT

    def _mark_remaining_not_run(self) -> None:
        while self._cursor < len(self._walk):
            cap = self._walk[self._cursor]
            self._cursor += 1
            seq = self._cursor
            now = format_timestamp(self._clock())
            nn = f"{seq:02d}"
            rel = f"by_capability/{nn}_{cap.id}"
            cap_dir = self._writer.session_dir / "by_capability" / f"{nn}_{cap.id}"
            cap_dir.mkdir(parents=True, exist_ok=True)
            (cap_dir / "meta.json").write_text(
                json.dumps(
                    {
                        "capability_id": cap.id,
                        "seq": seq,
                        "label": cap.id,
                        "status": STATUS_NOT_RUN,
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            (cap_dir / "NOTES.md").write_text(
                _cap_notes_template(cap),
                encoding="utf-8",
            )
            (cap_dir / "frames.ndjson").write_text("", encoding="utf-8")
            self._writer.append_index(
                CapabilityAttempt(
                    seq=seq,
                    capability_id=cap.id,
                    status=STATUS_NOT_RUN,
                    started_at=now,
                    ended_at=now,
                    frame_seq_start=None,
                    frame_seq_end=None,
                    frames=0,
                    dir=rel,
                    skip_ok=cap.skip_ok,
                    attempt=1,
                )
            )

    def _read_key(self) -> str:
        try:
            line = self._input("")
        except EOFError:
            return "q"
        text = (line or "").strip().lower()
        if not text:
            return ""
        return text[0]

    def _print(self, msg: str) -> None:
        self._output.write(msg)
        self._output.flush()

    def _print_summary(self, counts: dict[str, int]) -> None:
        self._print("\n# capability session complete\n")
        self._print(f"# path: {self._writer.session_dir}\n")
        self._print(
            f"# done={counts['done']} skipped={counts['skipped']} "
            f"not_run={counts['not_run']}\n"
        )
        for attempt in self._writer.index:
            self._print(
                f"#   {attempt.capability_id}: {attempt.status} "
                f"({attempt.frames} frames)\n"
            )
        self._print(
            "# listen-only; haul-back: "
            f"tar czf ~/capability-guided.tgz -C {self._writer.session_dir.parent} "
            f"{self._writer.session_dir.name}\n"
        )


def parse_id_list(value: str | None) -> list[str] | None:
    """Parse a comma-separated capability id list; empty → None."""
    if value is None:
        return None
    parts = [p.strip() for p in value.split(",") if p.strip()]
    return parts or None


def run_capability_session(
    transport: Transport,
    session_dir: Path,
    catalog: Sequence[Capability],
    *,
    label: str = "capability-guided",
    source: dict | None = None,
    skip_ids: Sequence[str] | None = None,
    arm_on_prompt: bool = True,
    input_fn: Callable[[str], str] | None = None,
    output: TextIO | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> dict[str, int]:
    """Convenience: open writer + run :class:`CapabilitySession`."""
    writer = CapabilitySessionWriter(
        session_dir,
        label=label,
        source=source,
        catalog=catalog,
        clock=clock,
    )
    config = CapabilitySessionConfig(
        catalog=list(catalog),
        skip_ids=set(skip_ids or ()),
        arm_on_prompt=arm_on_prompt,
    )
    session = CapabilitySession(
        transport,
        writer,
        config,
        input_fn=input_fn,
        output=output,
        clock=clock,
    )
    return session.run()


def _session_notes_template(label: str) -> str:
    title = label or "capability-guided"
    return (
        f"# Capability-guided session — {title}\n\n"
        "## Intent\n\n"
        "Bulk labeled listen capture while exercising the wireless remote.\n"
        "This session is **listen-only** (no auto TX from pentairsnoop).\n\n"
        "## Operator notes\n\n"
        "- Remote quirks / label mismatches:\n"
        "- Caps skipped and why:\n"
        "- Bus / EW11 notes:\n\n"
        "## Integrity\n\n"
        "Annotate only observed frames. Do not invent panel-success TX.\n"
    )


def _cap_notes_template(cap: Capability) -> str:
    return (
        f"# {cap.id}\n\n"
        f"**Group:** {cap.group}  \n"
        f"**skip_ok:** {cap.skip_ok}\n\n"
        f"## Prompt\n\n{cap.prompt}\n\n"
        "## Observed\n\n"
        "- Actions on remote:\n"
        "- Notable frames (cmd / addresses):\n"
        "- Outcome:\n"
    )
