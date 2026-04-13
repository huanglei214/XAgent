import unittest
from pathlib import Path

from typer.testing import CliRunner

from xagent.cli.main import app
from xagent.agent.traces import TraceRecorder


class TraceCliTests(unittest.TestCase):
    def test_trace_latest_and_show(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = Path.cwd()
            recorder = TraceRecorder(
                cwd=root,
                mode="run",
                model="ep-test",
                provider="ark",
                task_kind="read",
            )
            recorder.emit("task_started", {"input": "hello"})
            recorder.finish_success(output_text="done", duration_seconds=0.1)

            latest = runner.invoke(app, ["trace", "latest"])
            shown = runner.invoke(app, ["trace", "show", recorder.trace_id])

        self.assertEqual(latest.exit_code, 0)
        self.assertIn("Trace Summary", latest.output)
        self.assertIn(recorder.trace_id, latest.output)
        self.assertEqual(shown.exit_code, 0)
        self.assertIn("Trace Events", shown.output)
        self.assertIn("task_started", shown.output)

    def test_trace_failed_lists_failures(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = Path.cwd()
            recorder = TraceRecorder(
                cwd=root,
                mode="chat",
                model="ep-test",
                provider="ark",
                task_kind="debug",
            )
            recorder.emit("task_started", {"input": "debug this"})
            recorder.finish_failure(error="boom", stage="runtime", duration_seconds=0.2)

            result = runner.invoke(app, ["trace", "failed"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Failed Traces", result.output)
        self.assertIn("boom", result.output)
