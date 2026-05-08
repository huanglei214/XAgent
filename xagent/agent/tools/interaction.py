from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable

from xagent.agent.tools.base import Tool, ToolResult, tool


@tool(
    name="ask_user",
    description="Ask the user a short clarification question.",
    exclusive=True,
    parameters={
        "type": "object",
        "properties": {"question": {"type": "string"}},
        "required": ["question"],
    },
)
class AskUserTool(Tool):
    def __init__(self, ask: Callable[[str], str | Awaitable[str]]) -> None:
        self.ask = ask

    async def execute(self, question: str) -> ToolResult:
        answer = self.ask(question)
        if inspect.isawaitable(answer):
            answer = await answer
        return ToolResult.ok(str(answer))
