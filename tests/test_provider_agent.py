from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from xagent.agent import Agent, AgentError
from xagent.agent.permissions import SessionApprover
from xagent.agent.tools import ToolRegistry, build_default_tools
from xagent.providers import ModelEvent, ModelRequest
from xagent.providers.util import MessageBuilder
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
    return [ModelEvent.text_delta(text, raw={"delta": text}), ModelEvent.message_done()]


def tool_response(name: str, arguments: str, *, call_id: str = "call_1") -> list[ModelEvent]:
    return [
        ModelEvent.tool_call_delta(
            {
                "index": 0,
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": arguments},
            },
            raw={"tool": name},
        ),
        ModelEvent.message_done(),
    ]


def make_agent(tmp_path: Path, provider: ScriptedProvider, registry: ToolRegistry | None = None) -> Agent:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    session = SessionStore(tmp_path / "sessions").create(workspace_path=workspace)
    tools = registry or build_default_tools(
        workspace=workspace,
        approver=SessionApprover(default_allow=True),
        ask_user=lambda question: "answer",
    )
    return Agent(
        provider=provider,
        model="test-model",
        session=session,
        tools=tools,
        max_duration_seconds=10,
    )


def test_message_builder_assembles_text_and_tool_calls() -> None:
    builder = MessageBuilder()
    for event in [
        ModelEvent.text_delta("hello "),
        ModelEvent.text_delta("world"),
        ModelEvent.tool_call_delta(
            {
                "index": 0,
                "id": "call_1",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path": "README.md"}'},
            }
        ),
    ]:
        builder.apply(event)

    message = builder.final_message()

    assert message["content"] == "hello world"
    assert message["tool_calls"][0]["function"]["name"] == "read_file"


@pytest.mark.asyncio
async def test_agent_records_plain_text_response(tmp_path) -> None:
    provider = ScriptedProvider([text_response("hello")])
    agent = make_agent(tmp_path, provider)

    final = await agent.run("hi")

    assert final["content"] == "hello"
    system_prompt = provider.requests[0].messages[0]
    assert system_prompt["role"] == "system"
    assert "You are XAgent" in system_prompt["content"]
    assert "<identity>" in system_prompt["content"]
    assert "<runtime_context>" in system_prompt["content"]
    assert "<tool_use>" in system_prompt["content"]
    assert str(agent.session.workspace_path) in system_prompt["content"]
    assert agent.session.session_id in system_prompt["content"]
    assert "test-model" in system_prompt["content"]
    assert "shell blacklist" in system_prompt["content"]
    assert "parameters" not in system_prompt["content"]
    records = agent.session.read_records()
    assert records[1]["message"] == {"role": "user", "content": "hi"}
    assert records[2]["message"] == {"role": "assistant", "content": "hello"}
    trace_text = agent.session.trace_path.read_text(encoding="utf-8")
    assert "model_request" in trace_text
    assert "model_final" in trace_text
    assert "model_event" not in trace_text


@pytest.mark.asyncio
async def test_agent_can_trace_model_stream_events_when_enabled(tmp_path) -> None:
    provider = ScriptedProvider([text_response("hello")])
    agent = make_agent(tmp_path, provider)
    agent.trace_model_events = True

    await agent.run("hi")

    assert "model_event" in agent.session.trace_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_agent_executes_tool_then_continues_react_loop(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "README.md").write_text("hello from file", encoding="utf-8")
    session = SessionStore(tmp_path / "sessions").create(workspace_path=workspace)
    tools = build_default_tools(
        workspace=workspace,
        approver=SessionApprover(default_allow=True),
        ask_user=lambda question: "answer",
    )
    provider = ScriptedProvider(
        [
            tool_response("read_file", '{"path": "README.md"}'),
            text_response("done"),
        ]
    )
    agent = Agent(provider=provider, model="test-model", session=session, tools=tools)

    final = await agent.run("read it")

    assert final["content"] == "done"
    messages = [record["message"] for record in session.read_records() if record["type"] == "message"]
    assert messages[1]["tool_calls"][0]["function"]["name"] == "read_file"
    assert messages[2]["role"] == "tool"
    assert "hello from file" in messages[2]["content"]


@pytest.mark.asyncio
async def test_tool_argument_parse_error_is_returned_to_model(tmp_path) -> None:
    provider = ScriptedProvider(
        [
            tool_response("read_file", "{bad"),
            text_response("fixed"),
        ]
    )
    agent = make_agent(tmp_path, provider)

    final = await agent.run("bad tool")

    assert final["content"] == "fixed"
    tool_messages = [
        record["message"]
        for record in agent.session.read_records()
        if record.get("message", {}).get("role") == "tool"
    ]
    assert "Could not parse arguments" in tool_messages[0]["content"]


@pytest.mark.asyncio
async def test_blacklisted_shell_command_returns_tool_error_and_trace(tmp_path) -> None:
    provider = ScriptedProvider(
        [
            tool_response("shell", '{"command": "rm -rf tmp"}'),
            text_response("done"),
        ]
    )
    agent = make_agent(tmp_path, provider)

    final = await agent.run("run shell")

    assert final["content"] == "done"
    tool_messages = [
        record["message"]
        for record in agent.session.read_records()
        if record.get("message", {}).get("role") == "tool"
    ]
    assert "blacklist rule: rm" in tool_messages[0]["content"]
    trace_lines = [json.loads(line) for line in agent.session.trace_path.read_text().splitlines()]
    shell_trace = next(
        line for line in trace_lines if line.get("type") == "tool_result" and line.get("name") == "shell"
    )
    assert shell_trace["is_error"] is True
    assert "blacklist rule: rm" in shell_trace["content"]


@pytest.mark.asyncio
async def test_empty_final_response_gets_one_retry(tmp_path) -> None:
    provider = ScriptedProvider(
        [
            [ModelEvent.message_done({"role": "assistant", "content": ""})],
            text_response("non-empty"),
        ]
    )
    agent = make_agent(tmp_path, provider)

    final = await agent.run("say something")

    assert final["content"] == "non-empty"
    assert len(provider.requests) == 2
    assert (
        provider.requests[1].messages[-1]["content"]
        == "Your previous response was empty. Provide a final answer."
    )


@pytest.mark.asyncio
async def test_context_compaction_uses_summary_prompt_template(tmp_path) -> None:
    provider = ScriptedProvider(
        [
            text_response("summary"),
            text_response("final"),
        ]
    )
    agent = make_agent(tmp_path, provider)
    agent.context_char_threshold = 1

    final = await agent.run("please summarize")

    assert final["content"] == "final"
    compaction_prompt = provider.requests[0].messages[0]
    assert compaction_prompt["role"] == "system"
    assert "Summarize the current task state" in compaction_prompt["content"]
    assert "<summary_goal>" in compaction_prompt["content"]
    assert "<must_include>" in compaction_prompt["content"]
    assert "<summary_style>" in compaction_prompt["content"]
    assert provider.requests[0].metadata["purpose"] == "compaction"


@pytest.mark.asyncio
async def test_repeated_tool_calls_stop_loop(tmp_path) -> None:
    provider = ScriptedProvider(
        [
            tool_response("read_file", '{"path": "missing.txt"}'),
            tool_response("read_file", '{"path": "missing.txt"}'),
            tool_response("read_file", '{"path": "missing.txt"}'),
            tool_response("read_file", '{"path": "missing.txt"}'),
        ]
    )
    agent = make_agent(tmp_path, provider)
    agent.max_repeated_tool_calls = 2

    with pytest.raises(AgentError, match="repeated tool"):
        await agent.run("loop")


@pytest.mark.asyncio
async def test_usage_event_is_traced(tmp_path) -> None:
    provider = ScriptedProvider(
        [
            [
                ModelEvent.text_delta("hello"),
                ModelEvent.usage_event({"total_tokens": 3}, raw={"usage": True}),
                ModelEvent.message_done(),
            ]
        ]
    )
    agent = make_agent(tmp_path, provider)

    await agent.run("hi")
    trace_lines = [json.loads(line) for line in agent.session.trace_path.read_text().splitlines()]

    assert any(line.get("usage") == {"total_tokens": 3} for line in trace_lines)
