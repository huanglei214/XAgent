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
        self.cards: list[tuple[str, str]] = []
        self.reactions: list[tuple[str, str, str]] = []
        self.fail_reactions = False
        self._reaction_index = 0
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
        assert callable(kwargs["callback"])
        return object()

    def build_ws_client(self, **kwargs: Any) -> FakeWsClient:
        self.ws_client._auto_reconnect = kwargs["auto_reconnect"]
        return self.ws_client

    def send_text(self, client: Any, *, chat_id: str, text: str) -> None:
        assert client is self.client
        self.sent.append((chat_id, text))

    def send_markdown_card(self, client: Any, *, chat_id: str, markdown: str) -> None:
        assert client is self.client
        self.cards.append((chat_id, markdown))

    def add_reaction(self, client: Any, *, message_id: str, emoji_type: str) -> str:
        assert client is self.client
        if self.fail_reactions:
            raise RuntimeError("reaction failed")
        self._reaction_index += 1
        reaction_id = f"reaction_{self._reaction_index}"
        self.reactions.append(("add", message_id, emoji_type))
        return reaction_id

    def delete_reaction(self, client: Any, *, message_id: str, reaction_id: str) -> None:
        assert client is self.client
        if self.fail_reactions:
            raise RuntimeError("reaction failed")
        self.reactions.append(("delete", message_id, reaction_id))


def make_event(
    text: str,
    *,
    chat_id: str = "oc_room",
    chat_type: str = "p2p",
    message_type: str = "text",
    message_id: str = "om_message",
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
                "message_id": message_id,
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
    channel, sdk = await make_started_channel(bus)

    received = await channel.handle_message(make_event("hello"))
    inbound = await bus.consume_inbound()

    assert received == inbound
    assert inbound.content == "hello"
    assert inbound.channel == "lark"
    assert inbound.chat_id == "oc_room"
    assert inbound.sender_id == "ou_user"
    assert inbound.external_message_id == "om_message"
    assert inbound.metadata["chat_type"] == "p2p"
    assert sdk.reactions == [("add", "om_message", "OnIt")]


@pytest.mark.asyncio
async def test_lark_group_mention_publishes_inbound_and_strips_mention() -> None:
    bus = MessageBus()
    channel, sdk = await make_started_channel(bus)

    received = await channel.handle_message(
        make_event("@_user_1 继续", chat_type="group", mentions=[bot_mention()])
    )
    inbound = await bus.consume_inbound()

    assert received == inbound
    assert inbound.content == "继续"
    assert inbound.chat_id == "oc_room"
    assert sdk.reactions == [("add", "om_message", "OnIt")]


@pytest.mark.asyncio
async def test_lark_group_without_mention_is_ignored() -> None:
    bus = MessageBus()
    channel, sdk = await make_started_channel(bus)

    received = await channel.handle_message(make_event("hello", chat_type="group"))

    assert received is None
    assert bus.inbound.empty()
    assert sdk.reactions == []


@pytest.mark.asyncio
async def test_lark_ignores_non_text_empty_and_bot_self_messages() -> None:
    bus = MessageBus()
    channel, sdk = await make_started_channel(bus)

    assert await channel.handle_message(make_event("hello", message_type="image")) is None
    assert await channel.handle_message(make_event("   ")) is None
    assert await channel.handle_message(make_event("hello", sender_id="ou_bot")) is None
    assert await channel.handle_message(make_event("hello", sender_type="app")) is None
    assert bus.inbound.empty()
    assert sdk.reactions == []


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
            metadata={"external_message_id": "om_message"},
        )
    )

    assert sdk.sent == [("oc_room", "hello")]
    assert sdk.reactions == [("add", "om_message", "DONE")]


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
            metadata={"error": True, "external_message_id": "om_error"},
        )
    )

    assert sdk.sent == [("oc_room", "模型调用失败")]
    assert sdk.cards == []
    assert sdk.reactions == [("add", "om_error", "DONE")]


@pytest.mark.asyncio
async def test_lark_auto_message_format_sends_markdown_card_for_markdown() -> None:
    bus = MessageBus()
    channel, sdk = await make_started_channel(bus)

    await channel.send(
        OutboundEvent(
            content="## 标题\n\n- 第一项\n- 第二项",
            channel="lark",
            chat_id="oc_room",
            stream=StreamState(kind=StreamKind.END, stream_id="s1"),
            metadata={"external_message_id": "om_message"},
        )
    )

    assert sdk.sent == []
    assert sdk.cards == [("oc_room", "**标题**\n\n- 第一项\n- 第二项")]
    assert sdk.reactions == [("add", "om_message", "DONE")]


@pytest.mark.asyncio
async def test_lark_heading_after_list_is_not_nested_under_previous_bullet() -> None:
    bus = MessageBus()
    channel, sdk = await make_started_channel(bus)

    await channel.send(
        OutboundEvent(
            content="### 1. 民生文化\n- 第一项\n### 2. 楼市政策\n- 第二项",
            channel="lark",
            chat_id="oc_room",
            stream=StreamState(kind=StreamKind.END, stream_id="s1"),
        )
    )

    assert sdk.cards == [("oc_room", "**1. 民生文化**\n\n- 第一项\n\n**2. 楼市政策**\n\n- 第二项")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        "```python\nprint('hi')\n```",
        "[OpenAI](https://openai.com)",
        "`xagent gateway`",
        "| A | B |\n| - | - |",
    ],
)
async def test_lark_auto_message_format_detects_common_markdown(content: str) -> None:
    bus = MessageBus()
    channel, sdk = await make_started_channel(bus)

    await channel.send(
        OutboundEvent(
            content=content,
            channel="lark",
            chat_id="oc_room",
            stream=StreamState(kind=StreamKind.END, stream_id="s1"),
        )
    )

    assert sdk.cards == [("oc_room", content)]


@pytest.mark.asyncio
async def test_lark_text_message_format_disables_markdown_card() -> None:
    bus = MessageBus()
    channel, sdk = await make_started_channel(
        bus,
        config=LarkChannelConfig(
            enabled=True,
            app_id="cli_test",
            app_secret="secret",
            message_format="text",
        ),
    )

    await channel.send(
        OutboundEvent(
            content="## 标题",
            channel="lark",
            chat_id="oc_room",
            stream=StreamState(kind=StreamKind.END, stream_id="s1"),
        )
    )

    assert sdk.sent == [("oc_room", "## 标题")]
    assert sdk.cards == []


@pytest.mark.asyncio
async def test_lark_markdown_card_message_format_forces_card_for_plain_text() -> None:
    bus = MessageBus()
    channel, sdk = await make_started_channel(
        bus,
        config=LarkChannelConfig(
            enabled=True,
            app_id="cli_test",
            app_secret="secret",
            message_format="markdown_card",
        ),
    )

    await channel.send(
        OutboundEvent(
            content="plain answer",
            channel="lark",
            chat_id="oc_room",
            stream=StreamState(kind=StreamKind.END, stream_id="s1"),
        )
    )

    assert sdk.sent == []
    assert sdk.cards == [("oc_room", "plain answer")]


@pytest.mark.asyncio
async def test_lark_progress_and_error_force_text_even_with_markdown_format() -> None:
    bus = MessageBus()
    channel, sdk = await make_started_channel(
        bus,
        config=LarkChannelConfig(
            enabled=True,
            app_id="cli_test",
            app_secret="secret",
            message_format="markdown_card",
        ),
    )

    await channel.send(
        OutboundEvent(
            content="dreaming...",
            channel="lark",
            chat_id="oc_room",
            stream=StreamState(kind=StreamKind.END, stream_id="s1"),
            metadata={"progress": True},
        )
    )
    await channel.send(
        OutboundEvent(
            content="## error",
            channel="lark",
            chat_id="oc_room",
            stream=StreamState(kind=StreamKind.END, stream_id="s2"),
            metadata={"error": True},
        )
    )

    assert sdk.sent == [("oc_room", "dreaming..."), ("oc_room", "## error")]
    assert sdk.cards == []


@pytest.mark.asyncio
async def test_lark_markdown_card_falls_back_to_text_when_too_large() -> None:
    bus = MessageBus()
    channel, sdk = await make_started_channel(bus)
    large_markdown = "## 标题\n\n" + ("- 内容\n" * 8000)

    await channel.send(
        OutboundEvent(
            content=large_markdown,
            channel="lark",
            chat_id="oc_room",
            stream=StreamState(kind=StreamKind.END, stream_id="s1"),
        )
    )

    assert sdk.sent == [("oc_room", large_markdown.strip())]
    assert sdk.cards == []


@pytest.mark.asyncio
async def test_lark_send_removes_working_reaction_before_done() -> None:
    bus = MessageBus()
    channel, sdk = await make_started_channel(bus)

    await channel.handle_message(make_event("hello"))
    await bus.consume_inbound()
    await channel.send(
        OutboundEvent(
            content="done",
            channel="lark",
            chat_id="oc_room",
            stream=StreamState(kind=StreamKind.END, stream_id="s1"),
            metadata={"external_message_id": "om_message"},
        )
    )

    assert sdk.sent == [("oc_room", "done")]
    assert sdk.reactions == [
        ("add", "om_message", "OnIt"),
        ("delete", "om_message", "reaction_1"),
        ("add", "om_message", "DONE"),
    ]


@pytest.mark.asyncio
async def test_lark_reaction_failure_does_not_block_inbound_or_outbound() -> None:
    bus = MessageBus()
    sdk = FakeSdk()
    sdk.fail_reactions = True
    channel, sdk = await make_started_channel(bus, sdk=sdk)

    received = await channel.handle_message(make_event("hello"))
    inbound = await bus.consume_inbound()
    await channel.send(
        OutboundEvent(
            content="done",
            channel="lark",
            chat_id="oc_room",
            stream=StreamState(kind=StreamKind.END, stream_id="s1"),
            metadata={"external_message_id": "om_message"},
        )
    )

    assert received == inbound
    assert sdk.sent == [("oc_room", "done")]
    assert sdk.reactions == []


@pytest.mark.asyncio
async def test_lark_reactions_can_be_disabled() -> None:
    bus = MessageBus()
    channel, sdk = await make_started_channel(
        bus,
        config=LarkChannelConfig(
            enabled=True,
            app_id="cli_test",
            app_secret="secret",
            reactions_enabled=False,
        ),
    )

    await channel.handle_message(make_event("hello"))
    await bus.consume_inbound()
    await channel.send(
        OutboundEvent(
            content="done",
            channel="lark",
            chat_id="oc_room",
            stream=StreamState(kind=StreamKind.END, stream_id="s1"),
            metadata={"external_message_id": "om_message"},
        )
    )

    assert sdk.sent == [("oc_room", "done")]
    assert sdk.reactions == []


@pytest.mark.asyncio
async def test_lark_progress_outbound_does_not_add_done_reaction() -> None:
    bus = MessageBus()
    channel, sdk = await make_started_channel(bus)

    await channel.send(
        OutboundEvent(
            content="dreaming...",
            channel="lark",
            chat_id="oc_room",
            stream=StreamState(kind=StreamKind.END, stream_id="s1"),
            metadata={"external_message_id": "om_message", "progress": True},
        )
    )

    assert sdk.sent == [("oc_room", "dreaming...")]
    assert sdk.reactions == []


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

    assert channel.describe() == "lark domain=feishu require_mention=True message_format=auto streaming=False"


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
