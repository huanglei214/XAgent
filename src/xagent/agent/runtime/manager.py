from __future__ import annotations

import asyncio
import concurrent.futures
import queue
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional
from uuid import uuid4

from xagent.agent.memory import create_runtime_memory
from xagent.foundation.events import Event
from xagent.foundation.messages import Message, message_text
from xagent.scheduler.cron import JobScheduler, PersistentJobScheduler, ScheduledJobRecord


@dataclass
class EventStreamHandle:
    stream_id: str
    session_id: str
    events: "queue.Queue[dict[str, Any]]"


class SessionRuntimeManager:
    def __init__(
        self,
        *,
        cwd: str,
        agent_factory: Callable[[], Any],
        runtime_factory: Callable[..., tuple[Any, Any]],
    ) -> None:
        self.cwd = str(Path(cwd).resolve())
        self.agent_factory = agent_factory
        self.runtime_factory = runtime_factory
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._runtimes: dict[str, Any] = {}
        self._schedulers: dict[str, JobScheduler] = {}
        self._job_sessions: dict[str, str] = {}
        self._streams: dict[str, dict[str, Any]] = {}
        self._persistent_scheduler: Optional[PersistentJobScheduler] = None

    def create_session(self) -> str:
        return self._call(self._create_session())

    def list_sessions(self) -> list[dict[str, Any]]:
        return self._call(self._list_sessions())

    def get_session_status(self, session_id: str) -> Optional[dict[str, Any]]:
        return self._call(self._get_session_status(session_id))

    def get_session_messages(self, session_id: str) -> Optional[list[dict[str, Any]]]:
        return self._call(self._get_session_messages(session_id))

    def send_message(
        self,
        session_id: str,
        text: str,
        *,
        requested_skill_name: Optional[str] = None,
        source: str = "runtime.manager",
    ) -> dict[str, Any]:
        return self._call(
            self._send_message(
                session_id,
                text,
                requested_skill_name=requested_skill_name,
                source=source,
            )
        )

    def schedule_message(
        self,
        session_id: str,
        text: str,
        *,
        delay_seconds: float = 0.0,
        requested_skill_name: Optional[str] = None,
        source: str = "runtime.manager.schedule",
    ) -> dict[str, Any]:
        return self._call(
            self._schedule_message(
                session_id,
                text,
                delay_seconds=delay_seconds,
                requested_skill_name=requested_skill_name,
                source=source,
            )
        )

    def wait_for_job(self, job_id: str) -> dict[str, Any]:
        return self._call(self._wait_for_job(job_id))

    def add_cron_job(
        self,
        session_id: str,
        text: str,
        *,
        cron_expression: str,
        requested_skill_name: Optional[str] = None,
        retry_enabled: bool = False,
        retry_delay_seconds: float = 60.0,
        retry_backoff_multiplier: float = 1.0,
        max_retries: int = 0,
        source: str = "runtime.manager.cron",
    ) -> dict[str, Any]:
        return self._call(
            self._add_cron_job(
                session_id,
                text,
                cron_expression=cron_expression,
                requested_skill_name=requested_skill_name,
                retry_enabled=retry_enabled,
                retry_delay_seconds=retry_delay_seconds,
                retry_backoff_multiplier=retry_backoff_multiplier,
                max_retries=max_retries,
                source=source,
            )
        )

    def add_once_job(
        self,
        session_id: str,
        text: str,
        *,
        delay_seconds: float = 0.0,
        run_at: Optional[float] = None,
        requested_skill_name: Optional[str] = None,
        retry_enabled: bool = False,
        retry_delay_seconds: float = 60.0,
        retry_backoff_multiplier: float = 1.0,
        max_retries: int = 0,
        source: str = "runtime.manager.schedule",
    ) -> dict[str, Any]:
        return self._call(
            self._add_once_job(
                session_id,
                text,
                delay_seconds=delay_seconds,
                run_at=run_at,
                requested_skill_name=requested_skill_name,
                retry_enabled=retry_enabled,
                retry_delay_seconds=retry_delay_seconds,
                retry_backoff_multiplier=retry_backoff_multiplier,
                max_retries=max_retries,
                source=source,
            )
        )

    def list_jobs(self) -> list[dict[str, Any]]:
        return self._call(self._list_jobs())

    def remove_job(self, job_id: str) -> bool:
        return self._call(self._remove_job(job_id))

    def pause_job(self, job_id: str) -> dict[str, Any]:
        return self._call(self._pause_job(job_id))

    def resume_job(self, job_id: str) -> dict[str, Any]:
        return self._call(self._resume_job(job_id))

    def update_job(
        self,
        job_id: str,
        *,
        text: Optional[str] = None,
        cron_expression: Optional[str] = None,
        delay_seconds: Optional[float] = None,
        run_at: Optional[float] = None,
        requested_skill_name: Optional[str] = None,
        retry_enabled: Optional[bool] = None,
        retry_delay_seconds: Optional[float] = None,
        retry_backoff_multiplier: Optional[float] = None,
        max_retries: Optional[int] = None,
        enabled: Optional[bool] = None,
    ) -> dict[str, Any]:
        return self._call(
            self._update_job(
                job_id,
                text=text,
                cron_expression=cron_expression,
                delay_seconds=delay_seconds,
                run_at=run_at,
                requested_skill_name=requested_skill_name,
                retry_enabled=retry_enabled,
                retry_delay_seconds=retry_delay_seconds,
                retry_backoff_multiplier=retry_backoff_multiplier,
                max_retries=max_retries,
                enabled=enabled,
            )
        )

    def list_job_history(self, *, job_id: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
        return self._call(self._list_job_history(job_id=job_id, limit=limit))

    def start_persistent_scheduler(self, *, poll_interval_seconds: float = 1.0) -> None:
        self._call(self._start_persistent_scheduler(poll_interval_seconds=poll_interval_seconds))

    def open_event_stream(self, session_id: str, *, topics: Optional[list[str]] = None) -> EventStreamHandle:
        return self._call(self._open_event_stream(session_id, topics=topics))

    def close_event_stream(self, stream_id: str) -> None:
        self._call(self._close_event_stream(stream_id))

    def submit_message(
        self,
        session_id: str,
        text: str,
        *,
        requested_skill_name: Optional[str] = None,
        source: str = "runtime.manager",
    ):
        self._start_loop()
        return asyncio.run_coroutine_threadsafe(
            self._send_message(
                session_id,
                text,
                requested_skill_name=requested_skill_name,
                source=source,
            ),
            self._loop,
        )

    def close(self) -> None:
        if self._loop is None:
            return

        async def _shutdown() -> None:
            for scheduler in list(self._schedulers.values()):
                await scheduler.wait_for_all()
            self._schedulers.clear()
            self._job_sessions.clear()
            if self._persistent_scheduler is not None:
                await self._persistent_scheduler.stop()
                self._persistent_scheduler = None
            for stream in list(self._streams.values()):
                unsubscribe = stream.get("unsubscribe")
                if callable(unsubscribe):
                    unsubscribe()
            self._streams.clear()
            for runtime in list(self._runtimes.values()):
                await self._wait_for_runtime(runtime)
                close = getattr(runtime, "close", None)
                if callable(close):
                    close()
            self._runtimes.clear()

        try:
            self._call(_shutdown())
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread is not None:
                self._thread.join(timeout=2)
            self._loop = None
            self._thread = None
            self._ready.clear()

    def _start_loop(self) -> None:
        if self._loop is not None:
            return

        def _runner() -> None:
            loop = asyncio.new_event_loop()
            self._loop = loop
            asyncio.set_event_loop(loop)
            self._ready.set()
            loop.run_forever()
            loop.close()

        self._thread = threading.Thread(target=_runner, name="xagent-runtime-manager", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)
        if self._loop is None:
            raise RuntimeError("Runtime manager loop failed to start.")

    def _call(self, coro):
        self._start_loop()
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=30)
        except concurrent.futures.TimeoutError as exc:
            raise RuntimeError("Runtime manager operation timed out.") from exc

    async def _create_session(self) -> str:
        runtime = self._new_runtime(session_id=None)
        self._runtimes[runtime.session_id] = runtime
        return runtime.session_id

    async def _list_sessions(self) -> list[dict[str, Any]]:
        memory = create_runtime_memory(self.cwd)
        sessions = [
            {
                "session_id": summary.session_id,
                "saved_at": summary.saved_at,
                "created_at": summary.created_at,
                "message_count": summary.message_count,
                "recent_message_count": summary.recent_message_count,
                "checkpointed_message_count": summary.checkpointed_message_count,
                "preview": summary.preview,
                "branch": summary.branch,
                "is_latest": summary.is_latest,
                "loaded": summary.session_id in self._runtimes,
            }
            for summary in memory.episodic.list_sessions()
        ]
        seen = {item["session_id"] for item in sessions}
        for runtime in self._runtimes.values():
            if runtime.session_id in seen:
                continue
            sessions.append(
                {
                    "session_id": runtime.session_id,
                    "saved_at": 0.0,
                    "created_at": 0.0,
                    "message_count": len(runtime.messages),
                    "recent_message_count": len(runtime.messages),
                    "checkpointed_message_count": 0,
                    "preview": message_text(runtime.messages[-1]) if runtime.messages else "(empty session)",
                    "branch": "-",
                    "is_latest": False,
                    "loaded": True,
                }
            )
        sessions.sort(key=lambda item: (item["saved_at"], item["session_id"]), reverse=True)
        return sessions

    async def _get_session_status(self, session_id: str) -> Optional[dict[str, Any]]:
        runtime = await self._ensure_runtime(session_id, restore=True)
        if runtime is None:
            return None
        return self._build_status(runtime)

    async def _get_session_messages(self, session_id: str) -> Optional[list[dict[str, Any]]]:
        runtime = await self._ensure_runtime(session_id, restore=True)
        if runtime is None:
            return None
        return [self._serialize_message(message) for message in runtime.messages]

    async def _send_message(
        self,
        session_id: str,
        text: str,
        *,
        requested_skill_name: Optional[str] = None,
        source: str = "runtime.manager",
    ) -> dict[str, Any]:
        runtime = await self._ensure_runtime(session_id, restore=True)
        if runtime is None:
            raise KeyError(session_id)
        turn_result = await runtime.publish_user_message(
            text,
            source=source,
            requested_skill_name=requested_skill_name,
        )
        await self._wait_for_runtime(runtime)
        return self._build_turn_response(runtime, turn_result.message, turn_result.duration_seconds)

    async def _schedule_message(
        self,
        session_id: str,
        text: str,
        *,
        delay_seconds: float = 0.0,
        requested_skill_name: Optional[str] = None,
        source: str = "runtime.manager.schedule",
    ) -> dict[str, Any]:
        runtime = await self._ensure_runtime(session_id, restore=True)
        if runtime is None:
            raise KeyError(session_id)
        scheduler = self._schedulers.get(runtime.session_id)
        if scheduler is None:
            scheduler = JobScheduler(bus=runtime.bus)
            self._schedulers[runtime.session_id] = scheduler
        job = await scheduler.schedule_once(
            session_id=runtime.session_id,
            text=text,
            delay_seconds=delay_seconds,
            requested_skill_name=requested_skill_name,
            source=source,
        )
        self._job_sessions[job.job_id] = runtime.session_id
        return {
            "job_id": job.job_id,
            "session_id": runtime.session_id,
            "text": text,
            "run_at": job.run_at,
        }

    async def _add_once_job(
        self,
        session_id: str,
        text: str,
        *,
        delay_seconds: float = 0.0,
        run_at: Optional[float] = None,
        requested_skill_name: Optional[str] = None,
        retry_enabled: bool = False,
        retry_delay_seconds: float = 60.0,
        retry_backoff_multiplier: float = 1.0,
        max_retries: int = 0,
        source: str = "runtime.manager.schedule",
    ) -> dict[str, Any]:
        runtime = await self._ensure_runtime(session_id, restore=True)
        if runtime is None:
            raise KeyError(session_id)
        scheduler = await self._ensure_persistent_scheduler()
        job = scheduler.add_once(
            session_id=runtime.session_id,
            text=text,
            delay_seconds=delay_seconds,
            run_at=run_at,
            requested_skill_name=requested_skill_name,
            retry_enabled=retry_enabled,
            retry_delay_seconds=retry_delay_seconds,
            retry_backoff_multiplier=retry_backoff_multiplier,
            max_retries=max_retries,
            source=source,
        )
        return self._serialize_job(job)

    async def _add_cron_job(
        self,
        session_id: str,
        text: str,
        *,
        cron_expression: str,
        requested_skill_name: Optional[str] = None,
        retry_enabled: bool = False,
        retry_delay_seconds: float = 60.0,
        retry_backoff_multiplier: float = 1.0,
        max_retries: int = 0,
        source: str = "runtime.manager.cron",
    ) -> dict[str, Any]:
        runtime = await self._ensure_runtime(session_id, restore=True)
        if runtime is None:
            raise KeyError(session_id)
        scheduler = await self._ensure_persistent_scheduler()
        job = scheduler.add_cron(
            session_id=runtime.session_id,
            text=text,
            cron_expression=cron_expression,
            requested_skill_name=requested_skill_name,
            retry_enabled=retry_enabled,
            retry_delay_seconds=retry_delay_seconds,
            retry_backoff_multiplier=retry_backoff_multiplier,
            max_retries=max_retries,
            source=source,
        )
        return self._serialize_job(job)

    async def _list_jobs(self) -> list[dict[str, Any]]:
        scheduler = await self._ensure_persistent_scheduler()
        return [self._serialize_job(job) for job in scheduler.list_jobs()]

    async def _remove_job(self, job_id: str) -> bool:
        scheduler = await self._ensure_persistent_scheduler()
        return scheduler.remove_job(job_id)

    async def _pause_job(self, job_id: str) -> dict[str, Any]:
        scheduler = await self._ensure_persistent_scheduler()
        job = scheduler.store.pause_job(job_id)
        if job is None:
            raise KeyError(job_id)
        return self._serialize_job(job)

    async def _resume_job(self, job_id: str) -> dict[str, Any]:
        scheduler = await self._ensure_persistent_scheduler()
        job = scheduler.store.resume_job(job_id)
        if job is None:
            raise KeyError(job_id)
        return self._serialize_job(job)

    async def _update_job(
        self,
        job_id: str,
        *,
        text: Optional[str] = None,
        cron_expression: Optional[str] = None,
        delay_seconds: Optional[float] = None,
        run_at: Optional[float] = None,
        requested_skill_name: Optional[str] = None,
        retry_enabled: Optional[bool] = None,
        retry_delay_seconds: Optional[float] = None,
        retry_backoff_multiplier: Optional[float] = None,
        max_retries: Optional[int] = None,
        enabled: Optional[bool] = None,
    ) -> dict[str, Any]:
        scheduler = await self._ensure_persistent_scheduler()
        now = scheduler.clock()
        resolved_run_at = run_at
        if delay_seconds is not None:
            resolved_run_at = now + max(0.0, delay_seconds)
        job = scheduler.store.update_job(
            job_id,
            text=text,
            cron_expression=cron_expression,
            run_at=resolved_run_at,
            requested_skill_name=requested_skill_name,
            enabled=enabled,
            retry_enabled=retry_enabled,
            retry_delay_seconds=retry_delay_seconds,
            retry_backoff_multiplier=retry_backoff_multiplier,
            max_retries=max_retries,
            now=now,
        )
        if job is None:
            raise KeyError(job_id)
        return self._serialize_job(job)

    async def _list_job_history(self, *, job_id: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
        scheduler = await self._ensure_persistent_scheduler()
        return [self._serialize_job_history(entry) for entry in scheduler.store.list_history(job_id=job_id, limit=limit)]

    async def _wait_for_job(self, job_id: str) -> dict[str, Any]:
        scheduler = self._persistent_scheduler
        if scheduler is not None and scheduler.get_job(job_id) is not None:
            result = await scheduler.wait_for_job(job_id)
            return result

        session_id = self._job_sessions.get(job_id)
        if session_id is None:
            raise KeyError(job_id)
        runtime = await self._ensure_runtime(session_id, restore=True)
        if runtime is None:
            raise KeyError(session_id)
        scheduler_task = self._schedulers.get(runtime.session_id)
        if scheduler_task is not None:
            await scheduler_task.wait_for_job(job_id)
        await self._wait_for_runtime(runtime)
        if not runtime.messages:
            raise RuntimeError(f"Scheduled job '{job_id}' completed without producing messages.")
        final_message = runtime.messages[-1]
        return self._build_turn_response(runtime, final_message, None, job_id=job_id)

    async def _start_persistent_scheduler(self, *, poll_interval_seconds: float = 1.0) -> None:
        scheduler = await self._ensure_persistent_scheduler(poll_interval_seconds=poll_interval_seconds)
        await scheduler.start()

    async def _open_event_stream(
        self,
        session_id: str,
        *,
        topics: Optional[list[str]] = None,
    ) -> EventStreamHandle:
        runtime = await self._ensure_runtime(session_id, restore=True)
        if runtime is None:
            raise KeyError(session_id)

        stream_id = uuid4().hex
        event_queue: "queue.Queue[dict[str, Any]]" = queue.Queue()
        topic_filter = set(topics or [])

        async def _handler(event: Event) -> None:
            if event.session_id != runtime.session_id:
                return
            if topic_filter and event.topic not in topic_filter:
                return
            event_queue.put_nowait(self._serialize_event(event))

        unsubscribe = runtime.bus.subscribe("*", _handler)
        self._streams[stream_id] = {
            "session_id": runtime.session_id,
            "queue": event_queue,
            "unsubscribe": unsubscribe,
        }
        return EventStreamHandle(stream_id=stream_id, session_id=runtime.session_id, events=event_queue)

    async def _close_event_stream(self, stream_id: str) -> None:
        stream = self._streams.pop(stream_id, None)
        if stream is None:
            return
        unsubscribe = stream.get("unsubscribe")
        if callable(unsubscribe):
            unsubscribe()

    async def _ensure_runtime(self, session_id: str, *, restore: bool) -> Any | None:
        runtime = self._runtimes.get(session_id)
        if runtime is not None:
            return runtime

        runtime = self._new_runtime(session_id=session_id)
        restored = runtime.restore_session(session_id) if restore else None
        if restore and restored is None and not runtime.session_exists(session_id):
            runtime.close()
            return None
        self._runtimes[runtime.session_id] = runtime
        if runtime.session_id != session_id:
            self._runtimes.pop(session_id, None)
        return runtime

    async def _ensure_persistent_scheduler(self, *, poll_interval_seconds: float = 1.0) -> PersistentJobScheduler:
        if self._persistent_scheduler is not None:
            return self._persistent_scheduler
        scheduler = PersistentJobScheduler(
            cwd=self.cwd,
            dispatch=self._dispatch_persistent_job,
            poll_interval_seconds=poll_interval_seconds,
        )
        self._persistent_scheduler = scheduler
        return scheduler

    def _new_runtime(self, *, session_id: Optional[str]) -> Any:
        agent = self.agent_factory()
        _, runtime = self.runtime_factory(agent, session_id=session_id, cwd=self.cwd)
        return runtime

    async def _dispatch_persistent_job(self, job: ScheduledJobRecord) -> dict[str, Any]:
        runtime = await self._ensure_runtime(job.session_id, restore=True)
        if runtime is None:
            raise KeyError(job.session_id)
        turn_result = await runtime.publish_scheduled_job(
            job_id=job.job_id,
            text=job.text,
            requested_skill_name=job.requested_skill_name,
            source=job.source,
            run_at=job.next_run_at,
        )
        await self._wait_for_runtime(runtime)
        return self._build_turn_response(runtime, turn_result.message, turn_result.duration_seconds, job_id=job.job_id)

    async def _wait_for_runtime(self, runtime: Any) -> None:
        wait_for_background_tasks = getattr(runtime, "wait_for_background_tasks", None)
        if callable(wait_for_background_tasks):
            maybe_wait = wait_for_background_tasks()
            if asyncio.iscoroutine(maybe_wait) or isinstance(maybe_wait, asyncio.Future):
                await maybe_wait

    def _build_turn_response(
        self,
        runtime: Any,
        final_message: Message,
        duration_seconds: Optional[float],
        *,
        job_id: Optional[str] = None,
    ) -> dict[str, Any]:
        payload = {
            "session_id": runtime.session_id,
            "message": self._serialize_message(final_message),
            "text": message_text(final_message),
            "status": self._build_status(runtime),
        }
        if duration_seconds is not None:
            payload["duration_seconds"] = duration_seconds
        if job_id is not None:
            payload["job_id"] = job_id
        return payload

    def _build_status(self, runtime: Any) -> dict[str, Any]:
        working_memory = getattr(runtime, "working_memory", None)
        return {
            "session_id": runtime.session_id,
            "message_count": len(runtime.messages),
            "active_tools": list(getattr(working_memory, "active_tools", [])) if working_memory is not None else [],
            "requested_skill_name": getattr(working_memory, "requested_skill_name", None)
            if working_memory is not None
            else None,
            "current_plan": getattr(working_memory, "current_plan", None) if working_memory is not None else None,
            "scratchpad_keys": sorted(getattr(working_memory, "scratchpad", {}).keys())
            if working_memory is not None
            else [],
        }

    def _serialize_message(self, message: Message) -> dict[str, Any]:
        payload = message.model_dump(mode="json")
        payload["text"] = message_text(message)
        return payload

    def _serialize_job(self, job: ScheduledJobRecord) -> dict[str, Any]:
        return {
            "job_id": job.job_id,
            "session_id": job.session_id,
            "text": job.text,
            "schedule_type": job.schedule_type,
            "cron_expression": job.cron_expression,
            "run_at": job.run_at,
            "next_run_at": job.next_run_at,
            "requested_skill_name": job.requested_skill_name,
            "enabled": job.enabled,
            "last_run_at": job.last_run_at,
            "last_error": job.last_error,
            "last_result_text": job.last_result_text,
            "retry_enabled": job.retry_enabled,
            "retry_delay_seconds": job.retry_delay_seconds,
            "retry_backoff_multiplier": job.retry_backoff_multiplier,
            "max_retries": job.max_retries,
            "retry_count": job.retry_count,
            "created_at": job.created_at,
            "updated_at": job.updated_at,
        }

    def _serialize_job_history(self, entry) -> dict[str, Any]:
        return {
            "history_id": entry.history_id,
            "job_id": entry.job_id,
            "session_id": entry.session_id,
            "status": entry.status,
            "text": entry.text,
            "source": entry.source,
            "recorded_at": entry.recorded_at,
            "result_text": entry.result_text,
            "error_text": entry.error_text,
            "attempt": entry.attempt,
        }

    def _serialize_event(self, event: Event) -> dict[str, Any]:
        return {
            "event_id": event.event_id,
            "topic": event.topic,
            "session_id": event.session_id,
            "source": event.source,
            "created_at": event.created_at,
            "payload": self._to_jsonable(event.payload),
        }

    def _to_jsonable(self, value: Any) -> Any:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, list):
            return [self._to_jsonable(item) for item in value]
        if isinstance(value, tuple):
            return [self._to_jsonable(item) for item in value]
        if isinstance(value, dict):
            return {str(key): self._to_jsonable(item) for key, item in value.items()}
        if hasattr(value, "model_dump"):
            try:
                return self._to_jsonable(value.model_dump(mode="json"))
            except TypeError:
                return self._to_jsonable(value.model_dump())
        return str(value)
