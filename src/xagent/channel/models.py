from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class GroupIngressMode(str, Enum):
    MENTION_ONLY = "mention_only"
    ALL_TEXT = "all_text"


@dataclass(frozen=True)
class ChannelIdentity:
    channel: str
    user_id: str
    chat_id: str
    chat_type: str = "p2p"

    @property
    def is_group(self) -> bool:
        return self.chat_type.lower() != "p2p"


@dataclass(frozen=True)
class ChannelConversationKey:
    channel: str
    scope: str
    value: str

    def as_key(self) -> str:
        return f"{self.channel}:{self.scope}:{self.value}"


@dataclass
class ChannelEnvelope:
    text: str
    identity: ChannelIdentity
    mentions: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def channel(self) -> str:
        return self.identity.channel

    @property
    def is_group(self) -> bool:
        return self.identity.is_group
