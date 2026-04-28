from __future__ import annotations

import asyncio
import inspect
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional, Union
from uuid import uuid4

from xagent.agent.paths import (
    ensure_config_dir,
    get_scheduler_history_file,
    get_scheduler_jobs_file,
)
from xagent.bus.messages import InboundMessage
from xagent.bus.queue import MessageBus


@dataclass(frozen=True)
class CronExpression:
    """Parsed 5-field cron expression (minute hour day month weekday)."""

    expression: str
    minutes: set[int]
    hours: set[int]
    days: set[int]
    months: set[int]
    weekdays: set[int]

    @classmethod
    def parse(cls, expression: str) -> CronExpression:
        parts = expression.strip().split()
        if len(parts) != 5:
            raise ValueError("Cron expression must have 5 fields: minute hour day month weekday")
        minute, hour, day, month, weekday = parts
        return cls(
            expression=expression.strip(),
            minutes=_parse_field(minute, 0, 59),
            hours=_parse_field(hour, 0, 23),
            days=_parse_field(day, 1, 31),
            months=_parse_field(month, 1, 12),
            weekdays=_parse_field(weekday, 0, 7, normalize_weekday=True),
        )

    def matches(self, value: datetime) -> bool:
        cron_weekday = value.isoweekday() % 7
        return (
            value.minute in self.minutes
            and value.hour in self.hours
            and value.day in self.days
            and value.month in self.months
            and cron_weekday in self.weekdays
        )

    def next_run_after(self, current: datetime) -> datetime:
        candidate = current.replace(second=0, microsecond=0) + timedelta(minutes=1)
        for _ in range(366 * 24 * 60):
            if self.matches(candidate):
                return candidate
            candidate += timedelta(minutes=1)
        raise ValueError(f"Could not find next run time for cron expression: {self.expression}")


def _parse_field(raw: str, minimum: int, maximum: int, *, normalize_weekday: bool = False) -> set[int]:
    values: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            raise ValueError(f"Invalid empty cron field segment in '{raw}'")
        if part == "*":
            values.update(range(minimum, maximum + 1))
            continue
        if part.startswith("*/"):
            step = int(part[2:])
            if step <= 0:
                raise ValueError(f"Invalid cron step '{part}'")
            values.update(range(minimum, maximum + 1, step))
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start = int(start_text)
            end = int(end_text)
            if start > end:
                raise ValueError(f"Invalid cron range '{part}'")
            for value in range(start, end + 1):
                values.add(_normalize_value(value, minimum, maximum, normalize_weekday))
            continue
        value = int(part)
        values.add(_normalize_value(value, minimum, maximum, normalize_weekday))
    return values


def _normalize_value(value: int, minimum: int, maximum: int, normalize_weekday: bool) -> int:
    if normalize_weekday and value == 7:
        value = 0
    if value < minimum or value > maximum:
        raise ValueError(f"Cron value {value} outside allowed range {minimum}-{maximum}")
    return value


@dataclass
class ScheduledJob:
    """In-memory representation of a one-shot scheduled job."""

    session_id: str
    text: str
    run_at: float
    requested_skill_name: Optional[str] = None
    source: str = "scheduler"
    job_id: str = field(default_factory=lambda: uuid4().hex)


class JobScheduler:
    """Fire-and-forget scheduler that publishes inbound messages via MessageBus."""

    def __init__(self, *, bus: MessageBus) -> None:
        self.bus = bus
        self._tasks: dict[str, asyncio.Task[None]] = {}

    async def schedule_once(
        self,
        *,
        session_id: str,
        text: str,
        delay_seconds: float = 0.0,
        requested_skill_name: Optional[str] = None,
        source: str = "scheduler",
    ) -> ScheduledJob:
        run_at = time.time() + max(0.0, delay_seconds)
        job = ScheduledJob(
            session_id=session_id,
            text=text,
            run_at=run_at,
            requested_skill_name=requested_skill_name,
            source=source,
        )
        task = asyncio.create_task(self._run_job(job))
        self._tasks[job.job_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(job.job_id, None))
        return job

    async def wait_for_job(self, job_id: str) -> None:
        task = self._tasks.get(job_id)
        if task is None:
            return
        await task

    async def wait_for_all(self) -> None:
        if not self._tasks:
            return
        await asyncio.gather(*list(self._tasks.values()))

    async def _run_job(self, job: ScheduledJob) -> None:
        delay = max(0.0, job.run_at - time.time())
        if delay > 0:
            await asyncio.sleep(delay)
        await self.bus.publish_inbound(
            InboundMessage(
                content=job.text,
                source=job.source,
                channel="scheduler",
                sender_id="scheduler",
                chat_id=job.session_id,
                requested_skill_name=job.requested_skill_name,
                correlation_id=job.job_id,
                session_key_override=job.session_id,
                metadata={
                    "job_id": job.job_id,
                    "run_at": job.run_at,
                },
            )
        )


@dataclass
class ScheduledJobRecord:
    """Persistent record of a scheduled job stored on disk."""

    job_id: str
    session_id: str
    text: str
    schedule_type: str
    source: str
    created_at: float
    updated_at: float
    next_run_at: float
    requested_skill_name: Optional[str] = None
    cron_expression: Optional[str] = None
    run_at: Optional[float] = None
    enabled: bool = True
    last_run_at: Optional[float] = None
    last_error: Optional[str] = None
    last_result_text: Optional[str] = None
    retry_enabled: bool = False
    retry_delay_seconds: float = 60.0
    retry_backoff_multiplier: float = 1.0
    max_retries: int = 0
    retry_count: int = 0


@dataclass
class ScheduledJobHistoryEntry:
    """Single execution record in the job history log."""

    history_id: str
    job_id: str
    session_id: str
    status: str
    text: str
    recorded_at: float
    source: str
    result_text: Optional[str] = None
    error_text: Optional[str] = None
    attempt: int = 0


class ScheduledJobStore:
    """JSON-file-backed store for scheduled jobs and their history."""

    def __init__(self, cwd: str | Path) -> None:
        self.cwd = Path(cwd)
        self.path = get_scheduler_jobs_file(self.cwd)
        self.history_path = get_scheduler_history_file(self.cwd)

    def add_once(
        self,
        *,
        session_id: str,
        text: str,
        run_at: float,
        requested_skill_name: Optional[str] = None,
        source: str = "scheduler",
        retry_enabled: bool = False,
        retry_delay_seconds: float = 60.0,
        retry_backoff_multiplier: float = 1.0,
        max_retries: int = 0,
    ) -> ScheduledJobRecord:
        now = time.time()
        job = ScheduledJobRecord(
            job_id=uuid4().hex,
            session_id=session_id,
            text=text,
            schedule_type="once",
            source=source,
            created_at=now,
            updated_at=now,
            next_run_at=run_at,
            run_at=run_at,
            requested_skill_name=requested_skill_name,
            retry_enabled=retry_enabled,
            retry_delay_seconds=max(1.0, retry_delay_seconds),
            retry_backoff_multiplier=max(1.0, retry_backoff_multiplier),
            max_retries=max(0, max_retries),
        )
        jobs = self.list_jobs()
        jobs.append(job)
        self._save_jobs(jobs)
        return job

    def add_cron(
        self,
        *,
        session_id: str,
        text: str,
        cron_expression: str,
        requested_skill_name: Optional[str] = None,
        source: str = "scheduler",
        now: Optional[float] = None,
        retry_enabled: bool = False,
        retry_delay_seconds: float = 60.0,
        retry_backoff_multiplier: float = 1.0,
        max_retries: int = 0,
    ) -> ScheduledJobRecord:
        now_ts = now if now is not None else time.time()
        cron = CronExpression.parse(cron_expression)
        next_run_at = cron.next_run_after(datetime.fromtimestamp(now_ts).astimezone()).timestamp()
        job = ScheduledJobRecord(
            job_id=uuid4().hex,
            session_id=session_id,
            text=text,
            schedule_type="cron",
            source=source,
            created_at=now_ts,
            updated_at=now_ts,
            next_run_at=next_run_at,
            requested_skill_name=requested_skill_name,
            cron_expression=cron_expression,
            retry_enabled=retry_enabled,
            retry_delay_seconds=max(1.0, retry_delay_seconds),
            retry_backoff_multiplier=max(1.0, retry_backoff_multiplier),
            max_retries=max(0, max_retries),
        )
        jobs = self.list_jobs()
        jobs.append(job)
        self._save_jobs(jobs)
        return job

    def list_jobs(self) -> list[ScheduledJobRecord]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if not isinstance(payload, list):
            return []
        jobs = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            try:
                jobs.append(ScheduledJobRecord(**item))
            except TypeError:
                continue
        jobs.sort(key=lambda item: (item.next_run_at, item.created_at, item.job_id))
        return jobs

    def get_job(self, job_id: str) -> Optional[ScheduledJobRecord]:
        for job in self.list_jobs():
            if job.job_id == job_id:
                return job
        return None

    def remove_job(self, job_id: str) -> bool:
        jobs = self.list_jobs()
        updated = [job for job in jobs if job.job_id != job_id]
        if len(updated) == len(jobs):
            return False
        self._save_jobs(updated)
        return True

    def pause_job(self, job_id: str, *, now: Optional[float] = None) -> Optional[ScheduledJobRecord]:
        return self.update_job(job_id, enabled=False, now=now)

    def resume_job(self, job_id: str, *, now: Optional[float] = None) -> Optional[ScheduledJobRecord]:
        return self.update_job(job_id, enabled=True, now=now)

    def due_jobs(self, now: Optional[float] = None) -> list[ScheduledJobRecord]:
        now_ts = now if now is not None else time.time()
        return [job for job in self.list_jobs() if job.enabled and job.next_run_at <= now_ts]

    def mark_dispatched(self, job_id: str, *, now: Optional[float] = None) -> Optional[ScheduledJobRecord]:
        now_ts = now if now is not None else time.time()
        jobs = self.list_jobs()
        updated_jobs = []
        updated_job = None
        for job in jobs:
            if job.job_id != job_id:
                updated_jobs.append(job)
                continue
            updated_job = ScheduledJobRecord(**asdict(job))
            updated_job.updated_at = now_ts
            updated_job.last_run_at = now_ts
            updated_job.last_error = None
            updated_job.last_result_text = None
            if updated_job.schedule_type == "once":
                updated_job.enabled = False
                updated_job.next_run_at = float("inf")
            else:
                cron = CronExpression.parse(updated_job.cron_expression or "")
                updated_job.next_run_at = cron.next_run_after(datetime.fromtimestamp(now_ts).astimezone()).timestamp()
            updated_jobs.append(updated_job)
        if updated_job is None:
            return None
        self._save_jobs(updated_jobs)
        return updated_job

    def record_result(
        self,
        job_id: str,
        *,
        success: bool,
        result_text: Optional[str] = None,
        error_text: Optional[str] = None,
        now: Optional[float] = None,
    ) -> Optional[ScheduledJobRecord]:
        now_ts = now if now is not None else time.time()
        jobs = self.list_jobs()
        updated_jobs = []
        updated_job = None
        for job in jobs:
            if job.job_id != job_id:
                updated_jobs.append(job)
                continue
            updated_job = ScheduledJobRecord(**asdict(job))
            updated_job.updated_at = now_ts
            updated_job.last_result_text = result_text if success else None
            updated_job.last_error = None if success else error_text
            if success:
                updated_job.retry_count = 0
            elif updated_job.retry_enabled and updated_job.retry_count < updated_job.max_retries:
                updated_job.retry_count += 1
                updated_job.enabled = True
                retry_delay = max(1.0, updated_job.retry_delay_seconds) * (
                    max(1.0, updated_job.retry_backoff_multiplier) ** max(0, updated_job.retry_count - 1)
                )
                updated_job.next_run_at = now_ts + retry_delay
            updated_jobs.append(updated_job)
        if updated_job is None:
            return None
        self._save_jobs(updated_jobs)
        self.append_history(
            job_id=updated_job.job_id,
            session_id=updated_job.session_id,
            status="success" if success else "failed",
            text=updated_job.text,
            source=updated_job.source,
            result_text=result_text if success else None,
            error_text=error_text if not success else None,
            attempt=updated_job.retry_count,
            recorded_at=now_ts,
        )
        return updated_job

    def update_job(
        self,
        job_id: str,
        *,
        text: Optional[str] = None,
        cron_expression: Optional[str] = None,
        run_at: Optional[float] = None,
        requested_skill_name: Any = None,
        enabled: Optional[bool] = None,
        retry_enabled: Optional[bool] = None,
        retry_delay_seconds: Optional[float] = None,
        retry_backoff_multiplier: Optional[float] = None,
        max_retries: Optional[int] = None,
        now: Optional[float] = None,
    ) -> Optional[ScheduledJobRecord]:
        now_ts = now if now is not None else time.time()
        jobs = self.list_jobs()
        updated_jobs = []
        updated_job = None
        sentinel = object()
        requested_skill_value = requested_skill_name if requested_skill_name is not None else sentinel
        for job in jobs:
            if job.job_id != job_id:
                updated_jobs.append(job)
                continue
            updated_job = ScheduledJobRecord(**asdict(job))
            updated_job.updated_at = now_ts
            if text is not None:
                updated_job.text = text
            if requested_skill_value is not sentinel:
                updated_job.requested_skill_name = requested_skill_name
            if retry_enabled is not None:
                updated_job.retry_enabled = retry_enabled
            if retry_delay_seconds is not None:
                updated_job.retry_delay_seconds = max(1.0, retry_delay_seconds)
            if retry_backoff_multiplier is not None:
                updated_job.retry_backoff_multiplier = max(1.0, retry_backoff_multiplier)
            if max_retries is not None:
                updated_job.max_retries = max(0, max_retries)
            if cron_expression is not None:
                cron = CronExpression.parse(cron_expression)
                updated_job.schedule_type = "cron"
                updated_job.cron_expression = cron_expression
                updated_job.run_at = None
                updated_job.next_run_at = cron.next_run_after(datetime.fromtimestamp(now_ts).astimezone()).timestamp()
                updated_job.enabled = True if enabled is None else enabled
            elif run_at is not None:
                updated_job.schedule_type = "once"
                updated_job.cron_expression = None
                updated_job.run_at = run_at
                updated_job.next_run_at = run_at
                updated_job.enabled = True if enabled is None else enabled
            elif enabled is not None:
                updated_job.enabled = enabled
                if enabled and updated_job.schedule_type == "cron":
                    cron = CronExpression.parse(updated_job.cron_expression or "")
                    updated_job.next_run_at = cron.next_run_after(datetime.fromtimestamp(now_ts).astimezone()).timestamp()
                elif enabled and updated_job.schedule_type == "once" and updated_job.run_at is not None:
                    updated_job.next_run_at = max(updated_job.run_at, now_ts)
            updated_jobs.append(updated_job)
        if updated_job is None:
            return None
        self._save_jobs(updated_jobs)
        return updated_job

    def list_history(self, *, job_id: Optional[str] = None, limit: int = 100) -> list[ScheduledJobHistoryEntry]:
        if not self.history_path.exists():
            return []
        entries: list[ScheduledJobHistoryEntry] = []
        for line in self.history_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                entry = ScheduledJobHistoryEntry(**payload)
            except Exception:
                continue
            if job_id is not None and entry.job_id != job_id:
                continue
            entries.append(entry)
        entries.sort(key=lambda item: (item.recorded_at, item.history_id), reverse=True)
        return entries[:limit]

    def append_history(
        self,
        *,
        job_id: str,
        session_id: str,
        status: str,
        text: str,
        source: str,
        result_text: Optional[str] = None,
        error_text: Optional[str] = None,
        attempt: int = 0,
        recorded_at: Optional[float] = None,
    ) -> ScheduledJobHistoryEntry:
        entry = ScheduledJobHistoryEntry(
            history_id=uuid4().hex,
            job_id=job_id,
            session_id=session_id,
            status=status,
            text=text,
            source=source,
            recorded_at=recorded_at if recorded_at is not None else time.time(),
            result_text=result_text,
            error_text=error_text,
            attempt=attempt,
        )
        ensure_config_dir(self.cwd)
        with self.history_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
        return entry

    def _save_jobs(self, jobs: list[ScheduledJobRecord]) -> None:
        ensure_config_dir(self.cwd)
        payload = [asdict(job) for job in jobs]
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


DispatchInboundCallback = Callable[[InboundMessage], Union[Awaitable[dict[str, Any]], dict[str, Any]]]


class PersistentJobScheduler:
    """Long-running scheduler that polls ScheduledJobStore and dispatches due jobs."""

    def __init__(
        self,
        *,
        cwd: str | Path,
        dispatch_inbound: DispatchInboundCallback,
        poll_interval_seconds: float = 1.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = ScheduledJobStore(cwd)
        self.dispatch_inbound = dispatch_inbound
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
            inbound = InboundMessage(
                content=job.text,
                source=job.source,
                channel="scheduler",
                sender_id="scheduler",
                chat_id=job.session_id,
                requested_skill_name=job.requested_skill_name,
                correlation_id=job.job_id,
                session_key_override=job.session_id,
                metadata={
                    "job_id": job.job_id,
                    "run_at": job.next_run_at,
                },
            )
            result = self.dispatch_inbound(inbound)
            if inspect.isawaitable(result):
                result = await result
            result_text = str(result.get("text", "")) if isinstance(result, dict) else str(result)
            self.store.record_result(job.job_id, success=True, result_text=result_text, now=self.clock())
            return result
        except Exception as exc:
            self.store.record_result(job.job_id, success=False, error_text=str(exc), now=self.clock())
            raise
