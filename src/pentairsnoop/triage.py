"""Pattern: Query Object / Facade — offline triage of capability-guided sessions.

Load a hauled ``capability_guided`` session directory, summarize ``index.jsonl``,
and optionally extract root ``frames.ndjson`` slices by ``capability_id`` when
per-capability dual-write is missing or incomplete. No live transport or TX.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, TextIO


INDEX_FILENAME = "index.jsonl"
FRAMES_FILENAME = "frames.ndjson"


@dataclass(frozen=True)
class CapabilityIndexRow:
    """Pattern: Data Transfer Object — one summarized ``index.jsonl`` row."""

    seq: int
    capability_id: str
    status: str
    frames: int
    dir: str
    skip_ok: bool = False
    attempt: int = 1
    started_at: str = ""
    ended_at: str = ""
    frame_seq_start: int | None = None
    frame_seq_end: int | None = None

    @classmethod
    def from_dict(cls, obj: dict[str, Any]) -> CapabilityIndexRow:
        """Build from a parsed ``index.jsonl`` object (tolerant of extra keys)."""
        return cls(
            seq=int(obj.get("seq", 0)),
            capability_id=str(obj.get("capability_id", "")),
            status=str(obj.get("status", "")),
            frames=int(obj.get("frames", 0)),
            dir=str(obj.get("dir", "")),
            skip_ok=bool(obj.get("skip_ok", False)),
            attempt=int(obj.get("attempt", 1)),
            started_at=str(obj.get("started_at", "")),
            ended_at=str(obj.get("ended_at", "")),
            frame_seq_start=_optional_int(obj.get("frame_seq_start")),
            frame_seq_end=_optional_int(obj.get("frame_seq_end")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "seq": self.seq,
            "capability_id": self.capability_id,
            "status": self.status,
            "frames": self.frames,
            "dir": self.dir,
            "skip_ok": self.skip_ok,
            "attempt": self.attempt,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "frame_seq_start": self.frame_seq_start,
            "frame_seq_end": self.frame_seq_end,
        }


@dataclass(frozen=True)
class SessionTriageSummary:
    """Pattern: Data Transfer Object — whole-session index summary for CLI/table."""

    session_dir: Path
    rows: tuple[CapabilityIndexRow, ...]
    counts_by_status: dict[str, int]
    total_frames: int

    @property
    def done(self) -> tuple[CapabilityIndexRow, ...]:
        return tuple(r for r in self.rows if r.status == "done")

    @property
    def skipped(self) -> tuple[CapabilityIndexRow, ...]:
        return tuple(r for r in self.rows if r.status == "skipped")

    @property
    def not_run(self) -> tuple[CapabilityIndexRow, ...]:
        return tuple(r for r in self.rows if r.status == "not_run")


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def resolve_session_dir(session_dir: Path) -> Path:
    """Normalize and validate a capability session directory path."""
    path = session_dir.expanduser().resolve()
    if not path.is_dir():
        raise FileNotFoundError(f"session directory not found: {path}")
    return path


def index_path(session_dir: Path) -> Path:
    return resolve_session_dir(session_dir) / INDEX_FILENAME


def frames_path(session_dir: Path) -> Path:
    return resolve_session_dir(session_dir) / FRAMES_FILENAME


def load_index(session_dir: Path) -> list[CapabilityIndexRow]:
    """Parse ``index.jsonl`` into ordered capability rows.

    Blank lines are skipped. Invalid JSON or non-object lines raise ``ValueError``.
    """
    path = index_path(session_dir)
    if not path.is_file():
        raise FileNotFoundError(f"index.jsonl not found: {path}")

    rows: list[CapabilityIndexRow] = []
    with path.open(encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid JSON in {path.name} line {line_no}: {exc}"
                ) from exc
            if not isinstance(obj, dict):
                raise ValueError(
                    f"expected JSON object in {path.name} line {line_no}, "
                    f"got {type(obj).__name__}"
                )
            rows.append(CapabilityIndexRow.from_dict(obj))
    return rows


def summarize_session(session_dir: Path) -> SessionTriageSummary:
    """Load index and return status counts + per-capability frame totals."""
    root = resolve_session_dir(session_dir)
    rows = load_index(root)
    status_counts = Counter(r.status for r in rows)
    total_frames = sum(r.frames for r in rows)
    return SessionTriageSummary(
        session_dir=root,
        rows=tuple(rows),
        counts_by_status=dict(status_counts),
        total_frames=total_frames,
    )


def format_summary_table(summary: SessionTriageSummary) -> str:
    """Render a plain-text table of capability ids, statuses, and frame counts."""
    lines: list[str] = []
    header = f"{'SEQ':>3}  {'STATUS':<8}  {'FRAMES':>6}  {'ID':<28}  DIR"
    lines.append(header)
    lines.append("-" * len(header))
    for row in summary.rows:
        lines.append(
            f"{row.seq:>3}  {row.status:<8}  {row.frames:>6}  "
            f"{row.capability_id:<28}  {row.dir or '-'}"
        )
    lines.append("")
    counts = summary.counts_by_status
    parts = [
        f"done={counts.get('done', 0)}",
        f"skipped={counts.get('skipped', 0)}",
        f"not_run={counts.get('not_run', 0)}",
    ]
    other = {
        k: v
        for k, v in sorted(counts.items())
        if k not in ("done", "skipped", "not_run")
    }
    for key, val in other.items():
        parts.append(f"{key}={val}")
    lines.append(
        f"# summary: {', '.join(parts)}; "
        f"index_frames={summary.total_frames}; "
        f"session={summary.session_dir}"
    )
    return "\n".join(lines)


def iter_frames_ndjson(frames_file: Path) -> Iterable[dict[str, Any]]:
    """Yield frame objects from a root or per-cap ``frames.ndjson`` file."""
    if not frames_file.is_file():
        raise FileNotFoundError(f"frames.ndjson not found: {frames_file}")
    with frames_file.open(encoding="utf-8") as fh:
        for line_no, raw in enumerate(fh, start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid JSON in {frames_file.name} line {line_no}: {exc}"
                ) from exc
            if not isinstance(obj, dict):
                raise ValueError(
                    f"expected JSON object in {frames_file.name} line {line_no}, "
                    f"got {type(obj).__name__}"
                )
            yield obj


def extract_frames(
    session_dir: Path,
    capability_id: str,
    *,
    frames_file: Path | None = None,
) -> list[dict[str, Any]]:
    """Slice root ``frames.ndjson`` lines whose ``capability_id`` matches.

    Useful when hybrid C dual-write is incomplete and ``by_capability/.../frames.ndjson``
    is missing or empty. Matching is exact string equality on the frame field.
    """
    if not capability_id:
        raise ValueError("capability_id must be non-empty")
    root = resolve_session_dir(session_dir)
    source = frames_file if frames_file is not None else root / FRAMES_FILENAME
    return [
        obj
        for obj in iter_frames_ndjson(source)
        if obj.get("capability_id") == capability_id
    ]


def write_frames_ndjson(
    frames: Iterable[dict[str, Any]],
    dest: Path | TextIO,
) -> int:
    """Write frame dicts as NDJSON; return the number of lines written."""
    count = 0
    if isinstance(dest, Path):
        dest.parent.mkdir(parents=True, exist_ok=True)
        with dest.open("w", encoding="utf-8") as fh:
            for obj in frames:
                fh.write(json.dumps(obj, separators=(",", ":")) + "\n")
                count += 1
        return count

    for obj in frames:
        dest.write(json.dumps(obj, separators=(",", ":")) + "\n")
        count += 1
    return count


def capability_slice_path(
    session_dir: Path,
    row: CapabilityIndexRow,
) -> Path | None:
    """Return ``by_capability/.../frames.ndjson`` if ``row.dir`` is set and exists."""
    if not row.dir:
        return None
    root = resolve_session_dir(session_dir)
    path = root / row.dir / FRAMES_FILENAME
    return path if path.is_file() else None
