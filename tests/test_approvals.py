import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import BaseModel

from xagent.agent.loop import Agent
from xagent.coding.approvals import ApprovalStore, requires_approval
from xagent.foundation.messages import Message, ToolUsePart
from xagent.foundation.tools import Tool, ToolContext, ToolResult
from xagent.cli.runtime import make_approval_handler


class _FakeProvider:
    async def complete(self, request):
        return Message(
            role="assistant",
            content=[ToolUsePart(id="call_1", name="write_file", input={"path": "a.txt", "content": "x"})],
        )

    async def stream_text(self, request):  # pragma: no cover
        yield ""


class _WriteInput(BaseModel):
    path: str
    content: str


class ApprovalTests(unittest.IsolatedAsyncioTestCase):
    async def test_requires_approval(self) -> None:
        self.assertTrue(requires_approval("write_file"))
        self.assertFalse(requires_approval("read_file"))

    async def test_denied_tool_call_returns_error_tool_message(self) -> None:
        async def _handler(args, ctx: ToolContext) -> ToolResult:
            return ToolResult(content="should not run")

        tool = Tool(
            name="write_file",
            description="Write a file.",
            input_model=_WriteInput,
            handler=_handler,
        )

        agent = Agent(
            provider=_FakeProvider(),
            model="ep-test",
            system_prompt="You are XAgent",
            tools=[tool],
            approval_handler=lambda tool_use: False,
            max_steps=1,
        )

        with self.assertRaises(RuntimeError):
            await agent.run("write a file")

        self.assertEqual(agent.messages[-1].role, "tool")
        self.assertTrue(agent.messages[-1].content[0].is_error)

    async def test_approval_store_persists_allowed_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ApprovalStore(root)
            store.allow_tool("bash")

            reloaded = ApprovalStore(root)

        self.assertTrue(reloaded.is_allowed("bash"))

    async def test_make_approval_handler_persists_always_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ApprovalStore(root)
            handler = make_approval_handler(store)
            tool_use = ToolUsePart(id="call_1", name="bash", input={"command": "pwd"})

            with patch("xagent.cli.runtime.typer.prompt", return_value="a"):
                allowed = handler(tool_use)

        self.assertTrue(allowed)
        self.assertTrue(store.is_allowed("bash"))

    async def test_make_approval_handler_skips_prompt_for_persisted_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ApprovalStore(root)
            store.allow_tool("apply_patch")
            handler = make_approval_handler(store)
            tool_use = ToolUsePart(id="call_1", name="apply_patch", input={"path": "a.txt"})

            with patch("xagent.cli.runtime.typer.prompt") as prompt:
                allowed = handler(tool_use)

        self.assertTrue(allowed)
        prompt.assert_not_called()
