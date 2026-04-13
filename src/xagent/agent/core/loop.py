from __future__ import annotations

import inspect
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
        approval_handler: Optional[Callable[[ToolUsePart], Awaitable[bool] | bool]] = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self.system_prompt = system_prompt
        self.tools = tools or []
        self.middlewares = middlewares or []
        self.cwd = cwd
        self.max_steps = max_steps
        self.approval_handler = approval_handler
        self.messages: list[Message] = []
        self.trace_recorder = None
        self.last_error_stage = None

    def clear_messages(self) -> None:
        self.messages.clear()

    def set_messages(self, messages: list[Message]) -> None:
        self.messages = list(messages)

    async def run(
        self,
        user_text: str,
        on_tool_use: Optional[Callable[[ToolUsePart], None]] = None,
    ) -> Message:
        self.messages.append(Message(role="user", content=[TextPart(text=user_text)]))
        await self._before_agent_run(user_text)

        for step in range(1, self.max_steps + 1):
            await self._before_agent_step(step)
            request = ModelRequest(
                model=self.model,
                messages=[Message(role="system", content=[TextPart(text=self.system_prompt)]), *self.messages],
                tools=[tool.to_openai_tool() for tool in self.tools],
            )
            request = await self._before_model(request)
            try:
                assistant_message = await self.provider.complete(request)
            except Exception:
                self.last_error_stage = "model"
                raise
            await self._after_model(assistant_message)
            self.messages.append(assistant_message)

            tool_uses = [part for part in assistant_message.content if isinstance(part, ToolUsePart)]
            if not tool_uses:
                await self._after_agent_step(step)
                await self._after_agent_run(assistant_message)
                return assistant_message

            for tool_use in tool_uses:
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
                            tool_result = await tool.invoke(tool_use.input, ToolContext(cwd=self.cwd))
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

            await self._after_agent_step(step)

        self.last_error_stage = "agent"
        raise RuntimeError("Maximum number of agent steps reached.")

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
