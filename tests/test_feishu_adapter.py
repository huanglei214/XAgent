import json
import threading
import time
import unittest
from types import SimpleNamespace

from xagent.channel.feishu.adapter import FeishuChannelAdapter
from xagent.channel.feishu.config import FeishuConfig
from xagent.channel.models import GroupIngressMode


class _Bridge:
    def __init__(self) -> None:
        self.calls = []

    def dispatch_text(self, conversation_key, text, sink, *, requested_skill_name=None, source="channel") -> str:
        self.calls.append((conversation_key, text, source))
        sink.on_text("hello")
        sink.on_complete(f"done:{text}")
        return "session-1"


class _ApiClient:
    def __init__(self) -> None:
        self.sent_messages = []
        self.updated_messages = []
        self._next_message_id = 1

    def send_text_message(self, chat_id: str, text: str) -> str:
        self.sent_messages.append((chat_id, text))
        message_id = f"msg-{self._next_message_id}"
        self._next_message_id += 1
        return message_id

    def update_text_message(self, message_id: str, text: str) -> None:
        self.updated_messages.append((message_id, text))


class _LongClient:
    def __init__(self) -> None:
        self.closed = False
        self.started = False
        self.error = None

    def start(self) -> None:
        self.started = True
        if self.error is not None:
            raise self.error

    def close(self) -> None:
        self.closed = True


class FeishuAdapterTests(unittest.TestCase):
    def _make_config(self, **overrides) -> FeishuConfig:
        values = {
            "app_id": "app-id",
            "app_secret": "app-secret",
            "group_mode": GroupIngressMode.MENTION_ONLY,
            "partial_emit_chars": 1,
            "bot_open_id": "bot-open-id",
        }
        values.update(overrides)
        return FeishuConfig(**values)

    def _event(self, *, text: str, chat_type: str, mentions=None, user_id="user-1", chat_id="chat-1"):
        return SimpleNamespace(
            header=SimpleNamespace(event_id="evt-1"),
            event=SimpleNamespace(
                sender=SimpleNamespace(sender_id=SimpleNamespace(open_id=user_id, user_id=None)),
                message=SimpleNamespace(
                    chat_id=chat_id,
                    chat_type=chat_type,
                    message_id="msg-1",
                    content=json.dumps({"text": text}),
                    mentions=mentions or [],
                ),
            ),
        )

    def test_private_message_dispatches_and_streams_visible_text(self) -> None:
        bridge = _Bridge()
        api_client = _ApiClient()
        adapter = FeishuChannelAdapter(bridge=bridge, config=self._make_config(), api_client=api_client)
        payload = self._event(text="hello", chat_type="p2p")

        adapter._handle_envelope(adapter._event_to_envelope(payload))

        self.assertEqual(len(bridge.calls), 1)
        conversation_key, text, source = bridge.calls[0]
        self.assertEqual(conversation_key.as_key(), "feishu:user:user-1")
        self.assertEqual(text, "hello")
        self.assertEqual(source, "channel.feishu")
        self.assertEqual(api_client.sent_messages, [("chat-1", "hello")])
        self.assertEqual(api_client.updated_messages, [("msg-1", "done:hello")])

    def test_group_message_requires_mention_by_default(self) -> None:
        bridge = _Bridge()
        api_client = _ApiClient()
        adapter = FeishuChannelAdapter(bridge=bridge, config=self._make_config(), api_client=api_client)
        payload = self._event(text="hello", chat_type="group", mentions=[])

        adapter._handle_envelope(adapter._event_to_envelope(payload))

        self.assertEqual(bridge.calls, [])
        self.assertEqual(api_client.sent_messages, [])

    def test_group_message_dispatches_when_bot_is_mentioned(self) -> None:
        bridge = _Bridge()
        api_client = _ApiClient()
        adapter = FeishuChannelAdapter(bridge=bridge, config=self._make_config(), api_client=api_client)
        payload = self._event(
            text="hello",
            chat_type="group",
            mentions=[SimpleNamespace(id=SimpleNamespace(open_id="bot-open-id", user_id=None))],
        )

        adapter._handle_envelope(adapter._event_to_envelope(payload))

        self.assertEqual(len(bridge.calls), 1)
        self.assertEqual(bridge.calls[0][0].as_key(), "feishu:chat:chat-1")

    def test_denied_message_sends_denial_response(self) -> None:
        bridge = _Bridge()
        api_client = _ApiClient()
        config = self._make_config(allow_all=False, allowed_user_ids=("allowed-user",), deny_message="nope")
        adapter = FeishuChannelAdapter(bridge=bridge, config=config, api_client=api_client)
        payload = self._event(text="hello", chat_type="p2p")

        adapter._handle_envelope(adapter._event_to_envelope(payload))

        self.assertEqual(bridge.calls, [])
        self.assertEqual(api_client.sent_messages, [("chat-1", "nope")])
        self.assertEqual(api_client.updated_messages, [])

    def test_group_message_dispatches_when_all_text_is_enabled(self) -> None:
        bridge = _Bridge()
        api_client = _ApiClient()
        adapter = FeishuChannelAdapter(
            bridge=bridge,
            config=self._make_config(group_mode=GroupIngressMode.ALL_TEXT),
            api_client=api_client,
        )
        payload = self._event(text="hello", chat_type="group")

        adapter._handle_envelope(adapter._event_to_envelope(payload))

        self.assertEqual(len(bridge.calls), 1)
        self.assertEqual(bridge.calls[0][0].as_key(), "feishu:chat:chat-1")

    def test_serve_forever_fails_fast_on_startup_failure(self) -> None:
        bridge = _Bridge()
        api_client = _ApiClient()
        client = _LongClient()
        client.error = ConnectionError("boom")
        adapter = FeishuChannelAdapter(
            bridge=bridge,
            config=self._make_config(),
            api_client=api_client,
            long_connection_factory=lambda cfg, handler: client,
        )
        with self.assertRaisesRegex(ConnectionError, "boom"):
            adapter.serve_forever()

        self.assertEqual(adapter.status.last_error, "boom")
        self.assertFalse(adapter.status.connected)
        self.assertTrue(client.closed)

    def test_serve_forever_uses_official_long_connection_client(self) -> None:
        bridge = _Bridge()
        api_client = _ApiClient()
        client = _LongClient()
        adapter = FeishuChannelAdapter(
            bridge=bridge,
            config=self._make_config(),
            api_client=api_client,
            long_connection_factory=lambda cfg, handler: client,
        )
        adapter.serve_forever()

        self.assertTrue(client.started)
        self.assertTrue(client.closed)
        self.assertIsNone(adapter.status.last_error)

    def test_events_for_same_chat_are_queued_and_processed_in_order(self) -> None:
        class _BlockingBridge(_Bridge):
            def __init__(self) -> None:
                super().__init__()
                self.entered = []
                self.release_first = threading.Event()

            def dispatch_text(self, conversation_key, text, sink, *, requested_skill_name=None, source="channel") -> str:
                self.entered.append(text)
                if text == "first":
                    self.release_first.wait(timeout=1)
                sink.on_complete(f"done:{text}")
                return "session-1"

        bridge = _BlockingBridge()
        api_client = _ApiClient()
        adapter = FeishuChannelAdapter(bridge=bridge, config=self._make_config(), api_client=api_client)

        adapter._handle_sdk_event(self._event(text="first", chat_type="p2p"))
        time.sleep(0.05)
        adapter._handle_sdk_event(self._event(text="second", chat_type="p2p"))
        time.sleep(0.05)

        self.assertEqual(bridge.entered, ["first"])

        bridge.release_first.set()
        deadline = time.time() + 1
        while time.time() < deadline and bridge.entered != ["first", "second"]:
            time.sleep(0.01)

        self.assertEqual(bridge.entered, ["first", "second"])
        adapter.close()

    def test_worker_recovers_after_message_handling_failure(self) -> None:
        class _FlakyBridge(_Bridge):
            def __init__(self) -> None:
                super().__init__()
                self.fail_first = True

            def dispatch_text(self, conversation_key, text, sink, *, requested_skill_name=None, source="channel") -> str:
                self.calls.append((conversation_key, text, source))
                if self.fail_first:
                    self.fail_first = False
                    raise RuntimeError("boom")
                sink.on_complete(f"done:{text}")
                return "session-1"

        bridge = _FlakyBridge()
        api_client = _ApiClient()
        adapter = FeishuChannelAdapter(bridge=bridge, config=self._make_config(), api_client=api_client)

        adapter._handle_sdk_event(self._event(text="first", chat_type="p2p"))
        deadline = time.time() + 1
        while time.time() < deadline and len(bridge.calls) < 1:
            time.sleep(0.01)

        adapter._handle_sdk_event(self._event(text="second", chat_type="p2p"))
        deadline = time.time() + 1
        while time.time() < deadline and len(bridge.calls) < 2:
            time.sleep(0.01)

        self.assertEqual([call[1] for call in bridge.calls], ["first", "second"])
        self.assertEqual(api_client.sent_messages, [("chat-1", "done:second")])
        self.assertEqual(adapter.status.last_error, "boom")
        adapter.close()
