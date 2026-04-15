from __future__ import annotations

from typing import Protocol

from xagent.channel.access import AccessDecision
from xagent.channel.models import ChannelEnvelope, ChannelConversationKey


class ChannelSink(Protocol):
    def on_text(self, text: str) -> None:
        ...

    def on_complete(self, text: str) -> None:
        ...

    def on_error(self, error: str) -> None:
        ...


class ChannelAdapter(Protocol):
    def serve_forever(self) -> None:
        ...

    def close(self) -> None:
        ...


class ChannelAccessPolicy(Protocol):
    def evaluate(self, envelope: ChannelEnvelope) -> AccessDecision:
        ...


class SessionResolver(Protocol):
    def resolve(self, envelope: ChannelEnvelope) -> ChannelConversationKey:
        ...
