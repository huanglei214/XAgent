from typing import Any, Literal, Union

from pydantic import BaseModel


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
    parts = []
    for part in message.content:
        if isinstance(part, TextPart):
            parts.append(part.text)
    return "".join(parts)
