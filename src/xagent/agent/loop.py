from __future__ import annotations

import inspect
from typing import Awaitable, Callable, Optional

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
        cwd: str = ".",
        max_steps: int = 8,
        approval_handler: Optional[Callable[[ToolUsePart], Awaitable[bool] | bool]] = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self.system_prompt = system_prompt
        self.tools = tools or []
        self.cwd = cwd
        self.max_steps = max_steps
        self.approval_handler = approval_handler
        self.messages: list[Message] = []

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

        for _ in range(self.max_steps):
            request = ModelRequest(
                model=self.model,
                messages=[Message(role="system", content=[TextPart(text=self.system_prompt)]), *self.messages],
                tools=[tool.to_openai_tool() for tool in self.tools],
            )
            assistant_message = await self.provider.complete(request)
            self.messages.append(assistant_message)

            tool_uses = [part for part in assistant_message.content if isinstance(part, ToolUsePart)]
            if not tool_uses:
                return assistant_message

            for tool_use in tool_uses:
                if on_tool_use:
                    on_tool_use(tool_use)

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
                    tool_result = await tool.invoke(tool_use.input, ToolContext(cwd=self.cwd))
                    result = ToolResultPart(
                        tool_use_id=tool_use.id,
                        content=tool_result.content,
                        is_error=tool_result.is_error,
                    )

                self.messages.append(Message(role="tool", content=[result]))

        raise RuntimeError("Maximum number of agent steps reached.")


async def _is_tool_allowed(
    approval_handler: Callable[[ToolUsePart], Awaitable[bool] | bool],
    tool_use: ToolUsePart,
) -> bool:
    decision = approval_handler(tool_use)
    if inspect.isawaitable(decision):
        decision = await decision
    return bool(decision)
