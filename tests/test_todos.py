import unittest

from xagent.agent.todos import TODO_WRITE_TOOL_NAME, TodoMiddleware, TodoStore, create_todo_system
from xagent.bus.types import Message, TextPart, ToolUsePart
from xagent.bus.types import ModelRequest
from xagent.agent.tools import ToolContext


class TodoTests(unittest.IsolatedAsyncioTestCase):
    async def test_todo_write_replace_and_merge(self) -> None:
        tool, middleware, store = create_todo_system()
        ctx = ToolContext(cwd=".")

        result = await tool.invoke(
            {
                "merge": False,
                "todos": [
                    {"id": "1", "content": "Inspect repo", "status": "in_progress"},
                    {"id": "2", "content": "Write summary", "status": "pending"},
                ],
            },
            ctx,
        )
        self.assertIn("2 items", result.content)
        self.assertEqual(len(store.items), 2)

        result = await tool.invoke(
            {
                "merge": True,
                "todos": [
                    {"id": "2", "content": "Write summary", "status": "completed"},
                    {"id": "3", "content": "Add follow-up", "status": "pending"},
                ],
            },
            ctx,
        )
        self.assertIn("3 items", result.content)
        self.assertEqual(store.items[1].status, "completed")
        self.assertEqual(store.items[2].id, "3")

    async def test_todo_middleware_injects_reminder(self) -> None:
        store = TodoStore()
        middleware = TodoMiddleware(store)
        store.replace(
            [
                type("Todo", (), {"id": "1", "content": "Inspect repo", "status": "in_progress"})(),
                type("Todo", (), {"id": "2", "content": "Write summary", "status": "pending"})(),
            ]
        )
        store.steps_since_write = 10
        store.steps_since_last_reminder = 10

        request = ModelRequest(model="ep-test", messages=[Message(role="system", content=[TextPart(text="base")])])
        updated = await middleware.before_model(agent=None, request=request)

        self.assertIsNotNone(updated)
        self.assertEqual(updated.messages[-1].role, "system")
        self.assertIn("Current todos", updated.messages[-1].content[0].text)

    async def test_todo_after_tool_resets_write_counter(self) -> None:
        store = TodoStore()
        middleware = TodoMiddleware(store)
        store.steps_since_write = 99

        await middleware.after_tool(
            agent=None,
            tool_use=ToolUsePart(id="call_1", name=TODO_WRITE_TOOL_NAME, input={"merge": False}),
            result=type("Result", (), {"is_error": False})(),
        )

        self.assertEqual(store.steps_since_write, 0)
