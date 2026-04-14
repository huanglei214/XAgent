import os
from typing import AsyncIterator

from anthropic import AsyncAnthropic

from xagent.cli.config.schema import ModelConfig
from xagent.foundation.messages import Message, TextPart, message_text
from xagent.foundation.models import ModelRequest


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
        response = await self._client.messages.create(
            model=request.model,
            messages=_to_anthropic_messages(request),
            max_tokens=request.max_tokens or 1024,
            temperature=request.temperature,
        )
        text_parts = []
        for block in response.content:
            if getattr(block, "type", None) == "text" and getattr(block, "text", None):
                text_parts.append(TextPart(text=block.text))
        return Message(role="assistant", content=text_parts)

    async def stream_complete(self, request: ModelRequest) -> AsyncIterator[Message]:
        stream = await self._client.messages.create(
            model=request.model,
            messages=_to_anthropic_messages(request),
            max_tokens=request.max_tokens or 1024,
            temperature=request.temperature,
            stream=True,
        )
        text_parts: list[str] = []
        async for event in stream:
            if getattr(event, "type", None) == "content_block_delta":
                delta = getattr(event.delta, "text", None)
                if delta:
                    text_parts.append(delta)
                    yield Message(role="assistant", content=[TextPart(text="".join(text_parts))])

    async def stream_text(self, request: ModelRequest) -> AsyncIterator[str]:
        stream = await self._client.messages.create(
            model=request.model,
            messages=_to_anthropic_messages(request),
            max_tokens=request.max_tokens or 1024,
            temperature=request.temperature,
            stream=True,
        )
        async for event in stream:
            if getattr(event, "type", None) == "content_block_delta":
                delta = getattr(event.delta, "text", None)
                if delta:
                    yield delta


def _to_anthropic_messages(request: ModelRequest) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for message in request.messages:
        if message.role == "system":
            continue
        text = message_text(message)
        messages.append({"role": message.role, "content": text})
    return messages
