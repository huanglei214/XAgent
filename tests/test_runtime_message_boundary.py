import json
import queue
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from xagent.agent.runtime.manager import SessionKeyStore
from xagent.agent.runtime.message_boundary import InboundMessage, ManagedRuntimeBoundary


class _ExplodingManager:
    def __init__(self) -> None:
        self.created_sessions = 0

    def resolve_session_id(self, session_key: str) -> str:
        return session_key

    def get_session_status(self, session_id: str):
        return {"session_id": session_id}

    def create_session(self) -> str:
        self.created_sessions += 1
        return f"session-{self.created_sessions}"

    def open_event_stream(self, session_id: str, *, topics=None):
        raise RuntimeError("stream open failed")

    def close_event_stream(self, stream_id: str) -> None:
        return None

    def submit_message(self, *args, **kwargs):
        raise AssertionError("submit_message should not be called when stream setup fails")

    def close(self) -> None:
        return None


class RuntimeMessageBoundaryTests(unittest.TestCase):
    def test_inbound_message_uses_feishu_private_session_key_shape(self) -> None:
        message = InboundMessage(
            content="hello",
            source="channel.feishu",
            channel="feishu",
            sender_id="user-1",
            chat_id="chat-1",
            metadata={"chat_type": "p2p"},
        )

        self.assertEqual(message.session_key, "feishu:user:user-1")

    def test_session_key_store_reuses_legacy_channel_mapping(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy_path = root / ".xagent" / "channel-sessions.json"
            legacy_path.parent.mkdir(parents=True, exist_ok=True)
            legacy_path.write_text(json.dumps({"feishu:user:user-1": "session-legacy"}), encoding="utf-8")

            store = SessionKeyStore(tmp)
            resolved = store.resolve_session_id(
                "feishu:user:user-1",
                session_exists=lambda session_id: {"session_id": session_id},
                create_session=lambda: "new-session",
            )

        self.assertEqual(resolved, "session-legacy")

    def test_managed_boundary_emits_failed_when_stream_setup_fails(self) -> None:
        boundary = ManagedRuntimeBoundary(manager=_ExplodingManager())
        outbound_queue: "queue.Queue[object]" = queue.Queue()
        boundary.out_msg_bus.subscribe(lambda message: outbound_queue.put_nowait(message))

        inbound = InboundMessage(
            content="hello",
            source="gateway.http",
            channel="gateway",
            sender_id="gateway",
            chat_id="session-1",
            session_key_override="session-1",
        )

        boundary.publish_nowait(inbound)
        outbound = outbound_queue.get(timeout=2)

        self.assertEqual(outbound.kind, "failed")
        self.assertEqual(outbound.correlation_id, inbound.correlation_id)
        self.assertIn("stream open failed", outbound.error)
