from __future__ import annotations

import json
import inspect
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Awaitable, Callable

from xagent.agent.tools.registry import ToolExecution, ToolRegistry
from xagent.providers.types import ModelEvent, ModelRequest, Provider
from xagent.providers.util import MessageBuilder
from xagent.session import Session

EventSink = Callable[[ModelEvent], Awaitable[None] | None]


class AgentError(RuntimeError):
    pass


@dataclass
class Agent:
    provider: Provider
    model: str
    session: Session
    tools: ToolRegistry
    system_prompt: str = "You are XAgent, a helpful local AI agent."
    temperature: float | None = None
    max_tokens: int | None = None
    max_steps: int = 50
    max_duration_seconds: float = 600.0
    max_repeated_tool_calls: int = 3
    context_char_threshold: int = 120_000
    trace_model_events: bool = False

    async def run(self, user_text: str, *, on_event: EventSink | None = None) -> dict[str, Any]:
        self.session.append_message({"role": "user", "content": user_text})
        await self._maybe_compact()
        started_at = perf_counter()
        last_tool_signature: str | None = None
        repeated_count = 0
        empty_retry_used = False

        for step in range(1, self.max_steps + 1):
            self._check_time_budget(started_at)
            request = self._build_request(step=step)
            assistant_message = await self._call_model(request, on_event=on_event)
            tool_calls = assistant_message.get("tool_calls") or []

            if tool_calls:
                self.session.append_message(assistant_message)
                signatures = [
                    json.dumps(call.get("function") or {}, sort_keys=True, ensure_ascii=False)
                    for call in tool_calls
                ]
                signature = "|".join(signatures)
                if signature == last_tool_signature:
                    repeated_count += 1
                else:
                    last_tool_signature = signature
                    repeated_count = 1
                if repeated_count > self.max_repeated_tool_calls:
                    raise AgentError("Agent stopped: repeated tool call loop detected.")
                await self._execute_tools(tool_calls)
                continue

            content = str(assistant_message.get("content") or "")
            if not content.strip() and not empty_retry_used:
                empty_retry_used = True
                self.session.append_message(assistant_message)
                self.session.append_message(
                    {
                        "role": "user",
                        "content": "Your previous response was empty. Provide a final answer.",
                    }
                )
                continue

            self.session.append_message(assistant_message)
            return assistant_message

        raise AgentError("Agent stopped: maximum number of steps reached.")

    def _build_request(self, *, step: int) -> ModelRequest:
        messages = [{"role": "system", "content": self.system_prompt}]
        messages.extend(self.session.read_model_messages())
        return ModelRequest(
            model=self.model,
            messages=messages,
            tools=self.tools.openai_tools(),
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            metadata={
                "session_id": self.session.session_id,
                "workspace_path": str(self.session.workspace_path),
                "step": step,
            },
        )

    async def _call_model(self, request: ModelRequest, *, on_event: EventSink | None) -> dict[str, Any]:
        self.session.append_trace(
            "model_request",
            {"request": request.to_openai_kwargs(), "metadata": request.metadata},
        )
        builder = MessageBuilder()
        final_message: dict[str, Any] | None = None
        try:
            async for event in self.provider.stream(request):
                if self.trace_model_events:
                    self.session.append_trace(
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
                    self.session.append_trace("model_usage", {"usage": event.usage})
                if on_event is not None:
                    maybe = on_event(event)
                    if inspect.isawaitable(maybe):
                        await maybe
        except Exception as exc:
            self.session.append_trace(
                "model_error",
                {"error": str(exc), "error_type": type(exc).__name__},
            )
            raise
        final_message = final_message or builder.final_message()
        self.session.append_trace("model_final", {"message": final_message})
        return final_message

    async def _execute_tools(self, tool_calls: list[dict[str, Any]]) -> None:
        prepared = [self.tools.prepare(call) for call in tool_calls]
        for item in prepared:
            self.session.append_trace(
                "tool_prepare",
                {
                    "tool_call_id": item.call_id,
                    "name": item.name,
                    "arguments": item.arguments,
                    "parse_error": item.parse_error,
                },
            )
        executions = await self.tools.execute_many(prepared)
        for execution in executions:
            self._record_tool_execution(execution)
            self.session.append_message(execution.to_message())

    def _record_tool_execution(self, execution: ToolExecution) -> None:
        self.session.append_trace(
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

    async def _maybe_compact(self) -> None:
        if self.session.approximate_context_size() <= self.context_char_threshold:
            return
        messages = self.session.read_model_messages()
        request = ModelRequest(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Summarize the task state, key decisions, file changes, "
                        "important tool results, and remaining todo items."
                    ),
                },
                *messages,
            ],
            tools=[],
            metadata={"session_id": self.session.session_id, "purpose": "compaction"},
        )
        summary_message = await self._call_model(request, on_event=None)
        summary = str(summary_message.get("content") or "").strip()
        if summary:
            self.session.append_summary(summary)

    def _check_time_budget(self, started_at: float) -> None:
        if perf_counter() - started_at > self.max_duration_seconds:
            raise AgentError("Agent stopped: time budget exceeded.")
