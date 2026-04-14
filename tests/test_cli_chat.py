import unittest
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from xagent.cli.main import app
from xagent.cli.tui.commands import filter_commands, get_slash_query, insert_command
from xagent.cli.tui.tui import build_header_text, build_sidebar_text, build_transcript_text
from xagent.foundation.messages import Message, TextPart, ToolResultPart, ToolUsePart


class _FakeAgent:
    def __init__(self):
        self.model = "ep-test"
        self.cwd = "."
        self.messages = [Message(role="user", content=[TextPart(text="hello")])]
        self.tools = ["read_file", "write_file"]
        self.todo_store = type(
            "TodoStore",
            (),
            {"items": [type("Todo", (), {"status": "in_progress", "content": "Inspect repo"})()]},
        )()
        self.approval_store = type("ApprovalStore", (), {"allowed_tools": {"bash"}})()
        self.last_trace_recorder = type("Trace", (), {"trace_id": "trace-1", "status": "success"})()


class CliChatTests(unittest.TestCase):
    def test_default_entry_launches_tui(self) -> None:
        runner = CliRunner()
        with patch("xagent.cli.commands.chat.run_tui", new_callable=AsyncMock) as run_tui:
            result = runner.invoke(app, [])

        self.assertEqual(result.exit_code, 0)
        run_tui.assert_called_once()

    def test_build_header_text_contains_title_and_model(self) -> None:
        header = build_header_text(_FakeAgent())
        self.assertIn("XAgent", header)
        self.assertIn("ep-test", header)

    def test_build_sidebar_text_shows_status_todos_and_trace(self) -> None:
        sidebar = build_sidebar_text(_FakeAgent(), ["read_file"])
        self.assertIn("Model: ep-test", sidebar)
        self.assertIn("Todos", sidebar)
        self.assertIn("Inspect repo", sidebar)
        self.assertIn("trace-1", sidebar)
        self.assertIn("In Progress", sidebar)
        self.assertIn("Approvals", sidebar)
        self.assertIn("read_file", sidebar)

    def test_build_transcript_renders_user_assistant_and_tool_blocks(self) -> None:
        messages = [
            Message(role="user", content=[TextPart(text="hello")]),
            Message(
                role="assistant",
                content=[
                    TextPart(text="hi"),
                    ToolUsePart(id="call_1", name="read_file", input={"path": "README.md"}),
                ],
            ),
            Message(
                role="tool",
                content=[ToolResultPart(tool_use_id="call_1", content="README contents", is_error=False)],
            ),
        ]
        transcript = build_transcript_text(messages, ["notice"], active_tools=["bash"])

        self.assertIn("notice", transcript)
        self.assertIn("❯ hello", transcript)
        self.assertIn("● hi", transcript)
        self.assertIn("○ read_file", transcript)
        self.assertIn("✓ README contents", transcript)
        self.assertIn("… running bash", transcript)

    def test_slash_query_helpers(self) -> None:
        self.assertEqual(get_slash_query("/he"), "he")
        self.assertIsNone(get_slash_query("hello"))
        self.assertEqual(insert_command("help"), "/help ")

    def test_filter_commands_prefers_prefix_match(self) -> None:
        commands = filter_commands("st")
        self.assertGreaterEqual(len(commands), 1)
        self.assertEqual(commands[0]["name"], "status")
