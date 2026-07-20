"""Pattern: Strategy — pluggable byte I/O backends (serial, TCP, hex file)."""

from __future__ import annotations

import re
from pathlib import Path
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


_HEX_PAIR = re.compile(r"(?i)[0-9a-f]{2}")
# Offset prefixes in logic-analyzer / dump style fixtures: "< 0x0", "0000:", etc.
_DUMP_PREFIX = re.compile(
    r"(?i)^\s*(?:<\s*)?(?:0x)?[0-9a-f]+\s*:\s*|^\s*<\s*0x[0-9a-f]+\s+"
)


def parse_hex_bytes(text: str) -> bytes:
    """Decode contiguous hex or whitespace/dump-formatted hex into bytes.

    Accepts pure hex strings (PHP ``HexStringSampleConnector`` style) and
    fixture dumps such as::

        < 0x0\\t ff ff 00 ff a5 ...
    """
    pairs: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("/*"):
            continue
        if stripped.startswith("*/") or stripped.endswith("*/"):
            continue
        # Drop dump address column when present.
        cleaned = _DUMP_PREFIX.sub("", stripped, count=1)
        # Also handle tabs after "< 0xN" when regex left trailing noise.
        if cleaned.startswith("<"):
            parts = cleaned.split(None, 1)
            cleaned = parts[1] if len(parts) > 1 else ""
        pairs.extend(_HEX_PAIR.findall(cleaned))
    if not pairs:
        # Fallback: entire file as one hex blob (comments already skipped per line).
        pairs = _HEX_PAIR.findall(text)
    return bytes(int(p, 16) for p in pairs)


class HexFileTransport:
    """Pattern: Strategy — read a hex fixture file as a byte stream.

    Parity with PHP ``HexStringSampleConnector``: open loads bytes; ``read(n)``
    returns the next slice; ``write`` is unsupported for sample replay.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._data = b""
        self._pos = 0
        self._opened = False

    @property
    def path(self) -> Path:
        return self._path

    @property
    def remaining(self) -> int:
        """Unread bytes left in the loaded fixture."""
        return max(0, len(self._data) - self._pos)

    def open(self) -> None:
        """Load and parse the hex file into an in-memory byte buffer."""
        text = self._path.read_text(encoding="utf-8", errors="replace")
        self._data = parse_hex_bytes(text)
        self._pos = 0
        self._opened = True

    def close(self) -> None:
        """Release the in-memory buffer (idempotent)."""
        self._data = b""
        self._pos = 0
        self._opened = False

    def read(self, n: int) -> bytes:
        """Read up to ``n`` bytes; returns ``b''`` at EOF (like a drained stream)."""
        if not self._opened:
            raise RuntimeError("HexFileTransport is not open")
        if n <= 0:
            return b""
        end = min(self._pos + n, len(self._data))
        chunk = self._data[self._pos : end]
        self._pos = end
        return chunk

    def write(self, data: bytes) -> None:
        """Sample fixtures are read-only."""
        raise NotImplementedError("HexFileTransport does not support write")

    def read_all(self) -> bytes:
        """Return all remaining bytes (convenience for offline framing tests)."""
        if not self._opened:
            raise RuntimeError("HexFileTransport is not open")
        chunk = self._data[self._pos :]
        self._pos = len(self._data)
        return chunk
