from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import uuid4

from xagent.bus import InboundMessage, MessageBus, OutboundEvent, StreamKind, StreamState


@dataclass(frozen=True)
class InteractionContext:
    bus: MessageBus
    channel: str
    chat_id: str
    sender_id: str
    session_id: str


class InteractionBroker:
    """把工具里的追问转成当前 session 的下一条用户消息。"""

    def __init__(self) -> None:
        self._current: ContextVar[InteractionContext | None] = ContextVar(
            "xagent_interaction_context",
            default=None,
        )
        self._pending: dict[str, asyncio.Future[str]] = {}

    @contextmanager
    def activate(self, context: InteractionContext) -> Iterator[None]:
        token = self._current.set(context)
        try:
            yield
        finally:
            self._current.reset(token)

    async def ask_user(self, question: str) -> str:
        context = self._current_context()
        return await self.ask(context, question)

    def current_context(self) -> InteractionContext:
        return self._current_context()

    async def ask(self, context: InteractionContext, content: str) -> str:
        if context.session_id in self._pending:
            raise RuntimeError(f"Session {context.session_id!r} already has a pending interaction.")
        future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self._pending[context.session_id] = future
        await context.bus.publish_outbound(
            OutboundEvent(
                content=content,
                channel=context.channel,
                chat_id=context.chat_id,
                reply_to=context.sender_id,
                session_id=context.session_id,
                stream=StreamState(kind=StreamKind.END, stream_id=uuid4().hex),
            )
        )
        try:
            return await future
        finally:
            if self._pending.get(context.session_id) is future:
                del self._pending[context.session_id]

    def accept_reply(self, *, session_id: str, inbound: InboundMessage) -> bool:
        future = self._pending.get(session_id)
        if future is None or future.done():
            return False
        future.set_result(inbound.content)
        return True

    def _current_context(self) -> InteractionContext:
        context = self._current.get()
        if context is None:
            raise RuntimeError("No active interaction context is available.")
        return context


class ChatApprover:
    """通过当前聊天 session 询问用户是否允许高风险动作。"""

    def __init__(self, broker: InteractionBroker) -> None:
        self.broker = broker
        self.remembered: set[tuple[str, str]] = set()

    async def require(self, action: str, target: str, *, summary: str = "") -> bool:
        key = (action, target)
        if key in self.remembered:
            return True
        answer = await self.broker.ask_user(_permission_prompt(action, target, summary=summary))
        decision = _parse_permission_answer(answer)
        if decision == "session":
            self.remembered.add(key)
            return True
        return decision == "once"


def _permission_prompt(action: str, target: str, *, summary: str = "") -> str:
    lines = [
        "Permission required",
        f"Action: {action}",
        f"Target: {target}",
    ]
    if summary:
        lines.append(f"Summary: {summary[:500]}")
    lines.append(
        "Reply allow/yes/y/o/允许 to allow once, "
        "session/s/本会话 to remember, anything else to deny."
    )
    return "\n".join(lines)


def _parse_permission_answer(answer: str) -> str:
    normalized = answer.strip().lower()
    if normalized in {"session", "s", "本会话", "记住", "本会话允许"}:
        return "session"
    if normalized in {"allow", "yes", "y", "o", "once", "允许", "同意"}:
        return "once"
    return "deny"
