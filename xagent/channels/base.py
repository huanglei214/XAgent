from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from xagent.bus import InboundMessage, MessageBus, OutboundEvent


class BaseChannel(ABC):
    """外部消息源和进程内 Bus 之间的适配器基类。"""

    def __init__(self, *, name: str, bus: MessageBus) -> None:
        self.name = name
        self.bus = bus

    @property
    def supports_streaming(self) -> bool:
        return False

    @abstractmethod
    async def start(self) -> None:
        """准备 channel 资源，成功后应尽快返回。"""

    @abstractmethod
    async def run(self) -> None:
        """长期监听外部平台消息。"""

    @abstractmethod
    async def handle_message(self, message: Any) -> InboundMessage | None:
        """处理一条平台消息，并在需要时发布 InboundMessage 到 Bus。"""

    @abstractmethod
    async def send(self, event: OutboundEvent) -> None:
        """将一条出站消息发送给外部用户。"""

    @abstractmethod
    async def stop(self) -> None:
        """清理 channel 资源。"""
