from __future__ import annotations

from dataclasses import dataclass

from xagent.bus import InboundMessage, MessageBus, OutboundEvent
from xagent.channels.base import Channel


@dataclass
class ChannelManager:
    """Route messages between external channels and the in-process bus."""

    bus: MessageBus
    channels: dict[str, Channel]

    async def receive_once(self, channel_name: str) -> InboundMessage:
        channel = self.channels[channel_name]
        message = await channel.receive()
        message.channel = channel.name
        await self.bus.publish_inbound(message)
        return message

    async def dispatch_outbound_once(self) -> OutboundEvent:
        event = await self.bus.consume_outbound()
        channel = self.channels.get(event.channel)
        if channel is None:
            raise RuntimeError(f"No channel is configured for {event.channel!r}.")
        await channel.send(event)
        return event

    async def serve_forever(self) -> None:
        if not self.channels:
            raise RuntimeError("No channels are configured yet.")
