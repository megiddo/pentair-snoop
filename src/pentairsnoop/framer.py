"""Pattern: Parser — sync seek and length-based frame extraction from a byte stream."""

from __future__ import annotations


class Framer:
    """Pattern: Parser — extract framed messages from buffered bus bytes (A1)."""

    def feed(self, data: bytes) -> list[bytes]:
        """Accept raw bytes; return zero or more complete frames (stub)."""
        _ = data
        return []
