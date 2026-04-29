from __future__ import annotations

from typing import Any

from xagent.providers.types import ModelEvent


def safe_model_dump(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return value


class MessageBuilder:
    """Assemble OpenAI-style assistant messages from provider stream events."""

    def __init__(self) -> None:
        self._text_parts: list[str] = []
        self._tool_calls: dict[int, dict[str, Any]] = {}

    def apply(self, event: ModelEvent) -> None:
        if event.kind == "text_delta":
            self._text_parts.append(event.text)
        elif event.kind == "tool_call_delta" and event.tool_call is not None:
            self._apply_tool_delta(event.tool_call)

    def _apply_tool_delta(self, delta: dict[str, Any]) -> None:
        index = int(delta.get("index", len(self._tool_calls)))
        current = self._tool_calls.setdefault(
            index,
            {
                "id": delta.get("id") or f"call_{index}",
                "type": "function",
                "function": {"name": "", "arguments": ""},
            },
        )
        if delta.get("id"):
            current["id"] = delta["id"]
        if delta.get("type"):
            current["type"] = delta["type"]
        function_delta = delta.get("function") or {}
        if function_delta.get("name"):
            current["function"]["name"] += str(function_delta["name"])
        if function_delta.get("arguments"):
            current["function"]["arguments"] += str(function_delta["arguments"])

    def final_message(self) -> dict[str, Any]:
        content = "".join(self._text_parts)
        message: dict[str, Any] = {"role": "assistant", "content": content}
        if self._tool_calls:
            message["tool_calls"] = [self._tool_calls[index] for index in sorted(self._tool_calls)]
        return message
