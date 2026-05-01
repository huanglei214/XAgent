from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class StreamKind(str, Enum):
    DELTA = "delta"
    END = "end"


@dataclass(frozen=True)
class StreamState:
    kind: StreamKind
    stream_id: str


@dataclass
class InboundMessage:
    """进入进程内 Bus 的用户消息。

    channel/chat_id 表示共享上下文的会话空间；sender_id 只表示这个空间里
    具体是谁发言，不参与 session 身份的生成。
    """

    content: str
    channel: str = "cli"
    chat_id: str = "default"
    sender_id: str = "user"
    # 显式 session 覆盖，例如 `xagent agent --resume <id>`。
    session_id: str | None = None
    # 外部平台原始消息 ID，用于审计、去重或平台回复 API，不参与路由。
    external_message_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class OutboundEvent:
    """从 Bus 发往外部 channel 的出站消息信封。

    路由只依赖 channel/chat_id/reply_to；stream 描述这条 assistant 消息
    是增量片段，还是最终完整内容。
    """

    content: str = ""
    channel: str = "cli"
    chat_id: str = "default"
    reply_to: str | None = None
    session_id: str | None = None
    stream: StreamState | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
