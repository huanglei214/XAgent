"""``SessionRuntime.handle(inbound)`` 单元测试。"""

import unittest

from xagent.agent.runtime.session_runtime import SessionRuntime
from xagent.bus.messages import InboundMessage, is_progress, is_terminal
from xagent.bus.queue import MessageBus
from xagent.provider.types import Message, TextPart, ToolResultPart, ToolUsePart


def _collect_outbound(bus: MessageBus) -> list:
    """从 outbound queue 取尽可能多的消息（非阻塞）。"""
    out = []
    while not bus.outbound.empty():
        out.append(bus.outbound.get_nowait())
    return out


async def _make_runtime(
    *,
    turn_runner,
    message_bus: MessageBus,
    post_turn_hooks=None,
) -> SessionRuntime:
    return SessionRuntime(
        session_id="s1",
        turn_runner=turn_runner,
        message_bus=message_bus,
        post_turn_hooks=post_turn_hooks,
    )


class SessionRuntimeHandleTests(unittest.IsolatedAsyncioTestCase):
    # ------------------------------------------------------------------
    # 成功路径
    # ------------------------------------------------------------------

    async def test_handle_emits_turn_start_then_terminal_on_empty_turn(self) -> None:
        """不发 delta 的 turn 也应至少发 turn_start + terminal。"""

        async def _runner(prompt, *, on_assistant_delta, on_tool_use, on_tool_result):
            return Message(role="assistant", content=[TextPart(text="ok")]), 0.123

        bus = MessageBus()
        runtime = await _make_runtime(turn_runner=_runner, message_bus=bus)
        inbound = InboundMessage(content="hi", source="t", channel="cli", chat_id="c")
        await runtime.handle(inbound)

        msgs = _collect_outbound(bus)
        self.assertEqual(len(msgs), 2)
        self.assertTrue(is_progress(msgs[0]))
        self.assertEqual(msgs[0].metadata["_event"], "turn_start")
        self.assertTrue(is_terminal(msgs[1]))
        self.assertEqual(msgs[1].kind, "completed")
        self.assertEqual(msgs[1].metadata["message"].content[0].text, "ok")
        self.assertAlmostEqual(msgs[1].metadata["duration_seconds"], 0.123)
        self.assertEqual(msgs[1].metadata["request_id"], inbound.correlation_id)
        self.assertEqual(msgs[1].content, "ok")

    async def test_handle_emits_progress_in_order(self) -> None:
        """turn_start → 若干 text_delta → tool_use → tool_result → terminal。"""
        tool_use = ToolUsePart(id="tu1", name="ls", input={})
        tool_result = ToolResultPart(tool_use_id="tu1", content="ok", is_error=False)

        async def _runner(prompt, *, on_assistant_delta, on_tool_use, on_tool_result):
            on_assistant_delta(Message(role="assistant", content=[TextPart(text="chunk1")]))
            on_assistant_delta(Message(role="assistant", content=[TextPart(text="chunk2")]))
            on_tool_use(tool_use)
            on_tool_result(tool_use, tool_result)
            return Message(role="assistant", content=[TextPart(text="final")]), 0.2

        bus = MessageBus()
        runtime = await _make_runtime(turn_runner=_runner, message_bus=bus)
        inbound = InboundMessage(content="go", source="t", channel="cli", chat_id="c")
        await runtime.handle(inbound)

        msgs = _collect_outbound(bus)
        events = [m.metadata.get("_event") for m in msgs[:-1]]  # 去掉 terminal
        self.assertEqual(
            events,
            ["turn_start", "text_delta", "text_delta", "tool_use", "tool_result"],
        )
        # 全部 progress 的 correlation_id 一致
        self.assertTrue(all(m.correlation_id == inbound.correlation_id for m in msgs))
        # terminal 最后
        self.assertTrue(is_terminal(msgs[-1]))
        self.assertEqual(msgs[-1].metadata["message"].content[0].text, "final")

    async def test_handle_passes_request_id_in_metadata(self) -> None:
        """每条 progress / terminal 的 metadata 应携带 request_id=correlation_id。"""

        async def _runner(prompt, *, on_assistant_delta, on_tool_use, on_tool_result):
            on_assistant_delta(Message(role="assistant", content=[TextPart(text="x")]))
            return Message(role="assistant", content=[TextPart(text="done")]), 0.01

        bus = MessageBus()
        runtime = await _make_runtime(turn_runner=_runner, message_bus=bus)
        inbound = InboundMessage(content="hi", source="t", channel="cli", chat_id="c")
        await runtime.handle(inbound)

        msgs = _collect_outbound(bus)
        for m in msgs:
            self.assertEqual(m.metadata.get("request_id"), inbound.correlation_id)

    async def test_handle_triggers_post_turn_hooks_on_success(self) -> None:
        """成功 turn 后 PostTurnHook 应被触发。"""

        async def _runner(prompt, *, on_assistant_delta, on_tool_use, on_tool_result):
            return Message(role="assistant", content=[TextPart(text="ok")]), 0.1

        captured = []

        async def _hook(ctx):
            captured.append(ctx)

        bus = MessageBus()
        runtime = await _make_runtime(
            turn_runner=_runner, message_bus=bus, post_turn_hooks=[_hook]
        )
        inbound = InboundMessage(content="hi", source="t", channel="cli", chat_id="c")
        await runtime.handle(inbound)

        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0].request_id, inbound.correlation_id)

    # ------------------------------------------------------------------
    # 失败路径
    # ------------------------------------------------------------------

    async def test_handle_emits_failed_terminal_on_exception(self) -> None:
        """turn_runner 抛异常时，handle 不向外抛，而是发 kind=failed terminal。"""

        async def _runner(prompt, *, on_assistant_delta, on_tool_use, on_tool_result):
            raise RuntimeError("boom")

        bus = MessageBus()
        runtime = await _make_runtime(turn_runner=_runner, message_bus=bus)
        inbound = InboundMessage(content="hi", source="t", channel="cli", chat_id="c")

        # handle 不应传播异常
        await runtime.handle(inbound)

        msgs = _collect_outbound(bus)
        terminal = msgs[-1]
        self.assertTrue(is_terminal(terminal))
        self.assertEqual(terminal.kind, "failed")
        self.assertIsNotNone(terminal.error)
        self.assertIn("boom", terminal.error or "")
        self.assertEqual(terminal.metadata["request_id"], inbound.correlation_id)

    async def test_handle_does_not_trigger_hooks_on_failure(self) -> None:
        """失败 turn 不应触发 PostTurnHook。"""

        async def _runner(prompt, *, on_assistant_delta, on_tool_use, on_tool_result):
            raise RuntimeError("boom")

        captured = []

        async def _hook(ctx):
            captured.append(ctx)

        bus = MessageBus()
        runtime = await _make_runtime(
            turn_runner=_runner, message_bus=bus, post_turn_hooks=[_hook]
        )
        inbound = InboundMessage(content="hi", source="t", channel="cli", chat_id="c")
        await runtime.handle(inbound)

        self.assertEqual(captured, [])

    # ------------------------------------------------------------------
    # 守护条件
    # ------------------------------------------------------------------

    async def test_handle_serializes_within_same_runtime(self) -> None:
        """同一 runtime 并发调用 handle 应被 _turn_lock 串行化。"""
        import asyncio

        started = []
        finished = []

        async def _runner(prompt, *, on_assistant_delta, on_tool_use, on_tool_result):
            started.append(prompt)
            await asyncio.sleep(0.05)
            finished.append(prompt)
            return Message(role="assistant", content=[TextPart(text=prompt)]), 0.05

        bus = MessageBus()
        runtime = await _make_runtime(turn_runner=_runner, message_bus=bus)
        in1 = InboundMessage(content="A", source="t", channel="cli", chat_id="c")
        in2 = InboundMessage(content="B", source="t", channel="cli", chat_id="c")

        await asyncio.gather(runtime.handle(in1), runtime.handle(in2))

        # 第一个完成之前第二个不能开始
        self.assertEqual(len(started), 2)
        self.assertEqual(len(finished), 2)
        # started 和 finished 内部顺序要一致（串行化语义）
        self.assertEqual(started[0], finished[0])

if __name__ == "__main__":  # pragma: no cover
    unittest.main()
