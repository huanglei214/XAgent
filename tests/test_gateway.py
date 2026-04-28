import json
import socket
import unittest
from base64 import b64encode
from hashlib import sha1
from tempfile import TemporaryDirectory
from uuid import uuid4

from xagent.agent.memory import create_runtime_memory
from xagent.agent.runtime import SessionRuntime
from xagent.cli.runtime import ManagerFacingRuntimeAdapter
from xagent.bus.messages import InboundMessage
from xagent.bus.queue import MessageBus
from xagent.provider.types import Message, TextPart
from xagent.agent.runtime.manager import SessionRuntimeManager
from xagent.gateway.http import build_gateway_handler


class _GatewayAgent:
    def __init__(self) -> None:
        self.messages = []
        self.requested_skill_name = None
        self.trace_session_id = None
        self.abort_calls = 0
        self.cwd = "."
        self.model = "gateway-test"

    def clear_messages(self) -> None:
        self.messages.clear()

    def set_messages(self, messages) -> None:
        self.messages = list(messages)

    def set_requested_skill_name(self, requested_skill_name) -> None:
        self.requested_skill_name = requested_skill_name

    def abort(self) -> None:
        self.abort_calls += 1


def _build_test_runtime(agent, *, session_id=None, cwd=None, message_bus=None):
    memory = create_runtime_memory(cwd or ".", agent=agent)
    message_bus = message_bus or MessageBus()

    async def _turn_runner(prompt, *, on_assistant_delta, on_tool_use, on_tool_result):
        reply_text = f"echo:{prompt}"
        on_assistant_delta(Message(role="assistant", content=[TextPart(text="echo")]))
        agent.messages.extend(
            [
                Message(role="user", content=[TextPart(text=prompt)]),
                Message(role="assistant", content=[TextPart(text=reply_text)]),
            ]
        )
        return Message(role="assistant", content=[TextPart(text=reply_text)]), 0.05

    runtime = SessionRuntime(
        session_id=session_id or memory.episodic.new_session_id(),
        turn_runner=_turn_runner,
        agent=agent,
        memory=memory,
        message_bus=message_bus,
    )
    return message_bus, runtime


def _build_gateway_boundary(tmp: str) -> ManagerFacingRuntimeAdapter:
    manager = SessionRuntimeManager(
        cwd=tmp,
        agent_factory=_GatewayAgent,
        runtime_factory=_build_test_runtime,
    )
    return ManagerFacingRuntimeAdapter(manager=manager)


class GatewayBoundaryTests(unittest.TestCase):
    def test_boundary_creates_session_sends_and_reports_status(self) -> None:
        with TemporaryDirectory() as tmp:
            manager = _build_gateway_boundary(tmp)
            try:
                session_id = manager.create_session()
                self.assertIsInstance(session_id, str)

                outbound = manager.send_and_wait(
                    InboundMessage(
                        content="hello",
                        source="gateway.http",
                        channel="gateway",
                        sender_id="gateway",
                        chat_id=session_id,
                        correlation_id=uuid4().hex,
                        session_key_override=session_id,
                    )
                )
                self.assertEqual(outbound.session_id, session_id)
                self.assertEqual(outbound.content, "echo:hello")
                self.assertEqual(outbound.kind, "completed")

                status = manager.get_session_status(session_id)
                self.assertIsNotNone(status)
                self.assertEqual(status["session_id"], session_id)
                self.assertEqual(status["message_count"], 2)

                messages = manager.get_session_messages(session_id)
                self.assertEqual([item["text"] for item in messages], ["hello", "echo:hello"])

                sessions = manager.list_sessions()
                self.assertEqual(sessions[0]["session_id"], session_id)
            finally:
                manager.close()

    def test_boundary_returns_none_for_unknown_session(self) -> None:
        with TemporaryDirectory() as tmp:
            manager = _build_gateway_boundary(tmp)
            try:
                self.assertIsNone(manager.get_session_status("missing"))
                self.assertIsNone(manager.get_session_messages("missing"))
            finally:
                manager.close()


class GatewayHttpServerTests(unittest.TestCase):
    def test_http_server_exposes_session_endpoints(self) -> None:
        with TemporaryDirectory() as tmp:
            manager = _build_gateway_boundary(tmp)
            try:
                handler_cls = build_gateway_handler(manager)

                status, create_payload = self._request_json(handler_cls, "POST", "/sessions", {})
                self.assertEqual(status, 201)
                session_id = create_payload["session_id"]

                status, send_payload = self._request_json(
                    handler_cls,
                    "POST",
                    f"/sessions/{session_id}/messages",
                    {"text": "hello"},
                )
                self.assertEqual(status, 200)
                self.assertEqual(send_payload["text"], "echo:hello")

                status, status_payload = self._request_json(handler_cls, "GET", f"/sessions/{session_id}")
                self.assertEqual(status, 200)
                self.assertEqual(status_payload["message_count"], 2)

                status, messages_payload = self._request_json(
                    handler_cls, "GET", f"/sessions/{session_id}/messages"
                )
                self.assertEqual(status, 200)
                self.assertEqual(
                    [item["text"] for item in messages_payload["messages"]],
                    ["hello", "echo:hello"],
                )

                status, sessions_payload = self._request_json(handler_cls, "GET", "/sessions")
                self.assertEqual(status, 200)
                self.assertEqual(sessions_payload["sessions"][0]["session_id"], session_id)

                status, missing_payload = self._request_json(handler_cls, "GET", "/sessions/missing")
                self.assertEqual(status, 404)
                self.assertIn("not found", missing_payload["error"].lower())

                status, job_payload = self._request_json(
                    handler_cls,
                    "POST",
                    "/jobs",
                    {
                        "session_id": session_id,
                        "text": "nightly summary",
                        "cron_expression": "*/5 * * * *",
                        "retry_enabled": True,
                        "retry_delay_seconds": 30,
                        "retry_backoff_multiplier": 2.0,
                        "max_retries": 2,
                    },
                )
                self.assertEqual(status, 201)
                job_id = job_payload["job_id"]

                status, jobs_payload = self._request_json(handler_cls, "GET", "/jobs")
                self.assertEqual(status, 200)
                self.assertEqual(jobs_payload["jobs"][0]["job_id"], job_id)

                status, updated_job = self._request_json(
                    handler_cls,
                    "PATCH",
                    f"/jobs/{job_id}",
                    {"text": "updated summary", "enabled": False},
                )
                self.assertEqual(status, 200)
                self.assertEqual(updated_job["text"], "updated summary")
                self.assertFalse(updated_job["enabled"])
                self.assertEqual(updated_job["retry_backoff_multiplier"], 2.0)

                status, at_job = self._request_json(
                    handler_cls,
                    "POST",
                    "/jobs",
                    {
                        "session_id": session_id,
                        "text": "absolute time job",
                        "run_at": "2026-04-16T09:00:00+08:00",
                    },
                )
                self.assertEqual(status, 201)
                self.assertEqual(at_job["text"], "absolute time job")

                status, history_payload = self._request_json(handler_cls, "GET", "/jobs/history")
                self.assertEqual(status, 200)
                self.assertEqual(history_payload["history"], [])

                status, removed_payload = self._request_json(handler_cls, "DELETE", f"/jobs/{job_id}")
                self.assertEqual(status, 200)
                self.assertTrue(removed_payload["removed"])

                status, stream_body = self._request_raw(
                    handler_cls,
                    "POST",
                    f"/sessions/{session_id}/messages/stream",
                    {"text": "stream me"},
                )
                self.assertEqual(status, 200)
                self.assertIn("event: assistant.delta", stream_body)
                self.assertIn("event: session.turn.completed", stream_body)

                status, ws_events = self._request_websocket(
                    handler_cls,
                    f"/sessions/{session_id}/ws?once=1",
                    {"type": "user_message", "text": "ws hello"},
                )
                self.assertEqual(status, 101)
                self.assertEqual(ws_events[0]["topic"], "session.ready")
                self.assertTrue(any(event["topic"] == "assistant.delta" for event in ws_events))
                self.assertTrue(any(event["topic"] == "session.turn.completed" for event in ws_events))
            finally:
                manager.close()

    def _request_json(self, handler_cls, method: str, path: str, payload=None):
        body = b""
        headers = []
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers.append("Content-Type: application/json")
            headers.append(f"Content-Length: {len(body)}")
        request_lines = [f"{method} {path} HTTP/1.1", "Host: test", *headers, "", ""]
        request_bytes = "\r\n".join(request_lines).encode("utf-8") + body

        client, server_sock = socket.socketpair()
        try:
            client.settimeout(1)
            client.sendall(request_bytes)
            server = type("Server", (), {"server_name": "test", "server_port": 0})()
            handler_cls(server_sock, ("127.0.0.1", 0), server)
            response = b""
            while True:
                try:
                    chunk = client.recv(65536)
                except socket.timeout:
                    break
                if not chunk:
                    break
                response += chunk
        finally:
            client.close()
            server_sock.close()

        header_bytes, _, body_bytes = response.partition(b"\r\n\r\n")
        status_line = header_bytes.splitlines()[0].decode("utf-8")
        status = int(status_line.split(" ")[1])
        return status, json.loads(body_bytes.decode("utf-8"))

    def _request_raw(self, handler_cls, method: str, path: str, payload=None):
        body = b""
        headers = []
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            headers.append("Content-Type: application/json")
            headers.append(f"Content-Length: {len(body)}")
        request_lines = [f"{method} {path} HTTP/1.1", "Host: test", *headers, "", ""]
        request_bytes = "\r\n".join(request_lines).encode("utf-8") + body
        response = self._invoke_handler(handler_cls, request_bytes)
        header_bytes, _, body_bytes = response.partition(b"\r\n\r\n")
        status_line = header_bytes.splitlines()[0].decode("utf-8")
        status = int(status_line.split(" ")[1])
        return status, body_bytes.decode("utf-8")

    def _request_websocket(self, handler_cls, path: str, message: dict):
        websocket_key = b64encode(b"test-websocket-key").decode("utf-8")
        request_lines = [
            f"GET {path} HTTP/1.1",
            "Host: test",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {websocket_key}",
            "Sec-WebSocket-Version: 13",
            "",
            "",
        ]
        request_bytes = "\r\n".join(request_lines).encode("utf-8")
        request_bytes += self._encode_masked_websocket_frame(json.dumps(message).encode("utf-8"))
        request_bytes += self._encode_masked_websocket_frame(
            json.dumps({"type": "close"}).encode("utf-8")
        )

        response = self._invoke_handler(handler_cls, request_bytes)
        header_bytes, _, body_bytes = response.partition(b"\r\n\r\n")
        status_line = header_bytes.splitlines()[0].decode("utf-8")
        status = int(status_line.split(" ")[1])
        events = self._decode_server_websocket_frames(body_bytes)
        return status, events

    def _invoke_handler(self, handler_cls, request_bytes: bytes) -> bytes:
        client, server_sock = socket.socketpair()
        try:
            client.settimeout(1)
            client.sendall(request_bytes)
            server = type("Server", (), {"server_name": "test", "server_port": 0})()
            handler_cls(server_sock, ("127.0.0.1", 0), server)
            response = b""
            while True:
                try:
                    chunk = client.recv(65536)
                except socket.timeout:
                    break
                if not chunk:
                    break
                response += chunk
            return response
        finally:
            client.close()
            server_sock.close()

    def _encode_masked_websocket_frame(self, payload: bytes) -> bytes:
        mask = b"mask"
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
        header = bytearray([0x81])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        else:
            header.append(0x80 | 126)
            header.extend(length.to_bytes(2, "big"))
        header.extend(mask)
        return bytes(header) + masked

    def _decode_server_websocket_frames(self, payload: bytes):
        events = []
        index = 0
        while index + 2 <= len(payload):
            first = payload[index]
            second = payload[index + 1]
            opcode = first & 0x0F
            length = second & 0x7F
            index += 2
            if length == 126:
                length = int.from_bytes(payload[index : index + 2], "big")
                index += 2
            elif length == 127:
                length = int.from_bytes(payload[index : index + 8], "big")
                index += 8
            frame_payload = payload[index : index + length]
            index += length
            if opcode == 0x8:
                break
            if opcode != 0x1:
                continue
            events.append(json.loads(frame_payload.decode("utf-8")))
        return events
