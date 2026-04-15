import unittest

from xagent.channel import (
    ChannelEnvelope,
    ChannelIdentity,
    GroupIngressMode,
    StaticChannelAccessPolicy,
    build_conversation_key,
    is_group_message_allowed,
)


class ChannelContractTests(unittest.TestCase):
    def test_private_conversation_key_uses_user_scope(self) -> None:
        envelope = ChannelEnvelope(
            text="hello",
            identity=ChannelIdentity(channel="feishu", user_id="user-1", chat_id="chat-1", chat_type="p2p"),
        )

        key = build_conversation_key(envelope)

        self.assertEqual(key.scope, "user")
        self.assertEqual(key.value, "user-1")
        self.assertEqual(key.as_key(), "feishu:user:user-1")

    def test_group_conversation_key_uses_chat_scope(self) -> None:
        envelope = ChannelEnvelope(
            text="hello",
            identity=ChannelIdentity(channel="feishu", user_id="user-1", chat_id="chat-1", chat_type="group"),
        )

        key = build_conversation_key(envelope)

        self.assertEqual(key.scope, "chat")
        self.assertEqual(key.value, "chat-1")
        self.assertEqual(key.as_key(), "feishu:chat:chat-1")

    def test_group_ingress_mention_only_requires_mentions(self) -> None:
        envelope = ChannelEnvelope(
            text="hi",
            identity=ChannelIdentity(channel="feishu", user_id="user-1", chat_id="chat-1", chat_type="group"),
        )

        self.assertFalse(
            is_group_message_allowed(envelope, mode=GroupIngressMode.MENTION_ONLY, bot_open_id="bot-1")
        )

    def test_group_ingress_matches_bot_open_id(self) -> None:
        envelope = ChannelEnvelope(
            text="hi",
            identity=ChannelIdentity(channel="feishu", user_id="user-1", chat_id="chat-1", chat_type="group"),
            mentions=("bot-1",),
        )

        self.assertTrue(
            is_group_message_allowed(envelope, mode=GroupIngressMode.MENTION_ONLY, bot_open_id="bot-1")
        )

    def test_group_ingress_all_text_allows_group_messages(self) -> None:
        envelope = ChannelEnvelope(
            text="hi",
            identity=ChannelIdentity(channel="feishu", user_id="user-1", chat_id="chat-1", chat_type="group"),
        )

        self.assertTrue(is_group_message_allowed(envelope, mode=GroupIngressMode.ALL_TEXT))

    def test_access_policy_denies_non_matching_user_or_chat(self) -> None:
        policy = StaticChannelAccessPolicy(
            allow_all=False,
            allowed_user_ids=frozenset({"user-1"}),
            allowed_chat_ids=frozenset({"chat-1"}),
        )
        denied_user = ChannelEnvelope(
            text="blocked",
            identity=ChannelIdentity(channel="feishu", user_id="user-2", chat_id="chat-1", chat_type="p2p"),
        )
        denied_chat = ChannelEnvelope(
            text="blocked",
            identity=ChannelIdentity(channel="feishu", user_id="user-1", chat_id="chat-2", chat_type="group"),
        )

        self.assertFalse(policy.evaluate(denied_user).allowed)
        self.assertFalse(policy.evaluate(denied_chat).allowed)

    def test_access_policy_allows_empty_lists(self) -> None:
        policy = StaticChannelAccessPolicy()
        envelope = ChannelEnvelope(
            text="ok",
            identity=ChannelIdentity(channel="feishu", user_id="user-1", chat_id="chat-1", chat_type="p2p"),
        )

        self.assertTrue(policy.evaluate(envelope).allowed)
