"""Pattern: Command — write-path builders (CircuitChange / HeatChange).

Clean reimplementation from research + known hex. Does **not** port broken PHP
(``HeatChange`` imports / ``CircuitChange::parse`` → TempStatus).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any

from pentairsnoop.framer import a5_checksum_bytes
from pentairsnoop.messages import Message, find_a5_index

# Commands enum (lib/src/Enum/Commands.php) — local literals to avoid registry cycle.
CMD_CIRCUIT_CHANGE_REQUEST = 0x86
CMD_TEMP_CHANGE_REQUEST = 0x88

# PHP ``Command::Header()`` — leading idle sync used on encode.
DEFAULT_PREAMBLE = bytes((0xFF, 0x00, 0xFF))

# Defaults from PHP CircuitChange::change / HeatChange::set (unconfirmed on wire).
DEFAULT_WRITE_DST = 0x10
DEFAULT_WRITE_SRC = 0x20
DEFAULT_WRITE_PROTOCOL = 0x07

# Payload offsets relative to A5 (PHP *Bytes absolute − 3).
_A5_PAYLOAD0 = 6


class CircuitId(IntEnum):
    """Local PHP ``Enum\\CircuitChange`` wire IDs (naming **unconfirmed**).

    External sources often call body ``POOL`` ``0x06`` (local name ``POOL_LIGHT``)
    and treat local ``POOL=0x02`` as AUX1. Prefer numeric IDs until captures.
    """

    SPA = 0x01
    POOL = 0x02  # local PHP label; external often AUX1
    CLEANER = 0x03
    WATER_FEATURE = 0x04
    SPA_LIGHT = 0x05
    POOL_LIGHT = 0x06  # local PHP; external often POOL body circuit
    HEAT_BOOST = 0x85


# PHP HeatChange OR-style mode bits (also = spa<<2|pool with heater=1 each).
HEAT_POOL_MODE = 0x01
HEAT_SPA_MODE = 0x04


def pack_heat_mode(*, pool_mode: int = 0, spa_mode: int = 0) -> int:
    """Pack EasyTouch heat modes: ``(spa_mode << 2) | pool_mode`` (0–3 each)."""
    if not 0 <= pool_mode <= 3 or not 0 <= spa_mode <= 3:
        raise ValueError("pool_mode and spa_mode must be in 0..3")
    return ((spa_mode & 0x03) << 2) | (pool_mode & 0x03)


def build_a5_hex(
    *,
    protocol: int,
    destination: int,
    source: int,
    command: int,
    payload: bytes,
    preamble: bytes = DEFAULT_PREAMBLE,
) -> str:
    """Pattern: Command — PHP ``buildHex``: preamble+A5+header+payload+u16 BE CS.

    Checksum is ``sum(A5 .. last payload byte) % 65536`` (same as A1 framer).
    """
    if len(payload) > 255:
        raise ValueError("payload longer than 255 bytes")
    length = len(payload)
    body_after_a5 = bytes(
        (
            protocol & 0xFF,
            destination & 0xFF,
            source & 0xFF,
            command & 0xFF,
            length,
        )
    ) + payload
    from_a5 = bytes((0xA5,)) + body_after_a5
    trailer = a5_checksum_bytes(from_a5)
    return (preamble + from_a5 + trailer).hex()


def parse_circuit_name(token: str) -> int:
    """Resolve a circuit id from int / 0xNN / local PHP enum name."""
    raw = token.strip().lower().replace("-", "_")
    if raw.startswith("0x"):
        return int(raw, 16)
    if raw.isdigit():
        return int(raw, 10)
    try:
        return int(CircuitId[raw.upper()])
    except KeyError as exc:
        names = ", ".join(m.name.lower() for m in CircuitId)
        raise ValueError(
            f"unknown circuit {token!r}; use 0xNN or one of: {names}"
        ) from exc


@dataclass
class CircuitChange(Message):
    """Pattern: Command — CIRCUIT_CHANGE_REQUEST (0x86) encode/decode.

    Shape: DST ``0x10``, SRC ``0x20`` (defaults), LEN ``2``, payload ``circuit, on|off``.
    """

    circuit: int = 0
    status: int = 0
    type_name: str = "CircuitChange"

    @classmethod
    def build(
        cls,
        circuit: int,
        on: bool,
        *,
        protocol: int = DEFAULT_WRITE_PROTOCOL,
        destination: int = DEFAULT_WRITE_DST,
        source: int = DEFAULT_WRITE_SRC,
        preamble: bytes = DEFAULT_PREAMBLE,
    ) -> CircuitChange:
        """Build a write request (does not send)."""
        status = 1 if on else 0
        hex_s = build_a5_hex(
            protocol=protocol,
            destination=destination,
            source=source,
            command=CMD_CIRCUIT_CHANGE_REQUEST,
            payload=bytes((circuit & 0xFF, status)),
            preamble=preamble,
        )
        raw = bytes.fromhex(hex_s)
        return cls(
            raw=raw,
            protocol=protocol,
            destination=destination,
            source=source,
            command=CMD_CIRCUIT_CHANGE_REQUEST,
            length=2,
            circuit=circuit & 0xFF,
            status=status,
            type_name="CircuitChange",
        )

    def to_hex(self) -> str:
        """Emit full frame hex (``ff00ffa5…``) with correct A5 checksum."""
        return build_a5_hex(
            protocol=self.protocol,
            destination=self.destination,
            source=self.source,
            command=CMD_CIRCUIT_CHANGE_REQUEST,
            payload=bytes((self.circuit & 0xFF, 1 if self.status else 0)),
        )

    @classmethod
    def parse(cls, raw: bytes) -> CircuitChange:
        """Parse a 0x86 frame; does **not** return TempStatus (PHP bug avoided)."""
        a5 = find_a5_index(raw)
        base = Message.parse_header(raw, a5)
        if base.command != CMD_CIRCUIT_CHANGE_REQUEST:
            raise ValueError(f"expected cmd 0x86, got 0x{base.command:02x}")
        if base.length < 2:
            raise ValueError("CircuitChange length must be >= 2")
        circuit = raw[a5 + _A5_PAYLOAD0]
        status = raw[a5 + _A5_PAYLOAD0 + 1]
        return cls(
            raw=raw,
            protocol=base.protocol,
            destination=base.destination,
            source=base.source,
            command=base.command,
            length=base.length,
            circuit=circuit,
            status=status,
            type_name="CircuitChange",
        )

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["circuit"] = self.circuit
        d["status"] = self.status
        d["on"] = bool(self.status)
        return d


@dataclass
class HeatChange(Message):
    """Pattern: Command — TEMP_CHANGE_REQUEST (0x88) encode/decode.

    Payload: ``poolSet, spaSet, mode, 0x00``. Mode may be OR-style (PHP) or
    ``(spa<<2)|pool`` (njsPC); both produce ``0x05`` for dual heater.
    """

    pool_set: int = 0
    spa_set: int = 0
    mode: int = 0
    type_name: str = "HeatChange"

    @classmethod
    def build(
        cls,
        *,
        pool_set: int,
        spa_set: int,
        mode: int,
        protocol: int = DEFAULT_WRITE_PROTOCOL,
        destination: int = DEFAULT_WRITE_DST,
        source: int = DEFAULT_WRITE_SRC,
        preamble: bytes = DEFAULT_PREAMBLE,
    ) -> HeatChange:
        """Build a heat setpoint write request (does not send)."""
        payload = bytes((pool_set & 0xFF, spa_set & 0xFF, mode & 0xFF, 0x00))
        hex_s = build_a5_hex(
            protocol=protocol,
            destination=destination,
            source=source,
            command=CMD_TEMP_CHANGE_REQUEST,
            payload=payload,
            preamble=preamble,
        )
        raw = bytes.fromhex(hex_s)
        return cls(
            raw=raw,
            protocol=protocol,
            destination=destination,
            source=source,
            command=CMD_TEMP_CHANGE_REQUEST,
            length=4,
            pool_set=pool_set & 0xFF,
            spa_set=spa_set & 0xFF,
            mode=mode & 0xFF,
            type_name="HeatChange",
        )

    def to_hex(self) -> str:
        """Emit full frame hex matching ``lib/index.php`` ``$set_temp`` shape."""
        return build_a5_hex(
            protocol=self.protocol,
            destination=self.destination,
            source=self.source,
            command=CMD_TEMP_CHANGE_REQUEST,
            payload=bytes(
                (self.pool_set & 0xFF, self.spa_set & 0xFF, self.mode & 0xFF, 0x00)
            ),
        )

    @classmethod
    def parse(cls, raw: bytes) -> HeatChange:
        """Parse a 0x88 frame; does **not** misuse TempStatus / CircuitChangeBytes."""
        a5 = find_a5_index(raw)
        base = Message.parse_header(raw, a5)
        if base.command != CMD_TEMP_CHANGE_REQUEST:
            raise ValueError(f"expected cmd 0x88, got 0x{base.command:02x}")
        if base.length < 4:
            raise ValueError("HeatChange length must be >= 4")
        pool_set = raw[a5 + _A5_PAYLOAD0]
        spa_set = raw[a5 + _A5_PAYLOAD0 + 1]
        mode = raw[a5 + _A5_PAYLOAD0 + 2]
        return cls(
            raw=raw,
            protocol=base.protocol,
            destination=base.destination,
            source=base.source,
            command=base.command,
            length=base.length,
            pool_set=pool_set,
            spa_set=spa_set,
            mode=mode,
            type_name="HeatChange",
        )

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["pool_set"] = self.pool_set
        d["spa_set"] = self.spa_set
        d["mode"] = self.mode
        return d
