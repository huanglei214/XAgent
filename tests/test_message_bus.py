"""MessageBus（两条 asyncio.Queue）单元测试。

覆盖阶段 3 新增的 ``xagent.bus.queue.MessageBus`` 与
``xagent.bus.messages`` 中的辅助构造函数（``make_progress`` / ``make_terminal``）。
"""

import asyncio
import unittest

from xagent.bus.messages import (
    META_EVENT,
    META_PROGRESS,
    META_TERMINAL,
    META_TOOL_HINT,
    InboundMessage,
    OutboundMessage,
    is_progress,
    is_terminal,
    make_progress,
    make_terminal,
)
from xagent.bus.queue import MessageBus


def _make_outbound(content: str = "", kind: str = "message") -> OutboundMessage:
    """测试辅助：构造一条最小化的 OutboundMessage。"""
    return OutboundMessage(
        kind=kind,
        correlation_id="c1",
        session_id="s1",
        session_key="k1",
        source="test",
        channel="test",
        chat_id="chat1",
        content=content,
    )


class MessageBusTests(unittest.IsolatedAsyncioTestCase):
    async def test_publish_and_consume_inbound(self) -> None:
        """publish_inbound 与 consume_inbound 应能完成端到端投递。"""
        bus = MessageBus()
        inbound = InboundMessage(content="hi", source="test", channel="test")
        await bus.publish_inbound(inbound)
        self.assertEqual(bus.inbound_qsize(), 1)
        got = await bus.consume_inbound()
        self.assertIs(got, inbound)
        self.assertEqual(bus.inbound_qsize(), 0)

    async def test_publish_and_consume_outbound(self) -> None:
        """publish_outbound 与 consume_outbound 应能完成端到端投递。"""
        bus = MessageBus()
        outbound = _make_outbound(content="ok")
        await bus.publish_outbound(outbound)
        self.assertEqual(bus.outbound_qsize(), 1)
        got = await bus.consume_outbound()
        self.assertIs(got, outbound)
        self.assertEqual(bus.outbound_qsize(), 0)

    async def test_inbound_is_fifo(self) -> None:
        """inbound 队列必须按投递顺序交付。"""
        bus = MessageBus()
        for i in range(5):
            await bus.publish_inbound(
                InboundMessage(content=f"m{i}", source="t", channel="t")
            )
        received = [await bus.consume_inbound() for _ in range(5)]
        self.assertEqual([m.content for m in received], ["m0", "m1", "m2", "m3", "m4"])

    async def test_outbound_is_fifo(self) -> None:
        """outbound 队列必须按投递顺序交付。"""
        bus = MessageBus()
        for i in range(5):
            await bus.publish_outbound(_make_outbound(content=f"m{i}"))
        received = [await bus.consume_outbound() for _ in range(5)]
        self.assertEqual([m.content for m in received], ["m0", "m1", "m2", "m3", "m4"])

    async def test_inbound_and_outbound_are_independent(self) -> None:
        """两条队列必须彼此独立，互不串扰。"""
        bus = MessageBus()
        await bus.publish_inbound(InboundMessage(content="in", source="t", channel="t"))
        await bus.publish_outbound(_make_outbound(content="out"))
        self.assertEqual(bus.inbound_qsize(), 1)
        self.assertEqual(bus.outbound_qsize(), 1)
        got_in = await bus.consume_inbound()
        got_out = await bus.consume_outbound()
        self.assertEqual(got_in.content, "in")
        self.assertEqual(got_out.content, "out")

    async def test_consumer_waits_for_producer(self) -> None:
        """消费者在队列为空时应该阻塞，直到 producer publish 后才返回。"""
        bus = MessageBus()

        async def producer() -> None:
            # 让出控制权，确保 consumer 先 await 上队列
            await asyncio.sleep(0.01)
            await bus.publish_inbound(
                InboundMessage(content="late", source="t", channel="t")
            )

        consumer_task = asyncio.create_task(bus.consume_inbound())
        producer_task = asyncio.create_task(producer())
        result, _ = await asyncio.gather(consumer_task, producer_task)
        self.assertEqual(result.content, "late")


class OutboundMessageBuildersTests(unittest.TestCase):
    def test_make_progress_sets_progress_and_event_flags(self) -> None:
        """make_progress 应自动打上 _progress 与 _event 标记。"""
        msg = make_progress(
            correlation_id="c1",
            session_id="s1",
            session_key="k1",
            channel="cli",
            chat_id="c",
            source="runtime",
            event="tool_use",
            content="running tool",
        )
        self.assertTrue(is_progress(msg))
        self.assertFalse(is_terminal(msg))
        self.assertEqual(msg.metadata[META_PROGRESS], True)
        self.assertEqual(msg.metadata[META_EVENT], "tool_use")
        self.assertNotIn(META_TOOL_HINT, msg.metadata)

    def test_make_progress_supports_tool_hint_and_extra_metadata(self) -> None:
        """make_progress 支持 tool_hint / 额外 metadata 合并。"""
        msg = make_progress(
            correlation_id="c1",
            session_id="s1",
            session_key="k1",
            channel="cli",
            chat_id="c",
            source="runtime",
            event="tool_use",
            tool_hint=True,
            extra_metadata={"tool_name": "bash"},
        )
        self.assertTrue(msg.metadata[META_TOOL_HINT])
        self.assertEqual(msg.metadata["tool_name"], "bash")

    def test_make_terminal_sets_terminal_flag(self) -> None:
        """make_terminal 应自动打上 _terminal 标记且默认 kind=completed。"""
        msg = make_terminal(
            correlation_id="c1",
            session_id="s1",
            session_key="k1",
            channel="cli",
            chat_id="c",
            source="runtime",
            content="final answer",
        )
        self.assertTrue(is_terminal(msg))
        self.assertFalse(is_progress(msg))
        self.assertEqual(msg.kind, "completed")
        self.assertEqual(msg.metadata[META_TERMINAL], True)
        self.assertEqual(msg.content, "final answer")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
