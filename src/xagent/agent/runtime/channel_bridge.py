from __future__ import annotations

import concurrent.futures
import json
import logging
import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from xagent.agent.runtime import SessionRuntimeManager
from xagent.channel.contracts import ChannelSink
from xagent.channel.models import ChannelConversationKey
from xagent.foundation.runtime.paths import ensure_config_dir

logger = logging.getLogger(__name__)


@dataclass
class ChannelConversationStore:
    cwd: str

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self._path = ensure_config_dir(Path(self.cwd)) / "channel-sessions.json"

    def resolve_session_id(
        self,
        key: ChannelConversationKey,
        *,
        session_exists: Callable[[str], Any],
        create_session: Callable[[], str],
    ) -> str:
        """Resolve a stable session id for a channel conversation key.

        Note: if runtime status checks time out, prefer returning the cached session id
        to avoid blocking channel ingress workers.
        """
        resolved_key = key.as_key()
        with self._lock:
            mapping = self._load()
            session_id = mapping.get(resolved_key)
            if session_id:
                try:
                    if session_exists(session_id) is not None:
                        return session_id
                except Exception as exc:
                    # Degrade gracefully: keep using the cached session id when the runtime
                    # manager is temporarily unresponsive (e.g., timeouts).
                    logger.warning(
                        "[Bridge] session_exists check failed for session_id=%s (key=%s): %s; reusing cached session id",
                        session_id,
                        resolved_key,
                        exc,
                    )
                    return session_id
            session_id = create_session()
            mapping[resolved_key] = session_id
            self._save(mapping)
            return session_id

    def _load(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _save(self, mapping: dict[str, str]) -> None:
        self._path.write_text(json.dumps(mapping, indent=2, sort_keys=True), encoding="utf-8")


class ChannelRuntimeBridge:
    def __init__(
        self,
        *,
        cwd: str,
        manager: SessionRuntimeManager,
        conversation_store: ChannelConversationStore | None = None,
    ) -> None:
        self.cwd = str(Path(cwd).resolve())
        self.manager = manager
        self.conversation_store = conversation_store or ChannelConversationStore(self.cwd)

    def resolve_session_id(self, conversation_key: ChannelConversationKey) -> str:
        return self.conversation_store.resolve_session_id(
            conversation_key,
            session_exists=self.manager.get_session_status,
            create_session=self.manager.create_session,
        )

    def dispatch_text(
        self,
        conversation_key: ChannelConversationKey,
        text: str,
        sink: ChannelSink,
        *,
        requested_skill_name: str | None = None,
        source: str = "channel",
    ) -> str:
        logger.info("[Bridge] dispatch_text start: key=%s, text=%.100s, source=%s", conversation_key.as_key(), text, source)
        session_id = self.resolve_session_id(conversation_key)
        logger.info("[Bridge] Resolved session_id=%s for key=%s", session_id, conversation_key.as_key())
        try:
            stream = self.manager.open_event_stream(
                session_id,
                topics=["assistant.delta", "session.turn.completed", "session.turn.failed"],
            )
        except Exception as exc:
            logger.error("[Bridge] open_event_stream failed for session_id=%s: %s", session_id, exc)
            sink.on_error(f"Failed to open event stream: {exc}")
            return session_id
        logger.info("[Bridge] Event stream opened: stream_id=%s", stream.stream_id)
        try:
            future = self.manager.submit_message(
                session_id,
                text,
                requested_skill_name=requested_skill_name,
                source=source,
            )
        except Exception as exc:
            logger.error("[Bridge] submit_message failed for session_id=%s: %s", session_id, exc)
            sink.on_error(f"Failed to submit message: {exc}")
            self._close_stream_safe(stream.stream_id)
            return session_id
        logger.info("[Bridge] Message submitted, waiting for events...")
        terminal_seen = False
        event_count = 0
        try:
            while True:
                try:
                    event = stream.events.get(timeout=0.1)
                except queue.Empty:
                    if future.done():
                        exc = future.exception()
                        if exc is not None and not terminal_seen:
                            logger.error("[Bridge] Future completed with exception: %s", exc)
                            sink.on_error(str(exc))
                        else:
                            logger.info("[Bridge] Future done, terminal_seen=%s, breaking loop", terminal_seen)
                        break
                    continue
                event_count += 1
                terminal_seen = self._handle_event(event, sink) or terminal_seen
                if terminal_seen:
                    logger.info("[Bridge] Terminal event seen, breaking loop. Total events: %d", event_count)
                    break
            try:
                future.result(timeout=1)
            except concurrent.futures.TimeoutError:
                if terminal_seen:
                    logger.warning(
                        "[Bridge] Future not done after terminal event (session_id=%s). Cancelling to unblock channel worker.",
                        session_id,
                    )
                    future.cancel()
                else:
                    logger.error("[Bridge] Runtime completion timed out for session_id=%s", session_id)
                    sink.on_error("Runtime completion timed out.")
            except Exception as exc:
                if terminal_seen:
                    logger.exception(
                        "[Bridge] Future raised after terminal event (session_id=%s): %s",
                        session_id,
                        exc,
                    )
                else:
                    logger.exception("[Bridge] Future raised (session_id=%s): %s", session_id, exc)
                    sink.on_error(str(exc))
            logger.info(
                "[Bridge] dispatch_text completed. session_id=%s, terminal_seen=%s, events=%d",
                session_id,
                terminal_seen,
                event_count,
            )
        except concurrent.futures.TimeoutError:
            logger.error("[Bridge] Runtime completion timed out for session_id=%s", session_id)
            sink.on_error("Runtime completion timed out.")
        finally:
            self._close_stream_safe(stream.stream_id)
        return session_id

    def _close_stream_safe(self, stream_id: str) -> None:
        try:
            self.manager.close_event_stream(stream_id)
            logger.info("[Bridge] Event stream closed: stream_id=%s", stream_id)
        except Exception as exc:
            logger.warning("[Bridge] close_event_stream failed for stream_id=%s (leaked): %s", stream_id, exc)

    def _handle_event(self, event: dict[str, Any], sink: ChannelSink) -> bool:
        topic = event.get("topic")
        payload = event.get("payload") or {}
        if topic == "assistant.delta":
            sink.on_text(str(payload.get("text") or ""))
            return False
        if topic == "session.turn.completed":
            message = payload.get("message") or {}
            sink.on_complete(self._message_text_from_payload(message))
            return True
        if topic == "session.turn.failed":
            sink.on_error(str(payload.get("error") or "Unknown runtime failure."))
            return True
        return False

    def _message_text_from_payload(self, message: dict[str, Any]) -> str:
        direct_text = str(message.get("text") or "")
        if direct_text:
            return direct_text
        parts = []
        for item in message.get("content") or []:
            if item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
        return "".join(parts)
