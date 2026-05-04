from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from xagent.agent.runner import AgentError, AgentRunSpec, AgentRunner
from xagent.agent.tools.base import Tool, ToolResult, tool
from xagent.agent.tools.registry import ToolRegistry
from xagent.providers import ModelEvent, ModelRequest


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


@tool(
    name="echo",
    description="Echo text.",
    parameters={
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
    read_only=True,
)
class EchoTool(Tool):
    def execute(self, text: str) -> ToolResult:
        return ToolResult.ok(f"echo:{text}")


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


def make_registry(*tools: Tool) -> ToolRegistry:
    registry = ToolRegistry()
    for item in tools:
        registry.register(item)
    return registry


def make_spec(
    *,
    tools: ToolRegistry | None = None,
    messages: list[dict[str, Any]] | None = None,
    **kwargs: Any,
) -> AgentRunSpec:
    values: dict[str, Any] = {
        "model": "runner-model",
        "messages": messages or [{"role": "user", "content": "hi"}],
        "tools": tools or ToolRegistry(),
        "max_steps": 5,
        "max_duration_seconds": 10,
        "max_repeated_tool_calls": 3,
        "empty_retry_message": "retry please",
    }
    values.update(kwargs)
    return AgentRunSpec(**values)


@pytest.mark.asyncio
async def test_runner_returns_plain_text_response() -> None:
    provider = ScriptedProvider([text_response("hello")])
    runner = AgentRunner(provider)

    result = await runner.run(make_spec())

    assert result.final_message["content"] == "hello"
    assert result.steps == 1
    assert result.stop_reason == "completed"
    assert provider.requests[0].model == "runner-model"


@pytest.mark.asyncio
async def test_runner_executes_tool_then_continues_react_loop() -> None:
    provider = ScriptedProvider(
        [
            tool_response("echo", '{"text": "hello"}'),
            text_response("done"),
        ]
    )
    messages: list[dict[str, Any]] = []
    runner = AgentRunner(provider)

    result = await runner.run(
        make_spec(
            tools=make_registry(EchoTool()),
            on_message=messages.append,
        )
    )

    assert result.final_message["content"] == "done"
    assert result.tools_used == ["echo"]
    assert messages[0]["tool_calls"][0]["function"]["name"] == "echo"
    assert messages[1]["role"] == "tool"
    assert messages[1]["content"] == "echo:hello"
    assert provider.requests[1].messages[-1]["role"] == "tool"


@pytest.mark.asyncio
async def test_runner_returns_tool_argument_parse_error_to_model() -> None:
    provider = ScriptedProvider(
        [
            tool_response("echo", "{bad"),
            text_response("fixed"),
        ]
    )
    messages: list[dict[str, Any]] = []
    runner = AgentRunner(provider)

    result = await runner.run(
        make_spec(
            tools=make_registry(EchoTool()),
            on_message=messages.append,
        )
    )

    assert result.final_message["content"] == "fixed"
    assert "Could not parse arguments" in messages[1]["content"]


@pytest.mark.asyncio
async def test_runner_returns_unregistered_tool_to_model() -> None:
    provider = ScriptedProvider(
        [
            tool_response("missing_tool", "{}"),
            text_response("recovered"),
        ]
    )
    messages: list[dict[str, Any]] = []
    runner = AgentRunner(provider)

    result = await runner.run(make_spec(on_message=messages.append))

    assert result.final_message["content"] == "recovered"
    assert "Tool 'missing_tool' is not registered." in messages[1]["content"]


@pytest.mark.asyncio
async def test_runner_retries_empty_response_once() -> None:
    provider = ScriptedProvider(
        [
            [ModelEvent.message_done({"role": "assistant", "content": ""})],
            text_response("non-empty"),
        ]
    )
    messages: list[dict[str, Any]] = []
    runner = AgentRunner(provider)

    result = await runner.run(make_spec(on_message=messages.append))

    assert result.final_message["content"] == "non-empty"
    assert messages[1] == {"role": "user", "content": "retry please"}
    assert provider.requests[1].messages[-1] == {"role": "user", "content": "retry please"}


@pytest.mark.asyncio
async def test_runner_stops_on_repeated_tool_calls() -> None:
    provider = ScriptedProvider(
        [
            tool_response("echo", '{"text": "loop"}'),
            tool_response("echo", '{"text": "loop"}'),
            tool_response("echo", '{"text": "loop"}'),
        ]
    )
    runner = AgentRunner(provider)

    with pytest.raises(AgentError, match="repeated tool"):
        await runner.run(
            make_spec(
                tools=make_registry(EchoTool()),
                max_repeated_tool_calls=2,
            )
        )


@pytest.mark.asyncio
async def test_runner_stops_on_max_steps() -> None:
    provider = ScriptedProvider([tool_response("echo", '{"text": "again"}')])
    runner = AgentRunner(provider)

    with pytest.raises(AgentError, match="maximum number of steps"):
        await runner.run(
            make_spec(
                tools=make_registry(EchoTool()),
                max_steps=1,
            )
        )


@pytest.mark.asyncio
async def test_runner_callback_order() -> None:
    provider = ScriptedProvider(
        [
            tool_response("echo", '{"text": "hello"}'),
            [
                ModelEvent.text_delta("done"),
                ModelEvent.usage_event({"total_tokens": 3}),
                ModelEvent.message_done(),
            ],
        ]
    )
    order: list[str] = []

    def on_trace(kind: str, payload: dict[str, Any]) -> None:
        del payload
        order.append(f"trace:{kind}")

    def on_message(message: dict[str, Any]) -> None:
        order.append(f"message:{message['role']}")

    async def on_event(event: ModelEvent) -> None:
        order.append(f"event:{event.kind}")

    runner = AgentRunner(provider)

    await runner.run(
        make_spec(
            tools=make_registry(EchoTool()),
            on_event=on_event,
            on_trace=on_trace,
            on_message=on_message,
        )
    )

    assert order == [
        "trace:model_request",
        "event:tool_call_delta",
        "event:message_done",
        "trace:model_final",
        "message:assistant",
        "trace:tool_prepare",
        "trace:tool_result",
        "message:tool",
        "trace:model_request",
        "event:text_delta",
        "trace:model_usage",
        "event:usage",
        "event:message_done",
        "trace:model_final",
        "message:assistant",
    ]
