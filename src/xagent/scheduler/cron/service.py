from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional
from uuid import uuid4

from xagent.foundation.events import Event, InMemoryMessageBus


@dataclass
class ScheduledJob:
    session_id: str
    text: str
    run_at: float
    requested_skill_name: Optional[str] = None
    source: str = "scheduler"
    job_id: str = field(default_factory=lambda: uuid4().hex)


class JobScheduler:
    def __init__(self, *, bus: InMemoryMessageBus) -> None:
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
        await self.bus.publish(
            Event(
                topic="job.scheduled.triggered",
                session_id=job.session_id,
                payload={
                    "job_id": job.job_id,
                    "text": job.text,
                    "requested_skill_name": job.requested_skill_name,
                    "run_at": job.run_at,
                },
                source=job.source,
            )
        )
