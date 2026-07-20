"""Pattern: Command — CLI entry maps user intents to lab operations."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pentairsnoop import __version__
from pentairsnoop.messages import Message
from pentairsnoop.session import QuarantinedFrame, Session
from pentairsnoop.transport import HexFileTransport


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with A2 decode subcommands."""
    parser = argparse.ArgumentParser(
        prog="pentairsnoop",
        description=(
            "Lab CLI for Pentair RS485 protocol inspection, decode, and capture."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    sub = parser.add_subparsers(dest="command")

    decode = sub.add_parser(
        "decode-file",
        help="Decode a hex fixture file; print one JSON object per message (NDJSON).",
    )
    decode.add_argument("path", type=Path, help="Path to a .hex fixture")
    decode.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print each JSON object",
    )
    decode.add_argument(
        "--include-quarantine",
        action="store_true",
        help="Also emit Quarantined frames (bad checksum)",
    )

    dump = sub.add_parser(
        "dump",
        help="Decode a hex fixture and dump a JSON array (pretty by default).",
    )
    dump.add_argument("path", type=Path, help="Path to a .hex fixture")
    dump.add_argument(
        "--compact",
        action="store_true",
        help="Emit compact JSON instead of pretty-printed",
    )
    dump.add_argument(
        "--include-quarantine",
        action="store_true",
        help="Also include Quarantined frames (bad checksum)",
    )
    return parser


def _item_dict(item: Message | QuarantinedFrame) -> dict:
    return item.to_dict()


def _decode_path(
    path: Path, *, include_quarantine: bool
) -> list[dict]:
    session = Session(HexFileTransport(path))
    out: list[dict] = []
    for item in session.read_messages():
        if isinstance(item, QuarantinedFrame) and not include_quarantine:
            continue
        out.append(_item_dict(item))
    return out


def cmd_decode_file(path: Path, *, pretty: bool, include_quarantine: bool) -> int:
    """NDJSON stream of decoded messages (PHP ``Command::toJson``-like)."""
    if not path.is_file():
        print(f"error: file not found: {path}", file=sys.stderr)
        return 1
    for obj in _decode_path(path, include_quarantine=include_quarantine):
        if pretty:
            print(json.dumps(obj, indent=2))
        else:
            print(json.dumps(obj, separators=(",", ":")))
    return 0


def cmd_dump(path: Path, *, compact: bool, include_quarantine: bool) -> int:
    """JSON array dump of decoded messages."""
    if not path.is_file():
        print(f"error: file not found: {path}", file=sys.stderr)
        return 1
    objs = _decode_path(path, include_quarantine=include_quarantine)
    if compact:
        print(json.dumps(objs, separators=(",", ":")))
    else:
        print(json.dumps(objs, indent=2))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Parse argv and run the selected command; return a process exit code."""
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "decode-file":
        return cmd_decode_file(
            args.path,
            pretty=args.pretty,
            include_quarantine=args.include_quarantine,
        )
    if args.command == "dump":
        return cmd_dump(
            args.path,
            compact=args.compact,
            include_quarantine=args.include_quarantine,
        )
    # No subcommand: A0-compatible no-op (help via --help).
    return 0
