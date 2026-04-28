from __future__ import annotations

from typing import Any, Callable, Optional

from xagent.agent.runtime.serialization import to_jsonable
from xagent.agent.traces import TraceRecorder
from xagent.bus.messages import OutboundMessage
from xagent.channel.base import BaseChannel


class TraceChannel(BaseChannel):
    """把 runtime outbound 事件落到 TraceRecorder 的旁路观察者 channel。"""

    name = "trace"
    observe_all = True

    def __init__(
        self,
        bus,
        *,
        recorder_getter: Callable[[], Optional[TraceRecorder]],
    ) -> None:
        super().__init__(bus)
        self._recorder_getter = recorder_getter

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def send(self, msg: OutboundMessage) -> None:
        recorder = self._recorder_getter()
        if recorder is None:
            return
        event_type = self._event_type(msg)
        recorder.emit(
            event_type,
            payload=self._payload(msg),
            tags={
                "channel": msg.channel,
                "kind": msg.kind,
                "source": msg.source,
                "session_id": msg.session_id,
            },
        )

    def _event_type(self, msg: OutboundMessage) -> str:
        if msg.kind == "completed":
            return "runtime_completed"
        if msg.kind == "failed":
            return "runtime_failed"
        event = msg.metadata.get("_event")
        if isinstance(event, str) and event:
            return f"runtime_{event}"
        return f"runtime_{msg.kind}"

    def _payload(self, msg: OutboundMessage) -> dict[str, Any]:
        return {
            "correlation_id": msg.correlation_id,
            "session_id": msg.session_id,
            "session_key": msg.session_key,
            "channel": msg.channel,
            "chat_id": msg.chat_id,
            "source": msg.source,
            "kind": msg.kind,
            "content": msg.content,
            "error": msg.error,
            "reply_to": msg.reply_to,
            "metadata": to_jsonable(msg.metadata),
        }
