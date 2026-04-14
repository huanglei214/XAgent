import tempfile
import unittest
from pathlib import Path

from xagent.coding.middleware import EditGuardrailsMiddleware
from xagent.coding.tools.read_file import read_file_tool
from xagent.foundation.messages import ToolResultPart, ToolUsePart
from xagent.foundation.tools import ToolContext


class GuardrailTests(unittest.IsolatedAsyncioTestCase):
    async def test_guardrails_block_existing_file_edit_until_inspected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file_path = root / "README.md"
            file_path.write_text("hello", encoding="utf-8")
            middleware = EditGuardrailsMiddleware()
            agent = type("AgentStub", (), {"cwd": str(root)})()

            result = await middleware.before_tool(
                agent=agent,
                tool_use=ToolUsePart(
                    id="call_1",
                    name="write_file",
                    input={"path": "README.md", "content": "updated"},
                ),
            )

        self.assertIsInstance(result, ToolResultPart)
        self.assertTrue(result.is_error)
        self.assertIn("Inspect README.md", result.content)

    async def test_guardrails_allow_edit_after_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            file_path = root / "README.md"
            file_path.write_text("hello", encoding="utf-8")
            middleware = EditGuardrailsMiddleware()
            agent = type("AgentStub", (), {"cwd": str(root)})()

            await middleware.after_tool(
                agent=agent,
                tool_use=ToolUsePart(id="call_1", name="read_file", input={"path": "README.md"}),
                result=ToolResultPart(tool_use_id="call_1", content="hello", is_error=False),
            )
            result = await middleware.before_tool(
                agent=agent,
                tool_use=ToolUsePart(
                    id="call_2",
                    name="str_replace",
                    input={"path": "README.md", "old_text": "hello", "new_text": "hi"},
                ),
            )

        self.assertIsNone(result)

    async def test_guardrails_allow_new_file_write_without_prior_read(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            middleware = EditGuardrailsMiddleware()
            agent = type("AgentStub", (), {"cwd": str(root)})()

            result = await middleware.before_tool(
                agent=agent,
                tool_use=ToolUsePart(
                    id="call_1",
                    name="write_file",
                    input={"path": "notes.txt", "content": "new file"},
                ),
            )

        self.assertIsNone(result)

    async def test_read_file_can_request_external_access(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            external = (Path(tmp) / ".." / "external.txt").resolve()
            external.write_text("external", encoding="utf-8")
            ctx = ToolContext(
                cwd=str(root),
                request_path_access=lambda path, kind: True,
                allowed_external_paths=set(),
            )

            result = await read_file_tool.invoke({"path": str(external)}, ctx)

        self.assertIn("external", result.content)

    async def test_read_file_denied_external_access_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            external = (Path(tmp) / ".." / "external.txt").resolve()
            external.write_text("external", encoding="utf-8")
            ctx = ToolContext(
                cwd=str(root),
                request_path_access=lambda path, kind: False,
                allowed_external_paths=set(),
            )

            result = await read_file_tool.invoke({"path": str(external)}, ctx)

        self.assertTrue(result.is_error)
        self.assertIn("Access denied", result.content)
