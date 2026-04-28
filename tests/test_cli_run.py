"""CLI ``run`` 命令与 ``build_runtime_stack`` 的单元测试。"""

import queue
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock, patch

from xagent.bus.messages import InboundMessage, make_progress, make_terminal
from xagent.provider.types import Message, TextPart


class CliRunTests(unittest.IsolatedAsyncioTestCase):
    """`_run` 在完全 mock 下验证新流程的编排顺序。"""

    async def test_run_command_routes_prompt_through_channel_manager(self) -> None:
        """验证 ``_run`` 正确调用 build_runtime_stack -> start -> send_and_wait -> stop。"""
        fake_agent = Mock()
        fake_stack = Mock()
        fake_stack.start = AsyncMock()
        fake_stack.stop = AsyncMock()
        fake_stack.channel_manager = Mock()

        outbound = Mock()
        outbound.kind = "completed"
        outbound.content = "done"
        outbound.error = None
        outbound.metadata = {"duration_seconds": 0.5}
        fake_stack.channel_manager.send_and_wait = AsyncMock(return_value=outbound)

        with patch("xagent.cli.commands.run.build_runtime_agent", return_value=fake_agent), patch(
            "xagent.cli.commands.run.build_runtime_stack", return_value=fake_stack
        ), patch("xagent.cli.commands.run.render_final_message") as render_final, patch(
            "xagent.cli.commands.run.render_turn_status"
        ) as render_status:
            from xagent.cli.commands.run import _run

            await _run("hello")

        fake_stack.start.assert_awaited_once()
        fake_stack.channel_manager.send_and_wait.assert_awaited_once()
        fake_stack.stop.assert_awaited()
        render_final.assert_called_once()
        render_status.assert_called_once_with(0.5, fake_agent)


class _FakeAgent:
    """build_runtime_stack 单元测试使用的最小 agent 替身。"""

    def __init__(self, cwd: str) -> None:
        self.cwd = cwd
        self.middlewares: list = []
        self.trace_recorder = None


class _FakeManagedManager:
    """测试 manager-facing adapter 用的最小 manager 替身。"""

    def __init__(self) -> None:
        self.unsubscribed = False
        self.last_send = None

    def create_session(self, *, session_key=None) -> str:
        return "managed-session"

    def send_inbound_and_wait(self, message, *, timeout_seconds: float = 30.0):
        self.last_send = (message, timeout_seconds)
        return make_terminal(
            correlation_id=message.correlation_id,
            session_id="managed-session",
            session_key=message.session_key,
            channel=message.channel,
            chat_id=message.chat_id,
            source=message.source,
            content="done",
        )

    def open_outbound_stream(self, message, *, terminal_only: bool = False):
        event_queue = queue.Queue()
        event_queue.put_nowait(
            make_progress(
                correlation_id=message.correlation_id,
                session_id="managed-session",
                session_key=message.session_key,
                channel=message.channel,
                chat_id=message.chat_id,
                source=message.source,
                event="text_delta",
                content="hello",
            )
        )
        event_queue.put_nowait(
            make_terminal(
                correlation_id=message.correlation_id,
                session_id="managed-session",
                session_key=message.session_key,
                channel=message.channel,
                chat_id=message.chat_id,
                source=message.source,
                content="done",
            )
        )

        def _unsubscribe() -> None:
            self.unsubscribed = True

        return event_queue, _unsubscribe

    def close(self) -> None:
        return None


class BuildRuntimeStackTests(unittest.IsolatedAsyncioTestCase):
    """``build_runtime_stack`` 端到端组装测试：router + channel_manager + runtime.handle。"""

    async def test_send_and_wait_returns_terminal_with_message(self) -> None:
        """走完整真实栈：publish_inbound -> router -> runtime.handle -> terminal outbound。"""
        from xagent.cli.runtime import build_runtime_stack

        expected_message = Message(
            role="assistant", content=[TextPart(text="hello from agent")]
        )

        async def _runner(
            agent, prompt, *, on_assistant_delta=None, on_tool_use=None, on_tool_result=None
        ):
            # 绕过真实 run_agent_turn_stream；但 build_runtime_stack 内部用 partial
            # 把 agent 绑为首参，这里通过 monkeypatch run_agent_turn_stream 来覆盖。
            return expected_message, 0.321

        with tempfile.TemporaryDirectory() as tmp:
            agent = _FakeAgent(tmp)
            with patch(
                "xagent.cli.runtime.run_agent_turn_stream", side_effect=_runner
            ):
                stack = build_runtime_stack(agent, session_id="run", cwd=tmp)
                await stack.start()
                try:
                    inbound = InboundMessage(
                        content="hi",
                        source="cli.run",
                        channel="cli",
                        sender_id="cli",
                        chat_id="run",
                    )
                    outbound = await stack.channel_manager.send_and_wait(
                        inbound, timeout=5.0
                    )
                finally:
                    await stack.stop()

        self.assertEqual(outbound.kind, "completed")
        self.assertEqual(outbound.correlation_id, inbound.correlation_id)
        self.assertIs(outbound.metadata.get("message"), expected_message)
        self.assertAlmostEqual(outbound.metadata.get("duration_seconds"), 0.321)

    async def test_send_and_wait_returns_failed_when_runner_raises(self) -> None:
        """turn_runner 抛异常 -> terminal kind=failed，且 send_and_wait 不阻塞。"""
        from xagent.cli.runtime import build_runtime_stack

        async def _runner(
            agent, prompt, *, on_assistant_delta=None, on_tool_use=None, on_tool_result=None
        ):
            raise RuntimeError("boom")

        with tempfile.TemporaryDirectory() as tmp:
            agent = _FakeAgent(tmp)
            with patch(
                "xagent.cli.runtime.run_agent_turn_stream", side_effect=_runner
            ):
                stack = build_runtime_stack(agent, session_id="run", cwd=tmp)
                await stack.start()
                try:
                    inbound = InboundMessage(
                        content="hi",
                        source="cli.run",
                        channel="cli",
                        sender_id="cli",
                        chat_id="run",
                    )
                    outbound = await stack.channel_manager.send_and_wait(
                        inbound, timeout=5.0
                    )
                finally:
                    await stack.stop()

        self.assertEqual(outbound.kind, "failed")
        self.assertIn("boom", outbound.error or "")

    async def test_build_runtime_stack_registers_trace_channel(self) -> None:
        from xagent.channel.trace_channel import TraceChannel
        from xagent.cli.runtime import build_runtime_stack

        with tempfile.TemporaryDirectory() as tmp:
            agent = _FakeAgent(tmp)
            stack = build_runtime_stack(agent, session_id="run", cwd=tmp)
            try:
                channel = stack.channel_manager.get_channel("trace")
                self.assertIsInstance(channel, TraceChannel)
                self.assertTrue(channel.observe_all)
            finally:
                await stack.stop()


class BuildManagedRuntimeAdapterTests(unittest.TestCase):
    """`build_managed_runtime_boundary` 返回新的 manager-facing adapter。"""

    def test_build_managed_runtime_boundary_returns_manager_facing_adapter(self) -> None:
        from xagent.cli.runtime import ManagerFacingRuntimeAdapter, build_managed_runtime_boundary

        fake_manager = _FakeManagedManager()
        with patch("xagent.cli.runtime.SessionRuntimeManager", return_value=fake_manager):
            adapter = build_managed_runtime_boundary("/tmp/xagent")

        self.assertIsInstance(adapter, ManagerFacingRuntimeAdapter)
        self.assertEqual(adapter.create_session(), "managed-session")

    def test_manager_facing_adapter_compat_open_response_stream(self) -> None:
        from xagent.cli.runtime import ManagerFacingRuntimeAdapter

        adapter = ManagerFacingRuntimeAdapter(manager=_FakeManagedManager())
        inbound = InboundMessage(
            content="hello",
            source="gateway.http",
            channel="gateway",
            sender_id="gateway",
            chat_id="managed-session",
            session_key_override="managed-session",
        )

        outbound_queue, unsubscribe = adapter.open_response_stream(inbound)
        try:
            first = outbound_queue.get(timeout=2)
            second = outbound_queue.get(timeout=2)
        finally:
            unsubscribe()

        self.assertEqual(first.kind, "delta")
        self.assertEqual(first.content, "hello")
        self.assertEqual(second.kind, "completed")
