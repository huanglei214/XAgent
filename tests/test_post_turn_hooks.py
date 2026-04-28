"""``PostTurnHook`` 机制单元测试。"""

import unittest

from xagent.agent.runtime.session_runtime import (
    PostTurnContext,
    SessionRuntime,
)
from xagent.bus.messages import InboundMessage
from xagent.bus.queue import MessageBus
from xagent.provider.types import Message, TextPart


async def _simple_turn_runner(prompt, *, on_assistant_delta, on_tool_use, on_tool_result):
    """最小 turn_runner：不调用任何回调，直接返回结果。"""
    return Message(role="assistant", content=[TextPart(text="ok")]), 0.1


async def _failing_turn_runner(prompt, *, on_assistant_delta, on_tool_use, on_tool_result):
    """始终失败的 turn_runner。"""
    raise RuntimeError("boom")


class PostTurnHookTests(unittest.IsolatedAsyncioTestCase):
    async def test_hook_called_after_successful_turn(self) -> None:
        """turn 成功完成后 hook 应被调用一次，且 PostTurnContext 字段正确。"""
        captured: list[PostTurnContext] = []

        async def _hook(ctx: PostTurnContext) -> None:
            captured.append(ctx)

        runtime = SessionRuntime(
            session_id="s1",
            turn_runner=_simple_turn_runner,
            post_turn_hooks=[_hook],
            message_bus=MessageBus(),
        )

        await runtime.handle(InboundMessage(content="hi", source="test", chat_id="s1"))

        self.assertEqual(len(captured), 1)
        ctx = captured[0]
        self.assertEqual(ctx.session_id, "s1")
        self.assertEqual(ctx.source, "session_runtime")
        self.assertAlmostEqual(ctx.duration_seconds, 0.1)
        self.assertIsNotNone(ctx.request_id)

    async def test_hook_not_called_on_failed_turn(self) -> None:
        """turn 失败时 hook 不应被触发。"""
        captured: list[PostTurnContext] = []

        async def _hook(ctx: PostTurnContext) -> None:
            captured.append(ctx)

        runtime = SessionRuntime(
            session_id="s1",
            turn_runner=_failing_turn_runner,
            post_turn_hooks=[_hook],
            message_bus=MessageBus(),
        )

        await runtime.handle(InboundMessage(content="hi", source="test", chat_id="s1"))

        self.assertEqual(len(captured), 0)

    async def test_multiple_hooks_run_in_order(self) -> None:
        """多个 hook 按注册顺序串行执行。"""
        order: list[str] = []

        async def _hook_a(ctx: PostTurnContext) -> None:
            order.append("a")

        async def _hook_b(ctx: PostTurnContext) -> None:
            order.append("b")

        async def _hook_c(ctx: PostTurnContext) -> None:
            order.append("c")

        runtime = SessionRuntime(
            session_id="s1",
            turn_runner=_simple_turn_runner,
            post_turn_hooks=[_hook_a, _hook_b, _hook_c],
            message_bus=MessageBus(),
        )

        await runtime.handle(InboundMessage(content="hi", source="test", chat_id="s1"))
        self.assertEqual(order, ["a", "b", "c"])

    async def test_hook_exception_is_swallowed(self) -> None:
        """hook 抛异常不影响 turn 结果，也不阻止后续 hook。"""
        order: list[str] = []

        async def _boom(ctx: PostTurnContext) -> None:
            order.append("boom")
            raise ValueError("hook error")

        async def _after_boom(ctx: PostTurnContext) -> None:
            order.append("after")

        runtime = SessionRuntime(
            session_id="s1",
            turn_runner=_simple_turn_runner,
            post_turn_hooks=[_boom, _after_boom],
            message_bus=MessageBus(),
        )

        await runtime.handle(InboundMessage(content="hi", source="test", chat_id="s1"))

        self.assertEqual(order, ["boom", "after"])

    async def test_register_post_turn_hook_appends(self) -> None:
        """通过 register_post_turn_hook 后注册的 hook 在尾部执行。"""
        order: list[str] = []

        async def _init_hook(ctx: PostTurnContext) -> None:
            order.append("init")

        async def _late_hook(ctx: PostTurnContext) -> None:
            order.append("late")

        runtime = SessionRuntime(
            session_id="s1",
            turn_runner=_simple_turn_runner,
            post_turn_hooks=[_init_hook],
            message_bus=MessageBus(),
        )
        runtime.register_post_turn_hook(_late_hook)

        await runtime.handle(InboundMessage(content="hi", source="test", chat_id="s1"))
        self.assertEqual(order, ["init", "late"])

    async def test_no_hooks_is_noop(self) -> None:
        """没有注册任何 hook 时 turn 正常完成。"""
        runtime = SessionRuntime(
            session_id="s1",
            turn_runner=_simple_turn_runner,
            message_bus=MessageBus(),
        )

        await runtime.handle(InboundMessage(content="hi", source="test", chat_id="s1"))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
