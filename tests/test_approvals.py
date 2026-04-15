import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydantic import BaseModel

from xagent.agent.core import Agent
from xagent.agent.policies import ApprovalMiddleware, ApprovalStore, requires_approval
from xagent.foundation.messages import Message, ToolUsePart
from xagent.foundation.tools import Tool, ToolContext, ToolResult


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
            middleware = ApprovalMiddleware(store, prompt_fn=lambda prompt: "a")
            tool_use = ToolUsePart(id="call_1", name="bash", input={"command": "pwd"})

            result = await middleware.before_tool(agent=type("AgentStub", (), {"trace_recorder": None})(), tool_use=tool_use)

        self.assertIsNone(result)
        self.assertTrue(store.is_allowed("bash"))

    async def test_scoped_bash_approval_matches_same_command_prefix_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ApprovalStore(root)
            tool_use = ToolUsePart(id="call_1", name="bash", input={"command": "git status --short"})
            store.allow_scoped_tool_use(tool_use, cwd=root)

            reloaded = ApprovalStore(root)

        self.assertTrue(reloaded.is_allowed_tool_use(ToolUsePart(id="x", name="bash", input={"command": "git status"}), cwd=root))
        self.assertFalse(reloaded.is_allowed_tool_use(ToolUsePart(id="x", name="bash", input={"command": "git add ."}), cwd=root))

    async def test_scoped_path_approval_matches_same_prefix_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ApprovalStore(root)
            tool_use = ToolUsePart(id="call_1", name="write_file", input={"path": "src/app.py", "content": "x"})
            store.allow_scoped_tool_use(tool_use, cwd=root)

            reloaded = ApprovalStore(root)

        self.assertTrue(
            reloaded.is_allowed_tool_use(
                ToolUsePart(id="x", name="write_file", input={"path": "src/app.py", "content": "y"}),
                cwd=root,
            )
        )
        self.assertFalse(
            reloaded.is_allowed_tool_use(
                ToolUsePart(id="x", name="write_file", input={"path": "README.md", "content": "y"}),
                cwd=root,
            )
        )

    async def test_scoped_rule_expires(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ApprovalStore(root)
            tool_use = ToolUsePart(id="call_1", name="bash", input={"command": "git status"})
            store.allow_scoped_tool_use(tool_use, cwd=root, ttl_seconds=-1)

            reloaded = ApprovalStore(root)

        self.assertFalse(reloaded.is_allowed_tool_use(tool_use, cwd=root))

    async def test_make_approval_handler_accepts_async_prompt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ApprovalStore(root)

            async def _prompt(_: str) -> str:
                return "a"

            middleware = ApprovalMiddleware(store, prompt_fn=_prompt)
            tool_use = ToolUsePart(id="call_1", name="bash", input={"command": "pwd"})

            result = await middleware.before_tool(agent=type("AgentStub", (), {"trace_recorder": None})(), tool_use=tool_use)

        self.assertIsNone(result)
        self.assertTrue(store.is_allowed("bash"))

    async def test_make_approval_handler_persists_scoped_decision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ApprovalStore(root)
            middleware = ApprovalMiddleware(store, prompt_fn=lambda prompt: "s")
            tool_use = ToolUsePart(id="call_1", name="bash", input={"command": "git status --short"})

            result = await middleware.before_tool(agent=type("AgentStub", (), {"trace_recorder": None, "cwd": str(root)})(), tool_use=tool_use)

            reloaded = ApprovalStore(root)

        self.assertIsNone(result)
        self.assertFalse(reloaded.is_allowed("bash"))
        self.assertTrue(reloaded.is_allowed_tool_use(ToolUsePart(id="x", name="bash", input={"command": "git status"}), cwd=root))
        self.assertFalse(reloaded.is_allowed_tool_use(ToolUsePart(id="x", name="bash", input={"command": "git add ."}), cwd=root))

    async def test_make_approval_handler_skips_prompt_for_persisted_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ApprovalStore(root)
            store.allow_tool("apply_patch")
            middleware = ApprovalMiddleware(store, prompt_fn=lambda prompt: "n")
            tool_use = ToolUsePart(id="call_1", name="apply_patch", input={"path": "a.txt"})

            with patch.object(middleware, "prompt_fn") as prompt:
                result = await middleware.before_tool(
                    agent=type("AgentStub", (), {"trace_recorder": None})(),
                    tool_use=tool_use,
                )

        self.assertIsNone(result)
        prompt.assert_not_called()

    async def test_make_approval_handler_skips_prompt_for_matching_scoped_rule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ApprovalStore(root)
            store.allow_scoped_tool_use(
                ToolUsePart(id="call_1", name="write_file", input={"path": "src/app.py", "content": "x"}),
                cwd=root,
            )
            middleware = ApprovalMiddleware(store, prompt_fn=lambda prompt: "n")
            tool_use = ToolUsePart(id="call_2", name="write_file", input={"path": "src/app.py", "content": "y"})

            with patch.object(middleware, "prompt_fn") as prompt:
                result = await middleware.before_tool(
                    agent=type("AgentStub", (), {"trace_recorder": None, "cwd": str(root)})(),
                    tool_use=tool_use,
                )

        self.assertIsNone(result)
        prompt.assert_not_called()
