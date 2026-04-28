import unittest

from xagent.bus.messages import InboundMessage
from xagent.cli.runtime import ManagerFacingRuntimeAdapter


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

    def open_outbound_stream(self, message, *, terminal_only: bool = False):
        raise RuntimeError("stream open failed")

    def close(self) -> None:
        return None


class RuntimeMessageBoundaryTests(unittest.TestCase):
    def test_manager_facing_adapter_open_response_stream_raises_when_stream_setup_fails(self) -> None:
        boundary = ManagerFacingRuntimeAdapter(manager=_ExplodingManager())

        inbound = InboundMessage(
            content="hello",
            source="gateway.http",
            channel="gateway",
            sender_id="gateway",
            chat_id="session-1",
            session_key_override="session-1",
        )

        with self.assertRaisesRegex(RuntimeError, "stream open failed"):
            boundary.open_response_stream(inbound)
