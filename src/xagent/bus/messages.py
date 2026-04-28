from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, Optional
from uuid import uuid4


@dataclass
class InboundMessage:
    """Message flowing into the runtime from an external source (CLI, channel, gateway)."""

    content: str
    source: str
    channel: str = "local"
    sender_id: str = "local"
    chat_id: str = "local"
    requested_skill_name: Optional[str] = None
    reply_to: Optional[str] = None
    media: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    correlation_id: str = field(default_factory=lambda: uuid4().hex)
    session_key_override: Optional[str] = None

    @property
    def session_key(self) -> str:
        if self.session_key_override:
            return self.session_key_override
        session_scope = self.metadata.get("session_scope")
        session_value = self.metadata.get("session_value")
        if session_scope and session_value:
            return f"{self.channel}:{session_scope}:{session_value}"
        chat_type = str(self.metadata.get("chat_type") or "").lower()
        if chat_type:
            if chat_type == "p2p":
                return f"{self.channel}:user:{self.sender_id}"
            return f"{self.channel}:chat:{self.chat_id}"
        return f"{self.channel}:{self.chat_id or self.sender_id or self.source}"


@dataclass
class OutboundMessage:
    """Message flowing out of the runtime towards an external consumer."""

    kind: str
    correlation_id: str
    session_id: str
    session_key: str
    source: str
    channel: str
    chat_id: str
    content: str = ""
    reply_to: Optional[str] = None
    error: Optional[str] = None
    media: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# OutboundMessage.metadata 的约定键与辅助构造函数。
#
# 当前运行时约定：
# - 所有中间进度（turn/tool/thinking/compaction）与最终回复都走同一条
#   ``MessageBus.outbound`` 队列；
# - ``ChannelManager`` 和上层 channel/gateway 通过 metadata 里的约定键区分语义；
# - bus 层不维护 topic/subscriber 模型，只关心 inbound/outbound 两条 FIFO。
# ---------------------------------------------------------------------------


# OutboundMessage.metadata._event 的允许取值。
EventKind = Literal[
    "turn_start",
    "turn_end",
    "tool_use",
    "tool_result",
    "thinking_delta",
    "text_delta",
    "scheduler_fired",
    "compact_started",
    "compact_finished",
]


# metadata 的约定键说明（仅作为文档；实际存储为普通 dict[str, Any]）：
#   _progress:   bool  — 是否为中间进度消息（默认 False 表示最终回复）
#   _tool_hint:  bool  — 是否为工具相关提示（需配合 _progress=True）
#   _event:      str   — 事件子类型，取值见 EventKind
#   _stream:     bool  — 是否为流式增量 chunk（消费者应累积而非替换）
#   _terminal:   bool  — 是否为本 correlation_id 的最终消息
#   _source:     str   — 消息来源标签（例如 "scheduler"、"heartbeat"）
META_PROGRESS = "_progress"
META_TOOL_HINT = "_tool_hint"
META_EVENT = "_event"
META_STREAM = "_stream"
META_TERMINAL = "_terminal"
META_SOURCE = "_source"


def make_progress(
    *,
    correlation_id: str,
    session_id: str,
    session_key: str,
    channel: str,
    chat_id: str,
    source: str,
    event: EventKind,
    content: str = "",
    kind: str = "event",
    tool_hint: bool = False,
    stream: bool = False,
    extra_metadata: Optional[dict[str, Any]] = None,
) -> OutboundMessage:
    """构造一条中间进度 ``OutboundMessage``。

    会自动置 ``metadata._progress=True``、``metadata._event=<event>``，
    适用于 turn/tool/thinking 等"运行时事件"的发布。
    """

    metadata: dict[str, Any] = {
        META_PROGRESS: True,
        META_EVENT: event,
    }
    if tool_hint:
        metadata[META_TOOL_HINT] = True
    if stream:
        metadata[META_STREAM] = True
    if extra_metadata:
        metadata.update(extra_metadata)
    return OutboundMessage(
        kind=kind,
        correlation_id=correlation_id,
        session_id=session_id,
        session_key=session_key,
        source=source,
        channel=channel,
        chat_id=chat_id,
        content=content,
        metadata=metadata,
    )


def make_terminal(
    *,
    correlation_id: str,
    session_id: str,
    session_key: str,
    channel: str,
    chat_id: str,
    source: str,
    content: str,
    kind: str = "completed",
    reply_to: Optional[str] = None,
    error: Optional[str] = None,
    extra_metadata: Optional[dict[str, Any]] = None,
) -> OutboundMessage:
    """构造本 ``correlation_id`` 的最终 ``OutboundMessage``。

    会自动置 ``metadata._terminal=True``，ChannelManager 据此判定响应流结束。
    """

    metadata: dict[str, Any] = {META_TERMINAL: True}
    if extra_metadata:
        metadata.update(extra_metadata)
    return OutboundMessage(
        kind=kind,
        correlation_id=correlation_id,
        session_id=session_id,
        session_key=session_key,
        source=source,
        channel=channel,
        chat_id=chat_id,
        content=content,
        reply_to=reply_to,
        error=error,
        metadata=metadata,
    )


def is_progress(msg: OutboundMessage) -> bool:
    """判断一条 outbound 是否为中间进度消息。"""
    return bool(msg.metadata.get(META_PROGRESS))


def is_terminal(msg: OutboundMessage) -> bool:
    """判断一条 outbound 是否为本 correlation_id 的最终消息。"""
    return bool(msg.metadata.get(META_TERMINAL))
