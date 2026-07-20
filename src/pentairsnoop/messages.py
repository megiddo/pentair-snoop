"""Pattern: Command — typed bus message objects (cross-language DTOs)."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any


# Relative offsets from the A5 sync byte (PHP HeaderBytes / *StatusBytes assume
# normalized ``ff00ffa5`` with A5 at index 3; framer raw may include preamble).
_A5_PROTOCOL = 1
_A5_DST = 2
_A5_SRC = 3
_A5_COMMAND = 4
_A5_LENGTH = 5

# StatusCommandBytes relative to A5 (PHP index − 3).
_SS_HOURS = 6
_SS_MINUTES = 7
_SS_CIRCUITS = 8
_SS_WATER_TEMP = 20
_SS_HEATER_TEMP = 21
_SS_AIR_TEMP = 24

# TempStatusBytes relative to A5 (PHP index − 3).
_TS_WATER = 7
_TS_AIR = 8
_TS_WATER_SET = 9
_TS_SPA_SET = 10
_TS_INFO = 11

# CircuitStatus bitmask flags.
CIRCUIT_CLEANER_PUMP = 0x02
CIRCUIT_WATER_FEATURE = 0x04
CIRCUIT_SPA_LIGHT = 0x08
CIRCUIT_POOL_LIGHT = 0x10
CIRCUIT_FILTER_PUMP = 0x20


def find_a5_index(raw: bytes) -> int:
    """Return the index of the A5 byte in a framed standard message.

    Accepts preamble variants ``FF 00 FF A5`` / ``00 FF A5`` / idle ``FF*``.
    """
    i = 0
    while True:
        a5 = raw.find(0xA5, i)
        if a5 < 0:
            raise ValueError("A5 sync byte not found in raw frame")
        if a5 >= 3 and raw[a5 - 3 : a5] == b"\xff\x00\xff":
            return a5
        if a5 >= 2 and raw[a5 - 2 : a5] == b"\x00\xff":
            return a5
        i = a5 + 1


def _byte_from_a5(raw: bytes, a5: int, offset: int) -> int:
    idx = a5 + offset
    if idx >= len(raw):
        raise ValueError(f"raw too short for field at A5+{offset}")
    return raw[idx]


def _on_off(circuits: int, flag: int) -> str:
    return "on" if circuits & flag else "off"


@dataclass
class CircuitStatusFlags:
    """Pattern: Command — decoded CircuitStatus bitmask as on/off strings."""

    filterPump: str = "off"
    cleanerPump: str = "off"
    waterFeature: str = "off"
    spaLight: str = "off"
    poolLight: str = "off"

    @classmethod
    def from_circuits(cls, circuits: int) -> CircuitStatusFlags:
        return cls(
            filterPump=_on_off(circuits, CIRCUIT_FILTER_PUMP),
            cleanerPump=_on_off(circuits, CIRCUIT_CLEANER_PUMP),
            waterFeature=_on_off(circuits, CIRCUIT_WATER_FEATURE),
            spaLight=_on_off(circuits, CIRCUIT_SPA_LIGHT),
            poolLight=_on_off(circuits, CIRCUIT_POOL_LIGHT),
        )


@dataclass
class Message:
    """Pattern: Command — shared fields for a framed Pentair message."""

    raw: bytes = field(default_factory=bytes)
    protocol: int = 0
    destination: int = 0
    source: int = 0
    command: int = 0
    length: int = 0
    type_name: str = "Message"

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready dict similar to PHP ``Command::toJson`` (raw as hex)."""
        d = asdict(self)
        d["raw"] = self.raw.hex()
        # Keep type_name for CLI clarity; PHP encodes class props only.
        return d

    def to_json(self, *, pretty: bool = False) -> str:
        """Serialize like PHP ``Command::toJson``."""
        indent = 2 if pretty else None
        return json.dumps(self.to_dict(), indent=indent, separators=None if pretty else (",", ":"))

    @classmethod
    def parse_header(cls, raw: bytes, a5: int | None = None) -> Message:
        """Fill shared header fields from raw, indexing from A5."""
        a5_i = find_a5_index(raw) if a5 is None else a5
        return cls(
            raw=raw,
            protocol=_byte_from_a5(raw, a5_i, _A5_PROTOCOL),
            destination=_byte_from_a5(raw, a5_i, _A5_DST),
            source=_byte_from_a5(raw, a5_i, _A5_SRC),
            command=_byte_from_a5(raw, a5_i, _A5_COMMAND),
            length=_byte_from_a5(raw, a5_i, _A5_LENGTH),
            type_name=cls.__name__,
        )


@dataclass
class SystemStatus(Message):
    """Pattern: Command — SYSTEM_STATUS (0x02) typed fields from StatusCommandBytes."""

    hours: int = 0
    minutes: int = 0
    circuits: int = 0
    circuitStatus: CircuitStatusFlags = field(default_factory=CircuitStatusFlags)
    waterTemp: int = 0
    heaterTemp: int = 0
    airTemp: int = 0
    type_name: str = "SystemStatus"

    @classmethod
    def parse(cls, raw: bytes) -> SystemStatus:
        a5 = find_a5_index(raw)
        base = Message.parse_header(raw, a5)
        circuits = _byte_from_a5(raw, a5, _SS_CIRCUITS)
        return cls(
            raw=base.raw,
            protocol=base.protocol,
            destination=base.destination,
            source=base.source,
            command=base.command,
            length=base.length,
            hours=_byte_from_a5(raw, a5, _SS_HOURS),
            minutes=_byte_from_a5(raw, a5, _SS_MINUTES),
            circuits=circuits,
            circuitStatus=CircuitStatusFlags.from_circuits(circuits),
            waterTemp=_byte_from_a5(raw, a5, _SS_WATER_TEMP),
            heaterTemp=_byte_from_a5(raw, a5, _SS_HEATER_TEMP),
            airTemp=_byte_from_a5(raw, a5, _SS_AIR_TEMP),
            type_name="SystemStatus",
        )

    def to_dict(self) -> dict[str, Any]:
        d = super().to_dict()
        d["circuitStatus"] = asdict(self.circuitStatus)
        return d


@dataclass
class TempStatus(Message):
    """Pattern: Command — INFO/TempStatus (0x08) fields from TempStatusBytes."""

    water: int = 0
    air: int = 0
    waterSet: int = 0
    spaSet: int = 0
    info: int = 0
    type_name: str = "TempStatus"

    @classmethod
    def parse(cls, raw: bytes) -> TempStatus:
        a5 = find_a5_index(raw)
        base = Message.parse_header(raw, a5)
        return cls(
            raw=base.raw,
            protocol=base.protocol,
            destination=base.destination,
            source=base.source,
            command=base.command,
            length=base.length,
            water=_byte_from_a5(raw, a5, _TS_WATER),
            air=_byte_from_a5(raw, a5, _TS_AIR),
            waterSet=_byte_from_a5(raw, a5, _TS_WATER_SET),
            spaSet=_byte_from_a5(raw, a5, _TS_SPA_SET),
            info=_byte_from_a5(raw, a5, _TS_INFO),
            type_name="TempStatus",
        )


@dataclass
class Unknown(Message):
    """Pattern: Command — unknown / unregistered command byte (raw hex in JSON)."""

    type_name: str = "Unknown"

    @classmethod
    def parse(cls, raw: bytes, command: int | None = None) -> Unknown:
        """Parse A5 header fields when present; otherwise raw-only (e.g. IntelliChlor)."""
        try:
            a5 = find_a5_index(raw)
            base = Message.parse_header(raw, a5)
            return cls(
                raw=base.raw,
                protocol=base.protocol,
                destination=base.destination,
                source=base.source,
                command=base.command,
                length=base.length,
                type_name="Unknown",
            )
        except ValueError:
            return cls(
                raw=raw,
                command=0 if command is None else command,
                type_name="Unknown",
            )

    def to_dict(self) -> dict[str, Any]:
        """Unknowns emphasize raw hex; include header fields when available."""
        return {
            "type_name": self.type_name,
            "command": self.command,
            "protocol": self.protocol,
            "destination": self.destination,
            "source": self.source,
            "length": self.length,
            "raw": self.raw.hex(),
        }
