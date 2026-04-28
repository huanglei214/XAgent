"""``ChannelManager`` 单元测试。

覆盖 openspec 0001-simplify-bus 阶段 4 新增的
``xagent.agent.runtime.channel_manager.ChannelManager`` 与
``xagent.channel.base.BaseChannel``。
"""

import asyncio
import unittest

from xagent.agent.runtime.channel_manager import ChannelManager
from xagent.bus.messages import (
    InboundMessage,
    OutboundMessage,
    make_progress,
    make_terminal,
)
from xagent.bus.queue import MessageBus
from xagent.channel.base import BaseChannel


class _RecordingChannel(BaseChannel):
    """测试用 Channel：把收到的 outbound 原样存入列表。"""

    def __init__(self, bus: MessageBus, name: str = "test") -> None:
        super().__init__(bus)
        self.name = name
        self.sent: list[OutboundMessage] = []
        self.started = False
        self.stopped = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> None:
        self.stopped = True

    async def send(self, msg: OutboundMessage) -> None:
        self.sent.append(msg)


class _ObserverChannel(_RecordingChannel):
    """测试用 observer channel：应收到所有 outbound。"""

    observe_all = True


class _RaisingChannel(BaseChannel):
    """测试用 Channel：send 时抛异常，验证 ChannelManager 的容错。"""

    def __init__(self, bus: MessageBus) -> None:
        super().__init__(bus)
        self.name = "raising"

    async def start(self) -> None:  # pragma: no cover - 无分支
        pass

    async def stop(self) -> None:  # pragma: no cover - 无分支
        pass

    async def send(self, msg: OutboundMessage) -> None:
        raise RuntimeError("boom")


async def _produce(bus: MessageBus, msg: OutboundMessage, *, delay: float = 0.0) -> None:
    """测试辅助：延迟一会儿再把一条 outbound 投到 bus。"""
    if delay > 0:
        await asyncio.sleep(delay)
    await bus.publish_outbound(msg)


class ChannelManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_send_and_wait_returns_terminal_for_same_correlation(self) -> None:
        """send_and_wait 应阻塞直到收到同 correlation_id 的 _terminal outbound。"""
        bus = MessageBus()
        cm = ChannelManager(bus)
        await cm.start()

        inbound = InboundMessage(
            content="hi", source="test", channel="cli", chat_id="c1"
        )
        # 模拟 AgentRunner：从 inbound 队列取消息，再 publish 一条 terminal
        async def fake_runner() -> None:
            got = await bus.consume_inbound()
            await bus.publish_outbound(
                make_progress(
                    correlation_id=got.correlation_id,
                    session_id="s",
                    session_key=got.session_key,
                    channel=got.channel,
                    chat_id=got.chat_id,
                    source="runtime",
                    event="turn_start",
                )
            )
            await bus.publish_outbound(
                make_terminal(
                    correlation_id=got.correlation_id,
                    session_id="s",
                    session_key=got.session_key,
                    channel=got.channel,
                    chat_id=got.chat_id,
                    source="runtime",
                    content="done",
                )
            )

        runner = asyncio.create_task(fake_runner())
        try:
            final = await cm.send_and_wait(inbound, timeout=2.0)
        finally:
            await runner
            await cm.stop()

        self.assertEqual(final.content, "done")
        self.assertTrue(final.metadata.get("_terminal"))

    async def test_send_and_wait_timeout_raises(self) -> None:
        """无人生产 outbound 时 send_and_wait 应超时抛 TimeoutError。"""
        bus = MessageBus()
        cm = ChannelManager(bus)
        await cm.start()
        try:
            inbound = InboundMessage(content="x", source="t", channel="cli")
            with self.assertRaises(asyncio.TimeoutError):
                await cm.send_and_wait(inbound, timeout=0.05)
        finally:
            await cm.stop()

    async def test_open_response_stream_yields_progress_and_terminal(self) -> None:
        """open_response_stream 应按序 yield 进度+终止消息，并在终止后结束迭代。"""
        bus = MessageBus()
        cm = ChannelManager(bus)
        await cm.start()

        inbound = InboundMessage(content="hi", source="t", channel="cli", chat_id="c1")

        async def fake_runner() -> None:
            got = await bus.consume_inbound()
            for i in range(3):
                await bus.publish_outbound(
                    make_progress(
                        correlation_id=got.correlation_id,
                        session_id="s",
                        session_key=got.session_key,
                        channel=got.channel,
                        chat_id=got.chat_id,
                        source="runtime",
                        event="text_delta",
                        content=f"chunk{i}",
                    )
                )
            await bus.publish_outbound(
                make_terminal(
                    correlation_id=got.correlation_id,
                    session_id="s",
                    session_key=got.session_key,
                    channel=got.channel,
                    chat_id=got.chat_id,
                    source="runtime",
                    content="final",
                )
            )

        runner = asyncio.create_task(fake_runner())
        try:
            collected: list[OutboundMessage] = []
            async for msg in cm.open_response_stream(inbound):
                collected.append(msg)
        finally:
            await runner
            await cm.stop()

        self.assertEqual(len(collected), 4)
        self.assertEqual([m.content for m in collected[:3]], ["chunk0", "chunk1", "chunk2"])
        self.assertTrue(collected[-1].metadata.get("_terminal"))

    async def test_concurrent_requests_are_isolated_by_correlation_id(self) -> None:
        """两个并发 request 的响应只能流向各自的 per-request queue。"""
        bus = MessageBus()
        cm = ChannelManager(bus)
        await cm.start()

        in_a = InboundMessage(content="A", source="t", channel="cli", chat_id="c1")
        in_b = InboundMessage(content="B", source="t", channel="cli", chat_id="c2")

        async def fake_runner() -> None:
            # 任意顺序地产出两条响应，验证 ChannelManager 能按 correlation_id 正确路由
            await bus.consume_inbound()
            await bus.consume_inbound()
            await bus.publish_outbound(
                make_terminal(
                    correlation_id=in_b.correlation_id,
                    session_id="s",
                    session_key=in_b.session_key,
                    channel=in_b.channel,
                    chat_id=in_b.chat_id,
                    source="runtime",
                    content="B-done",
                )
            )
            await bus.publish_outbound(
                make_terminal(
                    correlation_id=in_a.correlation_id,
                    session_id="s",
                    session_key=in_a.session_key,
                    channel=in_a.channel,
                    chat_id=in_a.chat_id,
                    source="runtime",
                    content="A-done",
                )
            )

        runner = asyncio.create_task(fake_runner())
        try:
            final_a, final_b = await asyncio.gather(
                cm.send_and_wait(in_a, timeout=2.0),
                cm.send_and_wait(in_b, timeout=2.0),
            )
        finally:
            await runner
            await cm.stop()

        self.assertEqual(final_a.content, "A-done")
        self.assertEqual(final_a.correlation_id, in_a.correlation_id)
        self.assertEqual(final_b.content, "B-done")
        self.assertEqual(final_b.correlation_id, in_b.correlation_id)

    async def test_response_scope_is_cleaned_after_send_and_wait(self) -> None:
        """send_and_wait 结束后，注册表内不应残留该 correlation_id。"""
        bus = MessageBus()
        cm = ChannelManager(bus)
        await cm.start()

        inbound = InboundMessage(content="hi", source="t", channel="cli", chat_id="c")

        async def fake_runner() -> None:
            got = await bus.consume_inbound()
            await bus.publish_outbound(
                make_terminal(
                    correlation_id=got.correlation_id,
                    session_id="s",
                    session_key=got.session_key,
                    channel=got.channel,
                    chat_id=got.chat_id,
                    source="runtime",
                    content="ok",
                )
            )

        runner = asyncio.create_task(fake_runner())
        try:
            await cm.send_and_wait(inbound, timeout=2.0)
        finally:
            await runner
            await cm.stop()

        self.assertNotIn(inbound.correlation_id, cm._response_registry)

    async def test_response_scope_cleaned_after_stream_exhausted(self) -> None:
        """open_response_stream 迭代完成后，注册表应清空。"""
        bus = MessageBus()
        cm = ChannelManager(bus)
        await cm.start()

        inbound = InboundMessage(content="hi", source="t", channel="cli", chat_id="c")

        async def fake_runner() -> None:
            got = await bus.consume_inbound()
            await bus.publish_outbound(
                make_terminal(
                    correlation_id=got.correlation_id,
                    session_id="s",
                    session_key=got.session_key,
                    channel=got.channel,
                    chat_id=got.chat_id,
                    source="runtime",
                    content="ok",
                )
            )

        runner = asyncio.create_task(fake_runner())
        try:
            async for _ in cm.open_response_stream(inbound):
                pass
        finally:
            await runner
            await cm.stop()

        self.assertNotIn(inbound.correlation_id, cm._response_registry)

    async def test_duplicate_correlation_id_raises(self) -> None:
        """同一个 correlation_id 同时被注册两次应抛 RuntimeError。"""
        bus = MessageBus()
        cm = ChannelManager(bus)
        await cm.start()
        try:
            inbound = InboundMessage(
                content="dup", source="t", channel="cli", chat_id="c"
            )
            # 手动占用 scope
            await cm._register_response(inbound.correlation_id)
            try:
                with self.assertRaises(RuntimeError):
                    await cm.send_and_wait(inbound, timeout=0.05)
            finally:
                await cm._unregister_response(inbound.correlation_id)
        finally:
            await cm.stop()

    async def test_dispatch_forwards_to_channel_by_name(self) -> None:
        """dispatch loop 应按 msg.channel 找到同名 Channel 并调用 send。"""
        bus = MessageBus()
        cm = ChannelManager(bus)
        recorder = _RecordingChannel(bus, name="recorder")
        cm.register_channel(recorder)
        await cm.start()

        msg = make_terminal(
            correlation_id="c1",
            session_id="s",
            session_key="k",
            channel="recorder",
            chat_id="c",
            source="t",
            content="payload",
        )
        await bus.publish_outbound(msg)
        # 让 dispatch loop 处理一轮
        for _ in range(20):
            if recorder.sent:
                break
            await asyncio.sleep(0.01)
        await cm.stop()

        self.assertEqual(len(recorder.sent), 1)
        self.assertIs(recorder.sent[0], msg)

    async def test_channel_send_exception_is_swallowed(self) -> None:
        """Channel.send 抛异常不应影响 per-request queue 的 fan-out。"""
        bus = MessageBus()
        cm = ChannelManager(bus)
        cm.register_channel(_RaisingChannel(bus))
        await cm.start()

        inbound = InboundMessage(
            content="hi", source="t", channel="raising", chat_id="c"
        )

        async def fake_runner() -> None:
            got = await bus.consume_inbound()
            await bus.publish_outbound(
                make_terminal(
                    correlation_id=got.correlation_id,
                    session_id="s",
                    session_key=got.session_key,
                    channel=got.channel,
                    chat_id=got.chat_id,
                    source="runtime",
                    content="ok",
                )
            )

        runner = asyncio.create_task(fake_runner())
        try:
            final = await cm.send_and_wait(inbound, timeout=2.0)
        finally:
            await runner
            await cm.stop()

        self.assertEqual(final.content, "ok")

    async def test_observer_channel_receives_messages_for_all_channels(self) -> None:
        """observe_all=True 的 channel 应旁路收到所有 outbound。"""
        bus = MessageBus()
        cm = ChannelManager(bus)
        direct = _RecordingChannel(bus, name="cli")
        observer = _ObserverChannel(bus, name="trace")
        cm.register_channel(direct)
        cm.register_channel(observer)
        await cm.start()

        msg = make_terminal(
            correlation_id="c1",
            session_id="s",
            session_key="k",
            channel="cli",
            chat_id="c",
            source="runtime",
            content="payload",
        )
        await bus.publish_outbound(msg)
        for _ in range(20):
            if direct.sent and observer.sent:
                break
            await asyncio.sleep(0.01)
        await cm.stop()

        self.assertEqual(len(direct.sent), 1)
        self.assertEqual(len(observer.sent), 1)
        self.assertIs(observer.sent[0], msg)

    async def test_register_channel_requires_name(self) -> None:
        """没有 name 的 channel 注册时应直接报错。"""
        bus = MessageBus()
        cm = ChannelManager(bus)

        class _Nameless(BaseChannel):
            async def start(self) -> None:  # pragma: no cover
                pass

            async def stop(self) -> None:  # pragma: no cover
                pass

            async def send(self, msg: OutboundMessage) -> None:  # pragma: no cover
                pass

        with self.assertRaises(ValueError):
            cm.register_channel(_Nameless(bus))

    async def test_start_is_idempotent(self) -> None:
        """重复 start 不应创建多个 dispatch 协程。"""
        bus = MessageBus()
        cm = ChannelManager(bus)
        await cm.start()
        first_task = cm._task
        await cm.start()
        self.assertIs(cm._task, first_task)
        await cm.stop()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
