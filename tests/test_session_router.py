"""SessionRouter 单元测试。"""

import asyncio
import unittest

from xagent.agent.runtime.session_router import SessionRouter
from xagent.bus.messages import InboundMessage, is_terminal
from xagent.bus.queue import MessageBus


class _FakeRuntime:
    """测试用 runtime：记录收到的 inbound；可配置 handle 的延迟和异常。"""

    def __init__(
        self,
        *,
        delay: float = 0.0,
        raise_on_call: int = -1,
    ) -> None:
        self.calls: list[InboundMessage] = []
        self.delay = delay
        self.raise_on_call = raise_on_call
        self._call_count = 0
        self.running = asyncio.Event()
        self.finished = asyncio.Event()

    async def handle(self, inbound: InboundMessage) -> None:
        self._call_count += 1
        self.running.set()
        try:
            if self.delay > 0:
                await asyncio.sleep(self.delay)
            if self._call_count - 1 == self.raise_on_call:
                raise RuntimeError(f"boom-{self._call_count}")
            self.calls.append(inbound)
        finally:
            self.finished.set()


async def _default_provider(
    session_id: str,
    *,
    runtimes: dict[str, _FakeRuntime],
) -> _FakeRuntime:
    """provider：返回测试预置的 runtime 字典对应条目。"""
    if session_id not in runtimes:
        runtimes[session_id] = _FakeRuntime()
    return runtimes[session_id]


class SessionRouterTests(unittest.IsolatedAsyncioTestCase):
    # ------------------------------------------------------------------
    # 基本路由
    # ------------------------------------------------------------------

    async def test_single_session_messages_processed_fifo(self) -> None:
        """同一 session 的多条 inbound 应严格按入队顺序串行交给 runtime.handle。"""
        bus = MessageBus()
        runtimes: dict[str, _FakeRuntime] = {"s1": _FakeRuntime(delay=0.01)}

        router = SessionRouter(
            bus=bus,
            resolver=lambda key: "s1",
            provider=lambda sid: _default_provider(sid, runtimes=runtimes),
        )
        await router.start()
        try:
            for i in range(5):
                await bus.publish_inbound(
                    InboundMessage(content=f"m{i}", source="t", channel="cli")
                )
            # 等待全部完成
            for _ in range(100):
                if len(runtimes["s1"].calls) == 5:
                    break
                await asyncio.sleep(0.01)
        finally:
            await router.stop()

        self.assertEqual([m.content for m in runtimes["s1"].calls], ["m0", "m1", "m2", "m3", "m4"])

    async def test_two_sessions_run_concurrently(self) -> None:
        """不同 session 之间应并发：session A 的慢 turn 不能阻塞 session B。"""
        bus = MessageBus()
        slow = _FakeRuntime(delay=0.2)
        fast = _FakeRuntime(delay=0.0)
        runtimes = {"slow": slow, "fast": fast}

        def _resolver(key: str) -> str:
            # key 格式 "cli:chat_id"；直接用 chat_id 当 session_id
            return key.split(":", 1)[1]

        router = SessionRouter(
            bus=bus,
            resolver=_resolver,
            provider=lambda sid: _default_provider(sid, runtimes=runtimes),
        )
        await router.start()
        try:
            # 先投 slow，再投 fast；如果两者串行执行，fast 要等 slow 跑完（0.2s）
            await bus.publish_inbound(
                InboundMessage(content="slow-1", source="t", channel="cli", chat_id="slow")
            )
            await bus.publish_inbound(
                InboundMessage(content="fast-1", source="t", channel="cli", chat_id="fast")
            )
            # fast 应该在 slow 还在跑时就完成
            await asyncio.wait_for(fast.finished.wait(), timeout=0.5)
            # 此时 slow 应仍在运行或刚开始
            self.assertEqual(len(fast.calls), 1)
            # 等 slow 完成
            await asyncio.wait_for(slow.finished.wait(), timeout=1.0)
            self.assertEqual(len(slow.calls), 1)
        finally:
            await router.stop()

    # ------------------------------------------------------------------
    # 异常兜底
    # ------------------------------------------------------------------

    async def test_resolver_exception_emits_terminal_error_and_continues(self) -> None:
        """resolver 抛异常时发 terminal(kind=failed)，并继续消费后续 inbound。"""
        bus = MessageBus()
        call_count = {"n": 0}
        runtimes: dict[str, _FakeRuntime] = {}

        def _resolver(key: str) -> str:
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise ValueError("no such session")
            return "s1"

        router = SessionRouter(
            bus=bus,
            resolver=_resolver,
            provider=lambda sid: _default_provider(sid, runtimes=runtimes),
        )
        await router.start()
        try:
            bad = InboundMessage(content="bad", source="t", channel="cli", chat_id="c1")
            good = InboundMessage(content="good", source="t", channel="cli", chat_id="c2")
            await bus.publish_inbound(bad)
            # 等 terminal
            term = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
            self.assertTrue(is_terminal(term))
            self.assertEqual(term.kind, "failed")
            self.assertEqual(term.correlation_id, bad.correlation_id)
            self.assertIn("session resolution failed", term.error or "")

            # 后续 inbound 正常处理
            await bus.publish_inbound(good)
            for _ in range(100):
                if runtimes.get("s1") and runtimes["s1"].calls:
                    break
                await asyncio.sleep(0.01)
            self.assertEqual(runtimes["s1"].calls[0].content, "good")
        finally:
            await router.stop()

    async def test_provider_exception_emits_terminal_error(self) -> None:
        """provider 抛异常时发 terminal(kind=failed)。"""
        bus = MessageBus()

        async def _bad_provider(session_id: str):  # noqa: ARG001
            raise RuntimeError("provider down")

        router = SessionRouter(
            bus=bus,
            resolver=lambda key: "s1",
            provider=_bad_provider,
        )
        await router.start()
        try:
            inbound = InboundMessage(content="x", source="t", channel="cli", chat_id="c1")
            await bus.publish_inbound(inbound)
            term = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
            self.assertTrue(is_terminal(term))
            self.assertEqual(term.kind, "failed")
            self.assertEqual(term.correlation_id, inbound.correlation_id)
            self.assertIn("runtime provider failed", term.error or "")
        finally:
            await router.stop()

    async def test_runtime_handle_exception_emits_terminal_error(self) -> None:
        """runtime.handle 抛异常时发 terminal(kind=failed)，并继续消费下一条。"""
        bus = MessageBus()
        rt = _FakeRuntime(raise_on_call=0)  # 第一次 handle 抛异常
        runtimes = {"s1": rt}

        router = SessionRouter(
            bus=bus,
            resolver=lambda key: "s1",
            provider=lambda sid: _default_provider(sid, runtimes=runtimes),
        )
        await router.start()
        try:
            first = InboundMessage(content="fail", source="t", channel="cli", chat_id="c1")
            second = InboundMessage(content="ok", source="t", channel="cli", chat_id="c1")
            await bus.publish_inbound(first)
            term = await asyncio.wait_for(bus.consume_outbound(), timeout=1.0)
            self.assertEqual(term.kind, "failed")
            self.assertEqual(term.correlation_id, first.correlation_id)
            self.assertIn("turn execution failed", term.error or "")

            # 第二条应正常跑完
            await bus.publish_inbound(second)
            for _ in range(100):
                if rt.calls:
                    break
                await asyncio.sleep(0.01)
            self.assertEqual([m.content for m in rt.calls], ["ok"])
        finally:
            await router.stop()

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def test_stop_waits_for_in_flight_session_tasks(self) -> None:
        """stop() 应等待正在跑的 per-session task 自然收敛（或主动 cancel 收敛）。"""
        bus = MessageBus()
        rt = _FakeRuntime(delay=0.2)
        runtimes = {"s1": rt}

        router = SessionRouter(
            bus=bus,
            resolver=lambda key: "s1",
            provider=lambda sid: _default_provider(sid, runtimes=runtimes),
        )
        await router.start()
        await bus.publish_inbound(
            InboundMessage(content="x", source="t", channel="cli", chat_id="c1")
        )
        # 等 runtime.handle 真正开始
        await asyncio.wait_for(rt.running.wait(), timeout=1.0)
        # 调用 stop —— 应取消 in-flight task 并等待其 finally 收敛
        await router.stop()
        # 此时 session_tasks 应已清空
        self.assertEqual(router._session_tasks, {})

    async def test_start_is_idempotent(self) -> None:
        """重复 start 不应创建多个消费协程。"""
        bus = MessageBus()
        runtimes: dict[str, _FakeRuntime] = {}
        router = SessionRouter(
            bus=bus,
            resolver=lambda key: "s1",
            provider=lambda sid: _default_provider(sid, runtimes=runtimes),
        )
        await router.start()
        first = router._task
        await router.start()
        self.assertIs(router._task, first)
        await router.stop()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
