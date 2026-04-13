from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable, Optional, Type

from pydantic import BaseModel


class ToolContext(BaseModel):
    cwd: str


class ToolResult(BaseModel):
    content: str
    is_error: bool = False


class Tool:
    def __init__(
        self,
        name: str,
        description: str,
        input_model: Type[BaseModel],
        handler: Callable[[BaseModel, ToolContext], Awaitable[ToolResult] | ToolResult],
    ) -> None:
        self.name = name
        self.description = description
        self.input_model = input_model
        self._handler = handler

    def to_openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_model.model_json_schema(),
            },
        }

    async def invoke(self, raw_input: dict[str, Any], ctx: ToolContext) -> ToolResult:
        parsed = self.input_model.model_validate(raw_input)
        result = self._handler(parsed, ctx)
        if inspect.isawaitable(result):
            result = await result
        return result


def find_tool(tools: list[Tool], name: str) -> Optional[Tool]:
    for tool in tools:
        if tool.name == name:
            return tool
    return None
