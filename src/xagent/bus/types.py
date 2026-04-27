from __future__ import annotations

from typing import Any, AsyncIterator, Literal, Optional, Protocol, Union

from pydantic import BaseModel, Field


class TextPart(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ToolUsePart(BaseModel):
    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any]


class ToolResultPart(BaseModel):
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str
    is_error: bool = False


ContentPart = Union[TextPart, ToolUsePart, ToolResultPart]


class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: list[ContentPart]


def message_text(message: Message) -> str:
    """Extract plain text from a Message by concatenating all TextPart entries."""
    parts = []
    for part in message.content:
        if isinstance(part, TextPart):
            parts.append(part.text)
    return "".join(parts)


ProviderName = Literal["openai", "anthropic", "ark"]


class ModelConfig(BaseModel):
    name: str = Field(min_length=1)
    provider: ProviderName = "openai"
    base_url: Optional[str] = None
    api_key_env: str = Field(min_length=1)


class ModelRequest(BaseModel):
    model: str
    messages: list[Message]
    tools: list[dict] = Field(default_factory=list)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=None, ge=1)


class ModelProvider(Protocol):
    async def stream_complete(self, request: ModelRequest) -> AsyncIterator[Message]:
        ...

    async def complete(self, request: ModelRequest) -> Message:
        ...

    async def stream_text(self, request: ModelRequest) -> AsyncIterator[str]:
        ...
