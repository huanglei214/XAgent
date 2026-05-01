from __future__ import annotations

from typing import Any, AsyncIterator, cast

from xagent.providers.registry import ProviderSpec
from xagent.providers.types import ModelEvent, ModelRequest
from xagent.providers.util import MessageBuilder, safe_model_dump


class OpenAICompatProvider:
    """OpenAI-compatible provider using the OpenAI Python SDK."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_base: str | None = None,
        extra_headers: dict[str, str] | None = None,
        extra_body: dict[str, Any] | None = None,
        timeout_seconds: float = 120.0,
        spec: ProviderSpec | None = None,
    ) -> None:
        self.api_key = api_key
        self.api_base = api_base
        self.extra_headers = extra_headers or {}
        self.extra_body = extra_body or {}
        self.timeout_seconds = timeout_seconds
        self.spec = spec

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise RuntimeError("The openai package is required for OpenAICompatProvider.") from exc

        client = AsyncOpenAI(
            api_key=self._resolved_api_key(),
            base_url=self.api_base,
            timeout=self.timeout_seconds,
        )
        builder = MessageBuilder()
        completions = cast(Any, client.chat.completions)
        stream = await completions.create(**self._build_kwargs(request))
        async for chunk in stream:
            raw = safe_model_dump(chunk)
            if raw.get("usage"):
                yield ModelEvent.usage_event(raw["usage"], raw=raw)
            choices = raw.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            if delta.get("content"):
                event = ModelEvent.text_delta(str(delta["content"]), raw=raw)
                builder.apply(event)
                yield event
            for tool_delta in delta.get("tool_calls") or []:
                event = ModelEvent.tool_call_delta(tool_delta, raw=raw)
                builder.apply(event)
                yield event
        yield ModelEvent.message_done(builder.final_message())

    def _build_kwargs(self, request: ModelRequest) -> dict[str, Any]:
        kwargs: dict[str, Any] = request.to_openai_kwargs()
        kwargs["stream"] = True
        kwargs["stream_options"] = {"include_usage": True}
        if self.extra_headers:
            kwargs["extra_headers"] = self.extra_headers
        if self.extra_body:
            kwargs["extra_body"] = self.extra_body
        return kwargs

    def _resolved_api_key(self) -> str:
        return self.api_key or "no-key"
