from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Literal, Protocol

ModelEventKind = Literal["text_delta", "tool_call_delta", "message_done", "usage"]


@dataclass
class ModelRequest:
    model: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] = field(default_factory=list)
    temperature: float | None = None
    max_tokens: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_openai_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": self.messages,
        }
        if self.tools:
            kwargs["tools"] = self.tools
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.max_tokens is not None:
            kwargs["max_tokens"] = self.max_tokens
        return kwargs


@dataclass
class ModelEvent:
    kind: ModelEventKind
    text: str = ""
    tool_call: dict[str, Any] | None = None
    message: dict[str, Any] | None = None
    usage: dict[str, Any] | None = None
    raw: Any = None

    @classmethod
    def text_delta(cls, text: str, *, raw: Any = None) -> "ModelEvent":
        return cls(kind="text_delta", text=text, raw=raw)

    @classmethod
    def tool_call_delta(cls, tool_call: dict[str, Any], *, raw: Any = None) -> "ModelEvent":
        return cls(kind="tool_call_delta", tool_call=tool_call, raw=raw)

    @classmethod
    def message_done(cls, message: dict[str, Any] | None = None, *, raw: Any = None) -> "ModelEvent":
        return cls(kind="message_done", message=message, raw=raw)

    @classmethod
    def usage_event(cls, usage: dict[str, Any], *, raw: Any = None) -> "ModelEvent":
        return cls(kind="usage", usage=usage, raw=raw)


class Provider(Protocol):
    def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        ...
