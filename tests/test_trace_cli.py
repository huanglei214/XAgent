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
            recorder.emit("agent_step_started", {"step": 1}, tags={"step": 1})
            recorder.emit("tool_call_started", {"tool_name": "read_file"}, tags={"tool_name": "read_file"})
            recorder.emit(
                "tool_call_finished",
                {"tool_name": "read_file"},
                tags={"tool_name": "read_file", "status": "success"},
            )
            recorder.emit(
                "approval_decided",
                {"tool_name": "read_file", "decision": "allow_scope"},
                tags={"tool_name": "read_file"},
            )
            recorder.finish_success(output_text="done", duration_seconds=0.1)

            latest = runner.invoke(app, ["trace", "latest"])
            shown = runner.invoke(app, ["trace", "show", recorder.trace_id])

        self.assertEqual(latest.exit_code, 0)
        self.assertIn("Trace Summary", latest.output)
        self.assertIn(recorder.trace_id, latest.output)
        self.assertEqual(shown.exit_code, 0)
        self.assertIn("Trace Events", shown.output)
        self.assertIn("Trace Stats", shown.output)
        self.assertIn("read_file: started=1 success=1 error=0", shown.output)
        self.assertIn("Approvals: allow_scope=1", shown.output)
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

    def test_trace_list_filters_by_reason_and_tool(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = Path.cwd()
            first = TraceRecorder(
                cwd=root,
                mode="run",
                model="ep-test",
                provider="ark",
                task_kind="read",
            )
            first.emit("tool_call_started", {"tool_name": "read_file"}, tags={"tool_name": "read_file"})
            first.finish_success(output_text="done", duration_seconds=0.1, termination_reason="completed")

            second = TraceRecorder(
                cwd=root,
                mode="run",
                model="ep-test",
                provider="ark",
                task_kind="edit",
            )
            second.emit("tool_call_started", {"tool_name": "bash"}, tags={"tool_name": "bash"})
            second.finish_failure(
                error="loop",
                stage="agent",
                duration_seconds=0.2,
                termination_reason="repeated_tool_call",
            )

            result = runner.invoke(app, ["trace", "list", "--reason", "repeated_tool_call", "--tool", "bash"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Traces", result.output)
        self.assertIn(second.trace_id, result.output)
        self.assertNotIn(first.trace_id, result.output)

    def test_trace_sessions_summarizes_multi_turn_session(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            root = Path.cwd()
            first = TraceRecorder(
                cwd=root,
                mode="chat",
                model="ep-test",
                provider="ark",
                task_kind="read",
                session_id="session-1",
                tags={"session_restored": False},
            )
            first.emit("tool_call_started", {"tool_name": "read_file"}, tags={"tool_name": "read_file"})
            first.emit(
                "tool_call_finished",
                {"tool_name": "read_file"},
                tags={"tool_name": "read_file", "status": "success"},
            )
            first.finish_success(output_text="done", duration_seconds=0.1, termination_reason="completed")

            second = TraceRecorder(
                cwd=root,
                mode="chat",
                model="ep-test",
                provider="ark",
                task_kind="edit",
                session_id="session-1",
                tags={"session_restored": True},
            )
            second.emit("tool_call_started", {"tool_name": "bash"}, tags={"tool_name": "bash"})
            second.finish_failure(
                error="loop",
                stage="agent",
                duration_seconds=0.2,
                termination_reason="repeated_tool_call",
            )

            result = runner.invoke(app, ["trace", "sessions"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Trace Sessions", result.output)
        self.assertIn("session-1", result.output)
        self.assertIn("traces=2", result.output)
        self.assertIn("restored=1", result.output)
        self.assertIn("tools=bash, read_file", result.output)
