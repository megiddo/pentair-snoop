"""Pattern: Facade — thin session over transport, framer, and registry."""

from __future__ import annotations

from dataclasses import dataclass

from pentairsnoop.framer import Frame, FrameKind, Framer
from pentairsnoop.messages import Message, Unknown
from pentairsnoop.registry import MessageRegistry
from pentairsnoop.transport import Transport


@dataclass(frozen=True)
class QuarantinedFrame:
    """Pattern: Facade — bad-checksum frame held aside (not applied as status)."""

    frame: Frame
    reason: str = "checksum_mismatch"

    def to_dict(self) -> dict:
        return {
            "type_name": "Quarantined",
            "reason": self.reason,
            "kind": self.frame.kind.value,
            "checksum_ok": self.frame.checksum_ok,
            "raw": self.frame.raw.hex(),
        }


DecodedItem = Message | QuarantinedFrame


class Session:
    """Pattern: Facade — high-level read/decode path over lab transports."""

    def __init__(
        self,
        transport: Transport,
        framer: Framer | None = None,
        registry: MessageRegistry | None = None,
    ) -> None:
        self._transport = transport
        self._framer = framer if framer is not None else Framer()
        self._registry = (
            registry if registry is not None else MessageRegistry.default()
        )

    @property
    def transport(self) -> Transport:
        return self._transport

    @property
    def framer(self) -> Framer:
        return self._framer

    @property
    def registry(self) -> MessageRegistry:
        return self._registry

    def read_frames(self, chunk_size: int = 256) -> list[Frame]:
        """Read from the transport until EOF and return all framed messages.

        Opens/closes the transport for this call (fixture-friendly). Live
        persistent connections arrive in A3.
        """
        self._transport.open()
        try:
            self._framer.reset()
            frames: list[Frame] = []
            while True:
                chunk = self._transport.read(chunk_size)
                if not chunk:
                    break
                frames.extend(self._framer.feed(chunk))
            # Flush any frame completed at EOF without extra bytes.
            frames.extend(self._framer.feed(b""))
            return frames
        finally:
            self._transport.close()

    def decode_frame(self, frame: Frame) -> DecodedItem:
        """Decode one frame; quarantine bad checksums; IntelliChlor → Unknown."""
        if not frame.checksum_ok:
            return QuarantinedFrame(frame=frame)
        if frame.kind is FrameKind.INTELLICHLOR:
            return Unknown.parse(frame.raw)
        return self._registry.decode_a5(frame.raw)

    def read_messages(self, chunk_size: int = 256) -> list[DecodedItem]:
        """Frame then decode; bad checksums become ``QuarantinedFrame``."""
        return [self.decode_frame(f) for f in self.read_frames(chunk_size)]
