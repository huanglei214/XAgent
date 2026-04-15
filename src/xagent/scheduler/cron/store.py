from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from xagent.foundation.runtime.paths import (
    ensure_config_dir,
    get_scheduler_history_file,
    get_scheduler_jobs_file,
)
from xagent.scheduler.cron.expressions import CronExpression


@dataclass
class ScheduledJobRecord:
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
