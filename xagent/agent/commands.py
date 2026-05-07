from __future__ import annotations

from dataclasses import dataclass

from xagent.agent.memory import Dream


@dataclass(frozen=True)
class AgentCommand:
    name: str
    compact: bool = False
    message: str | None = None


class CommandRouter:
    """识别会话内 slash command，并把具体能力分发出去。"""

    def __init__(self, dream: Dream | None = None) -> None:
        self.dream = dream or Dream()

    def parse(self, content: str) -> AgentCommand | None:
        stripped = content.strip()
        if not stripped.startswith("/"):
            return None
        parts = stripped.split()
        name = parts[0].lower()
        if name == "/dream":
            unknown = [part for part in parts[1:] if part != "--compact"]
            if unknown:
                return AgentCommand(name="unknown", message=self.help_text())
            return AgentCommand(name="dream", compact="--compact" in parts[1:])
        if name == "/help":
            return AgentCommand(name="help", message=self.help_text())
        return AgentCommand(name="unknown", message=self.help_text())

    async def execute(self, command: AgentCommand, agent: object) -> None:
        if command.name == "dream":
            await self.dream.run(agent=agent, compact=command.compact)  # type: ignore[arg-type]

    @staticmethod
    def help_text() -> str:
        return "Available commands: /dream, /dream --compact, /help"
