from __future__ import annotations

import inspect
import json
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Awaitable, Callable

from xagent.agent.tools.registry import ToolExecution, ToolRegistry
from xagent.providers.types import ModelEvent, ModelRequest, Provider
from xagent.providers.util import MessageBuilder

EventSink = Callable[[ModelEvent], Awaitable[None] | None]
TraceSink = Callable[[str, dict[str, Any]], Awaitable[None] | None]
MessageSink = Callable[[dict[str, Any]], Awaitable[None] | None]


class AgentError(RuntimeError):
    pass


@dataclass
class AgentRunSpec:
    model: str
    messages: list[dict[str, Any]]
    tools: ToolRegistry
    max_steps: int
    max_duration_seconds: float
    max_repeated_tool_calls: int
    empty_retry_message: str
    temperature: float | None = None
    max_tokens: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    trace_model_events: bool = False
    on_event: EventSink | None = None
    on_trace: TraceSink | None = None
    on_message: MessageSink | None = None


@dataclass
class AgentRunResult:
    final_message: dict[str, Any]
    steps: int
    stop_reason: str = "completed"
    tools_used: list[str] = field(default_factory=list)


class AgentRunner:
    """执行单次 ReAct run；不感知 Session、Bus 或 Channel。"""

    def __init__(self, provider: Provider) -> None:
        self.provider = provider

    async def run(self, spec: AgentRunSpec) -> AgentRunResult:
        messages = list(spec.messages)
        started_at = perf_counter()
        last_tool_signature: str | None = None
        repeated_count = 0
        empty_retry_used = False
        tools_used: list[str] = []

        for step in range(1, spec.max_steps + 1):
            self._check_time_budget(started_at, spec.max_duration_seconds)
            request = self._build_request(spec, messages=messages, step=step)
            assistant_message = await call_model(
                self.provider,
                request,
                on_event=spec.on_event,
                on_trace=spec.on_trace,
                trace_model_events=spec.trace_model_events,
            )
            tool_calls = assistant_message.get("tool_calls") or []

            if tool_calls:
                messages.append(assistant_message)
                await _emit_message(spec.on_message, assistant_message)
                signature = self._tool_call_signature(tool_calls)
                if signature == last_tool_signature:
                    repeated_count += 1
                else:
                    last_tool_signature = signature
                    repeated_count = 1
                if repeated_count > spec.max_repeated_tool_calls:
                    raise AgentError("Agent stopped: repeated tool call loop detected.")
                executions = await self._execute_tools(spec, tool_calls)
                for execution in executions:
                    tools_used.append(execution.name)
                    tool_message = execution.to_message()
                    messages.append(tool_message)
                    await _emit_message(spec.on_message, tool_message)
                continue

            content = str(assistant_message.get("content") or "")
            if not content.strip() and not empty_retry_used:
                empty_retry_used = True
                messages.append(assistant_message)
                await _emit_message(spec.on_message, assistant_message)
                retry_message = {"role": "user", "content": spec.empty_retry_message}
                messages.append(retry_message)
                await _emit_message(spec.on_message, retry_message)
                continue

            messages.append(assistant_message)
            await _emit_message(spec.on_message, assistant_message)
            return AgentRunResult(
                final_message=assistant_message,
                steps=step,
                tools_used=tools_used,
            )

        raise AgentError("Agent stopped: maximum number of steps reached.")

    def _build_request(
        self,
        spec: AgentRunSpec,
        *,
        messages: list[dict[str, Any]],
        step: int,
    ) -> ModelRequest:
        metadata = {**spec.metadata, "step": step}
        return ModelRequest(
            model=spec.model,
            messages=list(messages),
            tools=spec.tools.openai_tools(),
            temperature=spec.temperature,
            max_tokens=spec.max_tokens,
            metadata=metadata,
        )

    async def _execute_tools(
        self,
        spec: AgentRunSpec,
        tool_calls: list[dict[str, Any]],
    ) -> list[ToolExecution]:
        prepared = [spec.tools.prepare(call) for call in tool_calls]
        for item in prepared:
            await _emit_trace(
                spec.on_trace,
                "tool_prepare",
                {
                    "tool_call_id": item.call_id,
                    "name": item.name,
                    "arguments": item.arguments,
                    "parse_error": item.parse_error,
                },
            )
        executions = await spec.tools.execute_many(prepared)
        for execution in executions:
            await self._record_tool_execution(spec, execution)
        return executions

    async def _record_tool_execution(
        self,
        spec: AgentRunSpec,
        execution: ToolExecution,
    ) -> None:
        await _emit_trace(
            spec.on_trace,
            "tool_result",
            {
                "tool_call_id": execution.call_id,
                "name": execution.name,
                "arguments": execution.arguments,
                "content": execution.result.content,
                "is_error": execution.result.is_error,
                "data": execution.result.data,
                "duration_seconds": execution.duration_seconds,
            },
        )

    @staticmethod
    def _tool_call_signature(tool_calls: list[dict[str, Any]]) -> str:
        signatures = [
            json.dumps(call.get("function") or {}, sort_keys=True, ensure_ascii=False)
            for call in tool_calls
        ]
        return "|".join(signatures)

    @staticmethod
    def _check_time_budget(started_at: float, max_duration_seconds: float) -> None:
        if perf_counter() - started_at > max_duration_seconds:
            raise AgentError("Agent stopped: time budget exceeded.")


async def call_model(
    provider: Provider,
    request: ModelRequest,
    *,
    on_event: EventSink | None = None,
    on_trace: TraceSink | None = None,
    trace_model_events: bool = False,
) -> dict[str, Any]:
    await _emit_trace(
        on_trace,
        "model_request",
        {"request": request.to_openai_kwargs(), "metadata": request.metadata},
    )
    builder = MessageBuilder()
    final_message: dict[str, Any] | None = None
    try:
        async for event in provider.stream(request):
            if trace_model_events:
                await _emit_trace(
                    on_trace,
                    "model_event",
                    {
                        "kind": event.kind,
                        "text": event.text,
                        "tool_call": event.tool_call,
                        "message": event.message,
                        "usage": event.usage,
                        "raw": event.raw,
                    },
                )
            if event.kind in {"text_delta", "tool_call_delta"}:
                builder.apply(event)
            if event.kind == "message_done":
                final_message = event.message or builder.final_message()
            if event.kind == "usage":
                await _emit_trace(on_trace, "model_usage", {"usage": event.usage})
            await _emit_event(on_event, event)
    except Exception as exc:
        await _emit_trace(
            on_trace,
            "model_error",
            {"error": str(exc), "error_type": type(exc).__name__},
        )
        raise
    final_message = final_message or builder.final_message()
    await _emit_trace(on_trace, "model_final", {"message": final_message})
    return final_message


async def _emit_event(callback: EventSink | None, event: ModelEvent) -> None:
    if callback is None:
        return
    maybe = callback(event)
    if inspect.isawaitable(maybe):
        await maybe


async def _emit_trace(
    callback: TraceSink | None,
    kind: str,
    payload: dict[str, Any],
) -> None:
    if callback is None:
        return
    maybe = callback(kind, payload)
    if inspect.isawaitable(maybe):
        await maybe


async def _emit_message(callback: MessageSink | None, message: dict[str, Any]) -> None:
    if callback is None:
        return
    maybe = callback(message)
    if inspect.isawaitable(maybe):
        await maybe
