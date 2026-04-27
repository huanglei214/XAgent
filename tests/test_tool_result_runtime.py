import unittest

from xagent.agent.tool_result_runtime import format_tool_result_for_message, summarize_tool_result_for_ui
from xagent.agent.tools import ToolResult


class ToolResultRuntimeTests(unittest.TestCase):
    def test_format_tool_result_for_message_uses_summary_only_for_search_tools(self) -> None:
        result = ToolResult.ok(
            "Found 2 matches for pattern 'hello'.",
            content="README.md:1: hello\nsrc/app.py:2: hello",
            data={"matches": ["README.md:1: hello", "src/app.py:2: hello"]},
        )

        formatted = format_tool_result_for_message("grep_search", result)

        self.assertEqual(formatted, '{"ok": true, "summary": "Found 2 matches for pattern \'hello\'."}')

    def test_format_tool_result_for_message_preserves_read_file_content(self) -> None:
        result = ToolResult.ok(
            "Read 2 line(s) from README.md.",
            content="   1: hello\n   2: world",
            data={"path": "README.md"},
        )

        formatted = format_tool_result_for_message("read_file", result)

        self.assertEqual(formatted, "   1: hello\n   2: world")

    def test_summarize_tool_result_for_ui_prefers_summary(self) -> None:
        content = '{"ok": true, "summary": "Found 2 entries under .", "data": {"entries": ["a", "b"]}}'
        self.assertEqual(summarize_tool_result_for_ui("", content, False), "Found 2 entries under .")
