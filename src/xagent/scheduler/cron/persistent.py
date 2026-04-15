from __future__ import annotations

import asyncio
import inspect
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Union

from xagent.scheduler.cron.store import ScheduledJobRecord, ScheduledJobStore


DispatchCallback = Callable[[ScheduledJobRecord], Union[Awaitable[dict[str, Any]], dict[str, Any]]]


class PersistentJobScheduler:
    def __init__(
        self,
        *,
        cwd: str | Path,
        dispatch: DispatchCallback,
        poll_interval_seconds: float = 1.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = ScheduledJobStore(cwd)
        self.dispatch = dispatch
        self.poll_interval_seconds = max(0.1, poll_interval_seconds)
        self.clock = clock
        self._runner_task: Optional[asyncio.Task[None]] = None
        self._dispatch_tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}

    def list_jobs(self) -> list[ScheduledJobRecord]:
        return self.store.list_jobs()

    def get_job(self, job_id: str) -> Optional[ScheduledJobRecord]:
        return self.store.get_job(job_id)

    def remove_job(self, job_id: str) -> bool:
        task = self._dispatch_tasks.pop(job_id, None)
        if task is not None:
            task.cancel()
        return self.store.remove_job(job_id)

    def add_once(
        self,
        *,
        session_id: str,
        text: str,
        delay_seconds: float = 0.0,
        run_at: Optional[float] = None,
        requested_skill_name: Optional[str] = None,
        retry_enabled: bool = False,
        retry_delay_seconds: float = 60.0,
        retry_backoff_multiplier: float = 1.0,
        max_retries: int = 0,
        source: str = "scheduler",
    ) -> ScheduledJobRecord:
        resolved_run_at = run_at if run_at is not None else self.clock() + max(0.0, delay_seconds)
        return self.store.add_once(
            session_id=session_id,
            text=text,
            run_at=resolved_run_at,
            requested_skill_name=requested_skill_name,
            retry_enabled=retry_enabled,
            retry_delay_seconds=retry_delay_seconds,
            retry_backoff_multiplier=retry_backoff_multiplier,
            max_retries=max_retries,
            source=source,
        )

    def add_cron(
        self,
        *,
        session_id: str,
        text: str,
        cron_expression: str,
        requested_skill_name: Optional[str] = None,
        retry_enabled: bool = False,
        retry_delay_seconds: float = 60.0,
        retry_backoff_multiplier: float = 1.0,
        max_retries: int = 0,
        source: str = "scheduler",
    ) -> ScheduledJobRecord:
        return self.store.add_cron(
            session_id=session_id,
            text=text,
            cron_expression=cron_expression,
            requested_skill_name=requested_skill_name,
            retry_enabled=retry_enabled,
            retry_delay_seconds=retry_delay_seconds,
            retry_backoff_multiplier=retry_backoff_multiplier,
            max_retries=max_retries,
            source=source,
            now=self.clock(),
        )

    async def start(self) -> None:
        if self._runner_task is not None and not self._runner_task.done():
            return
        self._runner_task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._runner_task is not None:
            self._runner_task.cancel()
            try:
                await self._runner_task
            except asyncio.CancelledError:
                pass
            self._runner_task = None
        if self._dispatch_tasks:
            tasks = list(self._dispatch_tasks.values())
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            self._dispatch_tasks.clear()

    async def wait_for_job(self, job_id: str) -> Optional[dict[str, Any]]:
        while True:
            task = self._dispatch_tasks.get(job_id)
            if task is not None:
                return await task

            job = self.store.get_job(job_id)
            if job is None:
                raise KeyError(job_id)
            if job.last_error:
                raise RuntimeError(job.last_error)
            if job.last_result_text is not None:
                return {"job_id": job.job_id, "text": job.last_result_text}
            await asyncio.sleep(self.poll_interval_seconds)

    async def _run_loop(self) -> None:
        while True:
            now = self.clock()
            for job in self.store.due_jobs(now):
                if job.job_id in self._dispatch_tasks:
                    continue
                dispatched = self.store.mark_dispatched(job.job_id, now=now)
                if dispatched is None:
                    continue
                task = asyncio.create_task(self._dispatch_job(dispatched))
                self._dispatch_tasks[job.job_id] = task
                task.add_done_callback(lambda _: self._dispatch_tasks.pop(job.job_id, None))
            await asyncio.sleep(self.poll_interval_seconds)

    async def _dispatch_job(self, job: ScheduledJobRecord) -> dict[str, Any]:
        try:
            result = self.dispatch(job)
            if inspect.isawaitable(result):
                result = await result
            result_text = str(result.get("text", "")) if isinstance(result, dict) else str(result)
            self.store.record_result(job.job_id, success=True, result_text=result_text, now=self.clock())
            return result
        except Exception as exc:
            self.store.record_result(job.job_id, success=False, error_text=str(exc), now=self.clock())
            raise
