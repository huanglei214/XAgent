"""External channel extension point. CLI is intentionally not a channel."""

from xagent.channels.base import Channel
from xagent.channels.manager import ChannelManager

__all__ = ["Channel", "ChannelManager"]
