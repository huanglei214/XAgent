"""Channel 路由与 per-request 响应分发。

openspec 0001-simplify-bus 阶段 4：实现 ``ChannelManager``，作为
``MessageBus.outbound`` 的**唯一消费者**，承担两件事：

1. **per-request fan-out**：``send_and_wait`` / ``open_response_stream``
   为每个 request 注册一条临时 queue，dispatch loop 看到匹配 correlation_id
   的 outbound 就复制一份过去；
2. **按 channel 名转发**：根据 ``OutboundMessage.channel`` 选取已注册的
   ``BaseChannel`` 实例并调用其 ``send``。

本阶段仅新增此模块与测试；现有 ``message_boundary`` 不做拆除，阶段 5+
才会迁移上游到新接口。
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator, Optional

from xagent.bus.messages import InboundMessage, OutboundMessage, is_terminal
from xagent.bus.queue import MessageBus
from xagent.channel.base import BaseChannel

logger = logging.getLogger(__name__)


class ChannelManager:
    """唯一的 outbound 消费者。

    使用方式：
        bus = MessageBus()
        cm = ChannelManager(bus)
        cm.register_channel(TuiChannel(bus))
        await cm.start()
        final = await cm.send_and_wait(inbound, timeout=30)
        await cm.stop()
    """

    def __init__(self, bus: MessageBus) -> None:
        """绑定 ``MessageBus``；调用方需在 ``start`` 前完成所有 ``register_channel``。"""
        self._bus = bus
        self._channels: dict[str, BaseChannel] = {}
        self._response_registry: dict[str, asyncio.Queue[OutboundMessage]] = {}
        self._registry_lock = asyncio.Lock()
        self._task: Optional[asyncio.Task[None]] = None
        self._started = False

    # ------------------------------------------------------------------
    # 通道注册
    # ------------------------------------------------------------------

    def register_channel(self, channel: BaseChannel) -> None:
        """注册一个 Channel；按 ``channel.name`` 作为键，重复注册会覆盖。"""
        if not channel.name:
            raise ValueError("BaseChannel.name 必须为非空字符串")
        self._channels[channel.name] = channel

    def get_channel(self, name: str) -> Optional[BaseChannel]:
        """按名查 Channel；找不到返回 ``None``。"""
        return self._channels.get(name)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """启动 dispatch loop；重复调用无副作用。"""
        if self._started:
            return
        self._started = True
        self._task = asyncio.create_task(self._dispatch_loop(), name="channel-manager-dispatch")

    async def stop(self) -> None:
        """停止 dispatch loop 并等待协程退出。"""
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

    # ------------------------------------------------------------------
    # per-request API
    # ------------------------------------------------------------------

    async def send_and_wait(
        self,
        inbound: InboundMessage,
        *,
        timeout: Optional[float] = None,
    ) -> OutboundMessage:
        """发布 inbound 并阻塞等待匹配 correlation_id 的 ``_terminal=True`` outbound。

        中间进度消息会被 drop（仅通过 channel.send 转发给常驻通道）。
        """
        queue = await self._register_response(inbound.correlation_id)
        try:
            await self._bus.publish_inbound(inbound)
            while True:
                msg = await asyncio.wait_for(queue.get(), timeout=timeout)
                if is_terminal(msg):
                    return msg
        finally:
            await self._unregister_response(inbound.correlation_id)

    def open_response_stream(
        self,
        inbound: InboundMessage,
    ) -> AsyncIterator[OutboundMessage]:
        """返回 async iterator：按顺序 yield 本 inbound 的全部 outbound，直到 ``_terminal``。

        调用方必须耗尽该 iterator（或 ``aclose()``）以触发 scope 清理。
        """
        return _ResponseStream(self, inbound)

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    async def _register_response(self, correlation_id: str) -> "asyncio.Queue[OutboundMessage]":
        """为某个 correlation_id 注册一条 per-request queue，返回该 queue。"""
        queue: asyncio.Queue[OutboundMessage] = asyncio.Queue()
        async with self._registry_lock:
            if correlation_id in self._response_registry:
                raise RuntimeError(
                    f"correlation_id {correlation_id!r} 已被另一个请求占用"
                )
            self._response_registry[correlation_id] = queue
        return queue

    async def _unregister_response(self, correlation_id: str) -> None:
        """清理 per-request queue 注册。"""
        async with self._registry_lock:
            self._response_registry.pop(correlation_id, None)

    async def _dispatch_loop(self) -> None:
        """持续从 outbound 队列取消息，fan-out 到 per-request queue + 转发到 channel。"""
        while True:
            msg = await self._bus.consume_outbound()
            await self._dispatch_one(msg)

    async def _dispatch_one(self, msg: OutboundMessage) -> None:
        """处理单条 outbound：先 fan-out，再转发到对应 channel。"""
        # 1. per-request fan-out
        queue = self._response_registry.get(msg.correlation_id)
        if queue is not None:
            try:
                queue.put_nowait(msg)
            except asyncio.QueueFull:  # 默认 Queue() 无上限，这里仅作兜底
                logger.warning(
                    "per-request queue 满，丢弃 correlation_id=%s",
                    msg.correlation_id,
                )
        # 2. 按 channel 名转发
        channel = self._channels.get(msg.channel)
        if channel is not None:
            try:
                await channel.send(msg)
            except Exception:  # noqa: BLE001 - 吞异常避免影响其他 request
                logger.exception(
                    "Channel %s 发送失败；correlation_id=%s",
                    msg.channel,
                    msg.correlation_id,
                )


class _ResponseStream:
    """``open_response_stream`` 返回的 async iterator 实现。

    使用独立类而非 async generator，便于保证 ``scope`` 在任何退出路径下都被清理。
    """

    def __init__(self, manager: ChannelManager, inbound: InboundMessage) -> None:
        self._manager = manager
        self._inbound = inbound
        self._queue: Optional[asyncio.Queue[OutboundMessage]] = None
        self._closed = False
        self._terminated = False

    def __aiter__(self) -> "_ResponseStream":
        return self

    async def __anext__(self) -> OutboundMessage:
        """返回下一条 outbound；到达 terminal 后再次调用抛 ``StopAsyncIteration``。"""
        if self._closed or self._terminated:
            await self.aclose()
            raise StopAsyncIteration
        if self._queue is None:
            # 惰性注册：第一次迭代时才注册 scope 并 publish inbound
            self._queue = await self._manager._register_response(
                self._inbound.correlation_id
            )
            await self._manager._bus.publish_inbound(self._inbound)
        msg = await self._queue.get()
        if is_terminal(msg):
            self._terminated = True
        return msg

    async def aclose(self) -> None:
        """显式清理 per-request scope；重复调用安全。"""
        if self._closed:
            return
        self._closed = True
        if self._queue is not None:
            await self._manager._unregister_response(self._inbound.correlation_id)
            self._queue = None
