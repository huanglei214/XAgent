from __future__ import annotations

import logging
import queue
import threading
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from xagent.bus.messages import InboundMessage, OutboundMessage
from xagent.channel.access import StaticChannelAccessPolicy
from xagent.channel.feishu.client import FeishuApiClient, FeishuLongConnectionClient
from xagent.channel.feishu.config import FeishuConfig
from xagent.channel.models import ChannelEnvelope, ChannelIdentity
from xagent.channel.session_routing import is_group_message_allowed

logger = logging.getLogger(__name__)


class ResponseStreamBoundary(Protocol):
    def open_response_stream(
        self,
        inbound: InboundMessage,
    ) -> tuple["queue.Queue[OutboundMessage]", Callable[[], None]]:
        ...


class FeishuTextStreamSink:
    def __init__(
        self, api_client: FeishuApiClient, chat_id: str, *, partial_emit_chars: int
    ) -> None:
        self._api_client = api_client
        self._chat_id = chat_id
        self._partial_emit_chars = max(1, partial_emit_chars)
        self._last_sent_text = ""
        self._last_snapshot = ""
        self._message_id: str | None = None

    def _publish_snapshot(self, text: str) -> None:
        if self._message_id is None:
            logger.info(
                "[FeishuSink] Sending new message to chat_id=%s, text_len=%d",
                self._chat_id,
                len(text),
            )
            self._message_id = self._api_client.send_text_message(self._chat_id, text)
            logger.info("[FeishuSink] Message sent, message_id=%s", self._message_id)
        else:
            logger.info(
                "[FeishuSink] Updating message_id=%s, text_len=%d", self._message_id, len(text)
            )
            self._api_client.update_text_message(self._message_id, text)
        self._last_sent_text = text

    def on_text(self, text: str) -> None:
        self._last_snapshot = text
        if len(text) - len(self._last_sent_text) < self._partial_emit_chars:
            return
        self._publish_snapshot(text)

    def on_complete(self, text: str) -> None:
        final_text = text or self._last_snapshot
        if not final_text:
            logger.info(
                "[FeishuSink] on_complete called with empty text, chat_id=%s", self._chat_id
            )
            return
        if final_text != self._last_sent_text:
            self._publish_snapshot(final_text)
        logger.info(
            "[FeishuSink] on_complete finished for chat_id=%s, message_id=%s",
            self._chat_id,
            self._message_id,
        )

    def on_error(self, error: str) -> None:
        logger.error("[FeishuSink] on_error called for chat_id=%s, error=%s", self._chat_id, error)
        self._publish_snapshot(f"Error: {error}")


@dataclass
class FeishuAdapterStatus:
    connected: bool = False
    last_error: str | None = None


class FeishuChannelAdapter:
    def __init__(
        self,
        *,
        boundary: ResponseStreamBoundary,
        config: FeishuConfig,
        api_client: FeishuApiClient | None = None,
        long_connection_factory: Callable[
            [FeishuConfig, Callable[[Any], None]], FeishuLongConnectionClient
        ]
        | None = None,
    ) -> None:
        self.boundary = boundary
        self.config = config
        self.api_client = api_client or FeishuApiClient(config)
        self.long_connection_factory = long_connection_factory or (
            lambda cfg, handler: FeishuLongConnectionClient(cfg, handler)
        )
        self.access_policy = StaticChannelAccessPolicy(
            allow_all=config.allow_all,
            allowed_user_ids=frozenset(config.allowed_user_ids),
            allowed_chat_ids=frozenset(config.allowed_chat_ids),
        )
        self.status = FeishuAdapterStatus()
        self._stop_event = threading.Event()
        self._chat_queues: dict[str, "queue.Queue[ChannelEnvelope | None]"] = {}
        self._chat_workers: dict[str, threading.Thread] = {}
        self._chat_lock = threading.Lock()

    def serve_forever(self) -> None:
        client = self.long_connection_factory(self.config, self._handle_sdk_event)
        self.status.connected = True
        self.status.last_error = None
        try:
            client.start()
        except Exception as exc:
            self.status.connected = False
            self.status.last_error = str(exc)
            raise
        finally:
            self.status.connected = False
            client.close()

    def _handle_sdk_event(self, event: Any) -> None:
        logger.info("[Feishu] Received SDK event, type=%s", type(event).__name__)
        envelope = self._event_to_envelope(event)
        if envelope is None:
            logger.warning(
                "[Feishu] Event converted to None envelope, event_type=%s", type(event).__name__
            )
            return
        if self._stop_event.is_set():
            logger.warning(
                "[Feishu] Stop event is set, dropping envelope for chat_id=%s",
                envelope.identity.chat_id,
            )
            return
        logger.info(
            "[Feishu] Envelope created: chat_id=%s, user_id=%s, text=%.100s",
            envelope.identity.chat_id,
            envelope.identity.user_id,
            envelope.text,
        )
        self._enqueue_envelope(envelope)

    def _enqueue_envelope(self, envelope: ChannelEnvelope) -> None:
        chat_id = envelope.identity.chat_id
        with self._chat_lock:
            work_queue = self._chat_queues.get(chat_id)
            worker = self._chat_workers.get(chat_id)
            if work_queue is None or worker is None or not worker.is_alive():
                logger.info(
                    "[Feishu] Creating new worker for chat_id=%s (queue=%s, worker=%s, alive=%s)",
                    chat_id,
                    work_queue is not None,
                    worker is not None,
                    worker.is_alive() if worker is not None else False,
                )
                work_queue = queue.Queue()
                worker = threading.Thread(
                    target=self._worker_loop,
                    args=(chat_id, work_queue),
                    name=f"xagent-feishu-{chat_id}",
                    daemon=True,
                )
                self._chat_queues[chat_id] = work_queue
                self._chat_workers[chat_id] = worker
                worker.start()
            else:
                logger.info(
                    "[Feishu] Reusing existing worker for chat_id=%s, queue_size=%d",
                    chat_id,
                    work_queue.qsize(),
                )
        work_queue.put_nowait(envelope)
        logger.info(
            "[Feishu] Envelope enqueued for chat_id=%s, queue_size=%d", chat_id, work_queue.qsize()
        )

    def _worker_loop(self, chat_id: str, work_queue: "queue.Queue[ChannelEnvelope | None]") -> None:
        logger.info("[Feishu] Worker loop started for chat_id=%s", chat_id)
        while True:
            envelope = work_queue.get()
            if envelope is None:
                logger.info("[Feishu] Worker received stop signal for chat_id=%s", chat_id)
                work_queue.task_done()
                return
            logger.info(
                "[Feishu] Worker processing envelope for chat_id=%s, text=%.100s",
                chat_id,
                envelope.text,
            )
            try:
                self._handle_envelope(envelope)
                logger.info("[Feishu] Worker finished processing envelope for chat_id=%s", chat_id)
            except Exception as exc:
                self.status.last_error = str(exc)
                logger.exception(
                    "[Feishu] Worker failed while processing chat %s: %s", chat_id, exc
                )
            finally:
                work_queue.task_done()

    def _handle_envelope(self, envelope: ChannelEnvelope | None) -> None:
        if envelope is None:
            return
        if not envelope.text:
            logger.warning(
                "[Feishu] Dropping envelope with empty text for chat_id=%s",
                envelope.identity.chat_id,
            )
            return
        if not is_group_message_allowed(
            envelope,
            mode=self.config.group_mode,
            bot_open_id=self.config.bot_open_id,
        ):
            logger.info(
                "[Feishu] Group message not allowed for chat_id=%s", envelope.identity.chat_id
            )
            return
        decision = self.access_policy.evaluate(envelope)
        if not decision.allowed:
            logger.warning(
                "[Feishu] Access denied for chat_id=%s, user_id=%s, reason=%s",
                envelope.identity.chat_id,
                envelope.identity.user_id,
                decision.reason,
            )
            self.api_client.send_text_message(envelope.identity.chat_id, self.config.deny_message)
            return

        logger.info(
            "[Feishu] Dispatching text for chat_id=%s via runtime boundary",
            envelope.identity.chat_id,
        )
        sink = FeishuTextStreamSink(
            self.api_client,
            envelope.identity.chat_id,
            partial_emit_chars=self.config.partial_emit_chars,
        )
        inbound = InboundMessage(
            content=envelope.text,
            source="channel.feishu",
            channel=envelope.channel,
            sender_id=envelope.identity.user_id,
            chat_id=envelope.identity.chat_id,
            reply_to=str(envelope.metadata.get("message_id") or "") or None,
            metadata={
                **dict(envelope.metadata),
                "chat_type": envelope.identity.chat_type,
            },
        )
        outbound_queue, unsubscribe = self.boundary.open_response_stream(inbound)
        try:
            self._drain_outbound_queue(
                outbound_queue,
                sink,
                correlation_id=inbound.correlation_id,
            )
        finally:
            unsubscribe()
        logger.info("[Feishu] Dispatch completed for chat_id=%s", envelope.identity.chat_id)

    def _drain_outbound_queue(
        self,
        outbound_queue: "queue.Queue[OutboundMessage]",
        sink: FeishuTextStreamSink,
        *,
        correlation_id: str,
    ) -> None:
        while True:
            outbound = outbound_queue.get(timeout=30)
            if outbound.correlation_id != correlation_id:
                continue
            if outbound.kind == "delta":
                sink.on_text(outbound.content)
                continue
            if outbound.kind == "completed":
                sink.on_complete(outbound.content)
                return
            if outbound.kind == "failed":
                sink.on_error(outbound.error or "Unknown runtime failure.")
                return

    def _event_to_envelope(self, event: Any) -> ChannelEnvelope | None:
        payload = getattr(event, "event", None)
        if (
            payload is None
            or getattr(payload, "message", None) is None
            or getattr(payload, "sender", None) is None
        ):
            return None

        message = payload.message
        sender = payload.sender
        sender_id = getattr(sender, "sender_id", None)
        content = getattr(message, "content", None) or ""
        if isinstance(content, str):
            try:
                import json

                content = json.loads(content)
            except Exception:
                content = {"text": content}
        text = str(content.get("text") or "").strip()
        mentions = []
        for item in getattr(message, "mentions", None) or []:
            mention_id = getattr(getattr(item, "id", None), "open_id", None) or getattr(
                getattr(item, "id", None), "user_id", None
            )
            if mention_id:
                mentions.append(str(mention_id))
        identity = ChannelIdentity(
            channel="feishu",
            user_id=str(
                getattr(sender_id, "open_id", None) or getattr(sender_id, "user_id", None) or ""
            ),
            chat_id=str(getattr(message, "chat_id", None) or ""),
            chat_type=str(getattr(message, "chat_type", None) or "p2p"),
        )
        return ChannelEnvelope(
            text=text,
            identity=identity,
            mentions=tuple(mentions),
            metadata={
                "event_id": getattr(getattr(event, "header", None), "event_id", None),
                "message_id": getattr(message, "message_id", None),
                "chat_type": getattr(message, "chat_type", None),
            },
        )

    def close(self) -> None:
        self._stop_event.set()
        with self._chat_lock:
            queues = list(self._chat_queues.values())
            workers = list(self._chat_workers.values())
        for work_queue in queues:
            work_queue.put_nowait(None)
        for worker in workers:
            worker.join(timeout=1.0)
