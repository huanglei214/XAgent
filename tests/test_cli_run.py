import unittest
from unittest.mock import AsyncMock, Mock, patch


class CliRunTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_command_routes_prompt_through_runtime_boundary(self) -> None:
        fake_agent = Mock()
        fake_boundary = Mock()
        fake_boundary.submit_and_wait = AsyncMock(
            return_value=type(
                "Outbound",
                (),
                {
                    "kind": "completed",
                    "content": "done",
                    "error": None,
                    "metadata": {"duration_seconds": 0.5},
                },
            )()
        )

        with patch("xagent.cli.commands.run.build_runtime_agent", return_value=fake_agent), patch(
            "xagent.cli.commands.run.build_local_runtime_boundary", return_value=fake_boundary
        ), patch("xagent.cli.commands.run.render_final_message") as render_final, patch(
            "xagent.cli.commands.run.render_turn_status"
        ) as render_status:
            from xagent.cli.commands.run import _run

            await _run("hello")

        fake_boundary.submit_and_wait.assert_awaited_once()
        render_final.assert_called_once()
        render_status.assert_called_once_with(0.5, fake_agent)
