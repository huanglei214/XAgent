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
