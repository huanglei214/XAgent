from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    read_only: bool = False
    exclusive: bool = False

    def to_openai_tool(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass
class ToolResult:
    content: str
    is_error: bool = False
    data: Any = None

    @classmethod
    def ok(cls, content: str, *, data: Any = None) -> "ToolResult":
        return cls(content=content, data=data)

    @classmethod
    def fail(cls, content: str, *, data: Any = None) -> "ToolResult":
        return cls(content=content, is_error=True, data=data)


class Tool:
    definition: ToolDefinition

    @property
    def name(self) -> str:
        return self.definition.name

    @property
    def read_only(self) -> bool:
        return self.definition.read_only

    @property
    def exclusive(self) -> bool:
        return self.definition.exclusive

    def to_openai_tool(self) -> dict[str, Any]:
        return self.definition.to_openai_tool()


def tool(
    *,
    name: str,
    description: str,
    parameters: dict[str, Any],
    read_only: bool = False,
    exclusive: bool = False,
) -> Callable[[type[Tool]], type[Tool]]:
    def decorate(cls: type[Tool]) -> type[Tool]:
        cls.definition = ToolDefinition(
            name=name,
            description=description,
            parameters=parameters,
            read_only=read_only,
            exclusive=exclusive,
        )
        return cls

    return decorate
