"""SessionRouter：``MessageBus.inbound`` 的全局单消费者。

当前运行时模型中：

- ``MessageBus.inbound`` 有且仅有一个消费者：``SessionRouter``。
- Router 根据 ``InboundMessage.session_key`` 解析出 ``session_id``，找到或创建
  对应 ``SessionRuntime``，调用 ``SessionRuntime.handle(inbound)``。
- 通过 ``dict[session_id, asyncio.Task]`` 的 task-chain 保证：
  - **同一 session 的 inbound 串行 FIFO**；
  - **不同 session 并发**（chain 头部 coroutine 在等 prev 时挂起，
    不阻塞 ``_consume_loop``）。
- 解析失败或 runtime 异常时，主动向 ``MessageBus.outbound`` 发
  ``make_terminal(kind="failed")``，避免 ``ChannelManager.send_and_wait``
  永远阻塞。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Optional, Protocol

from xagent.bus.messages import InboundMessage, make_terminal
from xagent.bus.queue import MessageBus

logger = logging.getLogger(__name__)


class _SessionHandler(Protocol):
    """Router 需要的最小 Runtime 协议：拥有 ``handle(inbound)`` 方法。"""

    async def handle(self, inbound: InboundMessage) -> None: ...


#: 根据 ``session_key`` 解析 ``session_id`` 的同步函数。
#: 失败时应抛异常；Router 会捕获并发 terminal-error。
SessionResolver = Callable[[str], str]

#: 根据 ``session_id`` 拿/建 SessionRuntime 的 async 函数。
RuntimeProvider = Callable[[str], Awaitable[_SessionHandler]]


class SessionRouter:
    """``MessageBus.inbound`` 的唯一消费者。"""

    def __init__(
        self,
        *,
        bus: MessageBus,
        resolver: SessionResolver,
        provider: RuntimeProvider,
    ) -> None:
        """绑定 bus、resolver、provider。

        - ``resolver(session_key) -> session_id``：同步函数，抛异常表示解析失败。
        - ``provider(session_id) -> SessionRuntime``：async 函数，返回可调用
          ``handle(inbound)`` 的对象。
        """
        self._bus = bus
        self._resolver = resolver
        self._provider = provider
        self._session_tasks: dict[str, asyncio.Task[None]] = {}
        self._task: Optional[asyncio.Task[None]] = None
        self._started = False

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """启动 inbound 消费循环；重复调用无副作用。"""
        if self._started:
            return
        self._started = True
        self._task = asyncio.create_task(self._consume_loop(), name="session-router")

    async def stop(self) -> None:
        """停止消费循环并等待所有正在进行的 per-session task 自然收敛。"""
        if not self._started:
            return
        self._started = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
            self._task = None
        pending = list(self._session_tasks.values())
        for t in pending:
            t.cancel()
        for t in pending:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._session_tasks.clear()

    # ------------------------------------------------------------------
    # 消费循环
    # ------------------------------------------------------------------

    async def _consume_loop(self) -> None:
        """持续从 ``bus.inbound`` 取 InboundMessage 并派发。"""
        while True:
            inbound = await self._bus.consume_inbound()
            try:
                session_id = self._resolver(inbound.session_key)
            except Exception as exc:  # noqa: BLE001 - 捕获所有 resolver 异常
                logger.exception(
                    "SessionResolver 失败；correlation_id=%s session_key=%s",
                    inbound.correlation_id,
                    inbound.session_key,
                )
                await self._emit_terminal_error(
                    inbound,
                    session_id="",
                    error=f"session resolution failed: {exc!s}",
                )
                continue
            await self._dispatch(session_id, inbound)

    async def _dispatch(self, session_id: str, inbound: InboundMessage) -> None:
        """按 session_id 串行 dispatch inbound：新 task 等待同 session 的 prev
        task 完成后再执行 ``runtime.handle(inbound)``。"""
        prev = self._session_tasks.get(session_id)

        async def _run_after_prev() -> None:
            # 1. 等同 session 前一个 task 完成（串行 FIFO）
            if prev is not None and not prev.done():
                try:
                    await prev
                except Exception:
                    # prev 的异常不应影响后续 turn；已在 prev 内部记录
                    pass
            # 2. 拿 runtime
            try:
                runtime = await self._provider(session_id)
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "RuntimeProvider 失败；session_id=%s correlation_id=%s",
                    session_id,
                    inbound.correlation_id,
                )
                await self._emit_terminal_error(
                    inbound,
                    session_id=session_id,
                    error=f"runtime provider failed: {exc!s}",
                )
                return
            # 3. 执行 turn
            try:
                await runtime.handle(inbound)
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "SessionRuntime.handle 失败；session_id=%s correlation_id=%s",
                    session_id,
                    inbound.correlation_id,
                )
                await self._emit_terminal_error(
                    inbound,
                    session_id=session_id,
                    error=f"turn execution failed: {exc!s}",
                )

        task = asyncio.create_task(
            _run_after_prev(), name=f"session-turn-{session_id}"
        )
        self._session_tasks[session_id] = task

        # 仅在"当前记录的 task 仍是自己"时清理；否则说明已被更晚的 inbound
        # 覆盖，不应误删指针。
        def _cleanup(t: asyncio.Task[Any], *, sid: str = session_id) -> None:
            if self._session_tasks.get(sid) is t:
                self._session_tasks.pop(sid, None)

        task.add_done_callback(_cleanup)

    # ------------------------------------------------------------------
    # 异常兜底
    # ------------------------------------------------------------------

    async def _emit_terminal_error(
        self,
        inbound: InboundMessage,
        *,
        session_id: str,
        error: str,
    ) -> None:
        """保证调用方（``ChannelManager.send_and_wait``）不会永远阻塞，
        即使 resolver/provider/handle 任一环节抛异常，也要发 terminal。"""
        msg = make_terminal(
            correlation_id=inbound.correlation_id,
            session_id=session_id,
            session_key=inbound.session_key,
            channel=inbound.channel,
            chat_id=inbound.chat_id,
            source="session-router",
            content="",
            kind="failed",
            error=error,
        )
        await self._bus.publish_outbound(msg)
