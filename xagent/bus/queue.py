from __future__ import annotations

import asyncio

from xagent.bus.messages import InboundMessage, OutboundEvent


class MessageBus:
    def __init__(self) -> None:
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[OutboundEvent] = asyncio.Queue()

    async def publish_inbound(self, message: InboundMessage) -> None:
        await self.inbound.put(message)

    async def consume_inbound(self) -> InboundMessage:
        return await self.inbound.get()

    async def publish_outbound(self, event: OutboundEvent) -> None:
        await self.outbound.put(event)

    async def consume_outbound(self) -> OutboundEvent:
        return await self.outbound.get()
