from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from xagent.agent import runtime as runtime_module
from xagent.agent.permissions import SessionApprover
from xagent.bus import InboundMessage, MessageBus, StreamKind
from xagent.config import default_config
from xagent.providers import ModelEvent, ModelRequest, ProviderSnapshot


class ScriptedProvider:
    def __init__(self, scripts: list[list[ModelEvent]]) -> None:
        self.scripts = scripts
        self.requests: list[ModelRequest] = []

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.requests.append(request)
        if not self.scripts:
            raise RuntimeError("No scripted response left")
        for event in self.scripts.pop(0):
            yield event


def text_response(text: str) -> list[ModelEvent]:
    return [ModelEvent.text_delta(text), ModelEvent.message_done()]


def make_runtime(tmp_path, monkeypatch, scripts: list[list[ModelEvent]]):
    config = default_config()
    config.workspace.sessions_path = str(tmp_path / "sessions")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provider = ScriptedProvider(scripts)
    snapshot = ProviderSnapshot(
        provider=provider,
        model="runtime-model",
        provider_name="openai_compat",
        api_base=None,
        signature=("test",),
    )
    monkeypatch.setattr(runtime_module, "make_provider", lambda config: snapshot)
    return (
        runtime_module.AgentRuntime(
            config=config,
            workspace_path=workspace,
            approver=SessionApprover(default_allow=True),
        ),
        provider,
    )


@pytest.mark.asyncio
async def test_runtime_dispatches_inbound_to_outbound(tmp_path, monkeypatch) -> None:
    runtime, provider = make_runtime(tmp_path, monkeypatch, [text_response("hello")])
    bus = MessageBus()
    inbound = InboundMessage(content="hi", channel="test", chat_id="room", sender_id="alice")

    await bus.publish_inbound(inbound)
    await runtime.dispatch_once(bus)

    delta = await bus.consume_outbound()
    final = await bus.consume_outbound()
    assert delta.stream is not None
    assert delta.stream.kind == StreamKind.DELTA
    assert delta.content == "hello"
    assert delta.channel == "test"
    assert delta.chat_id == "room"
    assert delta.reply_to == "alice"
    assert delta.session_id == "test:room"
    assert final.stream is not None
    assert final.stream.kind == StreamKind.END
    assert final.stream.stream_id == delta.stream.stream_id
    assert final.content == "hello"
    assert final.channel == "test"
    assert final.chat_id == "room"
    assert final.reply_to == "alice"
    assert final.session_id == "test:room"
    assert provider.requests[0].messages[-1]["content"] == "[sender:alice] hi"


@pytest.mark.asyncio
async def test_runtime_publishes_agent_errors(tmp_path, monkeypatch) -> None:
    runtime, _provider = make_runtime(tmp_path, monkeypatch, [])
    bus = MessageBus()
    inbound = InboundMessage(content="hi", channel="test", chat_id="room", sender_id="alice")

    await bus.publish_inbound(inbound)
    await runtime.dispatch_once(bus)

    event = await bus.consume_outbound()
    assert event.stream is not None
    assert event.stream.kind == StreamKind.END
    assert "No scripted response left" in event.content
    assert event.channel == "test"
    assert event.chat_id == "room"
    assert event.reply_to == "alice"
    assert event.session_id == "test:room"
    assert event.metadata["error"] is True


@pytest.mark.asyncio
async def test_runtime_reuses_session_for_same_channel_chat_id(tmp_path, monkeypatch) -> None:
    runtime, provider = make_runtime(
        tmp_path,
        monkeypatch,
        [text_response("one"), text_response("two")],
    )
    bus = MessageBus()

    await bus.publish_inbound(
        InboundMessage(content="first", channel="test", chat_id="room", sender_id="alice")
    )
    await runtime.dispatch_once(bus)
    await bus.consume_outbound()
    first_final = await bus.consume_outbound()

    await bus.publish_inbound(
        InboundMessage(content="second", channel="test", chat_id="room", sender_id="bob")
    )
    await runtime.dispatch_once(bus)
    await bus.consume_outbound()
    second_final = await bus.consume_outbound()

    assert first_final.session_id == "test:room"
    assert second_final.session_id == "test:room"
    assert len(list((tmp_path / "sessions").iterdir())) == 1
    assert provider.requests[0].messages[-1]["content"] == "[sender:alice] first"
    assert provider.requests[1].messages[-1]["content"] == "[sender:bob] second"


@pytest.mark.asyncio
async def test_runtime_prefers_explicit_session_id(tmp_path, monkeypatch) -> None:
    runtime, _provider = make_runtime(tmp_path, monkeypatch, [text_response("hello")])
    bus = MessageBus()
    inbound = InboundMessage(
        content="hi",
        channel="test",
        chat_id="room",
        session_id="manual:session",
    )

    await bus.publish_inbound(inbound)
    await runtime.dispatch_once(bus)

    await bus.consume_outbound()
    final = await bus.consume_outbound()
    assert final.session_id == "manual:session"
    assert (tmp_path / "sessions" / "manual:session").is_dir()
