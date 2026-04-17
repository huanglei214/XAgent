from __future__ import annotations

import asyncio
import concurrent.futures
import json
import logging
import queue
import sys
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional
from uuid import uuid4

from xagent.agent.memory import create_runtime_memory
from xagent.foundation.events import Event
from xagent.foundation.messages import Message, message_text
from xagent.foundation.runtime.paths import ensure_config_dir
from xagent.scheduler.cron import JobScheduler, PersistentJobScheduler, ScheduledJobRecord

logger = logging.getLogger(__name__)


@dataclass
class EventStreamHandle:
    stream_id: str
    session_id: str
    events: "queue.Queue[dict[str, Any]]"


@dataclass
class SessionKeyStore:
    cwd: str

    def __post_init__(self) -> None:
        self._lock = threading.RLock()
        self._path = ensure_config_dir(Path(self.cwd)) / "session-keys.json"
        self._legacy_channel_path = ensure_config_dir(Path(self.cwd)) / "channel-sessions.json"

    def resolve_session_id(
        self,
        session_key: str,
        *,
        session_exists: Callable[[str], Any],
        create_session: Callable[[], str],
    ) -> str:
        with self._lock:
            mapping = self._load()
            session_id = mapping.get(session_key)
            if session_id is None:
                session_id = self._load_legacy_channel_mapping().get(session_key)

        if session_id:
            try:
                if session_exists(session_id) is not None:
                    return session_id
            except Exception as exc:
                logger.warning(
                    "[RuntimeManager] session_exists failed for session_id=%s key=%s: %s; reusing cached id",
                    session_id,
                    session_key,
                    exc,
                )
                return session_id

        session_id = create_session()
        with self._lock:
            mapping = self._load()
            existing_session_id = mapping.get(session_key)
            if existing_session_id:
                return existing_session_id
            mapping[session_key] = session_id
            self._save(mapping)
        return session_id

    def set_session_id(self, session_key: str, session_id: str) -> None:
        with self._lock:
            mapping = self._load()
            mapping[session_key] = session_id
            self._save(mapping)

    def _load(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _load_legacy_channel_mapping(self) -> dict[str, str]:
        if not self._legacy_channel_path.exists():
            return {}
        try:
            return json.loads(self._legacy_channel_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _save(self, mapping: dict[str, str]) -> None:
        self._path.write_text(json.dumps(mapping, indent=2, sort_keys=True), encoding="utf-8")


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
        self._session_keys = SessionKeyStore(self.cwd)
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()
        self._runtimes: dict[str, Any] = {}
        self._schedulers: dict[str, JobScheduler] = {}
        self._job_sessions: dict[str, str] = {}
        self._streams: dict[str, dict[str, Any]] = {}
        self._persistent_scheduler: Optional[PersistentJobScheduler] = None

    def create_session(self, *, session_key: Optional[str] = None) -> str:
        session_id = self._call(self._create_session())
        if session_key is not None:
            self._session_keys.set_session_id(session_key, session_id)
        return session_id

    def resolve_session_id(self, session_key: str) -> str:
        return self._session_keys.resolve_session_id(
            session_key,
            session_exists=self.get_session_status,
            create_session=lambda: self.create_session(),
        )

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
        request_id: Optional[str] = None,
    ) -> dict[str, Any]:
        return self._call(
            self._send_message(
                session_id,
                text,
                requested_skill_name=requested_skill_name,
                source=source,
                request_id=request_id,
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
        request_id: Optional[str] = None,
    ):
        self._start_loop()
        return asyncio.run_coroutine_threadsafe(
            self._send_message(
                session_id,
                text,
                requested_skill_name=requested_skill_name,
                source=source,
                request_id=request_id,
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

        shutdown_coro = _shutdown()
        try:
            self._call(shutdown_coro)
        except RuntimeError:
            shutdown_coro.close()
            # Shutdown is best-effort during process teardown. If the runtime loop is
            # blocked on in-flight work when the user interrupts, stop the loop and
            # clear local references without surfacing a secondary traceback.
            if self._loop is not None:
                self._loop.call_soon_threadsafe(self._cancel_pending_tasks)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            if self._thread is not None:
                self._thread.join(timeout=2)
            self._loop = None
            self._thread = None
            self._ready.clear()

    def _cancel_pending_tasks(self) -> None:
        loop = asyncio.get_running_loop()
        for task in asyncio.all_tasks(loop):
            task.cancel()

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
        operation = getattr(getattr(coro, "cr_code", None), "co_name", type(coro).__name__)
        self._start_loop()
        logger.info(
            "[RuntimeManager] _call start: operation=%s loop_running=%s thread_alive=%s runtimes=%d streams=%d",
            operation,
            self._loop.is_running() if self._loop is not None else False,
            self._thread.is_alive() if self._thread is not None else False,
            len(self._runtimes),
            len(self._streams),
        )
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            result = future.result(timeout=30)
            logger.info("[RuntimeManager] _call done: operation=%s", operation)
            return result
        except concurrent.futures.TimeoutError as exc:
            logger.error(
                "[RuntimeManager] _call timeout: operation=%s loop_running=%s thread_alive=%s pending_streams=%d",
                operation,
                self._loop.is_running() if self._loop is not None else False,
                self._thread.is_alive() if self._thread is not None else False,
                len(self._streams),
            )
            self._log_thread_stacks()
            raise RuntimeError("Runtime manager operation timed out.") from exc

    async def _create_session(self) -> str:
        runtime = self._new_runtime(session_id=None)
        self._runtimes[runtime.session_id] = runtime
        self._session_keys.set_session_id(runtime.session_id, runtime.session_id)
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
        request_id: Optional[str] = None,
    ) -> dict[str, Any]:
        logger.info(
            "[RuntimeManager] _send_message start: session_id=%s source=%s text=%.100s",
            session_id,
            source,
            text,
        )
        runtime = await self._ensure_runtime(session_id, restore=True)
        if runtime is None:
            raise KeyError(session_id)
        turn_result = await runtime.publish_user_message(
            text,
            source=source,
            requested_skill_name=requested_skill_name,
            request_id=request_id,
        )
        await self._wait_for_runtime(runtime)
        logger.info("[RuntimeManager] _send_message done: session_id=%s", session_id)
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
        logger.info("[RuntimeManager] _open_event_stream start: session_id=%s topics=%s", session_id, topics)
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
        logger.info("[RuntimeManager] _open_event_stream done: session_id=%s stream_id=%s", session_id, stream_id)
        return EventStreamHandle(stream_id=stream_id, session_id=runtime.session_id, events=event_queue)

    async def _close_event_stream(self, stream_id: str) -> None:
        logger.info("[RuntimeManager] _close_event_stream start: stream_id=%s", stream_id)
        stream = self._streams.pop(stream_id, None)
        if stream is None:
            logger.info("[RuntimeManager] _close_event_stream noop: stream_id=%s", stream_id)
            return
        unsubscribe = stream.get("unsubscribe")
        if callable(unsubscribe):
            unsubscribe()
        logger.info("[RuntimeManager] _close_event_stream done: stream_id=%s", stream_id)

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

    async def _wait_for_runtime(self, runtime: Any, *, timeout: float = 5.0) -> None:
        wait_for_background_tasks = getattr(runtime, "wait_for_background_tasks", None)
        if callable(wait_for_background_tasks):
            maybe_wait = wait_for_background_tasks()
            if asyncio.iscoroutine(maybe_wait) or isinstance(maybe_wait, asyncio.Future):
                try:
                    await asyncio.wait_for(maybe_wait, timeout=timeout)
                except asyncio.TimeoutError:
                    logger.warning(
                        "_wait_for_runtime timed out after %.1fs for session_id=%s; "
                        "background tasks may still be running",
                        timeout,
                        getattr(runtime, "session_id", "?"),
                    )
                    # Best-effort: dump pending runtime tasks and attempt cancellation to unblock
                    # the manager loop for subsequent channel requests.
                    self._log_runtime_tasks(runtime)
                    self._cancel_runtime_tasks(runtime)

    def _log_thread_stacks(self) -> None:
        """Log all thread stacks to help diagnose runtime manager timeouts."""
        current_frames = sys._current_frames()
        for thread in threading.enumerate():
            frame = current_frames.get(thread.ident)
            if frame is None:
                continue
            stack_text = "".join(traceback.format_stack(frame))
            logger.error(
                "[RuntimeManager] thread dump: name=%s ident=%s alive=%s\n%s",
                thread.name,
                thread.ident,
                thread.is_alive(),
                stack_text,
            )
        for runtime in list(self._runtimes.values()):
            self._log_runtime_tasks(runtime)

    def _log_runtime_tasks(self, runtime: Any) -> None:
        """Log pending asyncio tasks tracked by SessionRuntime (if any)."""
        tasks = getattr(runtime, "_active_tasks", None)
        if not tasks:
            return
        try:
            session_id = getattr(runtime, "session_id", "?")
            logger.error("[RuntimeManager] runtime pending tasks: session_id=%s count=%d", session_id, len(tasks))
            for task in list(tasks):
                if task.done():
                    continue
                stack = task.get_stack(limit=20)
                if stack:
                    # Each frame is a FrameType; use format_stack for readable output.
                    stack_text = "".join(traceback.format_stack(stack[-1], limit=20))
                else:
                    stack_text = "(no stack)"
                logger.error(
                    "[RuntimeManager] pending task: session_id=%s task=%r cancelled=%s\n%s",
                    session_id,
                    task,
                    task.cancelled(),
                    stack_text,
                )
        except Exception as exc:
            logger.error("[RuntimeManager] failed to log runtime tasks: %s", exc)

    def _cancel_runtime_tasks(self, runtime: Any) -> None:
        """Best-effort cancel of runtime active tasks to avoid wedging the manager loop."""
        tasks = getattr(runtime, "_active_tasks", None)
        if not tasks:
            return
        session_id = getattr(runtime, "session_id", "?")
        for task in list(tasks):
            if task.done():
                continue
            try:
                task.cancel()
                logger.warning("[RuntimeManager] cancelled pending task: session_id=%s task=%r", session_id, task)
            except Exception as exc:
                logger.warning("[RuntimeManager] failed to cancel task: session_id=%s task=%r err=%s", session_id, task, exc)

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
