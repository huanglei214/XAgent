import asyncio
import unittest

from pydantic import BaseModel

from xagent.agent.core import Agent, AgentMiddleware
from xagent.foundation.messages import Message, TextPart, ToolUsePart, message_text
from xagent.foundation.tools import Tool, ToolContext, ToolResult


class _FakeProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, request):
        self.calls += 1
        if self.calls == 1:
            return Message(
                role="assistant",
                content=[
                    ToolUsePart(
                        id="tool_1",
                        name="echo_tool",
                        input={"value": "hello"},
                    )
                ],
            )
        return Message(role="assistant", content=[TextPart(text="Tool said hello")])

    async def stream_text(self, request):  # pragma: no cover - not used here
        yield ""


class _SlowProvider:
    async def complete(self, request):
        await asyncio.sleep(0.05)
        return Message(role="assistant", content=[TextPart(text="late")])

    async def stream_text(self, request):  # pragma: no cover - not used here
        yield ""


class _RepeatingToolProvider:
    async def complete(self, request):
        return Message(
            role="assistant",
            content=[
                ToolUsePart(
                    id="tool_repeat",
                    name="echo_tool",
                    input={"value": "hello"},
                )
            ],
        )

    async def stream_text(self, request):  # pragma: no cover - not used here
        yield ""


class _ErrorLoopProvider:
    async def complete(self, request):
        return Message(
            role="assistant",
            content=[
                ToolUsePart(
                    id="tool_fail",
                    name="failing_tool",
                    input={"value": "boom"},
                )
            ],
        )

    async def stream_text(self, request):  # pragma: no cover - not used here
        yield ""


class AgentLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_runs_tool_then_returns_final_message(self) -> None:
        async def _handler(args, ctx: ToolContext) -> ToolResult:
            return ToolResult(content=args.value)

        class EchoInput(BaseModel):
            value: str

        tool = Tool(
            name="echo_tool",
            description="Echo the provided value.",
            input_model=EchoInput,
            handler=_handler,
        )

        provider = _FakeProvider()
        agent = Agent(
            provider=provider,
            model="ep-test",
            system_prompt="You are XAgent",
            tools=[tool],
            cwd=".",
        )

        message = await agent.run("say hi")

        self.assertEqual(message_text(message), "Tool said hello")
        self.assertEqual(provider.calls, 2)

    async def test_agent_step_hooks_wrap_each_iteration(self) -> None:
        class StepMiddleware(AgentMiddleware):
            def __init__(self) -> None:
                self.events = []

            async def before_agent_step(self, *, agent, step: int) -> None:
                self.events.append(("before", step))

            async def after_agent_step(self, *, agent, step: int) -> None:
                self.events.append(("after", step))

        provider = _FakeProvider()
        middleware = StepMiddleware()
        agent = Agent(
            provider=provider,
            model="ep-test",
            system_prompt="You are XAgent",
            tools=[],
            middlewares=[middleware],
            cwd=".",
        )

        message = await agent.run("say hi")

        self.assertEqual(message_text(message), "Tool said hello")
        self.assertEqual(middleware.events, [("before", 1), ("after", 1), ("before", 2), ("after", 2)])

    async def test_agent_stops_when_total_duration_exceeds_budget(self) -> None:
        agent = Agent(
            provider=_SlowProvider(),
            model="ep-test",
            system_prompt="You are XAgent",
            tools=[],
            cwd=".",
            max_duration_seconds=0.01,
        )

        with self.assertRaisesRegex(RuntimeError, "timed out"):
            await agent.run("say hi")

        self.assertEqual(agent.last_termination_reason, "timeout")
        self.assertEqual(agent.last_error_stage, "agent")

    async def test_agent_stops_on_repeated_tool_loop(self) -> None:
        async def _handler(args, ctx: ToolContext) -> ToolResult:
            return ToolResult(content=args.value)

        class EchoInput(BaseModel):
            value: str

        tool = Tool(
            name="echo_tool",
            description="Echo the provided value.",
            input_model=EchoInput,
            handler=_handler,
        )
        agent = Agent(
            provider=_RepeatingToolProvider(),
            model="ep-test",
            system_prompt="You are XAgent",
            tools=[tool],
            cwd=".",
            max_repeated_tool_calls=2,
            max_steps=8,
        )

        with self.assertRaisesRegex(RuntimeError, "repeated tool loop"):
            await agent.run("say hi")

        self.assertEqual(agent.last_termination_reason, "repeated_tool_call")
        self.assertEqual(agent.last_error_stage, "agent")

    async def test_agent_stops_after_consecutive_tool_errors(self) -> None:
        async def _handler(args, ctx: ToolContext) -> ToolResult:
            return ToolResult(content="failed", is_error=True)

        class FailInput(BaseModel):
            value: str

        tool = Tool(
            name="failing_tool",
            description="Always fails.",
            input_model=FailInput,
            handler=_handler,
        )
        agent = Agent(
            provider=_ErrorLoopProvider(),
            model="ep-test",
            system_prompt="You are XAgent",
            tools=[tool],
            cwd=".",
            max_consecutive_errors=2,
            max_steps=8,
        )

        with self.assertRaisesRegex(RuntimeError, "too many consecutive tool errors"):
            await agent.run("say hi")

        self.assertEqual(agent.last_termination_reason, "consecutive_tool_errors")
        self.assertEqual(agent.last_error_stage, "agent")
