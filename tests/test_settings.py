"""Unit tests for ``.env`` transport settings (serial/tty default)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pentairsnoop.cli import build_parser, build_watch_transport
from pentairsnoop.settings import (
    DEFAULT_SERIAL_DEVICE,
    DEFAULT_TRANSPORT,
    TransportSettings,
    find_dotenv,
    load_env_file,
    load_transport_settings,
    parse_dotenv_text,
    resolve_cli_transport,
)
from pentairsnoop.transport import SerialTransport, TcpTransport


def test_parse_dotenv_text_basics() -> None:
    parsed = parse_dotenv_text(
        "# comment\n"
        "PENTAIR_TRANSPORT=serial\n"
        "export PENTAIR_SERIAL_DEVICE='/dev/ttyUSB0'\n"
        'PENTAIR_TCP_HOST="10.0.0.11"\n'
        "\n"
    )
    assert parsed["PENTAIR_TRANSPORT"] == "serial"
    assert parsed["PENTAIR_SERIAL_DEVICE"] == "/dev/ttyUSB0"
    assert parsed["PENTAIR_TCP_HOST"] == "10.0.0.11"


def test_default_transport_is_serial(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "PENTAIR_TRANSPORT",
        "PENTAIR_SERIAL_DEVICE",
        "PENTAIR_SERIAL_BAUD",
        "PENTAIR_TCP_HOST",
        "PENTAIR_TCP_PORT",
    ):
        monkeypatch.delenv(key, raising=False)
    settings = load_transport_settings(env_file=Path("/no/such/.env"))
    assert settings.transport == DEFAULT_TRANSPORT == "serial"
    assert settings.serial_device == DEFAULT_SERIAL_DEVICE


def test_load_env_file_and_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            [
                "PENTAIR_TRANSPORT=tcp",
                "PENTAIR_SERIAL_DEVICE=/dev/ttyPentair",
                "PENTAIR_SERIAL_BAUD=19200",
                "PENTAIR_TCP_HOST=192.168.1.9",
                "PENTAIR_TCP_PORT=9000",
            ]
        ),
        encoding="utf-8",
    )
    for key in (
        "PENTAIR_TRANSPORT",
        "PENTAIR_SERIAL_DEVICE",
        "PENTAIR_SERIAL_BAUD",
        "PENTAIR_TCP_HOST",
        "PENTAIR_TCP_PORT",
    ):
        monkeypatch.delenv(key, raising=False)
    assert load_env_file(env) == env
    settings = load_transport_settings(env_file=env)
    assert settings.transport == "tcp"
    assert settings.serial_device == "/dev/ttyPentair"
    assert settings.serial_baud == 19200
    assert settings.tcp_endpoint == "192.168.1.9:9000"


def test_find_dotenv_walks_up(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("PENTAIR_TRANSPORT=serial\n", encoding="utf-8")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    assert find_dotenv(nested) == env


def test_resolve_cli_overrides_env() -> None:
    settings = TransportSettings(
        transport="serial",
        serial_device="/dev/from-env",
        serial_baud=9600,
        tcp_host="10.0.0.11",
        tcp_port=8899,
    )
    tcp, serial, file, baud = resolve_cli_transport(
        tcp=None, serial=None, file=None, settings=settings
    )
    assert tcp is None and serial == "/dev/from-env" and file is None

    tcp, serial, file, baud = resolve_cli_transport(
        tcp="", serial=None, file=None, settings=settings
    )
    assert tcp == "10.0.0.11:8899" and serial is None

    tcp, serial, file, baud = resolve_cli_transport(
        tcp="1.2.3.4:1234", serial=None, file=None, settings=settings
    )
    assert tcp == "1.2.3.4:1234"

    tcp, serial, file, baud = resolve_cli_transport(
        tcp=None, serial="", file=None, settings=settings
    )
    assert serial == "/dev/from-env"

    tcp, serial, file, baud = resolve_cli_transport(
        tcp=None, serial="/dev/override", file=None, settings=settings
    )
    assert serial == "/dev/override"

    path = Path("x.hex")
    tcp, serial, file, baud = resolve_cli_transport(
        tcp="ignored", serial="ignored", file=path, settings=settings
    )
    assert file == path and tcp is None and serial is None
    assert baud == 9600


def test_build_watch_uses_baud() -> None:
    t = build_watch_transport(
        tcp=None, serial="/dev/ttyUSB0", file=None, serial_baud=19200
    )
    assert isinstance(t, SerialTransport)
    assert t.baudrate == 19200


def test_parser_tcp_const_empty_means_env() -> None:
    args = build_parser().parse_args(["watch", "--tcp"])
    assert args.tcp == ""
    args = build_parser().parse_args(["watch", "--serial"])
    assert args.serial == ""
    args = build_parser().parse_args(["watch"])
    assert args.tcp is None and args.serial is None and args.file is None


def test_watch_help_mentions_env() -> None:
    help_text = build_parser().format_help()
    assert ".env" in help_text
    assert "serial" in help_text.lower()


def test_invalid_transport_falls_back_to_serial(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PENTAIR_TRANSPORT", "nope")
    monkeypatch.delenv("PENTAIR_SERIAL_DEVICE", raising=False)
    settings = load_transport_settings(env_file=Path("/no/such/.env"))
    assert settings.transport == "serial"


def test_resolve_tcp_mode_from_env() -> None:
    settings = TransportSettings(
        transport="tcp",
        serial_device="/dev/ttyUSB0",
        serial_baud=9600,
        tcp_host="9.9.9.9",
        tcp_port=1111,
    )
    tcp, serial, file, _baud = resolve_cli_transport(
        tcp=None, serial=None, file=None, settings=settings
    )
    assert tcp == "9.9.9.9:1111"
    assert serial is None
    t = build_watch_transport(tcp=tcp, serial=serial, file=file)
    assert isinstance(t, TcpTransport)
    assert t.host == "9.9.9.9"
    assert t.port == 1111
