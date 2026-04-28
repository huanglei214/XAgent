"""Channel 抽象基类。

核心约定：
- Channel 负责"对外 IO"——接收外部输入并 ``publish_inbound``、把
  ``OutboundMessage`` 渲染/发送到外部世界。
- Channel 不关心 SessionRuntime 的内部状态；所有 outbound 由
  ``ChannelManager`` 统一路由而来。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from xagent.bus.messages import InboundMessage, OutboundMessage
from xagent.bus.queue import MessageBus


class BaseChannel(ABC):
    """所有外部通道（feishu / http / tui / cli）的统一抽象。

    子类必须实现 ``start`` / ``stop`` / ``send``；``publish_inbound`` 已提供
    默认实现，直接把入站消息推给 ``MessageBus``。
    """

    #: 通道逻辑名，例如 ``"feishu"`` / ``"http"`` / ``"cli"`` / ``"tui"``；
    #: ``ChannelManager`` 会按 ``OutboundMessage.channel`` 字段选取同名 Channel。
    name: str = ""

    #: 是否旁路观察所有 outbound，而不受 ``msg.channel`` 约束。
    #: 典型用法是 ``TraceChannel`` 这类只做观测/落盘的 channel。
    observe_all: bool = False

    def __init__(self, bus: MessageBus) -> None:
        """保存 ``MessageBus`` 引用，供 ``publish_inbound`` 使用。"""
        self._bus = bus

    @abstractmethod
    async def start(self) -> None:
        """启动通道（建立连接、开启监听等）。幂等由子类保证。"""

    @abstractmethod
    async def stop(self) -> None:
        """停止通道并释放资源。应能在未 start 的情况下安全调用。"""

    @abstractmethod
    async def send(self, msg: OutboundMessage) -> None:
        """把一条 ``OutboundMessage`` 送达外部世界（渲染/发送/落盘）。

        实现中出现的异常应由 ``ChannelManager`` 捕获并记录，避免影响
        其他 channel 或 dispatch 循环。
        """

    async def publish_inbound(self, msg: InboundMessage) -> None:
        """把外部收到的消息推入 ``MessageBus.inbound`` 队列。

        子类一般无需覆写；若需统一打点 / 鉴权，可在覆写中 ``await super()``。
        """
        await self._bus.publish_inbound(msg)
