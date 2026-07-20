"""Smoke tests for A0 bootstrap: import, CLI help, stub modules."""

from __future__ import annotations

from pathlib import Path

import pytest

import pentairsnoop
from pentairsnoop import framer, messages, registry, session, transport
from pentairsnoop.cli import build_parser, main
from pentairsnoop.framer import Framer
from pentairsnoop.messages import Message
from pentairsnoop.registry import MessageRegistry
from pentairsnoop.session import Session


def test_package_version() -> None:
    assert pentairsnoop.__version__ == "0.1.0"


def test_cli_help_exits_zero() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0


def test_cli_version_exits_zero() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0


def test_cli_main_no_args() -> None:
    assert main([]) == 0


def test_build_parser_prog() -> None:
    assert build_parser().prog == "pentairsnoop"


def test_framer_feed_incomplete_returns_empty() -> None:
    assert Framer().feed(b"\xff\x00") == []


def test_message_defaults() -> None:
    msg = Message()
    assert msg.raw == b""
    assert msg.command == 0


def test_registry_unknown_and_registered() -> None:
    reg = MessageRegistry()
    unknown = reg.parse(0x99, b"\x01\x02")
    assert unknown.command == 0x99
    assert unknown.raw == b"\x01\x02"

    def _parse(raw: bytes) -> Message:
        return Message(raw=raw, command=0x02, length=len(raw))

    reg.register(0x02, _parse)
    typed = reg.parse(0x02, b"abcd")
    assert typed.command == 0x02
    assert typed.length == 4


class _DummyTransport:
    """Minimal Strategy stand-in for Session wiring tests."""

    def open(self) -> None:
        return None

    def close(self) -> None:
        return None

    def read(self, n: int) -> bytes:
        return b""[:n]

    def write(self, data: bytes) -> None:
        _ = data


def test_session_wires_defaults() -> None:
    sess = Session(_DummyTransport())
    assert isinstance(sess.framer, Framer)
    assert isinstance(sess.registry, MessageRegistry)
    assert sess.transport is not None


def test_session_accepts_injected_deps() -> None:
    fr = Framer()
    reg = MessageRegistry()
    t = _DummyTransport()
    sess = Session(t, framer=fr, registry=reg)
    assert sess.framer is fr
    assert sess.registry is reg
    assert sess.transport is t


def test_module_pattern_docstrings() -> None:
    for mod in (transport, framer, messages, registry, session):
        assert mod.__doc__ is not None
        assert "Pattern:" in mod.__doc__


def test_samples_dir_fixture(samples_dir: Path) -> None:
    """Document fixture path; A1 will decode files under parent samples/."""
    assert samples_dir.name == "samples"
    # Parent tree may or may not be present in isolated checkouts.
    if samples_dir.is_dir():
        assert samples_dir.exists()
