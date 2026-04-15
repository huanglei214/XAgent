from __future__ import annotations

from xagent.channel.models import ChannelConversationKey, ChannelEnvelope, GroupIngressMode


def is_group_message_allowed(
    envelope: ChannelEnvelope,
    *,
    mode: GroupIngressMode,
    bot_open_id: str | None = None,
) -> bool:
    if not envelope.is_group:
        return True
    if mode is GroupIngressMode.ALL_TEXT:
        return True
    if not envelope.mentions:
        return False
    if bot_open_id:
        return bot_open_id in envelope.mentions
    return True


def build_conversation_key(envelope: ChannelEnvelope) -> ChannelConversationKey:
    scope = "chat" if envelope.is_group else "user"
    value = envelope.identity.chat_id if envelope.is_group else envelope.identity.user_id
    return ChannelConversationKey(channel=envelope.channel, scope=scope, value=value)
