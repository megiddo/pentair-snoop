"""Pattern: Strategy — pluggable byte I/O backends (serial, TCP, hex file)."""

from __future__ import annotations

import random
import re
import socket
import time
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

# Lab defaults from parent ``api/app/settings.php`` / ``local_rs485.py``.
DEFAULT_EW11_HOST = "10.0.0.11"
DEFAULT_EW11_PORT = 8899
DEFAULT_SERIAL_BAUD = 9600
DEFAULT_SERIAL_BYTESIZE = 8
DEFAULT_SERIAL_PARITY = "N"
DEFAULT_SERIAL_STOPBITS = 1
DEFAULT_CONNECT_TIMEOUT = 5.0
DEFAULT_READ_TIMEOUT = 1.0
DEFAULT_BACKOFF_INITIAL = 0.5
DEFAULT_BACKOFF_MAX = 30.0


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


def backoff_delay(
    attempt: int,
    *,
    initial: float = DEFAULT_BACKOFF_INITIAL,
    maximum: float = DEFAULT_BACKOFF_MAX,
    rng: Callable[[], float] | None = None,
) -> float:
    """Exponential reconnect delay with full-jitter (attempt is 0-based).

    ``delay = uniform(0, min(maximum, initial * 2**attempt))``.
    """
    if attempt < 0:
        attempt = 0
    cap = min(maximum, initial * (2**attempt))
    pick = rng if rng is not None else random.random
    return pick() * cap


class ReconnectingTransport:
    """Pattern: Strategy — persistent link with exponential backoff + jitter.

    Subclasses implement ``_connect_once``, ``_disconnect_once``, ``_raw_read``,
    and ``_raw_write``. ``read`` never open/closes per call: on disconnect or
    error it backs off and reconnects while ``open()`` lifecycle is active.
    """

    def __init__(
        self,
        *,
        reconnect: bool = True,
        backoff_initial: float = DEFAULT_BACKOFF_INITIAL,
        backoff_max: float = DEFAULT_BACKOFF_MAX,
        sleep: Callable[[float], None] = time.sleep,
        rng: Callable[[], float] | None = None,
    ) -> None:
        self._reconnect = reconnect
        self._backoff_initial = backoff_initial
        self._backoff_max = backoff_max
        self._sleep = sleep
        self._rng = rng
        self._want_open = False
        self._connected = False
        self._ever_connected = False
        self._attempt = 0
        self._reconnect_count = 0

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def reconnect_count(self) -> int:
        """Successful reconnects after the initial ``open()`` connect."""
        return self._reconnect_count

    def open(self) -> None:
        """Connect once and keep the session marked open for reconnect."""
        self._want_open = True
        self._attempt = 0
        self._ensure_connected()

    def close(self) -> None:
        """Stop reconnecting and drop the underlying connection (idempotent)."""
        self._want_open = False
        self._disconnect_quiet()

    def read(self, n: int) -> bytes:
        """Read up to ``n`` bytes; reconnect on disconnect/error while open.

        Idle timeouts return no data without tearing down the link. A peer close
        (TCP ``recv`` → ``b''``) or I/O error triggers backoff reconnect.
        """
        if not self._want_open:
            raise RuntimeError(f"{type(self).__name__} is not open")
        if n <= 0:
            return b""
        while self._want_open:
            try:
                self._ensure_connected()
                data = self._raw_read(n)
                if data is None:
                    # Idle (timeout / no bytes) — keep connection, wait again.
                    continue
                if data:
                    self._attempt = 0
                    return data
                # Explicit empty bytes: peer closed the stream.
                self._disconnect_quiet()
                if not self._reconnect:
                    return b""
                self._wait_backoff()
            except OSError:
                self._disconnect_quiet()
                if not self._want_open:
                    return b""
                if not self._reconnect:
                    raise
                self._wait_backoff()
        return b""

    def write(self, data: bytes) -> None:
        """Write ``data``; reconnect once on failure when reconnect is enabled."""
        if not self._want_open:
            raise RuntimeError(f"{type(self).__name__} is not open")
        try:
            self._ensure_connected()
            self._raw_write(data)
        except OSError:
            self._disconnect_quiet()
            if not self._reconnect or not self._want_open:
                raise
            self._wait_backoff()
            self._ensure_connected()
            self._raw_write(data)

    def _ensure_connected(self) -> None:
        if self._connected:
            return
        while self._want_open:
            try:
                self._connect_once()
                self._connected = True
                if self._ever_connected:
                    self._reconnect_count += 1
                self._ever_connected = True
                self._attempt = 0
                return
            except OSError:
                self._connected = False
                if not self._reconnect:
                    raise
                self._wait_backoff()
        raise RuntimeError(f"{type(self).__name__} closed during connect")

    def _wait_backoff(self) -> None:
        delay = backoff_delay(
            self._attempt,
            initial=self._backoff_initial,
            maximum=self._backoff_max,
            rng=self._rng,
        )
        self._attempt += 1
        self._sleep(delay)

    def _disconnect_quiet(self) -> None:
        try:
            self._disconnect_once()
        except OSError:
            pass
        self._connected = False

    def _connect_once(self) -> None:
        raise NotImplementedError

    def _disconnect_once(self) -> None:
        raise NotImplementedError

    def _raw_read(self, n: int) -> bytes | None:
        """Return bytes, ``None`` if idle (still connected), or ``b''`` if peer closed."""
        raise NotImplementedError

    def _raw_write(self, data: bytes) -> None:
        raise NotImplementedError


class TcpTransport(ReconnectingTransport):
    """Pattern: Strategy — persistent TCP client (EW11 serial bridge).

    Defaults match parent ``settings.php``: host ``10.0.0.11``, port ``8899``.
    """

    def __init__(
        self,
        host: str = DEFAULT_EW11_HOST,
        port: int = DEFAULT_EW11_PORT,
        *,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
        reconnect: bool = True,
        backoff_initial: float = DEFAULT_BACKOFF_INITIAL,
        backoff_max: float = DEFAULT_BACKOFF_MAX,
        sleep: Callable[[float], None] = time.sleep,
        rng: Callable[[], float] | None = None,
        sock_factory: Callable[[], socket.socket] | None = None,
    ) -> None:
        super().__init__(
            reconnect=reconnect,
            backoff_initial=backoff_initial,
            backoff_max=backoff_max,
            sleep=sleep,
            rng=rng,
        )
        self.host = host
        self.port = port
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self._sock_factory = sock_factory or (lambda: socket.socket())
        self._sock: socket.socket | None = None

    def _connect_once(self) -> None:
        sock = self._sock_factory()
        sock.settimeout(self.connect_timeout)
        try:
            sock.connect((self.host, self.port))
        except OSError:
            sock.close()
            raise
        sock.settimeout(self.read_timeout)
        self._sock = sock

    def _disconnect_once(self) -> None:
        sock = self._sock
        self._sock = None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()

    def _raw_read(self, n: int) -> bytes | None:
        if self._sock is None:
            raise OSError("TCP socket not connected")
        try:
            return self._sock.recv(n)
        except (TimeoutError, socket.timeout):
            # Idle bus — keep the socket; Session/watch keeps framing buffer.
            return None

    def _raw_write(self, data: bytes) -> None:
        if self._sock is None:
            raise OSError("TCP socket not connected")
        self._sock.sendall(data)


class SerialTransport(ReconnectingTransport):
    """Pattern: Strategy — persistent pyserial USB-RS485 link.

    Defaults: **9600 8N1** from parent ``local_rs485.py`` — starting config,
    **unconfirmed** for all adapters until capture validation.
    """

    def __init__(
        self,
        port: str,
        *,
        baudrate: int = DEFAULT_SERIAL_BAUD,
        bytesize: int = DEFAULT_SERIAL_BYTESIZE,
        parity: str = DEFAULT_SERIAL_PARITY,
        stopbits: float = DEFAULT_SERIAL_STOPBITS,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
        reconnect: bool = True,
        backoff_initial: float = DEFAULT_BACKOFF_INITIAL,
        backoff_max: float = DEFAULT_BACKOFF_MAX,
        sleep: Callable[[float], None] = time.sleep,
        rng: Callable[[], float] | None = None,
        serial_factory: Callable[..., object] | None = None,
    ) -> None:
        super().__init__(
            reconnect=reconnect,
            backoff_initial=backoff_initial,
            backoff_max=backoff_max,
            sleep=sleep,
            rng=rng,
        )
        self.port = port
        self.baudrate = baudrate
        self.bytesize = bytesize
        self.parity = parity
        self.stopbits = stopbits
        self.read_timeout = read_timeout
        self._serial_factory = serial_factory
        self._ser: object | None = None

    def _connect_once(self) -> None:
        factory = self._serial_factory
        if factory is None:
            import serial  # lazy: keep import optional for non-serial tests

            factory = serial.Serial
        ser = factory(
            port=self.port,
            baudrate=self.baudrate,
            bytesize=self.bytesize,
            parity=self.parity,
            stopbits=self.stopbits,
            timeout=self.read_timeout,
        )
        self._ser = ser

    def _disconnect_once(self) -> None:
        ser = self._ser
        self._ser = None
        if ser is not None:
            close = getattr(ser, "close", None)
            if callable(close):
                close()

    def _raw_read(self, n: int) -> bytes | None:
        if self._ser is None:
            raise OSError("serial port not connected")
        read = getattr(self._ser, "read")
        data = read(n)
        if not isinstance(data, (bytes, bytearray)):
            raise OSError("serial read returned non-bytes")
        if not data:
            # pyserial timeout with no bytes — idle, not disconnect.
            return None
        return bytes(data)

    def _raw_write(self, data: bytes) -> None:
        if self._ser is None:
            raise OSError("serial port not connected")
        write = getattr(self._ser, "write")
        write(data)


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
