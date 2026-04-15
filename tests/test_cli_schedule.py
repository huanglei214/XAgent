import unittest
from unittest.mock import AsyncMock, patch

from typer.testing import CliRunner

from xagent.cli.main import app


class CliScheduleTests(unittest.TestCase):
    def test_schedule_once_entry_runs_async_handler(self) -> None:
        runner = CliRunner()
        with patch("xagent.cli.commands.schedule.run_scheduled_once", new_callable=AsyncMock) as run_once:
            result = runner.invoke(app, ["schedule", "once", "daily summary", "--delay-seconds", "5"])

        self.assertEqual(result.exit_code, 0)
        run_once.assert_awaited_once()
        self.assertEqual(run_once.await_args.kwargs["text"], "daily summary")
        self.assertEqual(run_once.await_args.kwargs["delay_seconds"], 5.0)
