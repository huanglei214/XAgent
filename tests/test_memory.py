from __future__ import annotations

import json
from collections.abc import AsyncIterator
from importlib.resources import files

import pytest

from xagent.agent.memory import (
    SECTION_ALLOWLIST,
    MemoryStore,
    apply_memory_operation,
    workspace_memory_id,
)
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


def operations_response(operations: list[dict[str, str]]) -> list[ModelEvent]:
    return text_response(json.dumps({"operations": operations}, ensure_ascii=False))


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


def test_memory_template_markdown_files_are_package_resources() -> None:
    template_dir = files("xagent").joinpath("templates", "memory")

    assert template_dir.joinpath("user.md").is_file()
    assert template_dir.joinpath("soul.md").is_file()
    assert template_dir.joinpath("memory.md").is_file()


def test_memory_templates_match_section_allowlist() -> None:
    template_dir = files("xagent").joinpath("templates", "memory")

    assert _section_names(template_dir.joinpath("user.md").read_text(encoding="utf-8")) == SECTION_ALLOWLIST["user"]
    assert _section_names(template_dir.joinpath("soul.md").read_text(encoding="utf-8")) == SECTION_ALLOWLIST["soul"]
    assert (
        _section_names(template_dir.joinpath("memory.md").read_text(encoding="utf-8"))
        == SECTION_ALLOWLIST["workspace"]
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
    assert "## 个人信息" in store.user_path.read_text(encoding="utf-8")
    assert "## 沟通方式" in store.soul_path.read_text(encoding="utf-8")
    assert first_bundle.memory_path.name == "memory.md"
    assert first_bundle.memory_path.read_text(encoding="utf-8").startswith("# Workspace Memory")
    meta = json.loads(first_bundle.memory_path.with_name("meta.json").read_text(encoding="utf-8"))
    assert meta["workspace_path"] == str(first.resolve())


def test_memory_store_does_not_overwrite_existing_memory_files(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    paths = store.workspace_paths(workspace)
    paths.path.mkdir(parents=True)
    store.root.mkdir(parents=True, exist_ok=True)
    store.user_path.write_text("# User Memory\n\ncustom user\n", encoding="utf-8")
    store.soul_path.write_text("# Soul\n\ncustom soul\n", encoding="utf-8")
    paths.memory_path.write_text("# Workspace Memory\n\ncustom workspace\n", encoding="utf-8")

    store.ensure_workspace(workspace)

    assert store.user_path.read_text(encoding="utf-8") == "# User Memory\n\ncustom user\n"
    assert store.soul_path.read_text(encoding="utf-8") == "# Soul\n\ncustom soul\n"
    assert paths.memory_path.read_text(encoding="utf-8") == "# Workspace Memory\n\ncustom workspace\n"


def test_memory_operation_append_update_delete() -> None:
    contents = {
        "workspace": "# Workspace Memory\n\n## 架构决策\n\n- 旧边界\n",
        "user": "# User Memory\n\n## 工程偏好\n\n- 修改代码后需要跑测试。\n",
        "soul": "# Soul\n\n## 沟通方式\n\n- 回答可以更活泼一点。\n",
    }

    append = apply_memory_operation(
        contents=contents,
        raw_operation={
            "scope": "workspace",
            "op": "append",
            "section": "架构决策",
            "text": "- 三层运行边界是 `AgentLoop -> Agent -> AgentRunner`。",
        },
    )
    update = apply_memory_operation(
        contents=contents,
        raw_operation={
            "scope": "user",
            "op": "update",
            "section": "工程偏好",
            "old_text": "- 修改代码后需要跑测试。",
            "new_text": "- 修改代码后需要跑完整门禁。",
        },
    )
    delete = apply_memory_operation(
        contents=contents,
        raw_operation={
            "scope": "soul",
            "op": "delete",
            "section": "沟通方式",
            "text": "- 回答可以更活泼一点。",
        },
    )

    assert append["result"] == "applied"
    assert "AgentLoop" in contents["workspace"]
    assert update["result"] == "applied"
    assert "完整门禁" in contents["user"]
    assert delete["result"] == "applied"
    assert "更活泼" not in contents["soul"]


def test_memory_operation_append_creates_section_and_skips_duplicate() -> None:
    contents = {"user": "# User Memory\n"}

    first = apply_memory_operation(
        contents=contents,
        raw_operation={
            "scope": "user",
            "op": "append",
            "section": "交流偏好",
            "text": "- 使用中文交流。",
        },
    )
    second = apply_memory_operation(
        contents=contents,
        raw_operation={
            "scope": "user",
            "op": "append",
            "section": "交流偏好",
            "text": "- 使用中文交流。",
        },
    )

    assert first["result"] == "applied"
    assert "## 交流偏好" in contents["user"]
    assert second["result"] == "skipped_duplicate"


def test_memory_operation_appends_user_personal_info() -> None:
    contents = {
        "workspace": "# Workspace Memory\n\n## 项目定位\n\n",
        "user": "# User Memory\n\n## 个人信息\n\n",
    }

    result = apply_memory_operation(
        contents=contents,
        raw_operation={
            "scope": "user",
            "op": "append",
            "section": "个人信息",
            "text": "- 用户生日是 1 月 1 日。",
        },
    )

    assert result["result"] == "applied"
    assert "用户生日" in contents["user"]
    assert "用户生日" not in contents["workspace"]


def test_memory_operation_update_and_delete_skip_missing_ambiguous_and_invalid() -> None:
    contents = {
        "workspace": "# Workspace Memory\n\n## 当前约定\n\n- 重复\n- 重复\n",
        "user": "# User Memory\n\n## 工程偏好\n\n- 已有\n",
        "soul": "# Soul\n\n## 沟通方式\n\n- 已有\n",
    }

    missing = apply_memory_operation(
        contents=contents,
        raw_operation={
            "scope": "user",
            "op": "update",
            "section": "工程偏好",
            "old_text": "- 不存在",
            "new_text": "- 新文本",
        },
    )
    ambiguous = apply_memory_operation(
        contents=contents,
        raw_operation={
            "scope": "workspace",
            "op": "delete",
            "section": "当前约定",
            "text": "- 重复",
        },
    )
    invalid = apply_memory_operation(
        contents=contents,
        raw_operation={"scope": "soul", "op": "append", "section": "不存在", "text": "- x"},
    )

    assert missing["result"] == "skipped_not_found"
    assert ambiguous["result"] == "skipped_ambiguous"
    assert invalid["result"] == "skipped_invalid"


def test_memory_store_reads_legacy_dream_state_shapes(tmp_path) -> None:
    store = MemoryStore(tmp_path / "memory")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    paths = store.workspace_paths(workspace)
    store.ensure_workspace(workspace)
    store.write_dream_state(
        paths.workspace_id,
        {"sessions": {"cli:default": {"scopes": {"workspace": {"last_summary_id": "sum_1"}}}}},
    )

    assert store.last_dream_summary_id(workspace_path=workspace, session_id="cli:default") == "sum_1"

    store.write_dream_state(
        paths.workspace_id,
        {"sessions": {"cli:default": {"workspace_last_summary_id": "sum_2"}}},
    )

    assert store.last_dream_summary_id(workspace_path=workspace, session_id="cli:default") == "sum_2"


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
        [
            operations_response(
                [
                    {
                        "scope": "workspace",
                        "op": "append",
                        "section": "项目定位",
                        "text": "- 已更新",
                    },
                    {
                        "scope": "user",
                        "op": "append",
                        "section": "交流偏好",
                        "text": "- 使用中文交流",
                    },
                    {
                        "scope": "soul",
                        "op": "append",
                        "section": "沟通方式",
                        "text": "- 回答直接务实",
                    },
                ]
            )
        ],
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
    assert "使用中文交流" in store.user_path.read_text(encoding="utf-8")
    assert "回答直接务实" in store.soul_path.read_text(encoding="utf-8")
    assert memory_path.with_name("memory.md.bak").exists()
    assert store.user_backup_path.exists()
    assert store.soul_backup_path.exists()
    state = store.read_dream_state(workspace)
    assert state["sessions"]["test:room"]["last_summary_id"] == summary["summary_id"]
    assert "长期项目事实" in provider.requests[0].messages[-1]["content"]


@pytest.mark.asyncio
async def test_dream_command_can_update_user_personal_info(tmp_path, monkeypatch) -> None:
    agent_loop, _provider, workspace = make_agent_loop(
        tmp_path,
        monkeypatch,
        [
            operations_response(
                [
                    {
                        "scope": "user",
                        "op": "append",
                        "section": "个人信息",
                        "text": "- 用户生日是 1 月 1 日。",
                    }
                ]
            )
        ],
    )
    session = SessionStore(tmp_path / "sessions").open_for_chat(
        workspace_path=workspace,
        channel="test",
        chat_id="room",
    )
    session.append_summary("用户明确说自己的生日是 1 月 1 日。")
    bus = MessageBus()

    await bus.publish_inbound(InboundMessage(content="/dream", channel="test", chat_id="room"))
    await agent_loop.dispatch_once(bus)

    await bus.consume_outbound()
    assert (await bus.consume_outbound()).content == "dream done."
    store = agent_loop.memory_store
    assert store is not None
    workspace_memory = store.workspace_paths(workspace).memory_path.read_text(encoding="utf-8")
    assert "用户生日" in store.user_path.read_text(encoding="utf-8")
    assert "用户生日" not in workspace_memory


@pytest.mark.asyncio
async def test_dream_command_does_not_read_uncompacted_messages(tmp_path, monkeypatch) -> None:
    agent_loop, provider, workspace = make_agent_loop(
        tmp_path,
        monkeypatch,
        [operations_response([])],
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
            operations_response(
                [
                    {
                        "scope": "workspace",
                        "op": "append",
                        "section": "项目定位",
                        "text": "- forced",
                    }
                ]
            ),
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
async def test_dream_no_operations_advances_cursor_without_writing_memory(tmp_path, monkeypatch) -> None:
    agent_loop, _provider, workspace = make_agent_loop(
        tmp_path,
        monkeypatch,
        [operations_response([])],
    )
    session = SessionStore(tmp_path / "sessions").open_for_chat(
        workspace_path=workspace,
        channel="test",
        chat_id="room",
    )
    summary = session.append_summary("no durable memory")
    store = agent_loop.memory_store
    assert store is not None
    original_memory = store.load_bundle(workspace).workspace
    bus = MessageBus()

    await bus.publish_inbound(InboundMessage(content="/dream", channel="test", chat_id="room"))
    await agent_loop.dispatch_once(bus)

    await bus.consume_outbound()
    assert (await bus.consume_outbound()).content == "dream done."
    assert store.workspace_paths(workspace).memory_path.read_text(encoding="utf-8") == original_memory
    assert store.read_dream_state(workspace)["sessions"]["test:room"]["last_summary_id"] == summary["summary_id"]


@pytest.mark.asyncio
async def test_dream_invalid_json_returns_error_and_does_not_advance_cursor(tmp_path, monkeypatch) -> None:
    agent_loop, _provider, workspace = make_agent_loop(tmp_path, monkeypatch, [text_response("not json")])
    session = SessionStore(tmp_path / "sessions").open_for_chat(
        workspace_path=workspace,
        channel="test",
        chat_id="room",
    )
    session.append_summary("summary")
    bus = MessageBus()

    await bus.publish_inbound(InboundMessage(content="/dream", channel="test", chat_id="room"))
    await agent_loop.dispatch_once(bus)

    assert (await bus.consume_outbound()).content == "dreaming..."
    error = await bus.consume_outbound()
    assert error.metadata["error"] is True
    assert "valid JSON" in error.content
    store = agent_loop.memory_store
    assert store is not None
    assert store.read_dream_state(workspace)["sessions"] == {}


@pytest.mark.asyncio
async def test_unknown_slash_command_returns_help_without_agent_run(tmp_path, monkeypatch) -> None:
    agent_loop, provider, _workspace = make_agent_loop(tmp_path, monkeypatch, [])
    bus = MessageBus()

    await bus.publish_inbound(InboundMessage(content="/unknown", channel="test", chat_id="room"))
    await agent_loop.dispatch_once(bus)

    event = await bus.consume_outbound()
    assert "Available commands" in event.content
    assert provider.requests == []


def _section_names(content: str) -> set[str]:
    return {line.removeprefix("## ").strip() for line in content.splitlines() if line.startswith("## ")}
