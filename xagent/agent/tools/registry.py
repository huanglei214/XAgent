from __future__ import annotations

import asyncio
import inspect
import json
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from xagent.agent.tools.base import Tool, ToolResult


@dataclass
class PreparedToolCall:
    call_id: str
    name: str
    arguments: dict[str, Any]
    tool: Tool | None = None
    parse_error: str | None = None


@dataclass
class ToolExecution:
    call_id: str
    name: str
    arguments: dict[str, Any]
    result: ToolResult
    duration_seconds: float

    def to_message(self) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": self.call_id,
            "content": self.result.content,
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool {tool.name!r} is already registered")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def openai_tools(self) -> list[dict[str, Any]]:
        return [tool.to_openai_tool() for tool in self._tools.values()]

    def prepare(self, tool_call: dict[str, Any]) -> PreparedToolCall:
        call_id = str(tool_call.get("id") or "")
        function = tool_call.get("function") or {}
        name = str(function.get("name") or "")
        raw_arguments = function.get("arguments") or "{}"
        tool = self.get(name)
        try:
            parsed = json.loads(raw_arguments)
            if not isinstance(parsed, dict):
                raise ValueError("function arguments must decode to an object")
        except Exception as exc:
            return PreparedToolCall(
                call_id=call_id,
                name=name,
                arguments={},
                tool=tool,
                parse_error=f"Could not parse arguments for tool {name!r}: {exc}",
            )
        if tool is None:
            return PreparedToolCall(
                call_id=call_id,
                name=name,
                arguments=parsed,
                parse_error=f"Tool {name!r} is not registered.",
            )
        validation_error = _validate_arguments(tool, parsed)
        if validation_error is not None:
            return PreparedToolCall(
                call_id=call_id,
                name=name,
                arguments=parsed,
                tool=tool,
                parse_error=validation_error,
            )
        return PreparedToolCall(call_id=call_id, name=name, arguments=parsed, tool=tool)

    async def execute(self, prepared: PreparedToolCall) -> ToolExecution:
        started = perf_counter()
        if prepared.parse_error is not None or prepared.tool is None:
            result = ToolResult.fail(prepared.parse_error or "Tool is not available.")
        else:
            try:
                execute = getattr(prepared.tool, "execute")
                maybe = execute(**prepared.arguments)
                if inspect.isawaitable(maybe):
                    result = await maybe
                else:
                    result = maybe
            except Exception as exc:  # noqa: BLE001 - tool errors are model-visible
                result = ToolResult.fail(f"Tool {prepared.name!r} failed: {type(exc).__name__}: {exc}")
        return ToolExecution(
            call_id=prepared.call_id,
            name=prepared.name,
            arguments=prepared.arguments,
            result=result,
            duration_seconds=perf_counter() - started,
        )

    async def execute_many(self, prepared_calls: list[PreparedToolCall]) -> list[ToolExecution]:
        results: list[ToolExecution] = []
        read_batch: list[PreparedToolCall] = []

        async def flush_read_batch() -> None:
            nonlocal read_batch
            if not read_batch:
                return
            results.extend(await asyncio.gather(*(self.execute(call) for call in read_batch)))
            read_batch = []

        for prepared in prepared_calls:
            tool = prepared.tool
            can_parallelize = (
                prepared.parse_error is None
                and tool is not None
                and tool.read_only
                and not tool.exclusive
            )
            if can_parallelize:
                read_batch.append(prepared)
                continue
            await flush_read_batch()
            results.append(await self.execute(prepared))
        await flush_read_batch()
        return results


def _validate_arguments(tool: Tool, arguments: dict[str, Any]) -> str | None:
    schema = tool.definition.parameters
    for name in schema.get("required") or []:
        if name not in arguments:
            return f"Missing required argument {name!r} for tool {tool.name!r}."
    properties = schema.get("properties") or {}
    for name, value in arguments.items():
        expected = properties.get(name, {}).get("type")
        if expected and not _matches_json_type(value, expected):
            return f"Argument {name!r} for tool {tool.name!r} must be {expected}."
    return None


def _matches_json_type(value: Any, expected: str | list[str]) -> bool:
    if isinstance(expected, list):
        return any(_matches_json_type(value, item) for item in expected)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "null":
        return value is None
    return True
