from __future__ import annotations

import asyncio
import inspect
import queue
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Generic, Optional, TypeVar
from uuid import uuid4

from xagent.agent.runtime.manager import SessionRuntimeManager
from xagent.foundation.messages import Message, message_text

MessageT = TypeVar("MessageT")
MessageHandler = Callable[[MessageT], Any]

TERMINAL_OUTBOUND_KINDS = frozenset({"completed", "failed"})


@dataclass
class InboundMessage:
    content: str
    source: str
    channel: str = "local"
    sender_id: str = "local"
    chat_id: str = "local"
    requested_skill_name: Optional[str] = None
    reply_to: Optional[str] = None
    media: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    correlation_id: str = field(default_factory=lambda: uuid4().hex)
    session_key_override: Optional[str] = None

    @property
    def session_key(self) -> str:
        if self.session_key_override:
            return self.session_key_override
        session_scope = self.metadata.get("session_scope")
        session_value = self.metadata.get("session_value")
        if session_scope and session_value:
            return f"{self.channel}:{session_scope}:{session_value}"
        chat_type = str(self.metadata.get("chat_type") or "").lower()
        if chat_type:
            if chat_type == "p2p":
                return f"{self.channel}:user:{self.sender_id}"
            return f"{self.channel}:chat:{self.chat_id}"
        return f"{self.channel}:{self.chat_id or self.sender_id or self.source}"


@dataclass
class OutboundMessage:
    kind: str
    correlation_id: str
    session_id: str
    session_key: str
    source: str
    channel: str
    chat_id: str
    content: str = ""
    reply_to: Optional[str] = None
    error: Optional[str] = None
    media: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def _message_text_from_payload(message: Any) -> str:
    if message is None:
        return ""
    if isinstance(message, dict):
        direct_text = str(message.get("text") or "")
        if direct_text:
            return direct_text
        parts = []
        for item in message.get("content") or []:
            if item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        return "".join(parts)
    return message_text(message)


def _build_outbound_message(
    *,
    kind: str,
    inbound: InboundMessage,
    session_id: str,
    source: str,
    content: str = "",
    error: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> OutboundMessage:
    return OutboundMessage(
        kind=kind,
        correlation_id=inbound.correlation_id,
        session_id=session_id,
        session_key=inbound.session_key,
        source=source,
        channel=inbound.channel,
        chat_id=inbound.chat_id,
        content=content,
        reply_to=inbound.reply_to,
        error=error,
        metadata=metadata or {},
    )


def _runtime_event_to_outbound(
    *,
    topic: str,
    payload: dict[str, Any],
    source: str,
    inbound: InboundMessage,
    session_id: str,
) -> OutboundMessage:
    if topic == "assistant.delta":
        return _build_outbound_message(
            kind="delta",
            inbound=inbound,
            session_id=session_id,
            source=source,
            content=str(payload.get("text") or ""),
            metadata={"request_id": payload.get("request_id")},
        )
    if topic == "tool.called":
        return _build_outbound_message(
            kind="tool_called",
            inbound=inbound,
            session_id=session_id,
            source=source,
            metadata={
                "request_id": payload.get("request_id"),
                "tool_use": payload.get("tool_use"),
                "tool_name": payload.get("tool_name"),
                "tool_input": payload.get("tool_input"),
            },
        )
    if topic == "tool.finished":
        content = str(payload.get("content") or "")
        is_error = bool(payload.get("is_error", False))
        return _build_outbound_message(
            kind="tool_finished",
            inbound=inbound,
            session_id=session_id,
            source=source,
            content=content,
            error=content if is_error else None,
            metadata={
                "request_id": payload.get("request_id"),
                "result": payload.get("result"),
                "is_error": is_error,
            },
        )
    if topic == "session.turn.completed":
        return _build_outbound_message(
            kind="completed",
            inbound=inbound,
            session_id=session_id,
            source=source,
            content=_message_text_from_payload(payload.get("message")),
            metadata={
                "request_id": payload.get("request_id"),
                "message": payload.get("message"),
                "duration_seconds": payload.get("duration_seconds"),
            },
        )
    if topic == "session.turn.failed":
        return _build_outbound_message(
            kind="failed",
            inbound=inbound,
            session_id=session_id,
            source=source,
            error=str(payload.get("error") or "Unknown runtime failure."),
            metadata={
                "request_id": payload.get("request_id"),
                "error_type": payload.get("error_type"),
            },
        )
    if topic == "memory.compaction.completed":
        return _build_outbound_message(
            kind="compaction_completed",
            inbound=inbound,
            session_id=session_id,
            source=source,
            metadata=dict(payload),
        )
    return _build_outbound_message(
        kind="event",
        inbound=inbound,
        session_id=session_id,
        source=source,
        metadata=dict(payload),
    )


def _system_outbound_message(
    *,
    kind: str,
    session_id: str,
    source: str,
    payload: dict[str, Any],
) -> OutboundMessage:
    return OutboundMessage(
        kind=kind,
        correlation_id="system",
        session_id=session_id,
        session_key=f"local:{session_id}",
        source=source,
        channel="local",
        chat_id=session_id,
        metadata=dict(payload),
    )


def _subscribe_outbound_stream(
    out_msg_bus: "TypedMessageBus[OutboundMessage]",
    message: InboundMessage,
    *,
    terminal_only: bool = False,
) -> tuple["queue.Queue[OutboundMessage]", Callable[[], None]]:
    outbound_queue: "queue.Queue[OutboundMessage]" = queue.Queue()

    def _handler(outbound: OutboundMessage) -> None:
        outbound_queue.put_nowait(outbound)

    unsubscribe = out_msg_bus.subscribe(
        _handler,
        predicate=lambda outbound: outbound.correlation_id == message.correlation_id
        and (not terminal_only or outbound.kind in TERMINAL_OUTBOUND_KINDS),
    )
    return outbound_queue, unsubscribe


async def _await_terminal_outbound(
    out_msg_bus: "TypedMessageBus[OutboundMessage]",
    message: InboundMessage,
    submit: Callable[[InboundMessage], Any],
) -> OutboundMessage:
    loop = asyncio.get_running_loop()
    response_future: "asyncio.Future[OutboundMessage]" = loop.create_future()

    def _handler(outbound: OutboundMessage) -> None:
        if response_future.done():
            return
        response_future.set_result(outbound)

    unsubscribe = out_msg_bus.subscribe(
        _handler,
        predicate=lambda outbound: outbound.correlation_id == message.correlation_id
        and outbound.kind in TERMINAL_OUTBOUND_KINDS,
    )
    try:
        result = submit(message)
        if inspect.isawaitable(result):
            await result
        return await response_future
    finally:
        unsubscribe()


def _wait_for_terminal_outbound(
    out_msg_bus: "TypedMessageBus[OutboundMessage]",
    message: InboundMessage,
    submit: Callable[[InboundMessage], None],
    *,
    timeout_seconds: float,
) -> OutboundMessage:
    response_queue, unsubscribe = _subscribe_outbound_stream(
        out_msg_bus,
        message,
        terminal_only=True,
    )
    try:
        submit(message)
        return response_queue.get(timeout=timeout_seconds)
    finally:
        unsubscribe()


class TypedMessageBus(Generic[MessageT]):
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._handlers: dict[int, tuple[MessageHandler[MessageT], Optional[Callable[[MessageT], bool]]]] = {}
        self._next_handler_id = 0

    def subscribe(
        self,
        handler: MessageHandler[MessageT],
        *,
        predicate: Optional[Callable[[MessageT], bool]] = None,
    ) -> Callable[[], None]:
        with self._lock:
            handler_id = self._next_handler_id
            self._next_handler_id += 1
            self._handlers[handler_id] = (handler, predicate)

        def _unsubscribe() -> None:
            with self._lock:
                self._handlers.pop(handler_id, None)

        return _unsubscribe

    async def publish(self, message: MessageT) -> None:
        for handler, predicate in self._snapshot_handlers():
            if predicate is not None and not predicate(message):
                continue
            result = handler(message)
            if inspect.isawaitable(result):
                await result

    def publish_nowait(self, message: MessageT) -> None:
        for handler, predicate in self._snapshot_handlers():
            if predicate is not None and not predicate(message):
                continue
            result = handler(message)
            if inspect.isawaitable(result):
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    asyncio.run(result)
                else:
                    loop.create_task(result)

    def _snapshot_handlers(self) -> list[tuple[MessageHandler[MessageT], Optional[Callable[[MessageT], bool]]]]:
        with self._lock:
            return list(self._handlers.values())


class LocalRuntimeBoundary:
    def __init__(self, *, runtime: Any) -> None:
        self.runtime = runtime
        self.out_msg_bus: TypedMessageBus[OutboundMessage] = TypedMessageBus()
        self._internal_bus = runtime.bus
        self._inbound_index: dict[str, InboundMessage] = {}
        self._active_inbound: Optional[InboundMessage] = None
        self._unsubscribe_runtime = [
            self._internal_bus.subscribe("assistant.delta", self._handle_assistant_delta),
            self._internal_bus.subscribe("tool.called", self._handle_tool_called),
            self._internal_bus.subscribe("tool.finished", self._handle_tool_finished),
            self._internal_bus.subscribe("session.turn.completed", self._handle_turn_completed),
            self._internal_bus.subscribe("session.turn.failed", self._handle_turn_failed),
            self._internal_bus.subscribe("memory.compaction.completed", self._handle_compaction_completed),
        ]

    @property
    def session_id(self) -> str:
        return self.runtime.session_id

    @property
    def messages(self) -> list[Message]:
        return self.runtime.messages

    async def submit(self, message: InboundMessage):
        self._inbound_index[message.correlation_id] = message
        await self._handle_inbound(message)

    def list_sessions(self, limit: int = 20):
        return self.runtime.list_sessions(limit=limit)

    def save_session(self):
        return self.runtime.save_session()

    def start_new_session(self, *, save_current: bool = True) -> str:
        return self.runtime.start_new_session(save_current=save_current)

    def restore_session(self, session_id: str):
        return self.runtime.restore_session(session_id)

    def clear_session(self) -> None:
        self.runtime.clear_session()

    def abort(self) -> None:
        self.runtime.abort()

    async def wait_for_background_tasks(self) -> None:
        await self.runtime.wait_for_background_tasks()

    async def submit_and_wait(self, message: InboundMessage) -> OutboundMessage:
        return await _await_terminal_outbound(self.out_msg_bus, message, self.submit)

    def close(self) -> None:
        for unsubscribe in self._unsubscribe_runtime:
            unsubscribe()
        self.runtime.close()

    async def _handle_inbound(self, message: InboundMessage) -> None:
        self._active_inbound = message
        try:
            await self.runtime.publish_user_message(
                message.content,
                source=message.source,
                requested_skill_name=message.requested_skill_name,
                request_id=message.correlation_id,
            )
        finally:
            self._active_inbound = None

    def _handle_assistant_delta(self, event: Any) -> None:
        request_id = str(event.payload.get("request_id") or "")
        inbound = self._match_inbound(request_id)
        if inbound is None:
            return
        self.out_msg_bus.publish_nowait(
            _runtime_event_to_outbound(
                topic="assistant.delta",
                payload=event.payload,
                source=event.source,
                inbound=inbound,
                session_id=self.runtime.session_id,
            )
        )

    def _handle_tool_called(self, event: Any) -> None:
        request_id = str(event.payload.get("request_id") or "")
        inbound = self._match_inbound(request_id)
        if inbound is None:
            return
        self.out_msg_bus.publish_nowait(
            _runtime_event_to_outbound(
                topic="tool.called",
                payload={
                    **event.payload,
                    "tool_name": getattr(event.payload.get("tool_use"), "name", None),
                    "tool_input": getattr(event.payload.get("tool_use"), "input", None),
                },
                source=event.source,
                inbound=inbound,
                session_id=self.runtime.session_id,
            )
        )

    def _handle_tool_finished(self, event: Any) -> None:
        request_id = str(event.payload.get("request_id") or "")
        inbound = self._match_inbound(request_id)
        if inbound is None:
            return
        result = event.payload.get("result")
        self.out_msg_bus.publish_nowait(
            _runtime_event_to_outbound(
                topic="tool.finished",
                payload={
                    **event.payload,
                    "content": str(getattr(result, "content", "") or ""),
                    "is_error": getattr(result, "is_error", False),
                },
                source=event.source,
                inbound=inbound,
                session_id=self.runtime.session_id,
            )
        )

    def _handle_turn_completed(self, event: Any) -> None:
        request_id = str(event.payload.get("request_id") or "")
        inbound = self._match_inbound(request_id)
        if inbound is None:
            return
        self.out_msg_bus.publish_nowait(
            _runtime_event_to_outbound(
                topic="session.turn.completed",
                payload=event.payload,
                source=event.source,
                inbound=inbound,
                session_id=self.runtime.session_id,
            )
        )
        self._inbound_index.pop(inbound.correlation_id, None)

    def _handle_turn_failed(self, event: Any) -> None:
        request_id = str(event.payload.get("request_id") or "")
        inbound = self._match_inbound(request_id)
        if inbound is None:
            return
        self.out_msg_bus.publish_nowait(
            _runtime_event_to_outbound(
                topic="session.turn.failed",
                payload=event.payload,
                source=event.source,
                inbound=inbound,
                session_id=self.runtime.session_id,
            )
        )
        self._inbound_index.pop(inbound.correlation_id, None)

    def _handle_compaction_completed(self, event: Any) -> None:
        self.out_msg_bus.publish_nowait(
            _system_outbound_message(
                kind="compaction_completed",
                session_id=self.runtime.session_id,
                source=event.source,
                payload=event.payload or {},
            )
        )

    def _match_inbound(self, request_id: str) -> Optional[InboundMessage]:
        if not request_id:
            return None
        correlation_id = request_id.split(":", 1)[-1]
        inbound = self._inbound_index.get(correlation_id)
        if inbound is not None:
            return inbound
        if self._active_inbound is not None:
            return self._active_inbound
        for candidate in self._inbound_index.values():
            if request_id.endswith(candidate.correlation_id):
                return candidate
        return None


class ManagedRuntimeBoundary:
    def __init__(
        self,
        *,
        manager: SessionRuntimeManager,
    ) -> None:
        self.manager = manager
        self.out_msg_bus: TypedMessageBus[OutboundMessage] = TypedMessageBus()

    def close(self) -> None:
        self.manager.close()

    def create_session(self, *, session_key: Optional[str] = None) -> str:
        return self.manager.create_session(session_key=session_key)

    def list_sessions(self):
        return self.manager.list_sessions()

    def get_session_status(self, session_id: str):
        return self.manager.get_session_status(session_id)

    def get_session_messages(self, session_id: str):
        return self.manager.get_session_messages(session_id)

    def send_message(
        self,
        session_id: str,
        text: str,
        *,
        requested_skill_name: Optional[str] = None,
        source: str = "runtime.boundary",
        request_id: Optional[str] = None,
    ) -> dict[str, Any]:
        if self.manager.get_session_status(session_id) is None:
            raise KeyError(session_id)
        outbound = self.send_and_wait(
            InboundMessage(
                content=text,
                source=source,
                channel="managed",
                sender_id="managed",
                chat_id=session_id,
                requested_skill_name=requested_skill_name,
                correlation_id=request_id or uuid4().hex,
                session_key_override=session_id,
            )
        )
        if outbound.kind == "failed":
            raise RuntimeError(outbound.error or "Runtime execution failed.")
        return {
            "session_id": outbound.session_id,
            "message": outbound.metadata.get("message")
            or {"role": "assistant", "content": [{"type": "text", "text": outbound.content}], "text": outbound.content},
            "text": outbound.content,
            "status": self.manager.get_session_status(outbound.session_id),
            **({"duration_seconds": outbound.metadata.get("duration_seconds")} if outbound.metadata.get("duration_seconds") is not None else {}),
        }

    def add_cron_job(self, *args, **kwargs):
        return self.manager.add_cron_job(*args, **kwargs)

    def add_once_job(self, *args, **kwargs):
        return self.manager.add_once_job(*args, **kwargs)

    def schedule_message(self, *args, **kwargs):
        return self.manager.schedule_message(*args, **kwargs)

    def wait_for_job(self, job_id: str):
        return self.manager.wait_for_job(job_id)

    def list_jobs(self):
        return self.manager.list_jobs()

    def remove_job(self, job_id: str) -> bool:
        return self.manager.remove_job(job_id)

    def pause_job(self, job_id: str):
        return self.manager.pause_job(job_id)

    def resume_job(self, job_id: str):
        return self.manager.resume_job(job_id)

    def update_job(self, *args, **kwargs):
        return self.manager.update_job(*args, **kwargs)

    def list_job_history(self, **kwargs):
        return self.manager.list_job_history(**kwargs)

    def start_persistent_scheduler(self, *, poll_interval_seconds: float = 1.0) -> None:
        self.manager.start_persistent_scheduler(poll_interval_seconds=poll_interval_seconds)

    def publish_nowait(self, message: InboundMessage) -> None:
        worker = threading.Thread(
            target=self._dispatch_message,
            args=(message,),
            name=f"xagent-boundary-{message.correlation_id[:8]}",
            daemon=True,
        )
        worker.start()

    def open_response_stream(
        self,
        message: InboundMessage,
        *,
        terminal_only: bool = False,
    ) -> tuple["queue.Queue[OutboundMessage]", Callable[[], None]]:
        outbound_queue, unsubscribe = _subscribe_outbound_stream(
            self.out_msg_bus,
            message,
            terminal_only=terminal_only,
        )
        self.publish_nowait(message)
        return outbound_queue, unsubscribe

    def send_and_wait(self, message: InboundMessage, *, timeout_seconds: float = 30.0) -> OutboundMessage:
        return _wait_for_terminal_outbound(
            self.out_msg_bus,
            message,
            self.publish_nowait,
            timeout_seconds=timeout_seconds,
        )

    def _dispatch_message(self, message: InboundMessage) -> None:
        session_id = ""
        try:
            session_id = self.manager.resolve_session_id(message.session_key)
            stream = self.manager.open_event_stream(
                session_id,
                topics=[
                    "assistant.delta",
                    "tool.called",
                    "tool.finished",
                    "session.turn.completed",
                    "session.turn.failed",
                    "memory.compaction.completed",
                ],
            )
            future = self.manager.submit_message(
                session_id,
                message.content,
                requested_skill_name=message.requested_skill_name,
                source=message.source,
                request_id=message.correlation_id,
            )
            terminal_seen = False
            try:
                while True:
                    try:
                        event = stream.events.get(timeout=0.1)
                    except queue.Empty:
                        if future.done():
                            exc = future.exception()
                            if exc is not None and not terminal_seen:
                                self.out_msg_bus.publish_nowait(
                                    OutboundMessage(
                                        kind="failed",
                                        correlation_id=message.correlation_id,
                                        session_id=session_id,
                                        session_key=message.session_key,
                                        source=message.source,
                                        channel=message.channel,
                                        chat_id=message.chat_id,
                                        reply_to=message.reply_to,
                                        error=str(exc),
                                    )
                                )
                            break
                        continue
                    payload = event.get("payload") or {}
                    request_id = str(payload.get("request_id") or "")
                    if request_id and request_id != message.correlation_id:
                        continue
                    outbound = _runtime_event_to_outbound(
                        topic=str(event.get("topic") or "event"),
                        payload=payload,
                        source=str(event.get("source") or message.source),
                        inbound=message,
                        session_id=session_id,
                    )
                    self.out_msg_bus.publish_nowait(outbound)
                    terminal_seen = (outbound.kind in TERMINAL_OUTBOUND_KINDS) or terminal_seen
                    if terminal_seen and future.done():
                        break
            finally:
                self.manager.close_event_stream(stream.stream_id)
        except Exception as exc:
            self.out_msg_bus.publish_nowait(
                _build_outbound_message(
                    kind="failed",
                    inbound=message,
                    session_id="",
                    source=message.source,
                    error=str(exc),
                )
            )
