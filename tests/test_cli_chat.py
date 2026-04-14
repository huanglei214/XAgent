import unittest
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from xagent.cli.main import app
from xagent.cli.tui.commands import BUILTIN_COMMANDS, filter_commands, get_slash_query, insert_command
from xagent.cli.tui.tui import (
    SlashCommandCompleter,
    _ask_user_questions_via_prompt,
    _build_session_picker_values,
    _build_session_picker_row_text,
    _default_session_picker_selection,
    _filter_session_summaries,
    _format_session_option,
    _format_runtime_block,
    _parse_question_selection,
    _todo_line_style,
    build_resume_hint_text,
    build_header_text,
    build_sidebar_text,
    build_todo_text,
    build_transcript_text,
)
from xagent.agent.session import SessionSummary
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
        self.assertEqual(run_tui.await_args.kwargs["resume"], False)
        self.assertIsNone(run_tui.await_args.kwargs["resume_session_id"])

    def test_resume_entry_launches_tui_in_resume_mode(self) -> None:
        runner = CliRunner()
        with patch("xagent.cli.commands.chat.run_tui", new_callable=AsyncMock) as run_tui:
            result = runner.invoke(app, ["resume", "session-123"])

        self.assertEqual(result.exit_code, 0)
        run_tui.assert_called_once()
        self.assertEqual(run_tui.await_args.kwargs["resume"], True)
        self.assertEqual(run_tui.await_args.kwargs["resume_session_id"], "session-123")

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

    def test_build_todo_text_renders_todo_items(self) -> None:
        todo_text = build_todo_text(_FakeAgent())
        self.assertIn("Todos", todo_text)
        self.assertIn("[>] Inspect repo", todo_text)

    def test_build_todo_text_returns_empty_when_missing(self) -> None:
        agent = type("Agent", (), {"todo_store": type("TodoStore", (), {"items": []})()})()
        self.assertEqual(build_todo_text(agent), "")

    def test_todo_line_style_highlights_in_progress_item(self) -> None:
        self.assertEqual(_todo_line_style("  [>] Inspect repo"), "class:todo_active")
        self.assertEqual(_todo_line_style("  [ ] Draft plan"), "class:todo_item")
        self.assertEqual(_todo_line_style("  [x] Done"), "class:todo_done")
        self.assertEqual(_todo_line_style("  [-] Cancelled"), "class:todo_done")

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

    def test_slash_command_completer_includes_new_builtin(self) -> None:
        from prompt_toolkit.document import Document

        completer = SlashCommandCompleter(BUILTIN_COMMANDS)
        completions = list(completer.get_completions(Document("/ne"), None))
        self.assertTrue(any(item.text == "/new" for item in completions))

    def test_format_runtime_block_renders_multiline_section(self) -> None:
        block = _format_runtime_block("Loaded skills", ["lark-shared", "lark-im"])
        self.assertEqual(block, "Loaded skills\n  lark-shared\n  lark-im")

    def test_build_resume_hint_text_matches_cli_command(self) -> None:
        self.assertEqual(
            build_resume_hint_text("session-123"),
            "To continue this session, run xagent resume session-123",
        )

    def test_builtin_commands_include_resume(self) -> None:
        self.assertIn("resume", {command["name"] for command in BUILTIN_COMMANDS})
        self.assertIn("new", {command["name"] for command in BUILTIN_COMMANDS})
        self.assertIn("quit", {command["name"] for command in BUILTIN_COMMANDS})
        self.assertNotIn("exit", {command["name"] for command in BUILTIN_COMMANDS})

    def test_format_session_option_marks_latest_and_current(self) -> None:
        summary = SessionSummary(
            session_id="session-abcdef",
            saved_at=0,
            message_count=6,
            recent_message_count=4,
            checkpointed_message_count=2,
            preview="Fix the bug in resume flow",
            is_latest=True,
        )
        option = _format_session_option(2, summary, current_session_id="session-abcdef")
        self.assertIn("2. Fix the bug in resume flow", option)
        self.assertIn("6 msgs", option)
        self.assertIn("[latest | current]", option)

    def test_build_session_picker_values_preserves_ids_and_labels(self) -> None:
        sessions = [
            SessionSummary(
                session_id="session-1",
                saved_at=0,
                message_count=3,
                recent_message_count=3,
                checkpointed_message_count=0,
                preview="older",
                is_latest=False,
            ),
            SessionSummary(
                session_id="session-2",
                saved_at=0,
                message_count=5,
                recent_message_count=4,
                checkpointed_message_count=1,
                preview="latest one",
                is_latest=True,
            ),
        ]

        values = _build_session_picker_values(sessions, current_session_id="session-1")

        self.assertEqual(values[0][0], "session-1")
        self.assertEqual(values[1][0], "session-2")
        self.assertIn("[current]", values[0][1])
        self.assertIn("[latest]", values[1][1])

    def test_filter_session_summaries_matches_preview_branch_and_id(self) -> None:
        sessions = [
            SessionSummary(
                session_id="session-abc123",
                saved_at=0,
                message_count=5,
                recent_message_count=4,
                checkpointed_message_count=1,
                preview="fix resume picker",
                is_latest=False,
                branch="main",
            ),
            SessionSummary(
                session_id="session-def456",
                saved_at=0,
                message_count=2,
                recent_message_count=2,
                checkpointed_message_count=0,
                preview="investigate traces",
                is_latest=True,
                branch="feature/search",
            ),
        ]

        self.assertEqual(
            [item.session_id for item in _filter_session_summaries(sessions, "resume main")],
            ["session-abc123"],
        )
        self.assertEqual(
            [item.session_id for item in _filter_session_summaries(sessions, "def456")],
            ["session-def456"],
        )

    def test_build_session_picker_row_text_formats_columns(self) -> None:
        summary = SessionSummary(
            session_id="session-xyz",
            saved_at=0,
            message_count=7,
            recent_message_count=5,
            checkpointed_message_count=2,
            preview="conversation preview goes here",
            is_latest=False,
            created_at=0,
            branch="main",
        )
        created, updated, branch, conversation = _build_session_picker_row_text(summary, 96)
        self.assertIn("unknown time", created)
        self.assertIn("unknown time", updated)
        self.assertIn("main", branch)
        self.assertIn("conversation preview", conversation)

    def test_default_session_picker_selection_prefers_non_current(self) -> None:
        sessions = [
            SessionSummary(
                session_id="session-current",
                saved_at=0,
                message_count=3,
                recent_message_count=3,
                checkpointed_message_count=0,
                preview="current",
                is_latest=True,
            ),
            SessionSummary(
                session_id="session-other",
                saved_at=0,
                message_count=8,
                recent_message_count=4,
                checkpointed_message_count=4,
                preview="other",
                is_latest=False,
            ),
        ]
        self.assertEqual(
            _default_session_picker_selection(sessions, current_session_id="session-current"),
            "session-other",
        )

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
