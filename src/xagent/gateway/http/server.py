from __future__ import annotations

import base64
import hashlib
import json
import queue
import struct
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime
from typing import Any, Callable, Protocol
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from xagent.bus.messages import InboundMessage

WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
TERMINAL_TOPICS = {"session.turn.completed", "session.turn.failed"}
OUTBOUND_TOPIC_MAP = {
    "delta": "assistant.delta",
    "completed": "session.turn.completed",
    "failed": "session.turn.failed",
    "tool_called": "tool.called",
    "tool_finished": "tool.finished",
    "compaction_completed": "memory.compaction.completed",
}


class GatewayRuntimeBoundary(Protocol):
    def list_sessions(self) -> list[dict[str, Any]]:
        ...

    def list_jobs(self) -> list[dict[str, Any]]:
        ...

    def list_job_history(self, *, job_id=None, limit: int = 100) -> list[dict[str, Any]]:
        ...

    def get_session_status(self, session_id: str):
        ...

    def get_session_messages(self, session_id: str):
        ...

    def create_session(self, *, session_key=None) -> str:
        ...

    def add_cron_job(self, session_id: str, text: str, **kwargs):
        ...

    def add_once_job(self, session_id: str, text: str, **kwargs):
        ...

    def update_job(self, job_id: str, **kwargs):
        ...

    def remove_job(self, job_id: str) -> bool:
        ...

    def send_and_wait(self, inbound: InboundMessage):
        ...

    def open_response_stream(self, inbound: InboundMessage) -> tuple["queue.Queue[Any]", Callable[[], None]]:
        ...


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _read_json(handler: BaseHTTPRequestHandler):
    length = int(handler.headers.get("Content-Length", "0") or "0")
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


def _parse_run_at(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    timestamp = datetime.fromisoformat(str(value))
    if timestamp.tzinfo is None:
        timestamp = timestamp.astimezone()
    return timestamp.timestamp()


def _sse_response_start(handler: BaseHTTPRequestHandler) -> None:
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Connection", "close")
    handler.end_headers()


def _write_sse_event(handler: BaseHTTPRequestHandler, payload: dict) -> None:
    data = json.dumps(payload, ensure_ascii=False)
    body = f"event: {payload.get('topic', 'message')}\ndata: {data}\n\n".encode("utf-8")
    handler.wfile.write(body)
    handler.wfile.flush()


def _websocket_accept(key: str) -> str:
    digest = hashlib.sha1(f"{key}{WEBSOCKET_GUID}".encode("utf-8")).digest()
    return base64.b64encode(digest).decode("utf-8")


def _send_websocket_frame(handler: BaseHTTPRequestHandler, payload: bytes, opcode: int = 0x1) -> None:
    header = bytearray([0x80 | (opcode & 0x0F)])
    length = len(payload)
    if length < 126:
        header.append(length)
    elif length < (1 << 16):
        header.append(126)
        header.extend(struct.pack(">H", length))
    else:
        header.append(127)
        header.extend(struct.pack(">Q", length))
    handler.wfile.write(bytes(header) + payload)
    handler.wfile.flush()


def _read_websocket_frame(handler: BaseHTTPRequestHandler) -> tuple[int, bytes] | None:
    first = handler.rfile.read(2)
    if len(first) < 2:
        return None
    opcode = first[0] & 0x0F
    masked = bool(first[1] & 0x80)
    length = first[1] & 0x7F
    if length == 126:
        length = struct.unpack(">H", handler.rfile.read(2))[0]
    elif length == 127:
        length = struct.unpack(">Q", handler.rfile.read(8))[0]
    mask = handler.rfile.read(4) if masked else b""
    payload = handler.rfile.read(length)
    if masked and mask:
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    return opcode, payload


def _send_websocket_json(handler: BaseHTTPRequestHandler, payload: dict) -> None:
    _send_websocket_frame(handler, json.dumps(payload, ensure_ascii=False).encode("utf-8"))


def _outbound_to_event(message) -> dict:
    topic = OUTBOUND_TOPIC_MAP.get(message.kind, message.kind)
    payload: dict[str, object] = dict(message.metadata or {})
    if message.kind == "delta":
        payload["text"] = message.content
    elif message.kind == "completed":
        payload.setdefault("message", {"role": "assistant", "content": [{"type": "text", "text": message.content}], "text": message.content})
        payload.setdefault("duration_seconds", message.metadata.get("duration_seconds") if message.metadata else None)
    elif message.kind == "failed":
        payload["error"] = message.error
    return {
        "topic": topic,
        "session_id": message.session_id,
        "source": message.source,
        "payload": payload,
    }


def build_gateway_handler(manager: GatewayRuntimeBoundary):
    class GatewayHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            parts = [part for part in parsed.path.split("/") if part]
            query = parse_qs(parsed.query)

            if parts == ["sessions"]:
                try:
                    payload = {"sessions": manager.list_sessions()}
                except Exception as exc:
                    _json_response(self, 500, {"error": str(exc)})
                    return
                _json_response(self, 200, payload)
                return

            if parts == ["jobs"]:
                try:
                    payload = {"jobs": manager.list_jobs()}
                except Exception as exc:
                    _json_response(self, 500, {"error": str(exc)})
                    return
                _json_response(self, 200, payload)
                return

            if parts == ["jobs", "history"]:
                try:
                    limit = int(query.get("limit", ["100"])[0])
                    payload = {
                        "history": manager.list_job_history(
                            job_id=query.get("job_id", [None])[0],
                            limit=limit,
                        )
                    }
                except Exception as exc:
                    _json_response(self, 500, {"error": str(exc)})
                    return
                _json_response(self, 200, payload)
                return

            if len(parts) == 2 and parts[0] == "sessions":
                session_id = parts[1]
                try:
                    status = manager.get_session_status(session_id)
                except Exception as exc:
                    _json_response(self, 500, {"error": str(exc)})
                    return
                if status is None:
                    _json_response(self, 404, {"error": f"Session '{session_id}' was not found."})
                    return
                _json_response(self, 200, status)
                return

            if len(parts) == 3 and parts[0] == "sessions" and parts[2] == "messages":
                session_id = parts[1]
                try:
                    messages = manager.get_session_messages(session_id)
                except Exception as exc:
                    _json_response(self, 500, {"error": str(exc)})
                    return
                if messages is None:
                    _json_response(self, 404, {"error": f"Session '{session_id}' was not found."})
                    return
                _json_response(self, 200, {"messages": messages})
                return

            if len(parts) == 3 and parts[0] == "sessions" and parts[2] == "ws":
                session_id = parts[1]
                if manager.get_session_status(session_id) is None:
                    _json_response(self, 404, {"error": f"Session '{session_id}' was not found."})
                    return
                self._handle_websocket(
                    session_id,
                    once=query.get("once", ["0"])[0].lower() in {"1", "true", "yes"},
                )
                return

            _json_response(self, 404, {"error": "Not found."})

        def do_POST(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            parts = [part for part in parsed.path.split("/") if part]

            if parts == ["sessions"]:
                session_id = manager.create_session(session_key=f"gateway:{uuid4().hex}")
                _json_response(self, 201, {"session_id": session_id})
                return

            if parts == ["jobs"]:
                try:
                    payload = _read_json(self)
                except json.JSONDecodeError:
                    _json_response(self, 400, {"error": "Invalid JSON body."})
                    return
                session_id = str(payload.get("session_id", "")).strip()
                text = str(payload.get("text", "")).strip()
                if not session_id or not text:
                    _json_response(self, 400, {"error": "Fields 'session_id' and 'text' are required."})
                    return
                cron_expression = payload.get("cron_expression")
                delay_seconds = payload.get("delay_seconds")
                run_at = payload.get("run_at")
                try:
                    option_count = int(bool(cron_expression)) + int(delay_seconds is not None) + int(run_at is not None)
                    if option_count != 1:
                        raise ValueError("Specify exactly one of 'cron_expression', 'delay_seconds', or 'run_at'.")
                    if int(delay_seconds is not None) + int(run_at is not None) > 1:
                        raise ValueError("Choose only one of 'delay_seconds' or 'run_at'.")
                    if cron_expression:
                        response = manager.add_cron_job(
                            session_id,
                            text,
                            cron_expression=str(cron_expression),
                            requested_skill_name=payload.get("requested_skill_name"),
                            retry_enabled=bool(payload.get("retry_enabled", False)),
                            retry_delay_seconds=float(payload.get("retry_delay_seconds", 60.0)),
                            retry_backoff_multiplier=float(payload.get("retry_backoff_multiplier", 1.0)),
                            max_retries=int(payload.get("max_retries", 0)),
                            source="gateway.http",
                        )
                    else:
                        response = manager.add_once_job(
                            session_id,
                            text,
                            delay_seconds=float(delay_seconds) if delay_seconds is not None else 0.0,
                            run_at=_parse_run_at(run_at),
                            requested_skill_name=payload.get("requested_skill_name"),
                            retry_enabled=bool(payload.get("retry_enabled", False)),
                            retry_delay_seconds=float(payload.get("retry_delay_seconds", 60.0)),
                            retry_backoff_multiplier=float(payload.get("retry_backoff_multiplier", 1.0)),
                            max_retries=int(payload.get("max_retries", 0)),
                            source="gateway.http",
                        )
                except KeyError:
                    _json_response(self, 404, {"error": f"Session '{session_id}' was not found."})
                    return
                except Exception as exc:
                    _json_response(self, 400, {"error": str(exc)})
                    return
                _json_response(self, 201, response)
                return

            if len(parts) == 3 and parts[0] == "sessions" and parts[2] == "messages":
                session_id = parts[1]
                try:
                    payload = _read_json(self)
                except json.JSONDecodeError:
                    _json_response(self, 400, {"error": "Invalid JSON body."})
                    return
                text = str(payload.get("text", "")).strip()
                if not text:
                    _json_response(self, 400, {"error": "Field 'text' is required."})
                    return
                if manager.get_session_status(session_id) is None:
                    _json_response(self, 404, {"error": f"Session '{session_id}' was not found."})
                    return
                try:
                    outbound = manager.send_and_wait(
                        InboundMessage(
                            content=text,
                            source="gateway.http",
                            channel="gateway",
                            sender_id="gateway",
                            chat_id=session_id,
                            requested_skill_name=payload.get("requested_skill_name"),
                            correlation_id=uuid4().hex,
                            session_key_override=session_id,
                        )
                    )
                except Exception as exc:
                    _json_response(self, 500, {"error": str(exc)})
                    return
                response = {
                    "session_id": outbound.session_id,
                    "message": outbound.metadata.get("message")
                    or {
                        "role": "assistant",
                        "content": [{"type": "text", "text": outbound.content}],
                        "text": outbound.content,
                    },
                    "text": outbound.content,
                    "status": manager.get_session_status(outbound.session_id),
                }
                if outbound.metadata.get("duration_seconds") is not None:
                    response["duration_seconds"] = outbound.metadata["duration_seconds"]
                _json_response(self, 200, response)
                return

            if len(parts) == 4 and parts[0] == "sessions" and parts[2] == "messages" and parts[3] == "stream":
                session_id = parts[1]
                if manager.get_session_status(session_id) is None:
                    _json_response(self, 404, {"error": f"Session '{session_id}' was not found."})
                    return
                try:
                    payload = _read_json(self)
                except json.JSONDecodeError:
                    _json_response(self, 400, {"error": "Invalid JSON body."})
                    return
                text = str(payload.get("text", "")).strip()
                if not text:
                    _json_response(self, 400, {"error": "Field 'text' is required."})
                    return
                self._handle_sse_stream(
                    session_id,
                    text=text,
                    requested_skill_name=payload.get("requested_skill_name"),
                )
                return

            _json_response(self, 404, {"error": "Not found."})

        def do_PATCH(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            parts = [part for part in parsed.path.split("/") if part]

            if len(parts) == 2 and parts[0] == "jobs":
                job_id = parts[1]
                try:
                    payload = _read_json(self)
                except json.JSONDecodeError:
                    _json_response(self, 400, {"error": "Invalid JSON body."})
                    return
                try:
                    response = manager.update_job(
                        job_id,
                        text=payload.get("text"),
                        cron_expression=payload.get("cron_expression"),
                        delay_seconds=payload.get("delay_seconds"),
                        run_at=_parse_run_at(payload.get("run_at")),
                        requested_skill_name=payload.get("requested_skill_name"),
                        retry_enabled=payload.get("retry_enabled"),
                        retry_delay_seconds=payload.get("retry_delay_seconds"),
                        retry_backoff_multiplier=payload.get("retry_backoff_multiplier"),
                        max_retries=payload.get("max_retries"),
                        enabled=payload.get("enabled"),
                    )
                except KeyError:
                    _json_response(self, 404, {"error": f"Job '{job_id}' was not found."})
                    return
                except Exception as exc:
                    _json_response(self, 400, {"error": str(exc)})
                    return
                _json_response(self, 200, response)
                return

            _json_response(self, 404, {"error": "Not found."})

        def do_DELETE(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            parts = [part for part in parsed.path.split("/") if part]

            if len(parts) == 2 and parts[0] == "jobs":
                job_id = parts[1]
                try:
                    removed = manager.remove_job(job_id)
                except Exception as exc:
                    _json_response(self, 500, {"error": str(exc)})
                    return
                if not removed:
                    _json_response(self, 404, {"error": f"Job '{job_id}' was not found."})
                    return
                _json_response(self, 200, {"removed": True, "job_id": job_id})
                return

            _json_response(self, 404, {"error": "Not found."})

        def _handle_sse_stream(
            self,
            session_id: str,
            *,
            text: str,
            requested_skill_name=None,
        ) -> None:
            inbound = InboundMessage(
                content=text,
                source="gateway.sse",
                channel="gateway",
                sender_id="gateway",
                chat_id=session_id,
                requested_skill_name=requested_skill_name,
                session_key_override=session_id,
            )
            outbound_queue, unsubscribe = manager.open_response_stream(inbound)
            _sse_response_start(self)
            try:
                while True:
                    try:
                        outbound = outbound_queue.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    event = _outbound_to_event(outbound)
                    _write_sse_event(self, event)
                    if event.get("topic") in TERMINAL_TOPICS:
                        break
            finally:
                unsubscribe()

        def _handle_websocket(self, session_id: str, *, once: bool) -> None:
            websocket_key = self.headers.get("Sec-WebSocket-Key")
            if not websocket_key:
                _json_response(self, 400, {"error": "Missing Sec-WebSocket-Key header."})
                return

            self.send_response(101, "Switching Protocols")
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", _websocket_accept(websocket_key))
            self.end_headers()
            _send_websocket_json(self, {"topic": "session.ready", "session_id": session_id})

            while True:
                frame = _read_websocket_frame(self)
                if frame is None:
                    return
                opcode, payload = frame
                if opcode == 0x8:
                    _send_websocket_frame(self, b"", opcode=0x8)
                    return
                if opcode == 0x9:
                    _send_websocket_frame(self, payload, opcode=0xA)
                    continue
                if opcode != 0x1:
                    continue

                try:
                    message = json.loads(payload.decode("utf-8"))
                except json.JSONDecodeError:
                    _send_websocket_json(self, {"topic": "error", "payload": {"error": "Invalid JSON payload."}})
                    if once:
                        _send_websocket_frame(self, b"", opcode=0x8)
                        return
                    continue

                if message.get("type") == "close":
                    _send_websocket_frame(self, b"", opcode=0x8)
                    return

                if message.get("type") != "user_message":
                    _send_websocket_json(self, {"topic": "error", "payload": {"error": "Unsupported message type."}})
                    if once:
                        _send_websocket_frame(self, b"", opcode=0x8)
                        return
                    continue

                text = str(message.get("text", "")).strip()
                if not text:
                    _send_websocket_json(self, {"topic": "error", "payload": {"error": "Field 'text' is required."}})
                    if once:
                        _send_websocket_frame(self, b"", opcode=0x8)
                        return
                    continue

                inbound = InboundMessage(
                    content=text,
                    source="gateway.websocket",
                    channel="gateway",
                    sender_id="gateway",
                    chat_id=session_id,
                    requested_skill_name=message.get("requested_skill_name"),
                    session_key_override=session_id,
                )
                outbound_queue, unsubscribe = manager.open_response_stream(inbound)
                try:
                    while True:
                        try:
                            outbound = outbound_queue.get(timeout=0.1)
                        except queue.Empty:
                            continue
                        event = _outbound_to_event(outbound)
                        _send_websocket_json(self, event)
                        if event.get("topic") in TERMINAL_TOPICS:
                            break
                finally:
                    unsubscribe()

                if once:
                    _send_websocket_frame(self, b"", opcode=0x8)
                    return

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

    return GatewayHandler


class GatewayHTTPServer(ThreadingHTTPServer):
    def __init__(self, server_address, manager: GatewayRuntimeBoundary) -> None:
        super().__init__(server_address, build_gateway_handler(manager))
