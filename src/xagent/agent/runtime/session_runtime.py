from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Optional

from xagent.agent.compaction import AutoCompactService
from xagent.agent.memory import RuntimeMemory, create_runtime_memory
from xagent.agent.session import SessionLoadMetadata, SessionStore, SessionSummary
from xagent.bus.events import Event, InMemoryMessageBus
from xagent.provider.types import Message, ToolResultPart, ToolUsePart, message_text


@dataclass
class TurnResult:
    message: Message
    duration_seconds: float


@dataclass
class SessionRestoreResult:
    session_id: str
    metadata: SessionLoadMetadata


class SessionRuntime:
    def __init__(
        self,
        *,
        session_id: str,
        bus: InMemoryMessageBus,
        turn_runner: Callable[..., Any],
        agent: Any = None,
        memory: Optional[RuntimeMemory] = None,
        auto_compact_service: Optional[AutoCompactService] = None,
        session_store: Optional[SessionStore] = None,
        source: str = "session_runtime",
    ) -> None:
        self.session_id = session_id
        self.bus = bus
        self.turn_runner = turn_runner
        self.agent = agent
        self.memory = memory or self._build_memory(agent=agent, session_store=session_store)
        self.working_memory = self.memory.working if self.memory is not None else None
        self.episodic_memory = self.memory.episodic if self.memory is not None else None
        self.semantic_memory = self.memory.semantic if self.memory is not None else None
        self.auto_compact_service = auto_compact_service or self._build_auto_compact_service()
        self.source = source
        self._turn_lock = asyncio.Lock()
        self._pending_turns: dict[str, asyncio.Future[TurnResult]] = {}
        self._active_tasks: set[asyncio.Task[Any]] = set()
        self._request_counter = 0
        self._turn_active = False
        self._sync_agent_session_id()
        self._unsubscribe_user_message = self.bus.subscribe("user.message.received", self._handle_user_message)
        self._unsubscribe_scheduled_job = self.bus.subscribe("job.scheduled.triggered", self._handle_scheduled_job)

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

    async def publish_user_message(
        self,
        text: str,
        *,
        source: str = "channel",
        requested_skill_name: Optional[str] = None,
        request_id: Optional[str] = None,
    ) -> TurnResult:
        if request_id is None:
            self._request_counter += 1
            request_id = f"{self.session_id}:{self._request_counter}"
        loop = asyncio.get_running_loop()
        future: asyncio.Future[TurnResult] = loop.create_future()
        self._pending_turns[request_id] = future
        if self.working_memory is not None:
            self.working_memory.set_requested_skill_name(requested_skill_name)
        try:
            await self.bus.publish(
                Event(
                    topic="user.message.received",
                    session_id=self.session_id,
                    payload={
                        "text": text,
                        "request_id": request_id,
                        "requested_skill_name": requested_skill_name,
                    },
                    source=source,
                )
            )
            return await future
        finally:
            if self.working_memory is not None:
                self.working_memory.set_requested_skill_name(None)
            self._pending_turns.pop(request_id, None)

    async def publish_scheduled_job(
        self,
        *,
        job_id: str,
        text: str,
        source: str = "scheduler",
        requested_skill_name: Optional[str] = None,
        run_at: Optional[float] = None,
    ) -> TurnResult:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[TurnResult] = loop.create_future()
        self._pending_turns[job_id] = future
        try:
            await self.bus.publish(
                Event(
                    topic="job.scheduled.triggered",
                    session_id=self.session_id,
                    payload={
                        "job_id": job_id,
                        "text": text,
                        "requested_skill_name": requested_skill_name,
                        "run_at": run_at,
                    },
                    source=source,
                )
            )
            return await future
        finally:
            self._pending_turns.pop(job_id, None)

    def close(self) -> None:
        self._unsubscribe_user_message()
        self._unsubscribe_scheduled_job()

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

    async def _handle_user_message(self, event: Event) -> None:
        if event.session_id != self.session_id:
            return

        prompt = str(event.payload.get("text", ""))
        request_id = event.payload.get("request_id")
        task = asyncio.create_task(self._run_turn(prompt, request_id))
        self._active_tasks.add(task)
        task.add_done_callback(self._active_tasks.discard)

    async def _handle_scheduled_job(self, event: Event) -> None:
        if event.session_id != self.session_id:
            return

        prompt = str(event.payload.get("text", ""))
        request_id = str(event.payload.get("job_id") or f"{self.session_id}:job:{self._request_counter + 1}")
        requested_skill_name = event.payload.get("requested_skill_name")

        async def _run_scheduled_turn() -> None:
            if self.working_memory is not None:
                self.working_memory.set_requested_skill_name(requested_skill_name)
            try:
                await self._run_turn(prompt, request_id)
            finally:
                if self.working_memory is not None:
                    self.working_memory.set_requested_skill_name(None)

        task = asyncio.create_task(_run_scheduled_turn())
        self._active_tasks.add(task)
        task.add_done_callback(self._active_tasks.discard)

    async def _run_turn(self, prompt: str, request_id: Optional[str]) -> None:
        async with self._turn_lock:
            self._turn_active = True
            await self.bus.publish(
                Event(
                    topic="session.turn.requested",
                    session_id=self.session_id,
                    payload={"text": prompt, "request_id": request_id},
                    source=self.source,
                )
            )

            event_queue: asyncio.Queue = asyncio.Queue()
            publisher = asyncio.create_task(self._publish_queued_events(event_queue))

            def _queue_event(topic: str, payload: dict[str, Any]) -> None:
                event_queue.put_nowait((topic, payload))

            def _on_assistant_delta(snapshot: Message) -> None:
                _queue_event(
                    "assistant.delta",
                    {
                        "message": snapshot,
                        "text": message_text(snapshot),
                        "request_id": request_id,
                    },
                )

            def _on_tool_use(tool_use: ToolUsePart) -> None:
                if self.working_memory is not None:
                    self.working_memory.start_tool(tool_use.name)
                _queue_event(
                    "tool.called",
                    {
                        "tool_use": tool_use,
                        "tool_name": tool_use.name,
                        "tool_input": tool_use.input,
                        "request_id": request_id,
                    },
                )

            def _on_tool_result(tool_use: ToolUsePart, result: ToolResultPart) -> None:
                if self.working_memory is not None:
                    self.working_memory.finish_tool(tool_use.name)
                _queue_event(
                    "tool.finished",
                    {
                        "tool_use": tool_use,
                        "tool_name": tool_use.name,
                        "result": result,
                        "content": result.content,
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
                    self.episodic_memory.save(self.session_id, self.working_memory.messages, compact=False)
                await event_queue.put(None)
                await publisher
                if self.working_memory is not None:
                    self.working_memory.clear_active_tools()
                self._turn_active = False
                turn_result = TurnResult(message=message, duration_seconds=duration_seconds)
                await self.bus.publish(
                    Event(
                        topic="session.turn.completed",
                        session_id=self.session_id,
                        payload={
                            "message": message,
                            "duration_seconds": duration_seconds,
                            "request_id": request_id,
                        },
                        source=self.source,
                    )
                )
                future = self._pending_turns.get(request_id)
                if future is not None and not future.done():
                    future.set_result(turn_result)
                if self.auto_compact_service is not None:
                    await self.auto_compact_service.request_if_needed()
            except Exception as exc:
                await event_queue.put(None)
                await publisher
                if self.working_memory is not None:
                    self.working_memory.clear_active_tools()
                self._turn_active = False
                await self.bus.publish(
                    Event(
                        topic="session.turn.failed",
                        session_id=self.session_id,
                        payload={
                            "error": str(exc),
                            "error_type": type(exc).__name__,
                            "request_id": request_id,
                        },
                        source=self.source,
                    )
                )
                future = self._pending_turns.get(request_id)
                if future is not None and not future.done():
                    future.set_exception(exc)

    async def _publish_queued_events(self, event_queue: asyncio.Queue) -> None:
        while True:
            item: Optional[tuple[str, dict[str, Any]]] = await event_queue.get()
            if item is None:
                return
            topic, payload = item
            await self.bus.publish(
                Event(
                    topic=topic,
                    session_id=self.session_id,
                    payload=payload,
                    source=self.source,
                )
            )

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
        if self.working_memory is None or self.episodic_memory is None:
            return None
        return AutoCompactService(
            bus=self.bus,
            working_memory=self.working_memory,
            episodic_memory=self.episodic_memory,
            session_id_getter=lambda: self.session_id,
            is_turn_active=lambda: self._turn_active,
        )
