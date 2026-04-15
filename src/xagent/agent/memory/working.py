from __future__ import annotations

from typing import Any, Optional

from xagent.foundation.messages import Message


class WorkingMemory:
    def __init__(self, agent: Any = None) -> None:
        self.agent = agent
        self.active_tools: list[str] = []
        self.requested_skill_name: Optional[str] = None
        self.current_plan: Optional[str] = None
        self.scratchpad: dict[str, Any] = {}
        self._messages: list[Message] = []

    @property
    def messages(self) -> list[Message]:
        if self.agent is not None:
            return list(getattr(self.agent, "messages", []))
        return list(self._messages)

    def attach_agent(self, agent: Any) -> None:
        self.agent = agent

    def replace_messages(self, messages: list[Message]) -> None:
        if self.agent is not None:
            self.agent.clear_messages()
            self.agent.set_messages(messages)
            return
        self._messages = list(messages)

    def clear_messages(self) -> None:
        self.replace_messages([])

    def start_tool(self, tool_name: str) -> None:
        if tool_name not in self.active_tools:
            self.active_tools.append(tool_name)

    def finish_tool(self, tool_name: str) -> None:
        self.active_tools = [name for name in self.active_tools if name != tool_name]

    def clear_active_tools(self) -> None:
        self.active_tools = []

    def set_requested_skill_name(self, requested_skill_name: Optional[str]) -> None:
        self.requested_skill_name = requested_skill_name
        if self.agent is not None and hasattr(self.agent, "set_requested_skill_name"):
            self.agent.set_requested_skill_name(requested_skill_name)

    def set_current_plan(self, plan: Optional[str]) -> None:
        self.current_plan = plan

    def set_scratchpad_item(self, key: str, value: Any) -> None:
        self.scratchpad[key] = value

    def clear_turn_state(self) -> None:
        self.clear_active_tools()
        self.set_requested_skill_name(None)
