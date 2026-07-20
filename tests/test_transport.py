"""A3 tests: TcpTransport / SerialTransport reconnect, Session watch, CLI watch."""

from __future__ import annotations

import json
import socket
import sys
from pathlib import Path
from typing import Any

import pytest

from pentairsnoop.cli import (
    _parse_tcp_endpoint,
    _passes_filter,
    build_parser,
    build_watch_transport,
    cmd_watch,
    main,
    parse_command_filter,
)
from pentairsnoop.framer import Frame, FrameKind
from pentairsnoop.session import QuarantinedFrame, Session
from pentairsnoop.transport import (
    DEFAULT_EW11_HOST,
    DEFAULT_EW11_PORT,
    DEFAULT_SERIAL_BAUD,
    HexFileTransport,
    ReconnectingTransport,
    SerialTransport,
    TcpTransport,
    backoff_delay,
)

_SAMPLES = Path(__file__).resolve().parents[2] / "samples"
_INFO = "ffffffffffffffff00ffa50c0f10080d4848555a620400000000000000028a"


class FakeSocket:
    """Minimal socket stand-in for TcpTransport unit tests."""

    def __init__(
        self,
        chunks: list[bytes | BaseException] | None = None,
        *,
        fail_connect_times: int = 0,
    ) -> None:
        self.chunks = list(chunks or [])
        self.fail_connect_times = fail_connect_times
        self._connect_calls = 0
        self.closed = False
        self.sent: list[bytes] = []
        self.timeout: float | None = None
        self.shutdown_calls = 0

    def settimeout(self, value: float | None) -> None:
        self.timeout = value

    def connect(self, address: tuple[str, int]) -> None:
        self._connect_calls += 1
        _ = address
        if self._connect_calls <= self.fail_connect_times:
            raise OSError("connect refused")

    def recv(self, n: int) -> bytes:
        _ = n
        if not self.chunks:
            return b""
        item = self.chunks.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def sendall(self, data: bytes) -> None:
        if self.closed:
            raise OSError("closed")
        self.sent.append(data)

    def shutdown(self, how: int) -> None:
        _ = how
        self.shutdown_calls += 1

    def close(self) -> None:
        self.closed = True


class FakeSerial:
    """Minimal pyserial stand-in for SerialTransport unit tests."""

    def __init__(
        self,
        chunks: list[bytes | BaseException] | None = None,
        *,
        fail_open_times: int = 0,
        **kwargs: Any,
    ) -> None:
        self.kwargs = kwargs
        self.chunks = list(chunks or [])
        self.fail_open_times = fail_open_times
        self._open_calls = 0
        self.closed = False
        self.written: list[bytes] = []
        # Factory-style: first N constructions fail.
        type(self)._construct_count = getattr(type(self), "_construct_count", 0) + 1
        if type(self)._construct_count <= fail_open_times:
            raise OSError("port busy")

    def read(self, n: int) -> bytes:
        _ = n
        if not self.chunks:
            return b""
        item = self.chunks.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def write(self, data: bytes) -> int:
        self.written.append(data)
        return len(data)

    def close(self) -> None:
        self.closed = True


def test_backoff_delay_caps_and_jitter() -> None:
    assert backoff_delay(0, initial=1.0, maximum=30.0, rng=lambda: 0.0) == 0.0
    assert backoff_delay(0, initial=1.0, maximum=30.0, rng=lambda: 1.0) == 1.0
    assert backoff_delay(3, initial=0.5, maximum=2.0, rng=lambda: 1.0) == 2.0
    assert backoff_delay(-1, initial=1.0, maximum=5.0, rng=lambda: 0.5) == 0.5


def test_tcp_defaults() -> None:
    t = TcpTransport()
    assert t.host == DEFAULT_EW11_HOST
    assert t.port == DEFAULT_EW11_PORT


def test_tcp_open_read_close_persistent() -> None:
    sleeps: list[float] = []
    sock = FakeSocket([b"abc", b"de"])

    def factory() -> FakeSocket:
        return sock

    t = TcpTransport(
        "127.0.0.1",
        9999,
        sleep=sleeps.append,
        rng=lambda: 0.0,
        sock_factory=factory,  # type: ignore[arg-type]
    )
    t.open()
    assert t.connected
    assert t.read(10) == b"abc"
    assert t.read(10) == b"de"
    assert sleeps == []
    assert t.reconnect_count == 0
    t.close()
    assert not t.connected
    with pytest.raises(RuntimeError, match="not open"):
        t.read(1)


def test_tcp_reconnect_on_peer_close() -> None:
    sleeps: list[float] = []
    socks = [
        FakeSocket([b"hi", b""]),  # data then peer close
        FakeSocket([b"yo"]),
    ]
    idx = {"i": 0}

    def factory() -> FakeSocket:
        s = socks[idx["i"]]
        idx["i"] += 1
        return s

    t = TcpTransport(
        sleep=sleeps.append,
        rng=lambda: 0.0,
        sock_factory=factory,  # type: ignore[arg-type]
        backoff_initial=1.0,
    )
    t.open()
    assert t.read(10) == b"hi"
    assert t.read(10) == b"yo"
    assert t.reconnect_count == 1
    assert sleeps == [0.0]  # attempt 0 → 0 * cap
    t.close()


def test_tcp_reconnect_on_recv_error() -> None:
    sleeps: list[float] = []
    socks = [
        FakeSocket([OSError("reset"), b"ok"]),
        FakeSocket([b"ok"]),
    ]
    # First sock raises on first recv — simplify with two sockets:
    socks = [
        FakeSocket([OSError("reset")]),
        FakeSocket([b"ok"]),
    ]
    idx = {"i": 0}

    def factory() -> FakeSocket:
        s = socks[idx["i"]]
        idx["i"] += 1
        return s

    t = TcpTransport(
        sleep=sleeps.append,
        rng=lambda: 0.5,
        sock_factory=factory,  # type: ignore[arg-type]
        backoff_initial=2.0,
    )
    t.open()
    assert t.read(3) == b"ok"
    assert t.reconnect_count == 1
    assert sleeps == [1.0]  # 0.5 * 2.0
    t.close()


def test_tcp_idle_timeout_does_not_reconnect() -> None:
    sleeps: list[float] = []
    sock = FakeSocket([socket.timeout(), b"x"])

    t = TcpTransport(
        sleep=sleeps.append,
        sock_factory=lambda: sock,  # type: ignore[arg-type, return-value]
    )
    t.open()
    assert t.read(1) == b"x"
    assert t.reconnect_count == 0
    assert sleeps == []
    t.close()


def test_tcp_connect_retries_then_succeeds() -> None:
    sleeps: list[float] = []
    sock = FakeSocket([b"z"], fail_connect_times=2)

    t = TcpTransport(
        sleep=sleeps.append,
        rng=lambda: 0.0,
        sock_factory=lambda: sock,  # type: ignore[arg-type, return-value]
        backoff_initial=1.0,
    )
    t.open()
    assert len(sleeps) == 2
    assert t.read(1) == b"z"
    # Failures before first success are retries on open, not reconnect_count.
    assert t.reconnect_count == 0
    t.close()


def test_tcp_write_and_reconnect_write() -> None:
    sleeps: list[float] = []
    sock1 = FakeSocket([])
    sock1.closed = True  # sendall fails
    sock2 = FakeSocket([])
    socks = [sock1, sock2]
    idx = {"i": 0}

    def factory() -> FakeSocket:
        s = socks[idx["i"]]
        idx["i"] += 1
        return s

    # After open, sock1 is connected but write fails → reconnect to sock2.
    t = TcpTransport(
        sleep=sleeps.append,
        rng=lambda: 0.0,
        sock_factory=factory,  # type: ignore[arg-type]
    )
    t.open()
    # Force write failure path: mark connected sock closed after open.
    assert t._sock is sock1  # noqa: SLF001
    sock1.closed = True
    t.write(b"ping")
    assert sock2.sent == [b"ping"]
    assert t.reconnect_count == 1
    t.close()


def test_tcp_no_reconnect_raises() -> None:
    sock = FakeSocket([OSError("boom")])
    t = TcpTransport(
        reconnect=False,
        sock_factory=lambda: sock,  # type: ignore[arg-type, return-value]
    )
    t.open()
    with pytest.raises(OSError, match="boom"):
        t.read(1)
    t.close()


def test_tcp_read_zero_and_write_not_open() -> None:
    sock = FakeSocket([b"a"])
    t = TcpTransport(sock_factory=lambda: sock)  # type: ignore[arg-type, return-value]
    with pytest.raises(RuntimeError):
        t.write(b"x")
    t.open()
    assert t.read(0) == b""
    t.close()


def test_serial_defaults_and_read_reconnect() -> None:
    sleeps: list[float] = []
    instances: list[FakeSerial] = []

    def factory(**kwargs: Any) -> FakeSerial:
        if not instances:
            ser = FakeSerial.__new__(FakeSerial)
            ser.kwargs = kwargs
            ser.chunks = [b"ab", OSError("unplug")]
            ser.fail_open_times = 0
            ser._open_calls = 0
            ser.closed = False
            ser.written = []
            instances.append(ser)
            return ser
        ser = FakeSerial.__new__(FakeSerial)
        ser.kwargs = kwargs
        ser.chunks = [b"cd"]
        ser.fail_open_times = 0
        ser._open_calls = 0
        ser.closed = False
        ser.written = []
        instances.append(ser)
        return ser

    t = SerialTransport(
        "/dev/fake",
        sleep=sleeps.append,
        rng=lambda: 0.0,
        serial_factory=factory,
        backoff_initial=1.0,
    )
    assert t.baudrate == DEFAULT_SERIAL_BAUD
    t.open()
    assert instances[0].kwargs["baudrate"] == 9600
    assert instances[0].kwargs["parity"] == "N"
    assert t.read(2) == b"ab"
    assert t.read(2) == b"cd"
    assert t.reconnect_count == 1
    assert sleeps == [0.0]
    t.write(b"tx")
    assert instances[1].written == [b"tx"]
    t.close()
    assert instances[1].closed


def test_serial_idle_empty_does_not_disconnect() -> None:
    ser = FakeSerial([b"", b"x"])
    # Empty then data — empty is idle (None path).
    calls = {"n": 0}

    def factory(**kwargs: Any) -> FakeSerial:
        _ = kwargs
        calls["n"] += 1
        if calls["n"] == 1:
            return ser
        raise AssertionError("should not reconnect on idle empty")

    # Rebuild: first read returns b"" → None; second returns b"x"
    ser.chunks = [b"", b"x"]
    t = SerialTransport("/dev/fake", serial_factory=factory, sleep=lambda _d: None)
    t.open()
    assert t.read(1) == b"x"
    assert t.reconnect_count == 0
    t.close()


def test_session_iter_messages_hex_file_persistent() -> None:
    path = _SAMPLES / "status_temps.hex"
    if not path.is_file():
        pytest.skip("parent samples missing")
    transport = HexFileTransport(path)
    open_count = {"n": 0}
    close_count = {"n": 0}
    real_open = transport.open
    real_close = transport.close

    def counting_open() -> None:
        open_count["n"] += 1
        real_open()

    def counting_close() -> None:
        close_count["n"] += 1
        real_close()

    transport.open = counting_open  # type: ignore[method-assign]
    transport.close = counting_close  # type: ignore[method-assign]
    session = Session(transport)
    items = list(session.iter_messages())
    assert len(items) >= 1
    assert open_count["n"] == 1
    assert close_count["n"] == 1


def test_session_iter_messages_manage_lifecycle_false() -> None:
    raw = bytes.fromhex(_INFO)
    transport = HexFileTransport.__new__(HexFileTransport)
    transport._path = Path("mem")  # noqa: SLF001
    transport._data = raw  # noqa: SLF001
    transport._pos = 0  # noqa: SLF001
    transport._opened = True  # noqa: SLF001
    session = Session(transport)
    session._live_open = True  # noqa: SLF001
    items = list(session.iter_messages(manage_lifecycle=False))
    assert any(getattr(i, "type_name", None) == "TempStatus" or i.to_dict().get("type_name") == "TempStatus" for i in items)


def test_parse_command_filter() -> None:
    assert parse_command_filter(None) is None
    assert parse_command_filter([]) is None
    assert parse_command_filter(["0x02", "0x08"]) == frozenset({0x02, 0x08})
    assert parse_command_filter(["2,8"]) == frozenset({2, 8})
    assert parse_command_filter(["0x02,0x08"]) == frozenset({0x02, 0x08})


def test_build_watch_transport_variants() -> None:
    tcp = build_watch_transport(tcp="1.2.3.4:1234", serial=None, file=None)
    assert isinstance(tcp, TcpTransport)
    assert tcp.host == "1.2.3.4"
    assert tcp.port == 1234
    tcp_default = build_watch_transport(
        tcp=f"{DEFAULT_EW11_HOST}:{DEFAULT_EW11_PORT}", serial=None, file=None
    )
    assert isinstance(tcp_default, TcpTransport)
    ser = build_watch_transport(tcp=None, serial="/dev/ttyUSB0", file=None)
    assert isinstance(ser, SerialTransport)
    hx = build_watch_transport(tcp=None, serial=None, file=Path("x.hex"))
    assert isinstance(hx, HexFileTransport)


def test_watch_cli_file_with_cmd_filter(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    hex_path = tmp_path / "one.hex"
    hex_path.write_text(_INFO, encoding="utf-8")
    assert main(["watch", "--file", str(hex_path), "--cmd", "0x08", "--max-messages", "1"]) == 0
    out = capsys.readouterr().out.strip()
    obj = json.loads(out)
    assert obj["type_name"] == "TempStatus"
    assert obj["command"] == 0x08


def test_watch_cli_filters_out_other_cmds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    hex_path = tmp_path / "one.hex"
    hex_path.write_text(_INFO, encoding="utf-8")
    assert main(["watch", "--file", str(hex_path), "--cmd", "0x02"]) == 0
    assert capsys.readouterr().out.strip() == ""


def test_watch_cli_missing_file() -> None:
    assert main(["watch", "--file", "/no/such/file.hex"]) == 1


def test_watch_help_lists_defaults() -> None:
    help_text = build_parser().format_help()
    assert "watch" in help_text
    assert "10.0.0.11" in help_text
    assert "8899" in help_text


def test_watch_tcp_flag_default_const() -> None:
    parser = build_parser()
    args = parser.parse_args(["watch", "--tcp"])
    assert args.tcp == f"{DEFAULT_EW11_HOST}:{DEFAULT_EW11_PORT}"


def test_smoke_mentions_watch() -> None:
    assert "watch" in build_parser().format_help()


def test_tcp_peer_close_no_reconnect() -> None:
    sock = FakeSocket([b""])
    t = TcpTransport(
        reconnect=False,
        sock_factory=lambda: sock,  # type: ignore[arg-type, return-value]
    )
    t.open()
    assert t.read(1) == b""
    t.close()


def test_tcp_read_returns_when_closed_mid_idle() -> None:
    sock = FakeSocket([])

    def closing_recv(n: int) -> bytes:
        _ = n
        t.close()
        return b""

    sock.recv = closing_recv  # type: ignore[method-assign]
    t = TcpTransport(
        reconnect=True,
        sock_factory=lambda: sock,  # type: ignore[arg-type, return-value]
        sleep=lambda _d: None,
        rng=lambda: 0.0,
    )
    t.open()
    assert t.read(1) == b""


def test_tcp_oserror_when_closed_mid_read() -> None:
    sock = FakeSocket([])

    def boom(n: int) -> bytes:
        _ = n
        t._want_open = False  # noqa: SLF001
        raise OSError("gone")

    sock.recv = boom  # type: ignore[method-assign]
    t = TcpTransport(sock_factory=lambda: sock)  # type: ignore[arg-type, return-value]
    t.open()
    assert t.read(1) == b""


def test_tcp_write_no_reconnect_raises() -> None:
    sock = FakeSocket([])
    sock.closed = True
    t = TcpTransport(
        reconnect=False,
        sock_factory=lambda: sock,  # type: ignore[arg-type, return-value]
    )
    t.open()
    with pytest.raises(OSError):
        t.write(b"x")
    t.close()


def test_tcp_closed_during_connect_retries() -> None:
    sock = FakeSocket(fail_connect_times=99)

    def sleeper(_d: float) -> None:
        t._want_open = False  # noqa: SLF001

    t = TcpTransport(
        sock_factory=lambda: sock,  # type: ignore[arg-type, return-value]
        sleep=sleeper,
        rng=lambda: 0.0,
    )
    with pytest.raises(RuntimeError, match="closed during connect"):
        t.open()


def test_tcp_disconnect_shutdown_oserror() -> None:
    sock = FakeSocket([b"a"])

    def bad_shutdown(how: int) -> None:
        _ = how
        raise OSError("already down")

    sock.shutdown = bad_shutdown  # type: ignore[method-assign]
    t = TcpTransport(sock_factory=lambda: sock)  # type: ignore[arg-type, return-value]
    t.open()
    assert t.read(1) == b"a"
    t.close()


def test_tcp_raw_paths_without_socket() -> None:
    t = TcpTransport(sock_factory=lambda: FakeSocket())  # type: ignore[arg-type, return-value]
    with pytest.raises(OSError, match="not connected"):
        t._raw_read(1)  # noqa: SLF001
    with pytest.raises(OSError, match="not connected"):
        t._raw_write(b"x")  # noqa: SLF001


def test_tcp_connect_failure_closes_socket() -> None:
    closed = {"n": 0}

    class BoomSock(FakeSocket):
        def connect(self, address: tuple[str, int]) -> None:
            raise OSError("no route")

        def close(self) -> None:
            closed["n"] += 1
            super().close()

    t = TcpTransport(
        reconnect=False,
        sock_factory=lambda: BoomSock(),  # type: ignore[arg-type, return-value]
    )
    with pytest.raises(OSError, match="no route"):
        t.open()
    assert closed["n"] == 1


def test_serial_lazy_import(monkeypatch: pytest.MonkeyPatch) -> None:
    created: list[Any] = []

    class Mod:
        @staticmethod
        def Serial(**kwargs: Any) -> FakeSerial:
            ser = FakeSerial([b"z"], **kwargs)
            created.append(ser)
            return ser

    monkeypatch.setitem(sys.modules, "serial", Mod)
    t = SerialTransport("/dev/lazy", sleep=lambda _d: None)
    t.open()
    assert t.read(1) == b"z"
    t.close()
    assert created[0].closed


def test_serial_non_bytes_and_none_guard() -> None:
    class BadSer:
        def read(self, n: int) -> str:
            _ = n
            return "nope"

        def write(self, data: bytes) -> None:
            _ = data

        def close(self) -> None:
            return None

    t = SerialTransport(
        "/dev/bad",
        serial_factory=lambda **_k: BadSer(),
        reconnect=False,
    )
    t.open()
    with pytest.raises(OSError, match="non-bytes"):
        t.read(1)
    t._ser = None  # noqa: SLF001
    with pytest.raises(OSError, match="not connected"):
        t._raw_read(1)  # noqa: SLF001
    with pytest.raises(OSError, match="not connected"):
        t._raw_write(b"x")  # noqa: SLF001
    t.close()


def test_serial_disconnect_without_close_attr() -> None:
    class NoClose:
        pass

    t = SerialTransport("/dev/x", serial_factory=lambda **_k: NoClose())
    t.open()
    t.close()


def test_base_reconnect_not_implemented() -> None:
    base = ReconnectingTransport.__new__(ReconnectingTransport)
    with pytest.raises(NotImplementedError):
        base._connect_once()  # noqa: SLF001
    with pytest.raises(NotImplementedError):
        base._disconnect_once()  # noqa: SLF001
    with pytest.raises(NotImplementedError):
        base._raw_read(1)  # noqa: SLF001
    with pytest.raises(NotImplementedError):
        base._raw_write(b"")  # noqa: SLF001


def test_disconnect_quiet_swallows_oserror() -> None:
    t = TcpTransport(sock_factory=lambda: FakeSocket())  # type: ignore[arg-type, return-value]
    t.open()

    def boom() -> None:
        raise OSError("x")

    t._disconnect_once = boom  # type: ignore[method-assign]
    t._disconnect_quiet()  # noqa: SLF001
    assert not t.connected


def test_parse_filter_empty_token_and_quarantine() -> None:
    assert parse_command_filter(["0x02,", " ,8"]) == frozenset({2, 8})
    q = QuarantinedFrame(
        frame=Frame(kind=FrameKind.A5, raw=b"\x00", checksum_ok=False)
    )
    assert _passes_filter(q, frozenset({2})) is False
    assert _passes_filter(q, None) is True


def test_parse_tcp_host_only_and_invalid() -> None:
    assert _parse_tcp_endpoint("10.0.0.11") == ("10.0.0.11", DEFAULT_EW11_PORT)
    with pytest.raises(ValueError, match="invalid"):
        _parse_tcp_endpoint(":8899")
    with pytest.raises(ValueError, match="requires"):
        build_watch_transport(tcp=None, serial=None, file=None)


def test_cmd_watch_value_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert (
        cmd_watch(
            tcp=None,
            serial=None,
            file=None,
            cmds=None,
            pretty=False,
            include_quarantine=False,
            max_messages=None,
        )
        == 1
    )
    assert "error:" in capsys.readouterr().err


def test_watch_pretty_and_keyboard_interrupt(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    hex_path = tmp_path / "one.hex"
    hex_path.write_text(_INFO, encoding="utf-8")
    assert (
        main(
            [
                "watch",
                "--file",
                str(hex_path),
                "--pretty",
                "--include-quarantine",
                "--max-messages",
                "1",
            ]
        )
        == 0
    )
    assert "TempStatus" in capsys.readouterr().out

    def boom_iter(*_a: Any, **_k: Any):
        raise KeyboardInterrupt

    monkeypatch.setattr(Session, "iter_messages", boom_iter)
    assert main(["watch", "--file", str(hex_path)]) == 0
    assert "interrupted" in capsys.readouterr().err
