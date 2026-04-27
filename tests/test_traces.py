import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from pydantic import BaseModel

from xagent.agent.core import Agent, AgentAborted
from xagent.agent.paths import get_trace_artifacts_dir, get_trace_index_file
from xagent.bus.types import Message, TextPart, ToolUsePart, message_text
from xagent.agent.tools import Tool, ToolContext, ToolResult
from xagent.cli.runtime import make_external_path_approval_handler, run_agent_turn, run_agent_turn_stream
from xagent.agent.tools.workspace.files import read_file_tool


class _SuccessProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, request):
        self.calls += 1
        if self.calls == 1:
            return Message(
                role="assistant",
                content=[ToolUsePart(id="call_1", name="echo_tool", input={"value": "hello"})],
            )
        return Message(role="assistant", content=[TextPart(text="done")])

    async def stream_text(self, request):  # pragma: no cover
        yield ""

    async def stream_complete(self, request):
        self.calls += 1
        if self.calls == 1:
            yield Message(role="assistant", content=[TextPart(text="par")])
            yield Message(role="assistant", content=[TextPart(text="partial")])
            yield Message(
                role="assistant",
                content=[
                    TextPart(text="partial"),
                    ToolUsePart(id="call_1", name="echo_tool", input={"value": "hello"}),
                ],
            )
            return

        yield Message(role="assistant", content=[TextPart(text="done")])


class _FailureProvider:
    async def complete(self, request):
        raise RuntimeError("provider exploded")

    async def stream_text(self, request):  # pragma: no cover
        yield ""


class _AbortProvider:
    async def complete(self, request):
        await asyncio.sleep(0.2)
        return Message(role="assistant", content=[TextPart(text="late")])

    async def stream_text(self, request):  # pragma: no cover
        yield ""


class _EchoInput(BaseModel):
    value: str


class TraceTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_trace_writes_events_and_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            async def _handler(args, ctx: ToolContext) -> ToolResult:
                return ToolResult(content=args.value)

            tool = Tool(
                name="echo_tool",
                description="Echo value",
                input_model=_EchoInput,
                handler=_handler,
            )
            agent = Agent(
                provider=_SuccessProvider(),
                model="ep-test",
                system_prompt="You are XAgent",
                tools=[tool],
                cwd=str(root),
            )
            agent.provider_name = "ark"
            agent.runtime_mode = "run"

            message, duration = await run_agent_turn(agent, "read the repo")
            self.assertEqual(message.content[0].text, "done")
            self.assertGreaterEqual(duration, 0.0)

            trace_path = agent.last_trace_recorder.path
            events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
            event_types = [event["event_type"] for event in events]

            self.assertIn("task_started", event_types)
            self.assertIn("user_input", event_types)
            self.assertIn("agent_step_started", event_types)
            self.assertIn("model_request", event_types)
            self.assertIn("model_response", event_types)
            self.assertIn("tool_call_started", event_types)
            self.assertIn("tool_call_finished", event_types)
            self.assertIn("agent_step_finished", event_types)
            self.assertIn("state_snapshot", event_types)
            self.assertIn("task_finished", event_types)
            self.assertIn("model_request_artifact_written", event_types)
            self.assertIn("model_response_artifact_written", event_types)

            artifact_dir = get_trace_artifacts_dir(root) / agent.last_trace_recorder.trace_id
            self.assertTrue((artifact_dir / "step-1-request.json").exists())
            self.assertTrue((artifact_dir / "step-1-response.json").exists())

            index = json.loads(get_trace_index_file(root).read_text(encoding="utf-8"))
            self.assertEqual(index[-1]["status"], "success")
            self.assertEqual(index[-1]["provider"], "ark")
            self.assertEqual(index[-1]["task_kind"], "read")

    async def test_streaming_turn_emits_assistant_deltas(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            async def _handler(args, ctx: ToolContext) -> ToolResult:
                return ToolResult(content=args.value)

            tool = Tool(
                name="echo_tool",
                description="Echo value",
                input_model=_EchoInput,
                handler=_handler,
            )
            agent = Agent(
                provider=_SuccessProvider(),
                model="ep-test",
                system_prompt="You are XAgent",
                tools=[tool],
                cwd=str(root),
            )
            agent.provider_name = "ark"
            agent.runtime_mode = "run"

            snapshots = []
            tool_started = []
            tool_finished = []

            def _on_delta(message: Message) -> None:
                snapshots.append(message_text(message))

            def _on_tool_use(tool_use: ToolUsePart) -> None:
                tool_started.append(tool_use.name)

            def _on_tool_result(tool_use: ToolUsePart, result) -> None:
                tool_finished.append((tool_use.name, result.is_error))

            final_message, _ = await run_agent_turn_stream(
                agent,
                "read the repo",
                on_assistant_delta=_on_delta,
                on_tool_use=_on_tool_use,
                on_tool_result=_on_tool_result,
            )

        self.assertEqual(final_message.content[0].text, "done")
        self.assertEqual(snapshots[:2], ["par", "partial"])
        self.assertEqual(tool_started, ["echo_tool"])
        self.assertEqual(tool_finished, [("echo_tool", False)])

    async def test_failure_trace_keeps_replay_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = Agent(
                provider=_FailureProvider(),
                model="ep-test",
                system_prompt="You are XAgent",
                tools=[],
                cwd=str(root),
            )
            agent.provider_name = "ark"
            agent.runtime_mode = "run"

            with self.assertRaises(RuntimeError):
                await run_agent_turn(agent, "debug this failure")

            trace_path = agent.last_trace_recorder.path
            events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
            event_types = [event["event_type"] for event in events]

            self.assertIn("task_started", event_types)
            self.assertIn("user_input", event_types)
            self.assertIn("model_request", event_types)
            self.assertIn("state_snapshot", event_types)
            self.assertIn("task_failed", event_types)

            index = json.loads(get_trace_index_file(root).read_text(encoding="utf-8"))
            self.assertEqual(index[-1]["status"], "failed")
            self.assertIn("provider exploded", index[-1]["error"])

    async def test_external_path_approval_is_traced(self) -> None:
        class _ExternalReadProvider:
            def __init__(self, external_path: Path) -> None:
                self.calls = 0
                self.external_path = external_path

            async def complete(self, request):
                self.calls += 1
                if self.calls == 1:
                    return Message(
                        role="assistant",
                        content=[
                            ToolUsePart(
                                id="call_1",
                                name="read_file",
                                input={"path": str(self.external_path)},
                            )
                        ],
                    )
                return Message(role="assistant", content=[TextPart(text="loaded")])

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            external = (root.parent / "external-trace.txt").resolve()
            external.write_text("external", encoding="utf-8")

            agent = Agent(
                provider=_ExternalReadProvider(external),
                model="ep-test",
                system_prompt="You are XAgent",
                tools=[read_file_tool],
                cwd=str(root),
            )
            agent.provider_name = "ark"
            agent.runtime_mode = "run"
            agent.request_path_access = make_external_path_approval_handler(
                prompt_fn=lambda _: True,
                recorder_getter=lambda: getattr(agent, "trace_recorder", None),
            )

            message, _ = await run_agent_turn(agent, "read external file")
            self.assertEqual(message.content[0].text, "loaded")

            trace_path = agent.last_trace_recorder.path
            events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
            event_types = [event["event_type"] for event in events]

        self.assertIn("external_path_access_requested", event_types)
        self.assertIn("external_path_access_decided", event_types)

    async def test_aborted_trace_is_recorded_as_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = Agent(
                provider=_AbortProvider(),
                model="ep-test",
                system_prompt="You are XAgent",
                tools=[],
                cwd=str(root),
            )
            agent.provider_name = "ark"
            agent.runtime_mode = "run"

            task = asyncio.create_task(run_agent_turn(agent, "abort this"))
            await asyncio.sleep(0.02)
            agent.abort()

            with self.assertRaises(AgentAborted):
                await task

            trace_path = agent.last_trace_recorder.path
            events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
            event_types = [event["event_type"] for event in events]

            index = json.loads(get_trace_index_file(root).read_text(encoding="utf-8"))

        self.assertIn("task_cancelled", event_types)
        self.assertEqual(index[-1]["status"], "cancelled")
        self.assertEqual(index[-1]["termination_reason"], "aborted")
