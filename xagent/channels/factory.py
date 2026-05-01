from __future__ import annotations

from xagent.bus import MessageBus
from xagent.channels.base import BaseChannel
from xagent.channels.lark import LarkChannel
from xagent.config import AppConfig


def build_channels(config: AppConfig, bus: MessageBus) -> dict[str, BaseChannel]:
    channels: dict[str, BaseChannel] = {}
    if config.channels.lark.enabled:
        channel = LarkChannel(config=config.channels.lark, bus=bus)
        channels[channel.name] = channel
    return channels
