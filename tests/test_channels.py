from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import pytest

from xagent.bus import InboundMessage, MessageBus, OutboundEvent, StreamKind, StreamState
from xagent.channels import BaseChannel, ChannelManager


class FakeChannel(BaseChannel):
    def __init__(self, *, name: str, bus: MessageBus) -> None:
        super().__init__(name=name, bus=bus)
        self.incoming: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.sent: list[OutboundEvent] = []
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def run(self) -> None:
        while True:
            await self.handle_message(await self.incoming.get())

    async def handle_message(self, message: Any) -> InboundMessage | None:
        if not isinstance(message, InboundMessage) or not message.content:
            return None
        inbound = InboundMessage(
            content=message.content,
            channel=self.name,
            chat_id=message.chat_id,
            sender_id=message.sender_id,
            session_id=message.session_id,
            external_message_id=message.external_message_id,
            metadata=message.metadata,
        )
        await self.bus.publish_inbound(inbound)
        return inbound

    async def send(self, event: OutboundEvent) -> None:
        self.sent.append(event)

    async def stop(self) -> None:
        self.stopped = True


class FailingChannel(FakeChannel):
    async def run(self) -> None:
        raise RuntimeError("channel failed")


@pytest.mark.asyncio
async def test_base_channel_handle_message_publishes_inbound_to_bus() -> None:
    bus = MessageBus()
    channel = FakeChannel(name="fake", bus=bus)

    received = await channel.handle_message(
        InboundMessage(content="hi", chat_id="room", sender_id="alice")
    )
    inbound = await bus.consume_inbound()

    assert received == inbound
    assert inbound.content == "hi"
    assert inbound.channel == "fake"
    assert inbound.chat_id == "room"
    assert inbound.sender_id == "alice"


@pytest.mark.asyncio
async def test_base_channel_run_consumes_messages_from_queue() -> None:
    bus = MessageBus()
    channel = FakeChannel(name="fake", bus=bus)
    task = asyncio.create_task(channel.run())
    await channel.incoming.put(InboundMessage(content="hi", chat_id="room", sender_id="alice"))

    inbound = await bus.consume_inbound()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert inbound.content == "hi"
    assert inbound.channel == "fake"
    assert inbound.chat_id == "room"
    assert inbound.sender_id == "alice"


def test_base_channel_supports_streaming_defaults_to_false() -> None:
    channel = FakeChannel(name="fake", bus=MessageBus())

    assert channel.supports_streaming is False


@pytest.mark.asyncio
async def test_channel_manager_start_and_stop_channels() -> None:
    bus = MessageBus()
    channel = FakeChannel(name="fake", bus=bus)
    manager = ChannelManager(bus=bus, channels={"fake": channel})

    await manager.start()
    await manager.stop()

    assert channel.started is True
    assert channel.stopped is True


@pytest.mark.asyncio
async def test_channel_manager_routes_outbound_to_matching_channel() -> None:
    bus = MessageBus()
    fake = FakeChannel(name="fake", bus=bus)
    other = FakeChannel(name="other", bus=bus)
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
    routed = await manager.dispatch_outbound()

    assert routed is event
    assert fake.sent == [event]
    assert other.sent == []


@pytest.mark.asyncio
async def test_channel_manager_errors_for_unknown_outbound_channel() -> None:
    bus = MessageBus()
    manager = ChannelManager(bus=bus, channels={"fake": FakeChannel(name="fake", bus=bus)})
    await bus.publish_outbound(OutboundEvent(channel="missing"))

    with pytest.raises(RuntimeError, match="No channel"):
        await manager.dispatch_outbound()


@pytest.mark.asyncio
async def test_channel_manager_run_stops_channels_on_cancellation() -> None:
    bus = MessageBus()
    channel = FakeChannel(name="fake", bus=bus)
    manager = ChannelManager(bus=bus, channels={"fake": channel})
    task = asyncio.create_task(manager.run())
    while not channel.started:
        await asyncio.sleep(0)

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert channel.stopped is True


@pytest.mark.asyncio
async def test_channel_manager_run_stops_channels_on_channel_error() -> None:
    bus = MessageBus()
    channel = FailingChannel(name="fake", bus=bus)
    manager = ChannelManager(bus=bus, channels={"fake": channel})

    with pytest.raises(RuntimeError, match="channel failed"):
        await manager.run()

    assert channel.started is True
    assert channel.stopped is True
