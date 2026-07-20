"""Pattern: Facade — thin session over transport, framer, and registry."""

from __future__ import annotations

from pentairsnoop.framer import Framer
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
