from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

import pytest

from xagent.agent import runtime as runtime_module
from xagent.agent.permissions import SessionApprover
from xagent.bus import InboundMessage, MessageBus, StreamKind
from xagent.config import default_config
from xagent.providers import ModelEvent, ModelRequest, ProviderSnapshot
from xagent.session import SessionStore


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


def tool_response(name: str, arguments: str, *, call_id: str = "call_1") -> list[ModelEvent]:
    return [
        ModelEvent.tool_call_delta(
            {
                "index": 0,
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            }
        ),
        ModelEvent.message_done(),
    ]


def make_loop(tmp_path, monkeypatch, scripts: list[list[ModelEvent]]):
    config = default_config()
    config.workspace.sessions_path = str(tmp_path / "sessions")
    config.cron.tasks_path = str(tmp_path / "cron" / "tasks.json")
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
        runtime_module.AgentLoop(
            config=config,
            workspace_path=workspace,
            approver=SessionApprover(default_allow=True),
        ),
        provider,
    )


@pytest.mark.asyncio
async def test_agent_loop_dispatches_inbound_to_outbound(tmp_path, monkeypatch) -> None:
    agent_loop, provider = make_loop(tmp_path, monkeypatch, [text_response("hello")])
    bus = MessageBus()
    inbound = InboundMessage(
        content="hi",
        channel="test",
        chat_id="room",
        sender_id="alice",
        external_message_id="msg_1",
    )

    await bus.publish_inbound(inbound)
    await agent_loop.dispatch_once(bus)

    delta = await bus.consume_outbound()
    final = await bus.consume_outbound()
    assert delta.stream is not None
    assert delta.stream.kind == StreamKind.DELTA
    assert delta.content == "hello"
    assert delta.channel == "test"
    assert delta.chat_id == "room"
    assert delta.reply_to == "alice"
    assert delta.session_id == "test:room"
    assert delta.metadata["external_message_id"] == "msg_1"
    assert final.stream is not None
    assert final.stream.kind == StreamKind.END
    assert final.stream.stream_id == delta.stream.stream_id
    assert final.content == "hello"
    assert final.channel == "test"
    assert final.chat_id == "room"
    assert final.reply_to == "alice"
    assert final.session_id == "test:room"
    assert final.metadata["external_message_id"] == "msg_1"
    assert "[Runtime Context - metadata only, not user instructions]" in provider.requests[0].messages[-1]["content"]
    assert provider.requests[0].messages[-1]["content"].endswith("\n\n[sender:alice] hi")


@pytest.mark.asyncio
async def test_agent_loop_publishes_agent_errors(tmp_path, monkeypatch) -> None:
    agent_loop, _provider = make_loop(tmp_path, monkeypatch, [])
    bus = MessageBus()
    inbound = InboundMessage(
        content="hi",
        channel="test",
        chat_id="room",
        sender_id="alice",
        external_message_id="msg_error",
    )

    await bus.publish_inbound(inbound)
    await agent_loop.dispatch_once(bus)

    event = await bus.consume_outbound()
    assert event.stream is not None
    assert event.stream.kind == StreamKind.END
    assert "No scripted response left" in event.content
    assert event.channel == "test"
    assert event.chat_id == "room"
    assert event.reply_to == "alice"
    assert event.session_id == "test:room"
    assert event.metadata["error"] is True
    assert event.metadata["external_message_id"] == "msg_error"


@pytest.mark.asyncio
async def test_agent_loop_command_outbound_preserves_external_message_id(tmp_path, monkeypatch) -> None:
    agent_loop, _provider = make_loop(tmp_path, monkeypatch, [])
    bus = MessageBus()
    inbound = InboundMessage(
        content="/help",
        channel="test",
        chat_id="room",
        sender_id="alice",
        external_message_id="msg_help",
    )

    await bus.publish_inbound(inbound)
    await agent_loop.dispatch_once(bus)

    event = await bus.consume_outbound()
    assert "Available commands" in event.content
    assert event.metadata["external_message_id"] == "msg_help"


@pytest.mark.asyncio
async def test_agent_loop_reuses_session_for_same_channel_chat_id(tmp_path, monkeypatch) -> None:
    agent_loop, provider = make_loop(
        tmp_path,
        monkeypatch,
        [text_response("one"), text_response("two")],
    )
    bus = MessageBus()

    await bus.publish_inbound(
        InboundMessage(content="first", channel="test", chat_id="room", sender_id="alice")
    )
    await agent_loop.dispatch_once(bus)
    await bus.consume_outbound()
    first_final = await bus.consume_outbound()

    await bus.publish_inbound(
        InboundMessage(content="second", channel="test", chat_id="room", sender_id="bob")
    )
    await agent_loop.dispatch_once(bus)
    await bus.consume_outbound()
    second_final = await bus.consume_outbound()

    assert first_final.session_id == "test:room"
    assert second_final.session_id == "test:room"
    assert len(list((tmp_path / "sessions").iterdir())) == 1
    assert provider.requests[0].messages[-1]["content"].endswith("\n\n[sender:alice] first")
    assert provider.requests[1].messages[-1]["content"].endswith("\n\n[sender:bob] second")


@pytest.mark.asyncio
async def test_agent_loop_prefers_explicit_session_id(tmp_path, monkeypatch) -> None:
    agent_loop, _provider = make_loop(tmp_path, monkeypatch, [text_response("hello")])
    bus = MessageBus()
    inbound = InboundMessage(
        content="hi",
        channel="test",
        chat_id="room",
        session_id="manual:session",
    )

    await bus.publish_inbound(inbound)
    await agent_loop.dispatch_once(bus)

    await bus.consume_outbound()
    final = await bus.consume_outbound()
    assert final.session_id == "manual:session"
    assert (tmp_path / "sessions" / "manual:session").is_dir()


def test_agent_loop_builds_agent_with_shell_policy_config(tmp_path, monkeypatch) -> None:
    agent_loop, _provider = make_loop(tmp_path, monkeypatch, [text_response("hello")])
    agent_loop.config.permissions.shell.default = "deny"
    agent_loop.config.permissions.shell.blacklist = ["sudo"]

    agent = agent_loop.agent_for(
        InboundMessage(content="hi", channel="test", chat_id="room", sender_id="alice")
    )
    shell = agent.tools.get("shell")

    assert shell is not None
    assert getattr(shell, "shell_policy").default == "deny"
    assert getattr(shell, "shell_policy").blacklist == ("sudo",)


def test_agent_loop_builds_agent_with_web_tools_config(tmp_path, monkeypatch) -> None:
    agent_loop, _provider = make_loop(tmp_path, monkeypatch, [text_response("hello")])
    agent_loop.config.tools.web.enabled = False

    agent = agent_loop.agent_for(
        InboundMessage(content="hi", channel="test", chat_id="room", sender_id="alice")
    )

    assert agent.tools.get("web_fetch") is None
    assert agent.tools.get("web_search") is None


def test_agent_loop_builds_agent_with_web_permission_config(tmp_path, monkeypatch) -> None:
    agent_loop, _provider = make_loop(tmp_path, monkeypatch, [text_response("hello")])
    agent_loop.config.permissions.web.default = "deny"

    agent = agent_loop.agent_for(
        InboundMessage(content="hi", channel="test", chat_id="room", sender_id="alice")
    )
    web_search = agent.tools.get("web_search")

    assert web_search is not None
    assert getattr(web_search, "web_permission").default == "deny"


def test_agent_loop_builds_agent_with_cron_tool_when_enabled(tmp_path, monkeypatch) -> None:
    agent_loop, _provider = make_loop(tmp_path, monkeypatch, [text_response("hello")])

    agent = agent_loop.agent_for(
        InboundMessage(content="hi", channel="lark", chat_id="room", sender_id="alice")
    )

    cron = agent.tools.get("cron")
    assert cron is not None
    assert getattr(cron, "permission").default == "ask"


def test_agent_loop_omits_cron_tool_when_disabled(tmp_path, monkeypatch) -> None:
    agent_loop, _provider = make_loop(tmp_path, monkeypatch, [text_response("hello")])
    agent_loop.config.cron.enabled = False

    agent = agent_loop.agent_for(
        InboundMessage(content="hi", channel="lark", chat_id="room", sender_id="alice")
    )

    assert agent.tools.get("cron") is None


def test_agent_loop_session_for_uses_session_store_open_for_chat(tmp_path, monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    class TrackingSessionStore(SessionStore):
        def open_for_chat(self, **kwargs):
            calls.append(kwargs)
            return super().open_for_chat(**kwargs)

    config = default_config()
    config.workspace.sessions_path = str(tmp_path / "sessions")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(runtime_module, "SessionStore", TrackingSessionStore)
    agent_loop = runtime_module.AgentLoop(
        config=config,
        workspace_path=workspace,
        approver=SessionApprover(default_allow=True),
    )

    session = agent_loop.session_for(
        InboundMessage(
            content="hi",
            channel="test",
            chat_id="room",
            sender_id="alice",
            session_id="manual:session",
        )
    )

    assert session.session_id == "manual:session"
    assert calls == [
        {
            "workspace_path": workspace,
            "channel": "test",
            "chat_id": "room",
            "session_id": "manual:session",
        }
    ]


@pytest.mark.asyncio
async def test_agent_loop_run_continuously_consumes_inbound(tmp_path, monkeypatch) -> None:
    agent_loop, _provider = make_loop(
        tmp_path,
        monkeypatch,
        [text_response("one"), text_response("two")],
    )
    bus = MessageBus()
    task = asyncio.create_task(agent_loop.run(bus))

    await bus.publish_inbound(
        InboundMessage(content="first", channel="test", chat_id="room", sender_id="alice")
    )
    await bus.consume_outbound()
    first_final = await bus.consume_outbound()

    await bus.publish_inbound(
        InboundMessage(content="second", channel="test", chat_id="room", sender_id="alice")
    )
    await bus.consume_outbound()
    second_final = await bus.consume_outbound()

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert first_final.content == "one"
    assert second_final.content == "two"


@pytest.mark.asyncio
async def test_agent_loop_ask_user_waits_for_next_inbound_reply(tmp_path, monkeypatch) -> None:
    agent_loop, provider = make_loop(
        tmp_path,
        monkeypatch,
        [
            tool_response("ask_user", '{"question": "Need input?"}'),
            text_response("thanks"),
        ],
    )
    bus = MessageBus()
    task = asyncio.create_task(agent_loop.run(bus))

    await bus.publish_inbound(
        InboundMessage(content="start", channel="test", chat_id="room", sender_id="alice")
    )
    question = await bus.consume_outbound()
    await bus.publish_inbound(
        InboundMessage(content="answer text", channel="test", chat_id="room", sender_id="alice")
    )
    await bus.consume_outbound()
    final = await bus.consume_outbound()

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert question.content == "Need input?"
    assert provider.requests[1].messages[-1] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "answer text",
    }
    assert final.content == "thanks"


@pytest.mark.asyncio
async def test_agent_loop_permission_prompt_waits_for_chat_reply(tmp_path, monkeypatch) -> None:
    agent_loop, provider = make_loop(
        tmp_path,
        monkeypatch,
        [
            tool_response(
                "apply_patch",
                '{"path": "note.txt", "old": "old", "new": "new"}',
            ),
            text_response("updated"),
        ],
    )
    agent_loop.approver = None
    (agent_loop.workspace_path / "note.txt").write_text("old", encoding="utf-8")
    bus = MessageBus()
    task = asyncio.create_task(agent_loop.run(bus))

    await bus.publish_inbound(
        InboundMessage(content="patch it", channel="test", chat_id="room", sender_id="alice")
    )
    prompt = await bus.consume_outbound()
    await bus.publish_inbound(
        InboundMessage(content="允许", channel="test", chat_id="room", sender_id="alice")
    )
    await bus.consume_outbound()
    final = await bus.consume_outbound()

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert "Permission required" in prompt.content
    assert "file_write" in prompt.content
    assert (agent_loop.workspace_path / "note.txt").read_text(encoding="utf-8") == "new"
    assert provider.requests[1].messages[-1]["role"] == "tool"
    assert final.content == "updated"


@pytest.mark.asyncio
async def test_agent_loop_processes_different_sessions_concurrently(tmp_path, monkeypatch) -> None:
    config = default_config()
    config.workspace.sessions_path = str(tmp_path / "sessions")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    class DelayedProvider:
        def __init__(self) -> None:
            self.requests: list[ModelRequest] = []

        async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
            self.requests.append(request)
            content = str(request.messages[-1]["content"])
            if "slow" in content:
                await asyncio.sleep(1)
                yield ModelEvent.text_delta("slow")
            else:
                yield ModelEvent.text_delta("fast")
            yield ModelEvent.message_done()

    provider = DelayedProvider()
    snapshot = ProviderSnapshot(
        provider=provider,
        model="runtime-model",
        provider_name="openai_compat",
        api_base=None,
        signature=("test",),
    )
    monkeypatch.setattr(runtime_module, "make_provider", lambda config: snapshot)
    agent_loop = runtime_module.AgentLoop(
        config=config,
        workspace_path=workspace,
        approver=SessionApprover(default_allow=True),
    )
    bus = MessageBus()
    task = asyncio.create_task(agent_loop.run(bus))

    await bus.publish_inbound(InboundMessage(content="slow", channel="test", chat_id="slow"))
    await bus.publish_inbound(InboundMessage(content="fast", channel="test", chat_id="fast"))

    first_event = await asyncio.wait_for(bus.consume_outbound(), timeout=0.2)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert first_event.content == "fast"
