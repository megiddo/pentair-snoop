"""Pattern: Factory — map command bytes to typed message parsers."""

from __future__ import annotations

from collections.abc import Callable

from pentairsnoop.messages import Message

MessageParser = Callable[[bytes], Message]


class MessageRegistry:
    """Pattern: Factory — explicit cmd-byte → parser registry (A2)."""

    def __init__(self) -> None:
        self._parsers: dict[int, MessageParser] = {}

    def register(self, command_byte: int, parser: MessageParser) -> None:
        """Bind a command byte to a parser callable."""
        self._parsers[command_byte] = parser

    def parse(self, command_byte: int, raw: bytes) -> Message:
        """Build a typed message, or a bare ``Message`` for unknown bytes."""
        parser = self._parsers.get(command_byte)
        if parser is None:
            return Message(raw=raw, command=command_byte)
        return parser(raw)
