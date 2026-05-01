from __future__ import annotations

from typing import Protocol

from xagent.bus import InboundMessage, OutboundEvent


class Channel(Protocol):
    name: str

    async def receive(self) -> InboundMessage:
        ...

    async def send(self, event: OutboundEvent) -> None:
        ...
