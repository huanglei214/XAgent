"""xagent.bus 包对外导出。

阶段 3（openspec 0001-simplify-bus）：
- 引入 ``MessageBus``（两条 asyncio.Queue），与旧的 ``InMemoryMessageBus`` /
  ``TypedMessageBus`` 双轨并存。
- 新增 ``make_progress`` / ``make_terminal`` 等 ``OutboundMessage`` 构造辅助。
- 后续阶段（5~7）会逐步切换所有上游到 ``MessageBus`` 并最终删除旧实现。

已搬迁的类型：
- LLM 消息（``Message`` 等）→ ``xagent.provider.types``
- ``WorkspaceEscapeError`` → ``xagent.agent.errors``
"""

from xagent.bus.events import Event, EventHandler, InMemoryMessageBus
from xagent.bus.messages import (
    EventKind,
    InboundMessage,
    OutboundMessage,
    is_progress,
    is_terminal,
    make_progress,
    make_terminal,
)
from xagent.bus.queue import MessageBus
from xagent.bus.typed_bus import TypedMessageBus

__all__ = [
    "Event",
    "EventHandler",
    "EventKind",
    "InboundMessage",
    "InMemoryMessageBus",
    "MessageBus",
    "OutboundMessage",
    "TypedMessageBus",
    "is_progress",
    "is_terminal",
    "make_progress",
    "make_terminal",
]
