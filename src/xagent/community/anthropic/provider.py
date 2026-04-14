from __future__ import annotations

import json
import os
from typing import Any, AsyncIterator

from anthropic import AsyncAnthropic

from xagent.foundation.messages import Message, TextPart, ToolUsePart, message_text
from xagent.foundation.models import ModelConfig, ModelRequest


class AnthropicProvider:
    def __init__(self, config: ModelConfig) -> None:
        api_key = os.getenv(config.api_key_env)
        if not api_key:
            raise ValueError(
                f"Environment variable {config.api_key_env} is not set. "
                "Set it before running XAgent."
            )

        client_kwargs = {"api_key": api_key}
        if config.base_url:
            client_kwargs["base_url"] = config.base_url
        self._client = AsyncAnthropic(**client_kwargs)

    async def complete(self, request: ModelRequest) -> Message:
        response = await self._client.messages.create(**_to_anthropic_request_kwargs(request))
        return _from_anthropic_message(response)

    async def stream_complete(self, request: ModelRequest) -> AsyncIterator[Message]:
        stream = await self._client.messages.create(**_to_anthropic_request_kwargs(request, stream=True))
        blocks: dict[int, dict[str, Any]] = {}
        last_snapshot: str | None = None

        async for event in stream:
            event_type = getattr(event, "type", None)
            index = getattr(event, "index", None)

            if event_type == "content_block_start" and index is not None:
                blocks[index] = _block_from_anthropic(getattr(event, "content_block", None))
                snapshot = _snapshot_from_blocks(blocks)
                key = snapshot.model_dump_json()
                if snapshot.content and key != last_snapshot:
                    last_snapshot = key
                    yield snapshot
                continue

            if event_type == "content_block_delta" and index is not None:
                block = blocks.setdefault(index, {"type": "text", "text": ""})
                delta = getattr(event, "delta", None)
                delta_type = getattr(delta, "type", None)
                if delta_type == "text_delta":
                    block["text"] = f"{block.get('text', '')}{getattr(delta, 'text', '')}"
                elif delta_type == "input_json_delta":
                    partial = f"{block.get('_partial_json', '')}{getattr(delta, 'partial_json', '')}"
                    block["_partial_json"] = partial
                    try:
                        block["input"] = json.loads(partial)
                    except json.JSONDecodeError:
                        pass
                snapshot = _snapshot_from_blocks(blocks)
                key = snapshot.model_dump_json()
                if snapshot.content and key != last_snapshot:
                    last_snapshot = key
                    yield snapshot
                continue

            if event_type == "content_block_stop" and index is not None:
                block = blocks.get(index)
                if block and block.get("type") == "tool_use" and "_partial_json" in block:
                    try:
                        block["input"] = json.loads(block["_partial_json"])
                    except json.JSONDecodeError:
                        pass
                snapshot = _snapshot_from_blocks(blocks)
                key = snapshot.model_dump_json()
                if snapshot.content and key != last_snapshot:
                    last_snapshot = key
                    yield snapshot

    async def stream_text(self, request: ModelRequest) -> AsyncIterator[str]:
        stream = await self._client.messages.create(**_to_anthropic_request_kwargs(request, stream=True))
        async for event in stream:
            if getattr(event, "type", None) != "content_block_delta":
                continue
            delta = getattr(event, "delta", None)
            if getattr(delta, "type", None) == "text_delta":
                text = getattr(delta, "text", None)
                if text:
                    yield text


def _to_anthropic_request_kwargs(request: ModelRequest, stream: bool = False) -> dict[str, Any]:
    system_parts = []
    messages = []
    for message in request.messages:
        if message.role == "system":
            text = message_text(message)
            if text:
                system_parts.append(text)
            continue
        converted = _message_to_anthropic(message)
        if converted is not None:
            messages.append(converted)

    kwargs: dict[str, Any] = {
        "model": request.model,
        "messages": messages,
        "max_tokens": request.max_tokens or 1024,
        "temperature": request.temperature,
    }
    if system_parts:
        kwargs["system"] = "\n\n".join(system_parts)
    if request.tools:
        kwargs["tools"] = _tools_to_anthropic(request.tools)
    if stream:
        kwargs["stream"] = True
    return kwargs


def _message_to_anthropic(message: Message) -> dict[str, Any] | None:
    if message.role == "tool":
        parts = [part for part in message.content if part.type == "tool_result"]
        if not parts:
            return None
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": part.tool_use_id,
                    "content": part.content,
                    "is_error": part.is_error,
                }
                for part in parts
            ],
        }

    content = []
    for part in message.content:
        if part.type == "text":
            if part.text:
                content.append({"type": "text", "text": part.text})
        elif part.type == "tool_use":
            content.append(
                {
                    "type": "tool_use",
                    "id": part.id,
                    "name": part.name,
                    "input": part.input,
                }
            )
    if not content:
        return None
    return {"role": message.role, "content": content}


def _tools_to_anthropic(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    converted = []
    for tool in tools:
        function = tool.get("function", {})
        converted.append(
            {
                "name": function.get("name", ""),
                "description": function.get("description", ""),
                "input_schema": function.get("parameters", {"type": "object", "properties": {}}),
            }
        )
    return converted


def _from_anthropic_message(message: Any) -> Message:
    content = []
    for block in getattr(message, "content", []) or []:
        converted = _content_part_from_anthropic(block)
        if converted is not None:
            content.append(converted)
    return Message(role="assistant", content=content)


def _content_part_from_anthropic(block: Any):
    block_type = getattr(block, "type", None)
    if block_type == "text" and getattr(block, "text", None):
        return TextPart(text=block.text)
    if block_type == "tool_use":
        return ToolUsePart(
            id=getattr(block, "id", ""),
            name=getattr(block, "name", ""),
            input=getattr(block, "input", {}) or {},
        )
    return None


def _block_from_anthropic(block: Any) -> dict[str, Any]:
    block_type = getattr(block, "type", None)
    if block_type == "tool_use":
        return {
            "type": "tool_use",
            "id": getattr(block, "id", ""),
            "name": getattr(block, "name", ""),
            "input": getattr(block, "input", {}) or {},
            "_partial_json": json.dumps(getattr(block, "input", {}) or {}, ensure_ascii=False)
            if getattr(block, "input", None)
            else "",
        }
    return {"type": "text", "text": getattr(block, "text", "") or ""}


def _snapshot_from_blocks(blocks: dict[int, dict[str, Any]]) -> Message:
    content = []
    for index in sorted(blocks):
        block = blocks[index]
        if block.get("type") == "text":
            text = block.get("text", "")
            if text:
                content.append(TextPart(text=text))
            continue
        content.append(
            ToolUsePart(
                id=block.get("id", ""),
                name=block.get("name", ""),
                input=block.get("input", {}) or {},
            )
        )
    return Message(role="assistant", content=content)
