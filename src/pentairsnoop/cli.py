"""Pattern: Command — CLI entry maps user intents to lab operations."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from pentairsnoop import __version__
from pentairsnoop.messages import Message
from pentairsnoop.session import DecodedItem, QuarantinedFrame, Session
from pentairsnoop.transport import (
    DEFAULT_EW11_HOST,
    DEFAULT_EW11_PORT,
    HexFileTransport,
    SerialTransport,
    TcpTransport,
)


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with decode + watch subcommands."""
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

    watch = sub.add_parser(
        "watch",
        help=(
            "Live or fixture watch: persistent transport + NDJSON messages. "
            "Defaults: EW11 TCP "
            f"{DEFAULT_EW11_HOST}:{DEFAULT_EW11_PORT}; serial 9600 8N1 (unconfirmed)."
        ),
    )
    src = watch.add_mutually_exclusive_group(required=True)
    src.add_argument(
        "--tcp",
        nargs="?",
        const=f"{DEFAULT_EW11_HOST}:{DEFAULT_EW11_PORT}",
        metavar="HOST:PORT",
        help=(
            f"TCP to EW11 (default {DEFAULT_EW11_HOST}:{DEFAULT_EW11_PORT} "
            "from settings.php)"
        ),
    )
    src.add_argument(
        "--serial",
        metavar="PORT",
        help="USB-RS485 serial device (9600 8N1 starting defaults, unconfirmed)",
    )
    src.add_argument(
        "--file",
        type=Path,
        metavar="PATH",
        help="Offline hex fixture (EOF ends watch; no reconnect)",
    )
    watch.add_argument(
        "--cmd",
        action="append",
        dest="cmds",
        metavar="BYTE",
        help=(
            "Only emit messages with this A5 command byte (repeatable). "
            "Accepts 0x02, 2, or comma lists like 0x02,0x08"
        ),
    )
    watch.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print each JSON object",
    )
    watch.add_argument(
        "--include-quarantine",
        action="store_true",
        help="Also emit Quarantined frames (bad checksum)",
    )
    watch.add_argument(
        "--max-messages",
        type=int,
        default=None,
        metavar="N",
        help="Stop after N emitted messages (tests / bounded soak)",
    )
    return parser


def parse_command_filter(values: Sequence[str] | None) -> frozenset[int] | None:
    """Parse ``--cmd`` args into a set of command bytes; ``None`` means all."""
    if not values:
        return None
    out: set[int] = set()
    for raw in values:
        for part in raw.split(","):
            token = part.strip().lower()
            if not token:
                continue
            if token.startswith("0x"):
                out.add(int(token, 16))
            else:
                out.add(int(token, 10))
    return frozenset(out)


def _item_dict(item: Message | QuarantinedFrame) -> dict:
    return item.to_dict()


def _passes_filter(item: DecodedItem, cmds: frozenset[int] | None) -> bool:
    if cmds is None:
        return True
    if isinstance(item, QuarantinedFrame):
        return False
    return item.command in cmds


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


def _parse_tcp_endpoint(spec: str) -> tuple[str, int]:
    if ":" not in spec:
        return spec, DEFAULT_EW11_PORT
    host, _, port_s = spec.rpartition(":")
    if not host:
        raise ValueError(f"invalid --tcp endpoint: {spec!r}")
    return host, int(port_s)


def build_watch_transport(
    *,
    tcp: str | None,
    serial: str | None,
    file: Path | None,
) -> HexFileTransport | TcpTransport | SerialTransport:
    """Pattern: Strategy — select transport backend for ``watch``."""
    if file is not None:
        return HexFileTransport(file)
    if tcp is not None:
        host, port = _parse_tcp_endpoint(tcp)
        return TcpTransport(host, port)
    if serial is not None:
        return SerialTransport(serial)
    raise ValueError("watch requires --tcp, --serial, or --file")


def cmd_watch(
    *,
    tcp: str | None,
    serial: str | None,
    file: Path | None,
    cmds: Sequence[str] | None,
    pretty: bool,
    include_quarantine: bool,
    max_messages: int | None,
) -> int:
    """Persistent watch: open once, stream NDJSON until EOF/interrupt."""
    if file is not None and not file.is_file():
        print(f"error: file not found: {file}", file=sys.stderr)
        return 1
    try:
        transport = build_watch_transport(tcp=tcp, serial=serial, file=file)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    filter_cmds = parse_command_filter(cmds)
    session = Session(transport)
    emitted = 0
    try:
        for item in session.iter_messages():
            if isinstance(item, QuarantinedFrame) and not include_quarantine:
                continue
            if not _passes_filter(item, filter_cmds):
                continue
            obj = _item_dict(item)
            if pretty:
                print(json.dumps(obj, indent=2), flush=True)
            else:
                print(json.dumps(obj, separators=(",", ":")), flush=True)
            emitted += 1
            if max_messages is not None and emitted >= max_messages:
                break
    except KeyboardInterrupt:
        print("\n# watch interrupted", file=sys.stderr)
        return 0
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
    if args.command == "watch":
        return cmd_watch(
            tcp=args.tcp,
            serial=args.serial,
            file=args.file,
            cmds=args.cmds,
            pretty=args.pretty,
            include_quarantine=args.include_quarantine,
            max_messages=args.max_messages,
        )
    # No subcommand: A0-compatible no-op (help via --help).
    return 0
