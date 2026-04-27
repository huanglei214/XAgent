"""进程内消息总线：两条 asyncio.Queue。

参照 HKUDS/nanobot 的极简总线设计（见 openspec 0001-simplify-bus）：
- 仅保留 ``inbound`` / ``outbound`` 两条 FIFO 队列
- 不含 topic / 通配符 / predicate / 订阅者列表
- 所有"运行时事件"（turn / tool / thinking 等）都编码为
  ``OutboundMessage.metadata``，与最终回复一起走 outbound 队列

本模块是阶段 3 新增；与旧的 ``InMemoryMessageBus`` / ``TypedMessageBus`` 并存，
上游在阶段 5/6 逐步切换后再删除旧实现。
"""

from __future__ import annotations

import asyncio

from xagent.bus.messages import InboundMessage, OutboundMessage


class MessageBus:
    """进程内消息总线。仅包含 inbound / outbound 两条 ``asyncio.Queue``。"""

    def __init__(self) -> None:
        """初始化两条无界异步队列。"""
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """由外部通道（channel / gateway / CLI / scheduler）投递用户消息。"""
        await self.inbound.put(msg)

    async def consume_inbound(self) -> InboundMessage:
        """由 AgentRunner / SessionRuntimeManager 取下一条待处理消息。"""
        return await self.inbound.get()

    async def publish_outbound(self, msg: OutboundMessage) -> None:
        """由 Agent runtime 投递中间进度或最终回复。"""
        await self.outbound.put(msg)

    async def consume_outbound(self) -> OutboundMessage:
        """由 ChannelManager 取下一条待转发的响应消息。"""
        return await self.outbound.get()

    def inbound_qsize(self) -> int:
        """返回 inbound 队列当前待处理消息数。仅供观测使用。"""
        return self.inbound.qsize()

    def outbound_qsize(self) -> int:
        """返回 outbound 队列当前待处理消息数。仅供观测使用。"""
        return self.outbound.qsize()
