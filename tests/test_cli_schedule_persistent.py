import unittest
from unittest.mock import Mock, patch

from typer.testing import CliRunner

from xagent.cli.main import app


class CliSchedulePersistentTests(unittest.TestCase):
    def test_schedule_add_uses_manager_for_cron_job(self) -> None:
        runner = CliRunner()
        manager = Mock()
        manager.create_session.return_value = "session-1"
        manager.add_cron_job.return_value = {"job_id": "job-1", "session_id": "session-1"}

        with patch("xagent.cli.commands.schedule._build_manager", return_value=manager):
            result = runner.invoke(app, ["schedule", "add", "daily summary", "--cron", "*/5 * * * *"])

        self.assertEqual(result.exit_code, 0)
        manager.add_cron_job.assert_called_once()

    def test_schedule_add_supports_absolute_time(self) -> None:
        runner = CliRunner()
        manager = Mock()
        manager.create_session.return_value = "session-1"
        manager.add_once_job.return_value = {"job_id": "job-1", "session_id": "session-1"}

        with patch("xagent.cli.commands.schedule._build_manager", return_value=manager):
            result = runner.invoke(
                app,
                ["schedule", "add", "daily summary", "--at", "2026-04-16T09:00:00+08:00"],
            )

        self.assertEqual(result.exit_code, 0)
        manager.add_once_job.assert_called_once()
        self.assertIsNotNone(manager.add_once_job.call_args.kwargs["run_at"])

    def test_schedule_list_prints_jobs(self) -> None:
        runner = CliRunner()
        manager = Mock()
        manager.list_jobs.return_value = [
            {
                "job_id": "job-1",
                "session_id": "session-1",
                "text": "daily summary",
                "schedule_type": "cron",
                "cron_expression": "*/5 * * * *",
                "enabled": True,
                "next_run_at": 0.0,
                "last_error": None,
            }
        ]

        with patch("xagent.cli.commands.schedule._build_manager", return_value=manager):
            result = runner.invoke(app, ["schedule", "list"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("job-1", result.stdout)

    def test_schedule_remove_deletes_job(self) -> None:
        runner = CliRunner()
        manager = Mock()
        manager.remove_job.return_value = True

        with patch("xagent.cli.commands.schedule._build_manager", return_value=manager):
            result = runner.invoke(app, ["schedule", "remove", "job-1"])

        self.assertEqual(result.exit_code, 0)
        manager.remove_job.assert_called_once_with("job-1")

    def test_schedule_pause_resume_update_and_history(self) -> None:
        runner = CliRunner()
        manager = Mock()
        manager.pause_job.return_value = {"job_id": "job-1"}
        manager.resume_job.return_value = {"job_id": "job-1"}
        manager.update_job.return_value = {"job_id": "job-1"}
        manager.list_job_history.return_value = [
            {
                "recorded_at": 0.0,
                "job_id": "job-1",
                "status": "failed",
                "text": "daily summary",
                "attempt": 1,
                "error_text": "boom",
            }
        ]

        with patch("xagent.cli.commands.schedule._build_manager", return_value=manager):
            pause_result = runner.invoke(app, ["schedule", "pause", "job-1"])
            resume_result = runner.invoke(app, ["schedule", "resume", "job-1"])
            update_result = runner.invoke(
                app,
                ["schedule", "update", "job-1", "--text", "updated", "--at", "2026-04-16T09:00:00+08:00"],
            )
            history_result = runner.invoke(app, ["schedule", "history", "--job-id", "job-1"])

        self.assertEqual(pause_result.exit_code, 0)
        self.assertEqual(resume_result.exit_code, 0)
        self.assertEqual(update_result.exit_code, 0)
        self.assertEqual(history_result.exit_code, 0)
        manager.pause_job.assert_called_once_with("job-1")
        manager.resume_job.assert_called_once_with("job-1")
        manager.update_job.assert_called_once()
        manager.list_job_history.assert_called_once_with(job_id="job-1", limit=20)
        self.assertIn("job-1", history_result.stdout)
