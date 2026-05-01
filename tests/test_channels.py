from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from xagent.bus import InboundMessage, MessageBus, OutboundEvent, StreamKind, StreamState
from xagent.channels import ChannelManager


@dataclass
class FakeChannel:
    name: str
    incoming: asyncio.Queue[InboundMessage] = field(default_factory=asyncio.Queue)
    sent: list[OutboundEvent] = field(default_factory=list)

    async def receive(self) -> InboundMessage:
        return await self.incoming.get()

    async def send(self, event: OutboundEvent) -> None:
        self.sent.append(event)


@pytest.mark.asyncio
async def test_channel_manager_publishes_received_messages_to_bus() -> None:
    bus = MessageBus()
    channel = FakeChannel(name="fake")
    manager = ChannelManager(bus=bus, channels={"fake": channel})
    await channel.incoming.put(InboundMessage(content="hi", chat_id="room", sender_id="alice"))

    received = await manager.receive_once("fake")
    inbound = await bus.consume_inbound()

    assert received is inbound
    assert inbound.content == "hi"
    assert inbound.channel == "fake"
    assert inbound.chat_id == "room"
    assert inbound.sender_id == "alice"


@pytest.mark.asyncio
async def test_channel_manager_routes_outbound_to_matching_channel() -> None:
    bus = MessageBus()
    fake = FakeChannel(name="fake")
    other = FakeChannel(name="other")
    manager = ChannelManager(bus=bus, channels={"fake": fake, "other": other})
    event = OutboundEvent(
        content="hello",
        channel="fake",
        chat_id="room",
        reply_to="alice",
        session_id="fake:room",
        stream=StreamState(kind=StreamKind.END, stream_id="s1"),
    )

    await bus.publish_outbound(event)
    routed = await manager.dispatch_outbound_once()

    assert routed is event
    assert fake.sent == [event]
    assert other.sent == []


@pytest.mark.asyncio
async def test_channel_manager_errors_for_unknown_outbound_channel() -> None:
    bus = MessageBus()
    manager = ChannelManager(bus=bus, channels={"fake": FakeChannel(name="fake")})
    await bus.publish_outbound(OutboundEvent(channel="missing"))

    with pytest.raises(RuntimeError, match="No channel"):
        await manager.dispatch_outbound_once()
