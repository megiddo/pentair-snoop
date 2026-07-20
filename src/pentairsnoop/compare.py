"""Pattern: Strategy — side-by-side crafted TX vs captured/reference TX."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from pentairsnoop.framer import verify_a5_frame
from pentairsnoop.messages import find_a5_index


def _normalize_hex(hex_s: str) -> bytes:
    cleaned = "".join(hex_s.split()).lower()
    if cleaned.startswith("0x"):
        cleaned = cleaned[2:]
    if len(cleaned) % 2:
        raise ValueError(f"odd-length hex: {hex_s!r}")
    return bytes.fromhex(cleaned)


def _a5_slice(raw: bytes) -> bytes:
    """Return bytes from A5 through end (exclude leading preamble)."""
    a5 = find_a5_index(raw)
    return raw[a5:]


@dataclass(frozen=True)
class FieldDelta:
    """One compared field and whether crafted matched reference."""

    field: str
    crafted: Any
    reference: Any
    match: bool


@dataclass
class TxDiff:
    """Pattern: Strategy — structured craft vs reference TX comparison."""

    match: bool
    crafted_hex: str
    reference_hex: str
    deltas: list[FieldDelta] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "match": self.match,
            "crafted_hex": self.crafted_hex,
            "reference_hex": self.reference_hex,
            "deltas": [asdict(d) for d in self.deltas],
        }


def _header_fields(from_a5: bytes) -> dict[str, Any]:
    if len(from_a5) < 6:
        raise ValueError("frame too short for A5 header")
    length = from_a5[5]
    payload = from_a5[6 : 6 + length]
    trailer = from_a5[6 + length : 6 + length + 2]
    cs = (trailer[0] << 8) | trailer[1] if len(trailer) == 2 else None
    return {
        "protocol": from_a5[1],
        "destination": from_a5[2],
        "source": from_a5[3],
        "command": from_a5[4],
        "length": length,
        "payload": payload.hex(),
        "checksum": None if cs is None else f"{cs:04x}",
        "checksum_ok": verify_a5_frame(from_a5),
    }


def diff_tx(crafted_hex: str, reference_hex: str) -> TxDiff:
    """Compare crafted TX to a captured/reference TX hex string.

    Compares protocol, addresses, command, length, payload, and checksum
    (values + verify). Full-frame hex equality is also reported.
    """
    crafted = _normalize_hex(crafted_hex)
    reference = _normalize_hex(reference_hex)
    c_a5 = _a5_slice(crafted)
    r_a5 = _a5_slice(reference)
    c_fields = _header_fields(c_a5)
    r_fields = _header_fields(r_a5)

    keys = (
        "protocol",
        "destination",
        "source",
        "command",
        "length",
        "payload",
        "checksum",
        "checksum_ok",
    )
    deltas: list[FieldDelta] = []
    for key in keys:
        cv, rv = c_fields[key], r_fields[key]
        deltas.append(FieldDelta(field=key, crafted=cv, reference=rv, match=cv == rv))

    # Full wire equality including preamble (normalized lowercase hex).
    full_match = crafted.hex() == reference.hex()
    deltas.append(
        FieldDelta(
            field="full_hex",
            crafted=crafted.hex(),
            reference=reference.hex(),
            match=full_match,
        )
    )
    # A5-body equality (ignore idle/preamble differences).
    body_match = c_a5.hex() == r_a5.hex()
    deltas.append(
        FieldDelta(
            field="a5_body_hex",
            crafted=c_a5.hex(),
            reference=r_a5.hex(),
            match=body_match,
        )
    )

    structural = all(d.match for d in deltas if d.field not in ("full_hex",))
    return TxDiff(
        match=structural and body_match,
        crafted_hex=crafted.hex(),
        reference_hex=reference.hex(),
        deltas=deltas,
    )
