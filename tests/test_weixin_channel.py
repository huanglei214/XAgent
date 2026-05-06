from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

import pytest

from xagent.bus import MessageBus, OutboundEvent, StreamKind, StreamState
from xagent.channels import WeixinChannel, build_channels
from xagent.config import WeixinChannelConfig, default_config


class FakeWeixinApi:
    def __init__(self) -> None:
        self.opened = False
        self.closed = False
        self.configured: dict[str, Any] = {}
        self.qr_statuses: list[dict[str, Any]] = []
        self.updates: list[dict[str, Any]] = []
        self.sent: list[dict[str, str]] = []
        self.get_updates_calls = 0

    def configure(self, **kwargs: Any) -> None:
        self.configured.update({key: value for key, value in kwargs.items() if value is not None})

    async def open(self) -> None:
        self.opened = True

    async def close(self) -> None:
        self.closed = True

    async def get_qr_code(self) -> tuple[str, str]:
        return "qr-1", "https://login.example.test/qr-1"

    async def get_qr_status(self, qrcode_id: str, *, base_url: str | None = None) -> dict[str, Any]:
        del qrcode_id, base_url
        return self.qr_statuses.pop(0)

    async def get_updates(self, get_updates_buf: str) -> dict[str, Any]:
        del get_updates_buf
        self.get_updates_calls += 1
        if self.updates:
            return self.updates.pop(0)
        await asyncio.sleep(3600)
        return {}

    async def send_text(self, *, to_user_id: str, text: str, context_token: str) -> dict[str, Any]:
        self.sent.append(
            {
                "to_user_id": to_user_id,
                "text": text,
                "context_token": context_token,
            }
        )
        return {"errcode": 0}


def make_config(tmp_path, *, allow_from: list[str] | None = None, token: str | None = None):
    return WeixinChannelConfig(
        enabled=True,
        allow_from=allow_from or ["user_1"],
        token=token,
        state_dir=str(tmp_path / "weixin-state"),
    )


def make_text_message(
    *,
    from_user_id: str = "user_1",
    message_id: str = "msg_1",
    text: str = "hello",
    context_token: str = "ctx_1",
    message_type: int = 1,
) -> dict[str, Any]:
    return {
        "message_id": message_id,
        "from_user_id": from_user_id,
        "message_type": message_type,
        "context_token": context_token,
        "item_list": [{"type": 1, "text_item": {"text": text}}],
    }


@pytest.mark.asyncio
async def test_weixin_login_saves_state_and_force_clears_old_state(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("xagent.channels.weixin._print_qr_code", lambda url: None)
    api = FakeWeixinApi()
    api.qr_statuses = [{"status": "confirmed", "bot_token": "token-new", "baseurl": "https://new"}]
    config = make_config(tmp_path)
    state_path = tmp_path / "weixin-state" / "account.json"
    state_path.parent.mkdir()
    state_path.write_text(json.dumps({"token": "old"}), encoding="utf-8")
    channel = WeixinChannel(config=config, bus=MessageBus(), api=api)

    assert await channel.login(force=True) is True

    data = json.loads(state_path.read_text(encoding="utf-8"))
    assert data["token"] == "token-new"
    assert data["base_url"] == "https://new"
    assert api.opened is True
    assert api.closed is True


@pytest.mark.asyncio
async def test_weixin_start_uses_state_token(tmp_path) -> None:
    api = FakeWeixinApi()
    config = make_config(tmp_path)
    state_path = tmp_path / "weixin-state" / "account.json"
    state_path.parent.mkdir()
    state_path.write_text(
        json.dumps({"token": "token-state", "base_url": "https://state"}),
        encoding="utf-8",
    )
    channel = WeixinChannel(config=config, bus=MessageBus(), api=api)

    await channel.start()

    assert api.opened is True
    assert api.configured["token"] == "token-state"
    assert api.configured["base_url"] == "https://state"


@pytest.mark.asyncio
async def test_weixin_start_without_token_raises(tmp_path) -> None:
    channel = WeixinChannel(config=make_config(tmp_path), bus=MessageBus(), api=FakeWeixinApi())

    with pytest.raises(RuntimeError, match="xagent channels login weixin"):
        await channel.start()


@pytest.mark.asyncio
async def test_weixin_handle_message_publishes_text_and_caches_context(tmp_path) -> None:
    bus = MessageBus()
    channel = WeixinChannel(config=make_config(tmp_path), bus=bus, api=FakeWeixinApi())

    received = await channel.handle_message(make_text_message(text="你好"))
    inbound = await bus.consume_inbound()

    assert received == inbound
    assert inbound.content == "你好"
    assert inbound.channel == "weixin"
    assert inbound.chat_id == "user_1"
    assert inbound.sender_id == "user_1"
    assert inbound.external_message_id == "msg_1"
    state = json.loads((tmp_path / "weixin-state" / "account.json").read_text(encoding="utf-8"))
    assert state["context_tokens"]["user_1"] == "ctx_1"


@pytest.mark.asyncio
async def test_weixin_ignores_bot_duplicate_non_text_and_unauthorized(tmp_path) -> None:
    bus = MessageBus()
    channel = WeixinChannel(config=make_config(tmp_path), bus=bus, api=FakeWeixinApi())

    assert await channel.handle_message(make_text_message(message_type=2)) is None
    assert await channel.handle_message(make_text_message(text="", message_id="msg_empty")) is None
    assert await channel.handle_message(make_text_message(message_id="msg_dup")) is not None
    assert await channel.handle_message(make_text_message(message_id="msg_dup")) is None
    assert (
        await channel.handle_message(make_text_message(from_user_id="user_2", message_id="msg_denied"))
        is None
    )

    assert (await bus.consume_inbound()).external_message_id == "msg_dup"
    assert bus.inbound.empty()


@pytest.mark.asyncio
async def test_weixin_run_polls_updates_and_publishes_inbound(tmp_path) -> None:
    api = FakeWeixinApi()
    api.updates = [
        {
            "ret": 0,
            "errcode": 0,
            "get_updates_buf": "cursor-2",
            "longpolling_timeout_ms": 5000,
            "msgs": [make_text_message(text="poll hello")],
        }
    ]
    bus = MessageBus()
    channel = WeixinChannel(config=make_config(tmp_path, token="token-config"), bus=bus, api=api)
    await channel.start()

    task = asyncio.create_task(channel.run())
    inbound = await bus.consume_inbound()
    await channel.stop()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert inbound.content == "poll hello"
    state = json.loads((tmp_path / "weixin-state" / "account.json").read_text(encoding="utf-8"))
    assert state["get_updates_buf"] == "cursor-2"


@pytest.mark.asyncio
async def test_weixin_send_ignores_delta_and_sends_end(tmp_path) -> None:
    api = FakeWeixinApi()
    bus = MessageBus()
    channel = WeixinChannel(config=make_config(tmp_path, token="token-config"), bus=bus, api=api)
    await channel.start()
    await channel.handle_message(make_text_message(context_token="ctx-send"))

    await channel.send(
        OutboundEvent(
            content="hel",
            channel="weixin",
            chat_id="user_1",
            stream=StreamState(kind=StreamKind.DELTA, stream_id="s1"),
        )
    )
    await channel.send(
        OutboundEvent(
            content="hello",
            channel="weixin",
            chat_id="user_1",
            stream=StreamState(kind=StreamKind.END, stream_id="s1"),
        )
    )

    assert api.sent == [{"to_user_id": "user_1", "text": "hello", "context_token": "ctx-send"}]


@pytest.mark.asyncio
async def test_weixin_send_splits_long_text(tmp_path) -> None:
    api = FakeWeixinApi()
    channel = WeixinChannel(
        config=make_config(tmp_path, token="token-config"),
        bus=MessageBus(),
        api=api,
    )
    await channel.start()
    await channel.handle_message(make_text_message(context_token="ctx-send"))

    await channel.send(OutboundEvent(content="x" * 4001, channel="weixin", chat_id="user_1"))

    assert [len(item["text"]) for item in api.sent] == [4000, 1]


@pytest.mark.asyncio
async def test_weixin_send_without_context_token_does_not_send(tmp_path) -> None:
    api = FakeWeixinApi()
    channel = WeixinChannel(
        config=make_config(tmp_path, token="token-config"),
        bus=MessageBus(),
        api=api,
    )
    await channel.start()

    await channel.send(OutboundEvent(content="hello", channel="weixin", chat_id="user_1"))

    assert api.sent == []
    assert channel.last_send_error == "Weixin context_token is missing for chat_id=user_1"


def test_build_channels_includes_enabled_weixin_channel() -> None:
    config = default_config()
    config.channels.weixin.enabled = True

    channels = build_channels(config, MessageBus())

    assert list(channels) == ["weixin"]
    assert isinstance(channels["weixin"], WeixinChannel)


def test_weixin_channel_describe_shows_startup_summary(tmp_path) -> None:
    channel = WeixinChannel(
        config=make_config(tmp_path, allow_from=["*"]),
        bus=MessageBus(),
        api=FakeWeixinApi(),
    )

    summary = channel.describe()

    assert "weixin mode=long-poll" in summary
    assert "allow_from=*" in summary
    assert "poll=35s" in summary
    assert "account.json" in summary
