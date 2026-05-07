from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest

from xagent.agent.memory import MemoryStore, workspace_memory_id
from xagent.agent.permissions import SessionApprover
from xagent.agent import runtime as runtime_module
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


def make_agent_loop(
    tmp_path,
    monkeypatch,
    scripts: list[list[ModelEvent]],
    *,
    workspace_name: str = "workspace",
):
    config = default_config()
    config.workspace.sessions_path = str(tmp_path / "sessions")
    workspace = tmp_path / workspace_name
    workspace.mkdir()
    provider = ScriptedProvider(scripts)
    snapshot = ProviderSnapshot(
        provider=provider,
        model="memory-model",
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
            memory_store=MemoryStore(tmp_path / "memory"),
        ),
        provider,
        workspace,
    )


def test_memory_store_initializes_markdown_files_and_isolates_workspaces(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory")
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()

    first_bundle = store.load_bundle(first)
    second_bundle = store.load_bundle(second)

    assert first_bundle.workspace_id == workspace_memory_id(first)
    assert first_bundle.workspace_id != second_bundle.workspace_id
    assert store.user_path.exists()
    assert store.soul_path.exists()
    assert first_bundle.memory_path.name == "memory.md"
    assert first_bundle.memory_path.read_text(encoding="utf-8").startswith("# Workspace Memory")
    meta = json.loads(first_bundle.memory_path.with_name("meta.json").read_text(encoding="utf-8"))
    assert meta["workspace_path"] == str(first.resolve())


@pytest.mark.asyncio
async def test_system_prompt_injects_memory_bundle(tmp_path, monkeypatch) -> None:
    agent_loop, provider, workspace = make_agent_loop(tmp_path, monkeypatch, [text_response("hello")])
    store = agent_loop.memory_store
    assert store is not None
    paths = store.workspace_paths(workspace)
    store.ensure_workspace(workspace)
    store.user_path.write_text("用户偏好", encoding="utf-8")
    store.soul_path.write_text("沟通方式", encoding="utf-8")
    paths.memory_path.write_text("项目记忆", encoding="utf-8")
    bus = MessageBus()

    await bus.publish_inbound(InboundMessage(content="hi", channel="test", chat_id="room"))
    await agent_loop.dispatch_once(bus)

    system_prompt = provider.requests[0].messages[0]["content"]
    assert "<memory>" in system_prompt
    assert "<soul>" in system_prompt
    assert "沟通方式" in system_prompt
    assert "<user>" in system_prompt
    assert "用户偏好" in system_prompt
    assert "<workspace>" in system_prompt
    assert "项目记忆" in system_prompt


@pytest.mark.asyncio
async def test_dream_command_updates_memory_from_new_summary(tmp_path, monkeypatch) -> None:
    agent_loop, provider, workspace = make_agent_loop(
        tmp_path,
        monkeypatch,
        [text_response("# Workspace Memory\n\n## 项目定位\n\n已更新\n")],
    )
    session = SessionStore(tmp_path / "sessions").open_for_chat(
        workspace_path=workspace,
        channel="test",
        chat_id="room",
    )
    session.append_message({"role": "user", "content": "old"})
    summary = session.append_summary("长期项目事实")
    bus = MessageBus()

    await bus.publish_inbound(InboundMessage(content="/dream", channel="test", chat_id="room"))
    await agent_loop.dispatch_once(bus)

    first = await bus.consume_outbound()
    second = await bus.consume_outbound()
    assert first.content == "dreaming..."
    assert second.content == "dream done."
    assert first.stream is not None and first.stream.kind == StreamKind.END
    assert second.stream is not None and second.stream.kind == StreamKind.END
    store = agent_loop.memory_store
    assert store is not None
    memory_path = store.workspace_paths(workspace).memory_path
    assert "已更新" in memory_path.read_text(encoding="utf-8")
    state = store.read_dream_state(workspace)
    assert state["sessions"]["test:room"]["last_summary_id"] == summary["summary_id"]
    assert "长期项目事实" in provider.requests[0].messages[-1]["content"]


@pytest.mark.asyncio
async def test_dream_command_does_not_read_uncompacted_messages(tmp_path, monkeypatch) -> None:
    agent_loop, provider, workspace = make_agent_loop(
        tmp_path,
        monkeypatch,
        [text_response("# Workspace Memory\n\n## 项目定位\n\nsummary only\n")],
    )
    session = SessionStore(tmp_path / "sessions").open_for_chat(
        workspace_path=workspace,
        channel="test",
        chat_id="room",
    )
    session.append_message({"role": "user", "content": "old"})
    session.append_summary("compact summary")
    session.append_message({"role": "user", "content": "fresh raw message"})
    bus = MessageBus()

    await bus.publish_inbound(InboundMessage(content="/dream", channel="test", chat_id="room"))
    await agent_loop.dispatch_once(bus)

    dream_input = provider.requests[0].messages[-1]["content"]
    assert "compact summary" in dream_input
    assert "fresh raw message" not in dream_input


@pytest.mark.asyncio
async def test_dream_compact_forces_summary_before_memory_update(tmp_path, monkeypatch) -> None:
    agent_loop, provider, workspace = make_agent_loop(
        tmp_path,
        monkeypatch,
        [
            text_response("forced summary"),
            text_response("# Workspace Memory\n\n## 项目定位\n\nforced\n"),
        ],
    )
    session = SessionStore(tmp_path / "sessions").open_for_chat(
        workspace_path=workspace,
        channel="test",
        chat_id="room",
    )
    session.append_message({"role": "user", "content": "important decision"})
    bus = MessageBus()

    await bus.publish_inbound(InboundMessage(content="/dream --compact", channel="test", chat_id="room"))
    await agent_loop.dispatch_once(bus)

    await bus.consume_outbound()
    done = await bus.consume_outbound()
    assert done.content == "dream done."
    assert len(provider.requests) == 2
    assert provider.requests[0].metadata["purpose"] == "compaction"
    assert provider.requests[1].metadata["purpose"] == "dream"
    store = agent_loop.memory_store
    assert store is not None
    assert "forced" in store.workspace_paths(workspace).memory_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_dream_compact_empty_summary_publishes_error(tmp_path, monkeypatch) -> None:
    agent_loop, _provider, workspace = make_agent_loop(
        tmp_path,
        monkeypatch,
        [
            [ModelEvent.message_done({"role": "assistant", "content": ""})],
            [ModelEvent.message_done({"role": "assistant", "content": ""})],
        ],
    )
    session = SessionStore(tmp_path / "sessions").open_for_chat(
        workspace_path=workspace,
        channel="test",
        chat_id="room",
    )
    session.append_message({"role": "user", "content": "important decision"})
    bus = MessageBus()

    await bus.publish_inbound(InboundMessage(content="/dream --compact", channel="test", chat_id="room"))
    await agent_loop.dispatch_once(bus)

    assert (await bus.consume_outbound()).content == "dreaming..."
    error = await bus.consume_outbound()
    assert error.metadata["error"] is True
    assert "empty summary" in error.content


@pytest.mark.asyncio
async def test_dream_without_new_summary_finishes_without_model_call(tmp_path, monkeypatch) -> None:
    agent_loop, provider, _workspace = make_agent_loop(tmp_path, monkeypatch, [])
    bus = MessageBus()

    await bus.publish_inbound(InboundMessage(content="/dream", channel="test", chat_id="room"))
    await agent_loop.dispatch_once(bus)

    assert (await bus.consume_outbound()).content == "dreaming..."
    assert (await bus.consume_outbound()).content == "dream done."
    assert provider.requests == []


@pytest.mark.asyncio
async def test_unknown_slash_command_returns_help_without_agent_run(tmp_path, monkeypatch) -> None:
    agent_loop, provider, _workspace = make_agent_loop(tmp_path, monkeypatch, [])
    bus = MessageBus()

    await bus.publish_inbound(InboundMessage(content="/unknown", channel="test", chat_id="room"))
    await agent_loop.dispatch_once(bus)

    event = await bus.consume_outbound()
    assert "Available commands" in event.content
    assert provider.requests == []
