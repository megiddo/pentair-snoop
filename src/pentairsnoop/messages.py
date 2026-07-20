"""Pattern: Command — typed bus message objects (cross-language DTOs)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Message:
    """Pattern: Command — shared fields for a framed Pentair message."""

    raw: bytes = field(default_factory=bytes)
    protocol: int = 0
    destination: int = 0
    source: int = 0
    command: int = 0
    length: int = 0
