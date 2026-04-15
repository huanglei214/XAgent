import unittest
from unittest.mock import AsyncMock, Mock, patch

from xagent.foundation.messages import Message, TextPart


class CliRunTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_command_routes_prompt_through_session_runtime(self) -> None:
        fake_agent = Mock()
        fake_runtime = Mock()
        fake_runtime.publish_user_message = AsyncMock(
            return_value=type(
                "TurnResult",
                (),
                {
                    "message": Message(role="assistant", content=[TextPart(text="done")]),
                    "duration_seconds": 0.5,
                },
            )()
        )

        with patch("xagent.cli.commands.run.build_runtime_agent", return_value=fake_agent), patch(
            "xagent.cli.commands.run.build_session_runtime", return_value=(Mock(), fake_runtime)
        ), patch("xagent.cli.commands.run.render_final_message") as render_final, patch(
            "xagent.cli.commands.run.render_turn_status"
        ) as render_status:
            from xagent.cli.commands.run import _run

            await _run("hello")

        fake_runtime.publish_user_message.assert_awaited_once_with("hello", source="cli.run")
        render_final.assert_called_once()
        render_status.assert_called_once_with(0.5, fake_agent)
