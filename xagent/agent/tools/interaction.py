from __future__ import annotations

from collections.abc import Callable

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
    def __init__(self, ask: Callable[[str], str]) -> None:
        self.ask = ask

    async def execute(self, question: str) -> ToolResult:
        return ToolResult.ok(self.ask(question))
