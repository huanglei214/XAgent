from __future__ import annotations

import asyncio
import json
import threading
from typing import Any

import pytest

from xagent.bus import MessageBus, OutboundEvent, StreamKind, StreamState
from xagent.channels import LarkChannel, build_channels
from xagent.channels.lark import LarkSdkAdapter
from xagent.config import LarkChannelConfig, default_config


class FakeClient:
    pass


class FakeWsClient:
    def __init__(self) -> None:
        self.started = False
        self.disconnected = False
        self._auto_reconnect = True

    def start(self) -> None:
        self.started = True

    async def _disconnect(self) -> None:
        self.disconnected = True


class FakeSdk:
    def __init__(self) -> None:
        self.bot_info_calls = 0
        self.sent: list[tuple[str, str]] = []
        self.event_callback: Any | None = None
        self.ws_client = FakeWsClient()
        self.client = FakeClient()
        self.client_args: dict[str, Any] = {}

    def domain_for(self, domain: str) -> str:
        return f"domain:{domain}"

    def log_level_for(self, log_level: str) -> str:
        return log_level.upper()

    def build_client(self, **kwargs: Any) -> FakeClient:
        self.client_args = kwargs
        return self.client

    def get_bot_open_id(self, client: Any) -> str:
        assert client is self.client
        self.bot_info_calls += 1
        return "ou_bot"

    def build_event_handler(self, **kwargs: Any) -> object:
        self.event_callback = kwargs["callback"]
        return object()

    def build_ws_client(self, **kwargs: Any) -> FakeWsClient:
        self.ws_client._auto_reconnect = kwargs["auto_reconnect"]
        return self.ws_client

    def send_text(self, client: Any, *, chat_id: str, text: str) -> None:
        assert client is self.client
        self.sent.append((chat_id, text))


def make_event(
    text: str,
    *,
    chat_id: str = "oc_room",
    chat_type: str = "p2p",
    message_type: str = "text",
    sender_id: str = "ou_user",
    sender_type: str = "user",
    mentions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "event": {
            "sender": {
                "sender_id": {"open_id": sender_id},
                "sender_type": sender_type,
                "tenant_key": "tenant",
            },
            "message": {
                "message_id": "om_message",
                "chat_id": chat_id,
                "chat_type": chat_type,
                "message_type": message_type,
                "content": json.dumps({"text": text}, ensure_ascii=False),
                "mentions": mentions or [],
            },
        }
    }


def bot_mention() -> dict[str, Any]:
    return {"key": "@_user_1", "id": {"open_id": "ou_bot"}, "name": "XAgent"}


async def make_started_channel(
    bus: MessageBus,
    *,
    config: LarkChannelConfig | None = None,
    sdk: FakeSdk | None = None,
) -> tuple[LarkChannel, FakeSdk]:
    fake_sdk = sdk or FakeSdk()
    channel = LarkChannel(
        config=config
        or LarkChannelConfig(enabled=True, app_id="cli_test", app_secret="secret"),
        bus=bus,
        sdk=fake_sdk,
    )
    await channel.start()
    return channel, fake_sdk


@pytest.mark.asyncio
async def test_lark_channel_start_reads_config_and_fetches_bot_open_id_once() -> None:
    bus = MessageBus()
    sdk = FakeSdk()
    channel = LarkChannel(
        config=LarkChannelConfig(
            enabled=True,
            app_id="cli_config",
            app_secret="secret_config",
            verification_token="vt_config",
            encrypt_key="ek_config",
            auto_reconnect=False,
            log_level="debug",
        ),
        bus=bus,
        sdk=sdk,
    )

    await channel.start()
    await channel.start()

    assert channel.bot_open_id == "ou_bot"
    assert sdk.bot_info_calls == 1
    assert sdk.client_args == {
        "app_id": "cli_config",
        "app_secret": "secret_config",
        "domain": "domain:feishu",
        "log_level": "DEBUG",
    }
    assert sdk.ws_client._auto_reconnect is False


@pytest.mark.asyncio
async def test_lark_private_text_message_publishes_inbound() -> None:
    bus = MessageBus()
    channel, _sdk = await make_started_channel(bus)

    received = await channel.handle_message(make_event("hello"))
    inbound = await bus.consume_inbound()

    assert received == inbound
    assert inbound.content == "hello"
    assert inbound.channel == "lark"
    assert inbound.chat_id == "oc_room"
    assert inbound.sender_id == "ou_user"
    assert inbound.external_message_id == "om_message"
    assert inbound.metadata["chat_type"] == "p2p"


@pytest.mark.asyncio
async def test_lark_group_mention_publishes_inbound_and_strips_mention() -> None:
    bus = MessageBus()
    channel, _sdk = await make_started_channel(bus)

    received = await channel.handle_message(
        make_event("@_user_1 继续", chat_type="group", mentions=[bot_mention()])
    )
    inbound = await bus.consume_inbound()

    assert received == inbound
    assert inbound.content == "继续"
    assert inbound.chat_id == "oc_room"


@pytest.mark.asyncio
async def test_lark_group_without_mention_is_ignored() -> None:
    bus = MessageBus()
    channel, _sdk = await make_started_channel(bus)

    received = await channel.handle_message(make_event("hello", chat_type="group"))

    assert received is None
    assert bus.inbound.empty()


@pytest.mark.asyncio
async def test_lark_ignores_non_text_empty_and_bot_self_messages() -> None:
    bus = MessageBus()
    channel, _sdk = await make_started_channel(bus)

    assert await channel.handle_message(make_event("hello", message_type="image")) is None
    assert await channel.handle_message(make_event("   ")) is None
    assert await channel.handle_message(make_event("hello", sender_id="ou_bot")) is None
    assert await channel.handle_message(make_event("hello", sender_type="app")) is None
    assert bus.inbound.empty()


@pytest.mark.asyncio
async def test_lark_send_ignores_delta_and_sends_end() -> None:
    bus = MessageBus()
    channel, sdk = await make_started_channel(bus)

    await channel.send(
        OutboundEvent(
            content="hel",
            channel="lark",
            chat_id="oc_room",
            stream=StreamState(kind=StreamKind.DELTA, stream_id="s1"),
        )
    )
    await channel.send(
        OutboundEvent(
            content="hello",
            channel="lark",
            chat_id="oc_room",
            stream=StreamState(kind=StreamKind.END, stream_id="s1"),
        )
    )

    assert sdk.sent == [("oc_room", "hello")]


@pytest.mark.asyncio
async def test_lark_send_error_metadata_as_visible_text() -> None:
    bus = MessageBus()
    channel, sdk = await make_started_channel(bus)

    await channel.send(
        OutboundEvent(
            content="模型调用失败",
            channel="lark",
            chat_id="oc_room",
            stream=StreamState(kind=StreamKind.END, stream_id="s1"),
            metadata={"error": True},
        )
    )

    assert sdk.sent == [("oc_room", "模型调用失败")]


@pytest.mark.asyncio
async def test_lark_channel_stop_best_effort_disconnects() -> None:
    bus = MessageBus()
    channel, sdk = await make_started_channel(bus)

    await channel.stop()

    assert sdk.ws_client._auto_reconnect is False
    assert sdk.ws_client.disconnected is True


def test_build_channels_includes_enabled_lark_channel() -> None:
    config = default_config()
    assert build_channels(config, MessageBus()) == {}

    config.channels.lark.enabled = True
    config.channels.lark.app_id = "cli_test"
    config.channels.lark.app_secret = "secret"

    channels = build_channels(config, MessageBus())

    assert list(channels) == ["lark"]
    assert isinstance(channels["lark"], LarkChannel)


def test_lark_channel_describe_shows_startup_summary() -> None:
    channel = LarkChannel(
        config=LarkChannelConfig(enabled=True, app_id="cli_test", app_secret="secret"),
        bus=MessageBus(),
        sdk=FakeSdk(),
    )

    assert channel.describe() == "lark domain=feishu require_mention=True streaming=False"


def test_lark_sdk_adapter_stop_cleans_background_tasks() -> None:
    adapter = LarkSdkAdapter()
    ws_client = adapter.build_ws_client(
        app_id="cli_test",
        app_secret="secret",
        event_handler=object(),
        log_level=adapter.log_level_for("error"),
        domain=adapter.domain_for("feishu"),
        auto_reconnect=True,
    )
    connected = threading.Event()
    ping_started = threading.Event()
    finalized: list[str] = []

    async def fake_receive_loop() -> None:
        try:
            await asyncio.sleep(3600)
        finally:
            finalized.append("receive")

    async def fake_connect() -> None:
        ws_client._conn = object()
        asyncio.get_running_loop().create_task(fake_receive_loop())
        connected.set()

    async def fake_ping_loop() -> None:
        try:
            ping_started.set()
            await asyncio.sleep(3600)
        finally:
            finalized.append("ping")

    async def fake_disconnect() -> None:
        finalized.append("disconnect")
        ws_client._conn = None

    ws_client._connect = fake_connect
    ws_client._ping_loop = fake_ping_loop
    ws_client._disconnect = fake_disconnect

    thread = threading.Thread(target=adapter.run_ws_client, args=(ws_client,), daemon=True)
    thread.start()
    assert connected.wait(timeout=2)
    assert ping_started.wait(timeout=2)

    adapter.stop_ws_client(ws_client, timeout=2)
    thread.join(timeout=2)

    assert thread.is_alive() is False
    assert finalized[0] == "disconnect"
    assert sorted(finalized[1:]) == ["ping", "receive"]
