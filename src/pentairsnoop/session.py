"""Pattern: Facade — thin session over transport, framer, and registry."""

from __future__ import annotations

from pentairsnoop.framer import Frame, Framer
from pentairsnoop.registry import MessageRegistry
from pentairsnoop.transport import Transport


class Session:
    """Pattern: Facade — high-level read path over lab transports (A1+)."""

    def __init__(
        self,
        transport: Transport,
        framer: Framer | None = None,
        registry: MessageRegistry | None = None,
    ) -> None:
        self._transport = transport
        self._framer = framer if framer is not None else Framer()
        self._registry = registry if registry is not None else MessageRegistry()

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
