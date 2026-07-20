"""Pattern: Factory — map command bytes to typed message parsers."""

from __future__ import annotations

from collections.abc import Callable

from pentairsnoop.messages import Message, SystemStatus, TempStatus, Unknown

MessageParser = Callable[[bytes], Message]

# Commands enum values (lib/src/Enum/Commands.php).
CMD_UNKNOWN = 0x00
CMD_CIRCUIT_CHANGE_ACK = 0x01
CMD_SYSTEM_STATUS = 0x02
CMD_CLOCK_BROADCAST = 0x05
CMD_PUMP_STATUS_REQUEST = 0x07
CMD_INFO = 0x08
CMD_REMOTE_LAYOUT_ACK = 0x21
CMD_CIRCUIT_CHANGE_REQUEST = 0x86
CMD_TEMP_CHANGE_REQUEST = 0x88
CMD_REMOTE_LAYOUT_REQUEST = 0xE1


class MessageRegistry:
    """Pattern: Factory — explicit cmd-byte → parser registry (no scandir)."""

    def __init__(self) -> None:
        self._parsers: dict[int, MessageParser] = {}

    def register(self, command_byte: int, parser: MessageParser) -> None:
        """Bind a command byte to a parser callable."""
        self._parsers[command_byte] = parser

    def parse(self, command_byte: int, raw: bytes) -> Message:
        """Build a typed message; default is ``Unknown`` (not a bare Message)."""
        parser = self._parsers.get(command_byte)
        if parser is None:
            return Unknown.parse(raw, command=command_byte)
        return parser(raw)

    def decode_a5(self, raw: bytes) -> Message:
        """Decode a standard A5 frame: read command byte from A5, then dispatch."""
        from pentairsnoop.messages import find_a5_index

        a5 = find_a5_index(raw)
        if a5 + 4 >= len(raw):
            return Unknown.parse(raw)
        command = raw[a5 + 4]
        return self.parse(command, raw)

    @classmethod
    def default(cls) -> MessageRegistry:
        """Explicit registry: SystemStatus + TempStatus; other enum bytes → Unknown."""
        reg = cls()
        reg.register(CMD_SYSTEM_STATUS, SystemStatus.parse)
        reg.register(CMD_INFO, TempStatus.parse)
        # Acknowledge remaining Commands enum bytes as Unknown (deterministic).
        for byte in (
            CMD_UNKNOWN,
            CMD_CIRCUIT_CHANGE_ACK,
            CMD_CLOCK_BROADCAST,
            CMD_PUMP_STATUS_REQUEST,
            CMD_REMOTE_LAYOUT_ACK,
            CMD_CIRCUIT_CHANGE_REQUEST,
            CMD_TEMP_CHANGE_REQUEST,
            CMD_REMOTE_LAYOUT_REQUEST,
        ):
            reg.register(byte, Unknown.parse)
        return reg
