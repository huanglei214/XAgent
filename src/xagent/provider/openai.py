import os
import json
from typing import Any, AsyncIterator

from openai import AsyncOpenAI

from xagent.bus.types import Message, ModelConfig, ModelRequest, TextPart, ToolUsePart


class OpenAIChatProvider:
    """OpenAI-compatible chat completion provider."""

    def __init__(self, config: ModelConfig) -> None:
        self.provider_name = config.provider
        api_key = os.getenv(config.api_key_env)
        if not api_key:
            raise ValueError(
                f"Environment variable {config.api_key_env} is not set. "
                "Set it before running XAgent."
            )

        client_kwargs: dict[str, Any] = {"api_key": api_key}
        if config.base_url:
            client_kwargs["base_url"] = config.base_url
        self._client = AsyncOpenAI(**client_kwargs)

    async def complete(self, request: ModelRequest) -> Message:
        response = await self._client.chat.completions.create(
            model=request.model,
            messages=_to_openai_messages(request),
            tools=request.tools or None,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
        )
        return _from_openai_message(response.choices[0].message)

    async def stream_complete(self, request: ModelRequest) -> AsyncIterator[Message]:
        stream = await self._client.chat.completions.create(
            model=request.model,
            messages=_to_openai_messages(request),
            tools=request.tools or None,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            stream=True,
        )

        text_parts: list[str] = []
        tool_calls: dict[int, dict[str, Any]] = {}

        async for chunk in stream:
            choice = chunk.choices[0] if chunk.choices else None
            delta = choice.delta if choice else None
            if delta is None:
                continue

            if delta.content:
                text_parts.append(delta.content)

            for tool_call in delta.tool_calls or []:
                index = getattr(tool_call, "index", 0) or 0
                entry = tool_calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                if tool_call.id:
                    entry["id"] = tool_call.id
                if tool_call.function:
                    if tool_call.function.name:
                        entry["name"] = tool_call.function.name
                    if tool_call.function.arguments:
                        entry["arguments"] += tool_call.function.arguments

            yield _snapshot_message("".join(text_parts), tool_calls)

    async def stream_text(self, request: ModelRequest) -> AsyncIterator[str]:
        stream = await self._client.chat.completions.create(
            model=request.model,
            messages=_to_openai_messages(request),
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            stream=True,
        )
        async for chunk in stream:
            choice = chunk.choices[0] if chunk.choices else None
            delta = choice.delta.content if choice and choice.delta else None
            if delta:
                yield delta


def _to_openai_messages(request: ModelRequest) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for message in request.messages:
        if message.role in ("system", "user"):
            text = "".join(part.text for part in message.content if part.type == "text")
            converted.append({"role": message.role, "content": text})
            continue

        if message.role == "assistant":
            text = "".join(part.text for part in message.content if part.type == "text")
            tool_calls = []
            for part in message.content:
                if isinstance(part, ToolUsePart):
                    tool_calls.append(
                        {
                            "id": part.id,
                            "type": "function",
                            "function": {
                                "name": part.name,
                                "arguments": json.dumps(part.input),
                            },
                        }
                    )

            payload: dict[str, Any] = {"role": "assistant", "content": text or None}
            if tool_calls:
                payload["tool_calls"] = tool_calls
            converted.append(payload)
            continue

        tool_part = next((part for part in message.content if part.type == "tool_result"), None)
        if tool_part is None:
            continue
        converted.append(
            {
                "role": "tool",
                "tool_call_id": tool_part.tool_use_id,
                "content": tool_part.content,
            }
        )
    return converted


def _from_openai_message(message: Any) -> Message:
    content = []
    if message.content:
        content.append(TextPart(text=message.content))
    for tool_call in message.tool_calls or []:
        arguments = tool_call.function.arguments or "{}"
        content.append(
            ToolUsePart(
                id=tool_call.id,
                name=tool_call.function.name,
                input=json.loads(arguments),
            )
        )
    return Message(role="assistant", content=content)


def _snapshot_message(text: str, tool_calls: dict[int, dict[str, Any]]) -> Message:
    content = []
    if text:
        content.append(TextPart(text=text))

    for index in sorted(tool_calls):
        item = tool_calls[index]
        parsed_input: dict[str, Any] = {}
        if item["arguments"]:
            try:
                parsed_input = json.loads(item["arguments"])
            except Exception:
                parsed_input = {}
        if item["id"] and item["name"]:
            content.append(
                ToolUsePart(
                    id=item["id"],
                    name=item["name"],
                    input=parsed_input,
                )
            )
    return Message(role="assistant", content=content)
