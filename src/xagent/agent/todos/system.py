from __future__ import annotations

from typing import List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

from xagent.agent.core.middleware import AgentMiddleware
from xagent.bus.types import Message, TextPart
from xagent.bus.types import ModelRequest
from xagent.agent.tools import Tool, ToolContext, ToolResult


TODO_WRITE_TOOL_NAME = "todo_write"

REMINDER_CONFIG = {
    "steps_since_write": 10,
    "steps_between_reminders": 10,
}

TODO_TOOL_DESCRIPTION = """Create and manage a structured task list for the current session.

Use this for multi-step work, planning, or keeping track of ongoing progress.

Task states:
- pending
- in_progress
- completed
- cancelled

Rules:
- Keep at most one task in_progress at a time
- Update tasks as you work
- Mark tasks completed promptly
- Use merge=true to update existing items by id
- Use merge=false to replace the entire list
"""


TodoStatus = Literal["pending", "in_progress", "completed", "cancelled"]


class TodoItem(BaseModel):
    id: str = Field(description="Unique identifier for this todo item.")
    content: str = Field(description="Description of the task.")
    status: TodoStatus = Field(description="Current task status.")


class TodoWriteInput(BaseModel):
    todos: List[TodoItem] = Field(description="Todo items to create or update.")
    merge: bool = Field(description="Whether to merge by id or replace the whole list.")


class TodoStore:
    def __init__(self) -> None:
        self.items: List[TodoItem] = []
        self.steps_since_write = 10**9
        self.steps_since_last_reminder = 10**9

    def replace(self, todos: List[TodoItem]) -> None:
        self.items = list(todos)
        self.steps_since_write = 0

    def merge(self, todos: List[TodoItem]) -> None:
        for item in todos:
            for index, existing in enumerate(self.items):
                if existing.id == item.id:
                    self.items[index] = item
                    break
            else:
                self.items.append(item)
        self.steps_since_write = 0

    def mark_step(self) -> None:
        self.steps_since_write += 1
        self.steps_since_last_reminder += 1

    def note_reminder(self) -> None:
        self.steps_since_last_reminder = 0

    def summary(self) -> str:
        counts = {status: 0 for status in ("pending", "in_progress", "completed", "cancelled")}
        for item in self.items:
            counts[item.status] += 1
        parts = []
        for status in ("pending", "in_progress", "completed", "cancelled"):
            if counts[status] > 0:
                parts.append(f"{counts[status]} {status}")
        details = ", ".join(parts) if parts else "0 items"
        return f"Todo list updated. {len(self.items)} items: {details}."

    def reminder_text(self) -> str:
        lines = [f"{index + 1}. [{item.status}] {item.content}" for index, item in enumerate(self.items)]
        return (
            "The todo_write tool has not been used recently. If the task would benefit from tracking, "
            "consider updating the todo list.\n\nCurrent todos:\n" + "\n".join(lines)
        )


class TodoMiddleware(AgentMiddleware):
    def __init__(self, store: TodoStore) -> None:
        self.store = store

    async def before_model(self, *, agent, request: ModelRequest) -> Optional[ModelRequest]:
        self.store.mark_step()
        if not self.store.items:
            return None

        if (
            self.store.steps_since_write >= REMINDER_CONFIG["steps_since_write"]
            and self.store.steps_since_last_reminder >= REMINDER_CONFIG["steps_between_reminders"]
        ):
            self.store.note_reminder()
            request.messages = [
                *request.messages,
                Message(role="system", content=[TextPart(text=self.store.reminder_text())]),
            ]
            return request
        return None

    async def after_tool(self, *, agent, tool_use, result) -> None:
        if tool_use.name == TODO_WRITE_TOOL_NAME and not result.is_error:
            self.store.steps_since_write = 0


def create_todo_system() -> Tuple[Tool, AgentMiddleware, TodoStore]:
    store = TodoStore()

    async def _write_todos(args: TodoWriteInput, ctx: ToolContext) -> ToolResult:
        if args.merge:
            store.merge(args.todos)
        else:
            store.replace(args.todos)
        return ToolResult(content=store.summary())

    tool = Tool(
        name=TODO_WRITE_TOOL_NAME,
        description=TODO_TOOL_DESCRIPTION,
        input_model=TodoWriteInput,
        handler=_write_todos,
    )
    return tool, TodoMiddleware(store), store
