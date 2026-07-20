"""Pattern: Facade — thin session over transport, framer, and registry."""

from __future__ import annotations

from collections.abc import Iterator
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
        self._live_open = False

    @property
    def transport(self) -> Transport:
        return self._transport

    @property
    def framer(self) -> Framer:
        return self._framer

    @property
    def registry(self) -> MessageRegistry:
        return self._registry

    def open(self) -> None:
        """Open the transport once for a persistent watch (A3)."""
        if not self._live_open:
            self._transport.open()
            self._framer.reset()
            self._live_open = True

    def close(self) -> None:
        """Close the transport after a persistent watch (idempotent)."""
        if self._live_open:
            self._transport.close()
            self._live_open = False

    def read_frames(self, chunk_size: int = 256) -> list[Frame]:
        """Read from the transport until EOF and return all framed messages.

        Opens/closes once per call (fixture-friendly for ``HexFileTransport``).
        Live watch must use :meth:`iter_frames` / :meth:`iter_messages` so the
        connection stays open across frames (no open/close per frame).
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
            frames.extend(self._framer.feed(b""))
            return frames
        finally:
            self._transport.close()

    def iter_frames(
        self, chunk_size: int = 256, *, manage_lifecycle: bool = True
    ) -> Iterator[Frame]:
        """Yield frames from a persistent connection (no open/close per frame).

        When ``manage_lifecycle`` is True (default), opens once before the first
        read and closes when the iterator finishes or is closed. Pass
        ``manage_lifecycle=False`` if the caller already called :meth:`open`.
        """
        if manage_lifecycle:
            self.open()
        try:
            while True:
                chunk = self._transport.read(chunk_size)
                if not chunk:
                    # Fixture EOF or transport closed — flush and stop.
                    yield from self._framer.feed(b"")
                    break
                yield from self._framer.feed(chunk)
        finally:
            if manage_lifecycle:
                self.close()

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

    def iter_messages(
        self, chunk_size: int = 256, *, manage_lifecycle: bool = True
    ) -> Iterator[DecodedItem]:
        """Yield decoded messages over a persistent transport connection."""
        for frame in self.iter_frames(
            chunk_size, manage_lifecycle=manage_lifecycle
        ):
            yield self.decode_frame(frame)
