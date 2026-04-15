import unittest

from xagent.agent.tools.workspace.ask_user_question import (
    AskUserQuestionInput,
    AskUserQuestionResultData,
    create_ask_user_question_tool,
)
from xagent.foundation.tools import ToolContext


class AskUserQuestionToolTests(unittest.IsolatedAsyncioTestCase):
    async def test_ask_user_question_tool_returns_structured_answers(self) -> None:
        async def _callback(params: AskUserQuestionInput) -> AskUserQuestionResultData:
            return AskUserQuestionResultData(
                answers=[
                    {"question_index": 0, "selected_labels": ["Option A"]},
                ]
            )

        tool = create_ask_user_question_tool(_callback)
        result = await tool.invoke(
            {
                "questions": [
                    {
                        "question": "Choose one?",
                        "header": "Choice",
                        "options": [
                            {"label": "Option A", "description": "First"},
                            {"label": "Option B", "description": "Second"},
                        ],
                        "multi_select": False,
                    }
                ]
            },
            ToolContext(cwd="."),
        )

        self.assertFalse(result.is_error)
        self.assertEqual(result.summary, "Collected answers for 1 question(s).")
        self.assertEqual(result.data["answers"][0]["selected_labels"], ["Option A"])
