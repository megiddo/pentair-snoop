"""Pattern: Strategy — pluggable byte I/O backends (serial, TCP, hex file)."""

from __future__ import annotations

from typing import Protocol


class Transport(Protocol):
    """Pattern: Strategy — open/close/read/write byte stream to the bus."""

    def open(self) -> None:
        """Establish the underlying connection or file handle."""

    def close(self) -> None:
        """Release the underlying connection or file handle."""

    def read(self, n: int) -> bytes:
        """Read up to ``n`` bytes from the bus or fixture stream."""

    def write(self, data: bytes) -> None:
        """Write ``data`` to the bus (lab / write-craft paths only)."""
