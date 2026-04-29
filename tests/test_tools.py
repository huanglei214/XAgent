from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Any

import pytest

from xagent.agent.permissions import SessionApprover
from xagent.agent.tools import Tool, ToolRegistry, ToolResult, build_default_tools, tool


def test_tool_decorator_exports_openai_function_schema(tmp_path) -> None:
    registry = build_default_tools(
        workspace=tmp_path,
        approver=SessionApprover(default_allow=True),
        ask_user=lambda question: "answer",
    )

    read_schema = next(item for item in registry.openai_tools() if item["function"]["name"] == "read_file")

    assert read_schema["type"] == "function"
    assert read_schema["function"]["parameters"]["required"] == ["path"]


@pytest.mark.asyncio
async def test_read_and_patch_tools_use_injected_workspace_and_approver(tmp_path) -> None:
    (tmp_path / "note.txt").write_text("hello old", encoding="utf-8")
    registry = build_default_tools(
        workspace=tmp_path,
        approver=SessionApprover(default_allow=True),
        ask_user=lambda question: "answer",
    )

    read = registry.prepare(
        {
            "id": "call_read",
            "function": {"name": "read_file", "arguments": '{"path": "note.txt"}'},
        }
    )
    patch = registry.prepare(
        {
            "id": "call_patch",
            "function": {
                "name": "apply_patch",
                "arguments": '{"path": "note.txt", "old": "old", "new": "new"}',
            },
        }
    )

    read_result = await registry.execute(read)
    patch_result = await registry.execute(patch)

    assert "hello old" in read_result.result.content
    assert patch_result.result.is_error is False
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "hello new"


@pytest.mark.asyncio
async def test_argument_parse_error_returns_tool_error() -> None:
    registry = ToolRegistry()
    prepared = registry.prepare(
        {
            "id": "call_bad",
            "function": {"name": "missing", "arguments": "{not-json"},
        }
    )

    execution = await registry.execute(prepared)

    assert execution.result.is_error is True
    assert "Could not parse arguments" in execution.result.content


@tool(
    name="slow_read",
    description="Slow read-only tool.",
    read_only=True,
    parameters={"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]},
)
class SlowReadTool(Tool):
    def __init__(self, seen: list[str]) -> None:
        self.seen = seen

    async def execute(self, value: str) -> ToolResult:
        await asyncio.sleep(0.2)
        self.seen.append(value)
        return ToolResult.ok(value)


@tool(
    name="slow_write",
    description="Slow exclusive tool.",
    exclusive=True,
    parameters={"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]},
)
class SlowWriteTool(Tool):
    def __init__(self, seen: list[str]) -> None:
        self.seen = seen

    async def execute(self, value: str) -> ToolResult:
        await asyncio.sleep(0.2)
        self.seen.append(value)
        return ToolResult.ok(value)


@pytest.mark.asyncio
async def test_read_only_tools_parallelize_but_exclusive_tools_serialize() -> None:
    seen: list[str] = []
    registry = ToolRegistry()
    registry.register(SlowReadTool(seen))
    registry.register(SlowWriteTool(seen))
    calls: list[dict[str, Any]] = [
        {"id": "r1", "function": {"name": "slow_read", "arguments": '{"value": "a"}'}},
        {"id": "r2", "function": {"name": "slow_read", "arguments": '{"value": "b"}'}},
        {"id": "w1", "function": {"name": "slow_write", "arguments": '{"value": "c"}'}},
        {"id": "w2", "function": {"name": "slow_write", "arguments": '{"value": "d"}'}},
    ]

    started = perf_counter()
    await registry.execute_many([registry.prepare(call) for call in calls])
    elapsed = perf_counter() - started

    assert elapsed < 0.7
    assert seen[-2:] == ["c", "d"]
