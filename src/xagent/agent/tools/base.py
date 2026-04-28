from __future__ import annotations

import inspect
from typing import Any, Awaitable, Callable, Optional, TypeVar

from pydantic import BaseModel, Field

from xagent.agent.errors import WorkspaceEscapeError


class ToolContext(BaseModel):
    cwd: str
    request_path_access: Any = None
    allowed_external_paths: set[str] = Field(default_factory=set)


class ToolResult(BaseModel):
    content: str = ""
    is_error: bool = False
    summary: Optional[str] = None
    data: Any = None
    error: Optional[str] = None
    code: Optional[str] = None
    details: Any = None

    @classmethod
    def ok(
        cls,
        summary: str,
        *,
        content: Optional[str] = None,
        data: Any = None,
        details: Any = None,
    ) -> ToolResult:
        return cls(content=content or summary, is_error=False, summary=summary, data=data, details=details)

    @classmethod
    def fail(
        cls,
        error: str,
        *,
        summary: Optional[str] = None,
        code: Optional[str] = None,
        content: Optional[str] = None,
        details: Any = None,
    ) -> ToolResult:
        return cls(
            content=content or error,
            is_error=True,
            summary=summary or error,
            error=error,
            code=code,
            details=details,
        )


ToolInputT = TypeVar("ToolInputT", bound=BaseModel)


class Tool:
    """Wraps a named, typed handler into an OpenAI-compatible tool definition."""

    def __init__(
        self,
        name: str,
        description: str,
        input_model: type[ToolInputT],
        handler: Callable[[ToolInputT, ToolContext], Awaitable[ToolResult] | ToolResult],
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
        try:
            result = self._handler(parsed, ctx)
            if inspect.isawaitable(result):
                result = await result
            return result
        except WorkspaceEscapeError as exc:
            return ToolResult.fail(str(exc), code="WORKSPACE_ESCAPE")
        except Exception:
            raise


def find_tool(tools: list[Tool], name: str) -> Optional[Tool]:
    """Look up a Tool by name in a list."""
    for tool in tools:
        if tool.name == name:
            return tool
    return None
