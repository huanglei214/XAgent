from __future__ import annotations

from xagent.bus import MessageBus
from xagent.channels.base import BaseChannel
from xagent.channels.lark import LarkChannel
from xagent.channels.weixin import WeixinChannel
from xagent.config import AppConfig


def build_channels(config: AppConfig, bus: MessageBus) -> dict[str, BaseChannel]:
    channels: dict[str, BaseChannel] = {}
    if config.channels.lark.enabled:
        lark_channel = LarkChannel(config=config.channels.lark, bus=bus)
        channels[lark_channel.name] = lark_channel
    if config.channels.weixin.enabled:
        weixin_channel = WeixinChannel(config=config.channels.weixin, bus=bus)
        channels[weixin_channel.name] = weixin_channel
    return channels
