from __future__ import annotations

from typing import Optional

from xagent.bus.types import Message, ToolResultPart, ToolUsePart
from xagent.bus.types import ModelRequest


class AgentMiddleware:
    async def before_agent_run(self, *, agent, user_text: str) -> None:
        return None

    async def after_agent_run(self, *, agent, final_message: Message) -> None:
        return None

    async def before_agent_step(self, *, agent, step: int) -> None:
        return None

    async def after_agent_step(self, *, agent, step: int) -> None:
        return None

    async def before_model(self, *, agent, request: ModelRequest) -> Optional[ModelRequest]:
        return None

    async def after_model(self, *, agent, assistant_message: Message) -> None:
        return None

    async def before_tool(self, *, agent, tool_use: ToolUsePart) -> Optional[ToolResultPart]:
        return None

    async def after_tool(self, *, agent, tool_use: ToolUsePart, result: ToolResultPart) -> None:
        return None
