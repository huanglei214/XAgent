from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional
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
