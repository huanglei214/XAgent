from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from xagent.agent.compaction import AutoCompactService
from xagent.agent.memory import RuntimeMemory, create_runtime_memory
from xagent.agent.session import SessionLoadMetadata, SessionStore, SessionSummary
from xagent.bus.messages import (
    InboundMessage,
    make_progress,
    make_terminal,
)
from xagent.bus.queue import MessageBus
from xagent.provider.types import Message, ToolResultPart, ToolUsePart, message_text

logger = logging.getLogger(__name__)


@dataclass
class TurnResult:
    message: Message
    duration_seconds: float


@dataclass
class SessionRestoreResult:
    session_id: str
    metadata: SessionLoadMetadata


@dataclass
class PostTurnContext:
    """PostTurnHook 的上下文。

    仅在 turn **成功完成**后传入；失败分支不触发 hook。
    """

    session_id: str
    request_id: str
    message: Message
    duration_seconds: float
    source: str
    metadata: dict[str, Any] = field(default_factory=dict)


#: PostTurnHook 类型：接收 ``PostTurnContext``，async，返回 ``None``。
#: 约定：hook 抛出的异常会被捕获并记录，不会影响其他 hook 或 turn 本身。
PostTurnHook = Callable[[PostTurnContext], Awaitable[None]]


class SessionRuntime:
    def __init__(
        self,
        *,
        session_id: str,
        turn_runner: Callable[..., Any],
        agent: Any = None,
        memory: Optional[RuntimeMemory] = None,
        auto_compact_service: Optional[AutoCompactService] = None,
        session_store: Optional[SessionStore] = None,
        source: str = "session_runtime",
        post_turn_hooks: Optional[list[PostTurnHook]] = None,
        message_bus: MessageBus,
    ) -> None:
        self.session_id = session_id
        self.message_bus = message_bus
        self.turn_runner = turn_runner
        self.agent = agent
        self.memory = memory or self._build_memory(agent=agent, session_store=session_store)
        self.working_memory = self.memory.working if self.memory is not None else None
        self.episodic_memory = self.memory.episodic if self.memory is not None else None
        self.semantic_memory = self.memory.semantic if self.memory is not None else None
        self.auto_compact_service = auto_compact_service or self._build_auto_compact_service()
        self.source = source
        # post-turn hooks：在 turn 成功完成后依序串行触发，异常被吞。
        self._post_turn_hooks: list[PostTurnHook] = list(post_turn_hooks or [])
        # 自动把 AutoCompactService.on_post_turn 注册到 hook 列表（若未重复注册）。
        # 原先 _run_turn 里显式调用 auto_compact_service.request_if_needed() 的职责
        # 已转移到该 hook；时序完全等价（均发生在 set_result 之后）。
        if self.auto_compact_service is not None:
            hook = self.auto_compact_service.on_post_turn
            if hook not in self._post_turn_hooks:
                self._post_turn_hooks.append(hook)
        self._turn_lock = asyncio.Lock()
        self._active_tasks: set[asyncio.Task[Any]] = set()
        self._turn_active = False
        self._sync_agent_session_id()

    @property
    def messages(self) -> list[Message]:
        if self.working_memory is None:
            return []
        return self.working_memory.messages

    def list_sessions(self, limit: int = 20) -> list[SessionSummary]:
        if self.episodic_memory is None:
            return []
        return self.episodic_memory.list_sessions(limit=limit)

    def session_exists(self, session_id: str) -> bool:
        if self.episodic_memory is None:
            return False
        return self.episodic_memory.session_exists(session_id)

    def save_session(self):
        if self.episodic_memory is None or self.working_memory is None:
            return None
        return self.episodic_memory.save(self.session_id, self.working_memory.messages)

    def start_new_session(self, *, save_current: bool = True) -> str:
        if self.episodic_memory is None or self.working_memory is None:
            raise RuntimeError("Episodic/working memory is not configured.")
        if save_current:
            self.save_session()
        self.working_memory.clear_messages()
        self.working_memory.clear_turn_state()
        self._set_session_id(self.episodic_memory.new_session_id())
        return self.session_id

    def restore_session(self, session_id: str) -> Optional[SessionRestoreResult]:
        if self.episodic_memory is None or self.working_memory is None:
            raise RuntimeError("Session runtime does not support persistence.")
        restored = self.episodic_memory.restore(session_id)
        if restored is None:
            return None
        loaded_session_id, restored_messages, metadata = restored
        self.working_memory.replace_messages(restored_messages)
        self.working_memory.clear_turn_state()
        self._set_session_id(loaded_session_id)
        return SessionRestoreResult(session_id=loaded_session_id, metadata=metadata)

    def clear_session(self) -> None:
        if self.working_memory is not None:
            self.working_memory.clear_messages()
            self.working_memory.clear_turn_state()
        if self.episodic_memory is not None:
            self.episodic_memory.clear(session_id=self.session_id)

    def abort(self) -> None:
        if self.agent is not None and hasattr(self.agent, "abort"):
            self.agent.abort()

    def close(self) -> None:
        return None

    async def wait_for_background_tasks(self) -> None:
        """Wait for background tasks spawned by this runtime.

        This must not busy-loop: in some race cases the task set can change between
        the truthy check and materializing the list, which can starve the event loop
        and prevent timeouts/cancellation from being processed.
        """
        while True:
            tasks = [task for task in self._active_tasks if not task.done()]
            if not tasks:
                break
            # Preserve the first exception semantics while ensuring the loop yields.
            await asyncio.gather(*tasks)
        if self.auto_compact_service is not None:
            await self.auto_compact_service.wait_for_all()

    def register_post_turn_hook(self, hook: PostTurnHook) -> None:
        """注册一个 PostTurnHook。

        hook 在 turn 成功完成后按注册顺序串行触发；hook 抛出的异常会被
        捕获并记录，不影响其他 hook 或 turn 主流程。
        """
        self._post_turn_hooks.append(hook)

    async def _run_post_turn_hooks(self, ctx: PostTurnContext) -> None:
        """按注册顺序串行执行所有 post-turn hooks，异常被吞并记录。"""
        for hook in self._post_turn_hooks:
            try:
                await hook(ctx)
            except Exception:  # noqa: BLE001 - 隔离 hook 异常，避免影响主流程
                logger.exception(
                    "PostTurnHook 执行失败；session_id=%s request_id=%s",
                    ctx.session_id,
                    ctx.request_id,
                )

    async def handle(self, inbound: InboundMessage) -> None:
        """由 ``SessionRouter`` 调用；同一 session 内串行执行一个 turn。

        本方法**不返回业务结果**——调用方通过
        ``ChannelManager.send_and_wait(inbound)`` 拿匹配 correlation_id 的
        ``OutboundMessage``（kind=``completed`` / ``failed``），再从
        ``metadata["message"]`` / ``metadata["duration_seconds"]`` 取回
        业务层数据。
        """
        if self.message_bus is None:
            raise RuntimeError(
                "SessionRuntime.handle 需要在构造时传入 message_bus"
            )
        async with self._turn_lock:
            await self._handle_turn(inbound)

    async def _handle_turn(self, inbound: InboundMessage) -> None:
        """处理单次 turn；发 progress + terminal 到 ``message_bus.outbound``。"""
        request_id = inbound.correlation_id
        prompt = inbound.content
        self._turn_active = True

        # 注入 requested_skill（如有），让 turn_runner 内部看到与外层请求一致的技能上下文。
        if inbound.requested_skill_name and self.agent is not None:
            try:
                self.agent.set_requested_skill_name(inbound.requested_skill_name)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "set_requested_skill_name failed; skill=%s",
                    inbound.requested_skill_name,
                )

        # 用内部 event queue + 单 publisher 协程保证 progress 顺序（与旧
        # _run_turn 的 publish_queue 模式等价）。
        event_queue: asyncio.Queue[Optional[tuple[str, str, dict[str, Any]]]] = asyncio.Queue()

        def _queue_progress(event: str, content: str, extra: dict[str, Any]) -> None:
            event_queue.put_nowait((event, content, extra))

        async def _publisher() -> None:
            while True:
                item = await event_queue.get()
                if item is None:
                    return
                event, content, extra = item
                await self._emit_progress(
                    inbound, event=event, content=content, extra_metadata=extra
                )

        publisher_task = asyncio.create_task(_publisher())

        # 首条 progress：turn_start
        _queue_progress("turn_start", "", {"request_id": request_id})

        def _on_assistant_delta(snapshot: Message) -> None:
            if self.working_memory is not None and hasattr(
                self.working_memory, "observe_assistant_message"
            ):
                self.working_memory.observe_assistant_message(snapshot)
            _queue_progress(
                "text_delta",
                message_text(snapshot),
                {"snapshot": snapshot, "request_id": request_id},
            )

        def _on_tool_use(tool_use: ToolUsePart) -> None:
            if self.working_memory is not None:
                self.working_memory.start_tool(tool_use.name)
            _queue_progress(
                "tool_use",
                "",
                {
                    "tool_use": tool_use,
                    "tool_name": tool_use.name,
                    "request_id": request_id,
                },
            )

        def _on_tool_result(tool_use: ToolUsePart, result: ToolResultPart) -> None:
            if self.working_memory is not None:
                self.working_memory.finish_tool(tool_use.name)
            _queue_progress(
                "tool_result",
                "",
                {
                    "tool_use": tool_use,
                    "tool_name": tool_use.name,
                    "result": result,
                    "is_error": result.is_error,
                    "request_id": request_id,
                },
            )

        try:
            message, duration_seconds = await self.turn_runner(
                prompt,
                on_assistant_delta=_on_assistant_delta,
                on_tool_use=_on_tool_use,
                on_tool_result=_on_tool_result,
            )
            if self.episodic_memory is not None and self.working_memory is not None:
                self.episodic_memory.save(
                    self.session_id, self.working_memory.messages, compact=False
                )
            # 等所有 progress 发完再发 terminal，保证顺序
            await event_queue.put(None)
            await publisher_task
            if self.working_memory is not None:
                self.working_memory.clear_active_tools()
            self._turn_active = False
            await self._emit_terminal(
                inbound,
                message=message,
                duration_seconds=duration_seconds,
                request_id=request_id,
            )
            ctx = PostTurnContext(
                session_id=self.session_id,
                request_id=request_id,
                message=message,
                duration_seconds=duration_seconds,
                source=self.source,
            )
            await self._run_post_turn_hooks(ctx)
        except Exception as exc:  # noqa: BLE001 - turn 失败发 terminal 兜底
            # 先让 publisher 退出，避免泄漏 task
            await event_queue.put(None)
            try:
                await publisher_task
            except Exception:  # noqa: BLE001
                pass
            if self.working_memory is not None:
                self.working_memory.clear_active_tools()
            self._turn_active = False
            await self._emit_terminal(
                inbound,
                message=None,
                duration_seconds=0.0,
                error=f"{type(exc).__name__}: {exc!s}",
                request_id=request_id,
            )

    async def _emit_progress(
        self,
        inbound: InboundMessage,
        *,
        event: str,
        content: str = "",
        extra_metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """封装 ``publish_outbound(make_progress(...))``。"""
        assert self.message_bus is not None
        msg = make_progress(
            correlation_id=inbound.correlation_id,
            session_id=self.session_id,
            session_key=inbound.session_key,
            channel=inbound.channel,
            chat_id=inbound.chat_id,
            source=self.source,
            event=event,  # type: ignore[arg-type]
            content=content,
            extra_metadata=extra_metadata,
        )
        await self.message_bus.publish_outbound(msg)

    async def _emit_terminal(
        self,
        inbound: InboundMessage,
        *,
        message: Optional[Message],
        duration_seconds: float,
        request_id: str,
        error: Optional[str] = None,
    ) -> None:
        """封装 ``publish_outbound(make_terminal(...))``。

        成功时把 ``message`` / ``duration_seconds`` / ``request_id`` 写入
        ``metadata``；失败时只填 ``error`` + ``request_id``。
        """
        assert self.message_bus is not None
        if error is not None:
            msg = make_terminal(
                correlation_id=inbound.correlation_id,
                session_id=self.session_id,
                session_key=inbound.session_key,
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                source=self.source,
                content="",
                kind="failed",
                error=error,
                extra_metadata={"request_id": request_id},
            )
        else:
            msg = make_terminal(
                correlation_id=inbound.correlation_id,
                session_id=self.session_id,
                session_key=inbound.session_key,
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                source=self.source,
                content=message_text(message) if message is not None else "",
                kind="completed",
                extra_metadata={
                    "request_id": request_id,
                    "message": message,
                    "duration_seconds": duration_seconds,
                },
            )
        await self.message_bus.publish_outbound(msg)


    def _set_session_id(self, session_id: str) -> None:
        self.session_id = session_id
        self._sync_agent_session_id()

    def _sync_agent_session_id(self) -> None:
        if self.agent is not None:
            setattr(self.agent, "trace_session_id", self.session_id)
        if self.working_memory is not None:
            self.working_memory.attach_agent(self.agent)

    def _build_memory(
        self,
        *,
        agent: Any = None,
        session_store: Optional[SessionStore] = None,
    ) -> Optional[RuntimeMemory]:
        if agent is None and session_store is None:
            return None
        cwd = getattr(agent, "cwd", None)
        if cwd is None and session_store is not None:
            cwd = session_store.store.cwd if hasattr(session_store, "store") else session_store.cwd
        if cwd is None:
            return None
        return create_runtime_memory(cwd, agent=agent, session_store=session_store)

    def _build_auto_compact_service(self) -> Optional[AutoCompactService]:
        if (
            self.working_memory is None
            or self.episodic_memory is None
            or self.message_bus is None
        ):
            return None
        return AutoCompactService(
            message_bus=self.message_bus,
            working_memory=self.working_memory,
            episodic_memory=self.episodic_memory,
            session_id_getter=lambda: self.session_id,
            is_turn_active=lambda: self._turn_active,
        )
