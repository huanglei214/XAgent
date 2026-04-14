import unittest
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from xagent.cli.main import app
from xagent.cli.tui.commands import filter_commands, get_slash_query, insert_command
from xagent.cli.tui.tui import (
    SlashCommandCompleter,
    _ask_user_questions_via_prompt,
    _format_runtime_block,
    _parse_question_selection,
    build_header_text,
    build_sidebar_text,
    build_transcript_text,
)
from xagent.coding.tools.ask_user_question import AskUserQuestionInput
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

    def test_build_transcript_prefers_structured_tool_summary(self) -> None:
        messages = [
            Message(
                role="tool",
                content=[
                    ToolResultPart(
                        tool_use_id="call_1",
                        content='{"ok": true, "summary": "Found 2 entries under .", "data": {"entries": ["a", "b"]}}',
                        is_error=False,
                    )
                ],
            )
        ]
        transcript = build_transcript_text(messages, [])
        self.assertIn("✓ Found 2 entries under .", transcript)

    def test_slash_query_helpers(self) -> None:
        self.assertEqual(get_slash_query("/he"), "he")
        self.assertIsNone(get_slash_query("hello"))
        self.assertEqual(insert_command("help"), "/help ")

    def test_filter_commands_prefers_prefix_match(self) -> None:
        commands = filter_commands("st")
        self.assertGreaterEqual(len(commands), 1)
        self.assertEqual(commands[0]["name"], "status")

    def test_filter_commands_supports_external_skill_commands(self) -> None:
        commands = filter_commands(
            "rev",
            [
                {"name": "help", "description": "Show help", "type": "builtin"},
                {"name": "review-helper", "description": "Review repository changes", "type": "skill"},
            ],
        )
        self.assertEqual(commands[0]["name"], "review-helper")

    def test_slash_command_completer_includes_skills(self) -> None:
        from prompt_toolkit.document import Document

        completer = SlashCommandCompleter(
            [
                {"name": "help", "description": "Show help", "type": "builtin"},
                {"name": "review-helper", "description": "Review repository changes", "type": "skill"},
            ]
        )
        completions = list(completer.get_completions(Document("/rev"), None))
        self.assertEqual(completions[0].text, "/review-helper")

    def test_format_runtime_block_renders_multiline_section(self) -> None:
        block = _format_runtime_block("Loaded skills", ["lark-shared", "lark-im"])
        self.assertEqual(block, "Loaded skills\n  lark-shared\n  lark-im")

    def test_parse_question_selection_supports_single_and_multi(self) -> None:
        self.assertEqual(_parse_question_selection("2", False, 3), [2])
        self.assertEqual(_parse_question_selection("1, 3, 1", True, 3), [1, 3])

    def test_ask_user_questions_via_prompt_collects_answers(self) -> None:
        async def _prompt(_: str) -> str:
            return "2"

        params = AskUserQuestionInput.model_validate(
            {
                "questions": [
                    {
                        "question": "Choose one?",
                        "header": "Choice",
                        "options": [
                            {"label": "A", "description": "First"},
                            {"label": "B", "description": "Second"},
                        ],
                    }
                ]
            }
        )

        import asyncio

        result = asyncio.run(_ask_user_questions_via_prompt(_prompt, params))
        self.assertEqual(result.answers[0].selected_labels, ["B"])
