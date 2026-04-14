from __future__ import annotations

import asyncio
import inspect
import json
from time import perf_counter
from typing import Awaitable, Callable, Optional

from xagent.agent.core.middleware import AgentMiddleware
from xagent.foundation.messages import Message, TextPart, ToolResultPart, ToolUsePart
from xagent.foundation.models import ModelRequest
from xagent.foundation.tools import Tool, ToolContext, find_tool


class Agent:
    def __init__(
        self,
        provider,
        model: str,
        system_prompt: str,
        tools: Optional[list[Tool]] = None,
        middlewares: Optional[list[AgentMiddleware]] = None,
        cwd: str = ".",
        max_steps: int = 8,
        max_duration_seconds: Optional[float] = 300.0,
        max_consecutive_errors: int = 3,
        max_repeated_tool_calls: int = 3,
        approval_handler: Optional[Callable[[ToolUsePart], Awaitable[bool] | bool]] = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self.system_prompt = system_prompt
        self.tools = tools or []
        self.middlewares = middlewares or []
        self.cwd = cwd
        self.max_steps = max_steps
        self.max_duration_seconds = max_duration_seconds
        self.max_consecutive_errors = max_consecutive_errors
        self.max_repeated_tool_calls = max_repeated_tool_calls
        self.approval_handler = approval_handler
        self.messages: list[Message] = []
        self.trace_recorder = None
        self.last_error_stage = None
        self.last_termination_reason: Optional[str] = None
        self.requested_skill_name: Optional[str] = None
        self.skills = []
        self.allowed_external_paths: set[str] = set()
        self.request_path_access: Optional[Callable[[str, str], Awaitable[bool] | bool]] = None

    def clear_messages(self) -> None:
        self.messages.clear()

    def set_messages(self, messages: list[Message]) -> None:
        self.messages = list(messages)

    def set_requested_skill_name(self, requested_skill_name: Optional[str]) -> None:
        self.requested_skill_name = requested_skill_name

    async def run(
        self,
        user_text: str,
        on_tool_use: Optional[Callable[[ToolUsePart], None]] = None,
        on_tool_result: Optional[Callable[[ToolUsePart, ToolResultPart], None]] = None,
        on_assistant_delta: Optional[Callable[[Message], None]] = None,
    ) -> Message:
        self.last_error_stage = None
        self.last_termination_reason = None
        started_at = perf_counter()
        consecutive_error_count = 0
        repeated_tool_call_count = 0
        last_tool_signature: Optional[str] = None

        self.messages.append(Message(role="user", content=[TextPart(text=user_text)]))
        await self._before_agent_run(user_text)

        for step in range(1, self.max_steps + 1):
            self._ensure_within_budget(started_at)
            await self._before_agent_step(step)
            request = ModelRequest(
                model=self.model,
                messages=[Message(role="system", content=[TextPart(text=self.system_prompt)]), *self.messages],
                tools=[tool.to_openai_tool() for tool in self.tools],
            )
            request = await self._before_model(request)
            try:
                if on_assistant_delta and hasattr(self.provider, "stream_complete"):
                    assistant_message = await self._run_streaming_model(request, on_assistant_delta, started_at)
                else:
                    assistant_message = await self._await_with_budget(
                        self.provider.complete(request),
                        started_at=started_at,
                        timeout_message="Agent stopped: run timed out while waiting for model response.",
                    )
            except Exception:
                if self.last_termination_reason is None:
                    self.last_termination_reason = "model_error"
                if self.last_error_stage is None:
                    self.last_error_stage = "model"
                raise
            await self._after_model(assistant_message)
            self.messages.append(assistant_message)

            tool_uses = [part for part in assistant_message.content if isinstance(part, ToolUsePart)]
            if not tool_uses:
                self.last_termination_reason = "completed"
                await self._after_agent_step(step)
                await self._after_agent_run(assistant_message)
                return assistant_message

            for tool_use in tool_uses:
                self._ensure_within_budget(started_at)
                tool_signature = json.dumps(
                    {"name": tool_use.name, "input": tool_use.input},
                    ensure_ascii=False,
                    sort_keys=True,
                )
                if tool_signature == last_tool_signature:
                    repeated_tool_call_count += 1
                else:
                    last_tool_signature = tool_signature
                    repeated_tool_call_count = 1
                if self.max_repeated_tool_calls > 0 and repeated_tool_call_count > self.max_repeated_tool_calls:
                    self.last_error_stage = "agent"
                    self.last_termination_reason = "repeated_tool_call"
                    raise RuntimeError("Agent stopped: repeated tool loop detected.")

                if on_tool_use:
                    on_tool_use(tool_use)
                middleware_result = await self._before_tool(tool_use)
                if middleware_result is not None:
                    result = middleware_result
                else:
                    tool = find_tool(self.tools, tool_use.name)
                    if tool is None:
                        result = ToolResultPart(
                            tool_use_id=tool_use.id,
                            content=f"Tool '{tool_use.name}' is not registered.",
                            is_error=True,
                        )
                    elif self.approval_handler and not await _is_tool_allowed(self.approval_handler, tool_use):
                        result = ToolResultPart(
                            tool_use_id=tool_use.id,
                            content=f"Execution denied for tool '{tool_use.name}'.",
                            is_error=True,
                        )
                    else:
                        try:
                            tool_result = await self._await_with_budget(
                                tool.invoke(
                                    tool_use.input,
                                    ToolContext(
                                        cwd=self.cwd,
                                        request_path_access=self.request_path_access,
                                        allowed_external_paths=self.allowed_external_paths,
                                    ),
                                ),
                                started_at=started_at,
                                timeout_message=f"Agent stopped: run timed out while waiting for tool '{tool_use.name}'.",
                            )
                            result = ToolResultPart(
                                tool_use_id=tool_use.id,
                                content=tool_result.content,
                                is_error=tool_result.is_error,
                            )
                        except Exception as exc:
                            result = ToolResultPart(
                                tool_use_id=tool_use.id,
                                content=f"Tool '{tool_use.name}' failed: {exc}",
                                is_error=True,
                            )

                await self._after_tool(tool_use, result)
                self.messages.append(Message(role="tool", content=[result]))
                if on_tool_result:
                    on_tool_result(tool_use, result)
                if result.is_error:
                    consecutive_error_count += 1
                    if self.max_consecutive_errors > 0 and consecutive_error_count >= self.max_consecutive_errors:
                        self.last_error_stage = "agent"
                        self.last_termination_reason = "consecutive_tool_errors"
                        raise RuntimeError("Agent stopped: too many consecutive tool errors.")
                else:
                    consecutive_error_count = 0

            await self._after_agent_step(step)

        self.last_error_stage = "agent"
        self.last_termination_reason = "max_steps"
        raise RuntimeError("Maximum number of agent steps reached.")

    async def _run_streaming_model(
        self,
        request: ModelRequest,
        on_assistant_delta: Callable[[Message], None],
        started_at: float,
    ) -> Message:
        async def _consume() -> Message:
            latest: Optional[Message] = None
            async for snapshot in self.provider.stream_complete(request):
                latest = snapshot
                on_assistant_delta(snapshot)
            if latest is None:
                raise RuntimeError("Model stream ended without producing a message")
            return latest

        return await self._await_with_budget(
            _consume(),
            started_at=started_at,
            timeout_message="Agent stopped: run timed out while waiting for model response.",
        )

    async def _await_with_budget(
        self,
        awaitable,
        *,
        started_at: float,
        timeout_message: str,
    ):
        remaining = self._remaining_duration(started_at)
        if remaining is None:
            return await awaitable
        if remaining <= 0:
            self.last_error_stage = "agent"
            self.last_termination_reason = "timeout"
            raise RuntimeError(timeout_message)
        try:
            return await asyncio.wait_for(awaitable, timeout=remaining)
        except asyncio.TimeoutError as exc:
            self.last_error_stage = "agent"
            self.last_termination_reason = "timeout"
            raise RuntimeError(timeout_message) from exc

    def _ensure_within_budget(self, started_at: float) -> None:
        remaining = self._remaining_duration(started_at)
        if remaining is None:
            return
        if remaining <= 0:
            self.last_error_stage = "agent"
            self.last_termination_reason = "timeout"
            raise RuntimeError("Agent stopped: run timed out.")

    def _remaining_duration(self, started_at: float) -> Optional[float]:
        if self.max_duration_seconds is None:
            return None
        return self.max_duration_seconds - (perf_counter() - started_at)

    async def _before_agent_run(self, user_text: str) -> None:
        for middleware in self.middlewares:
            await middleware.before_agent_run(agent=self, user_text=user_text)

    async def _after_agent_run(self, final_message: Message) -> None:
        for middleware in self.middlewares:
            await middleware.after_agent_run(agent=self, final_message=final_message)

    async def _before_agent_step(self, step: int) -> None:
        for middleware in self.middlewares:
            await middleware.before_agent_step(agent=self, step=step)

    async def _after_agent_step(self, step: int) -> None:
        for middleware in self.middlewares:
            await middleware.after_agent_step(agent=self, step=step)

    async def _before_model(self, request: ModelRequest) -> ModelRequest:
        current = request
        for middleware in self.middlewares:
            updated = await middleware.before_model(agent=self, request=current)
            if updated is not None:
                current = updated
        return current

    async def _after_model(self, assistant_message: Message) -> None:
        for middleware in self.middlewares:
            await middleware.after_model(agent=self, assistant_message=assistant_message)

    async def _before_tool(self, tool_use: ToolUsePart) -> Optional[ToolResultPart]:
        for middleware in self.middlewares:
            result = await middleware.before_tool(agent=self, tool_use=tool_use)
            if result is not None:
                return result
        return None

    async def _after_tool(self, tool_use: ToolUsePart, result: ToolResultPart) -> None:
        for middleware in self.middlewares:
            await middleware.after_tool(agent=self, tool_use=tool_use, result=result)


async def _is_tool_allowed(
    approval_handler: Callable[[ToolUsePart], Awaitable[bool] | bool],
    tool_use: ToolUsePart,
) -> bool:
    decision = approval_handler(tool_use)
    if inspect.isawaitable(decision):
        decision = await decision
    return bool(decision)
