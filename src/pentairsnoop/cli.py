"""Pattern: Command — CLI entry maps user intents to lab operations."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from pentairsnoop import __version__
from pentairsnoop.capture import (
    CaptureWriter,
    default_session_dirname,
    describe_source,
    iter_capture_frames,
)
from pentairsnoop.compare import diff_tx
from pentairsnoop.craft import (
    DEFAULT_WRITE_DST,
    DEFAULT_WRITE_PROTOCOL,
    DEFAULT_WRITE_SRC,
    HEAT_POOL_MODE,
    HEAT_SPA_MODE,
    CircuitChange,
    HeatChange,
    pack_heat_mode,
    parse_circuit_name,
)
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
    """Build the top-level argument parser with decode, watch, and capture."""
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

    capture = sub.add_parser(
        "capture",
        help=(
            "Record timestamped raw.ndjson + frames.ndjson under a session dir. "
            "Same transports as watch; see docs/capture-procedure.md."
        ),
    )
    cap_src = capture.add_mutually_exclusive_group(required=True)
    cap_src.add_argument(
        "--tcp",
        nargs="?",
        const=f"{DEFAULT_EW11_HOST}:{DEFAULT_EW11_PORT}",
        metavar="HOST:PORT",
        help=(
            f"TCP to EW11 (default {DEFAULT_EW11_HOST}:{DEFAULT_EW11_PORT} "
            "from settings.php)"
        ),
    )
    cap_src.add_argument(
        "--serial",
        metavar="PORT",
        help="USB-RS485 serial device (9600 8N1 starting defaults, unconfirmed)",
    )
    cap_src.add_argument(
        "--file",
        type=Path,
        metavar="PATH",
        help="Offline hex fixture (EOF ends capture; format tests / replay)",
    )
    capture.add_argument(
        "--out",
        "-o",
        type=Path,
        default=Path("samples/captures"),
        metavar="DIR",
        help="Parent directory for session folders (default: samples/captures)",
    )
    capture.add_argument(
        "--label",
        default="session",
        help="Session label suffix (idle, circuit-filter, heat-setpoint, …)",
    )
    capture.add_argument(
        "--session-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="Explicit session directory (skips auto YYYYMMDDTHHMMSSZ_label)",
    )
    capture.add_argument(
        "--max-frames",
        type=int,
        default=None,
        metavar="N",
        help="Stop after N framed messages (tests / bounded capture)",
    )

    craft_c = sub.add_parser(
        "craft-circuit",
        help=(
            "Emit CircuitChange (0x86) TX hex (dry-run by default). "
            "Optional --send is gated and listens before/after write."
        ),
    )
    craft_c.add_argument(
        "circuit",
        help="Circuit id: 0x06 / 6 / pool_light (local PHP names; unconfirmed)",
    )
    craft_c.add_argument(
        "state",
        choices=("on", "off", "1", "0"),
        help="on/1 or off/0",
    )
    _add_craft_common(craft_c)

    craft_h = sub.add_parser(
        "craft-heat",
        help=(
            "Emit HeatChange (0x88) TX hex (dry-run by default). "
            "Matches lib/index.php $set_temp shape when args match."
        ),
    )
    craft_h.add_argument("--pool-set", type=int, required=True, metavar="F", help="Pool setpoint °F")
    craft_h.add_argument("--spa-set", type=int, required=True, metavar="F", help="Spa setpoint °F")
    mode_g = craft_h.add_mutually_exclusive_group()
    mode_g.add_argument(
        "--mode",
        type=lambda s: int(s, 0),
        default=None,
        metavar="BYTE",
        help="Raw mode byte (default: PHP OR 0x05 = pool+spa heater)",
    )
    mode_g.add_argument(
        "--pack-modes",
        action="store_true",
        help="Pack --pool-mode/--spa-mode as (spa<<2)|pool (0–3 each)",
    )
    craft_h.add_argument(
        "--pool-mode",
        type=int,
        default=1,
        metavar="0-3",
        help="With --pack-modes (default 1=heater)",
    )
    craft_h.add_argument(
        "--spa-mode",
        type=int,
        default=1,
        metavar="0-3",
        help="With --pack-modes (default 1=heater)",
    )
    _add_craft_common(craft_h)

    diff = sub.add_parser(
        "diff-tx",
        help="Diff crafted TX hex vs captured/reference TX (addrs, payload, CS)",
    )
    diff.add_argument("crafted", help="Crafted TX hex string")
    diff.add_argument("reference", help="Reference / capture TX hex string")
    diff.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON diff",
    )
    return parser


def _add_craft_common(p: argparse.ArgumentParser) -> None:
    """Shared craft options: protocol/addrs, dry-run emit, gated --send."""
    p.add_argument(
        "--protocol",
        type=lambda s: int(s, 0),
        default=DEFAULT_WRITE_PROTOCOL,
        metavar="BYTE",
        help=f"Protocol byte (default {DEFAULT_WRITE_PROTOCOL:#x})",
    )
    p.add_argument(
        "--dst",
        type=lambda s: int(s, 0),
        default=DEFAULT_WRITE_DST,
        metavar="BYTE",
        help=f"Destination (default {DEFAULT_WRITE_DST:#x})",
    )
    p.add_argument(
        "--src",
        type=lambda s: int(s, 0),
        default=DEFAULT_WRITE_SRC,
        metavar="BYTE",
        help=f"Source (default {DEFAULT_WRITE_SRC:#x}; unconfirmed)",
    )
    p.add_argument(
        "--diff",
        metavar="REF_HEX",
        default=None,
        help="Also diff crafted TX against this reference hex",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON (message + optional diff) instead of bare hex",
    )
    send = p.add_argument_group("optional live send (gated; default is dry-run)")
    send.add_argument(
        "--send",
        action="store_true",
        help=(
            "Actually write TX bytes (OFF by default). Requires --tcp or --serial. "
            "Listens before/after; do not use on live hardware unless safe/offline."
        ),
    )
    send_src = send.add_mutually_exclusive_group()
    send_src.add_argument(
        "--tcp",
        nargs="?",
        const=f"{DEFAULT_EW11_HOST}:{DEFAULT_EW11_PORT}",
        metavar="HOST:PORT",
        help=f"TCP for --send (default {DEFAULT_EW11_HOST}:{DEFAULT_EW11_PORT})",
    )
    send_src.add_argument(
        "--serial",
        metavar="PORT",
        help="Serial device for --send",
    )
    send.add_argument(
        "--listen-seconds",
        type=float,
        default=2.0,
        metavar="SEC",
        help="Listen window before and after --send (default 2.0)",
    )


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


def cmd_capture(
    *,
    tcp: str | None,
    serial: str | None,
    file: Path | None,
    out: Path,
    label: str,
    session_dir: Path | None,
    max_frames: int | None,
) -> int:
    """Record timestamped raw + framed logs (FR-S4 capture workflow)."""
    if file is not None and not file.is_file():
        print(f"error: file not found: {file}", file=sys.stderr)
        return 1
    try:
        transport = build_watch_transport(tcp=tcp, serial=serial, file=file)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if session_dir is not None:
        dest = session_dir
    else:
        dest = out / default_session_dirname(label)

    source = describe_source(tcp=tcp, serial=serial, file=file)
    writer = CaptureWriter(dest, label=label, source=source)
    writer.open()
    framed = 0
    try:
        for frame in iter_capture_frames(transport, writer):
            writer.write_frame(frame)
            framed += 1
            if max_frames is not None and framed >= max_frames:
                break
    except KeyboardInterrupt:
        print("\n# capture interrupted", file=sys.stderr)
    finally:
        writer.close()

    print(
        f"# capture wrote {framed} frames → {writer.session_dir}",
        file=sys.stderr,
    )
    return 0


def _parse_on_off(state: str) -> bool:
    return state.lower() in ("on", "1")


def _resolve_heat_mode(args: argparse.Namespace) -> int:
    if args.mode is not None:
        return args.mode & 0xFF
    if args.pack_modes:
        return pack_heat_mode(pool_mode=args.pool_mode, spa_mode=args.spa_mode)
    # Default: PHP OR-style dual heater (matches $set_temp mode 0x05).
    return (HEAT_POOL_MODE | HEAT_SPA_MODE) & 0xFF


def _emit_craft_result(
    msg: CircuitChange | HeatChange,
    *,
    as_json: bool,
    diff_ref: str | None,
) -> int:
    hex_s = msg.to_hex()
    result: dict = {"hex": hex_s, "message": msg.to_dict()}
    exit_code = 0
    if diff_ref is not None:
        d = diff_tx(hex_s, diff_ref)
        result["diff"] = d.to_dict()
        if not d.match:
            exit_code = 2
    if as_json:
        print(json.dumps(result, indent=2))
    else:
        print(hex_s)
        if diff_ref is not None:
            print(json.dumps(result["diff"], indent=2), file=sys.stderr)
            if not result["diff"]["match"]:
                print("# diff: MISMATCH", file=sys.stderr)
            else:
                print("# diff: MATCH", file=sys.stderr)
    return exit_code


def _listen_and_log(
    session: Session,
    *,
    seconds: float,
    label: str,
    clock=time.monotonic,
    sleep=time.sleep,
) -> int:
    """Read/decode for ``seconds``, printing NDJSON; return frame count.

    Transport reads should time out (TCP/serial defaults) so this loop can exit.
    """
    if seconds <= 0:
        return 0
    deadline = clock() + seconds
    count = 0
    print(f"# listen ({label}) {seconds}s", file=sys.stderr)
    while clock() < deadline:
        chunk = session.transport.read(256)
        if chunk:
            for frame in session.framer.feed(chunk):
                item = session.decode_frame(frame)
                print(json.dumps(_item_dict(item), separators=(",", ":")), flush=True)
                count += 1
        else:
            sleep(0.05)
    for frame in session.framer.feed(b""):
        item = session.decode_frame(frame)
        print(json.dumps(_item_dict(item), separators=(",", ":")), flush=True)
        count += 1
    return count


def cmd_send_with_listen(
    tx: bytes,
    *,
    tcp: str | None,
    serial: str | None,
    listen_seconds: float,
) -> int:
    """Gated lab send: listen → write TX → listen. Prefer dry-run without --send."""
    if tcp is None and serial is None:
        print("error: --send requires --tcp or --serial", file=sys.stderr)
        return 1
    if serial is not None:
        transport: TcpTransport | SerialTransport = SerialTransport(serial)
    else:
        assert tcp is not None
        host, port = _parse_tcp_endpoint(tcp)
        transport = TcpTransport(host, port)

    session = Session(transport)
    session.open()
    try:
        _listen_and_log(session, seconds=listen_seconds, label="pre-send")
        print(f"# SEND {tx.hex()}", file=sys.stderr)
        session.transport.write(tx)
        _listen_and_log(session, seconds=listen_seconds, label="post-send")
    except KeyboardInterrupt:
        print("\n# send interrupted", file=sys.stderr)
        return 0
    finally:
        session.close()
    return 0


def cmd_craft_circuit(
    *,
    circuit: str,
    state: str,
    protocol: int,
    dst: int,
    src: int,
    diff: str | None,
    as_json: bool,
    send: bool,
    tcp: str | None,
    serial: str | None,
    listen_seconds: float,
) -> int:
    """Build CircuitChange TX hex; optional gated --send with listen window."""
    try:
        cid = parse_circuit_name(circuit)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    msg = CircuitChange.build(
        cid,
        _parse_on_off(state),
        protocol=protocol,
        destination=dst,
        source=src,
    )
    code = _emit_craft_result(msg, as_json=as_json, diff_ref=diff)
    if send:
        send_code = cmd_send_with_listen(
            msg.raw,
            tcp=tcp,
            serial=serial,
            listen_seconds=listen_seconds,
        )
        return send_code if send_code else code
    return code


def cmd_craft_heat(
    *,
    pool_set: int,
    spa_set: int,
    mode: int,
    protocol: int,
    dst: int,
    src: int,
    diff: str | None,
    as_json: bool,
    send: bool,
    tcp: str | None,
    serial: str | None,
    listen_seconds: float,
) -> int:
    """Build HeatChange TX hex; optional gated --send with listen window."""
    msg = HeatChange.build(
        pool_set=pool_set,
        spa_set=spa_set,
        mode=mode,
        protocol=protocol,
        destination=dst,
        source=src,
    )
    code = _emit_craft_result(msg, as_json=as_json, diff_ref=diff)
    if send:
        send_code = cmd_send_with_listen(
            msg.raw,
            tcp=tcp,
            serial=serial,
            listen_seconds=listen_seconds,
        )
        return send_code if send_code else code
    return code


def cmd_diff_tx(crafted: str, reference: str, *, pretty: bool) -> int:
    """Exit 0 on match, 2 on deliberate/structural mismatch."""
    try:
        d = diff_tx(crafted, reference)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if pretty:
        print(json.dumps(d.to_dict(), indent=2))
    else:
        print(json.dumps(d.to_dict(), separators=(",", ":")))
    return 0 if d.match else 2


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
    if args.command == "capture":
        return cmd_capture(
            tcp=args.tcp,
            serial=args.serial,
            file=args.file,
            out=args.out,
            label=args.label,
            session_dir=args.session_dir,
            max_frames=args.max_frames,
        )
    if args.command == "craft-circuit":
        return cmd_craft_circuit(
            circuit=args.circuit,
            state=args.state,
            protocol=args.protocol,
            dst=args.dst,
            src=args.src,
            diff=args.diff,
            as_json=args.json,
            send=args.send,
            tcp=args.tcp,
            serial=args.serial,
            listen_seconds=args.listen_seconds,
        )
    if args.command == "craft-heat":
        try:
            mode = _resolve_heat_mode(args)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        return cmd_craft_heat(
            pool_set=args.pool_set,
            spa_set=args.spa_set,
            mode=mode,
            protocol=args.protocol,
            dst=args.dst,
            src=args.src,
            diff=args.diff,
            as_json=args.json,
            send=args.send,
            tcp=args.tcp,
            serial=args.serial,
            listen_seconds=args.listen_seconds,
        )
    if args.command == "diff-tx":
        return cmd_diff_tx(args.crafted, args.reference, pretty=args.pretty)
    # No subcommand: A0-compatible no-op (help via --help).
    return 0
