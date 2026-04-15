from __future__ import annotations

from dataclasses import dataclass

from xagent.channel.models import ChannelEnvelope


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: str | None = None


@dataclass
class StaticChannelAccessPolicy:
    allow_all: bool = False
    allowed_user_ids: frozenset[str] = frozenset()
    allowed_chat_ids: frozenset[str] = frozenset()

    def evaluate(self, envelope: ChannelEnvelope) -> AccessDecision:
        if self.allow_all:
            return AccessDecision(True)
        if self.allowed_chat_ids and envelope.identity.chat_id not in self.allowed_chat_ids:
            return AccessDecision(False, "chat_not_allowed")
        if self.allowed_user_ids and envelope.identity.user_id not in self.allowed_user_ids:
            return AccessDecision(False, "user_not_allowed")
        if not self.allowed_chat_ids and not self.allowed_user_ids:
            return AccessDecision(True)
        return AccessDecision(True)
