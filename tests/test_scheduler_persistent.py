import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from xagent.agent.runtime.scheduler import CronExpression, PersistentJobScheduler, ScheduledJobStore


class CronExpressionTests(unittest.TestCase):
    def test_parse_and_compute_next_run(self) -> None:
        cron = CronExpression.parse("*/15 9-10 * * 1-5")
        current = datetime(2026, 4, 20, 9, 7, tzinfo=timezone.utc)  # Monday

        next_run = cron.next_run_after(current)

        self.assertEqual(next_run.minute, 15)
        self.assertEqual(next_run.hour, 9)


class ScheduledJobStoreTests(unittest.TestCase):
    def test_add_list_remove_and_persist_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ScheduledJobStore(tmp)
            once = store.add_once(session_id="session-1", text="once task", run_at=100.0)
            cron = store.add_cron(
                session_id="session-2",
                text="cron task",
                cron_expression="*/5 * * * *",
                now=0.0,
            )

            reloaded = ScheduledJobStore(tmp)
            jobs = reloaded.list_jobs()

            self.assertEqual({job.job_id for job in jobs}, {once.job_id, cron.job_id})
            self.assertTrue(reloaded.remove_job(once.job_id))
            self.assertFalse(reloaded.remove_job("missing"))

    def test_retry_backoff_updates_next_run_at(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = ScheduledJobStore(tmp)
            job = store.add_once(
                session_id="session-1",
                text="retry me",
                run_at=10.0,
                retry_enabled=True,
                retry_delay_seconds=30.0,
                retry_backoff_multiplier=2.0,
                max_retries=3,
            )

            store.mark_dispatched(job.job_id, now=10.0)
            failed_once = store.record_result(job.job_id, success=False, error_text="boom", now=10.0)
            failed_twice = store.record_result(job.job_id, success=False, error_text="boom", now=40.0)

        self.assertEqual(failed_once.retry_count, 1)
        self.assertEqual(failed_once.next_run_at, 40.0)
        self.assertEqual(failed_twice.retry_count, 2)
        self.assertEqual(failed_twice.next_run_at, 100.0)


class PersistentJobSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_persistent_scheduler_dispatches_once_job_and_records_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dispatched = []

            async def _dispatch(job):
                dispatched.append(job.job_id)
                return {"job_id": job.job_id, "text": f"done:{job.text}"}

            scheduler = PersistentJobScheduler(
                cwd=tmp,
                dispatch=_dispatch,
                poll_interval_seconds=0.01,
            )

            job = scheduler.add_once(session_id="session-1", text="hello", delay_seconds=0.01)
            await scheduler.start()
            result = await scheduler.wait_for_job(job.job_id)
            await scheduler.stop()

            self.assertEqual(result["text"], "done:hello")
            stored = scheduler.get_job(job.job_id)
            self.assertIsNotNone(stored)
            self.assertEqual(stored.last_result_text, "done:hello")
            self.assertFalse(stored.enabled)
            self.assertEqual(dispatched, [job.job_id])

    async def test_persistent_scheduler_accepts_absolute_run_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dispatched = []

            async def _dispatch(job):
                dispatched.append(job.job_id)
                return {"job_id": job.job_id, "text": f"done:{job.text}"}

            scheduler = PersistentJobScheduler(
                cwd=tmp,
                dispatch=_dispatch,
                poll_interval_seconds=0.01,
                clock=lambda: 100.0,
            )

            job = scheduler.add_once(session_id="session-1", text="hello", run_at=100.0)
            await scheduler.start()
            result = await scheduler.wait_for_job(job.job_id)
            await scheduler.stop()

            self.assertEqual(result["text"], "done:hello")
            self.assertEqual(dispatched, [job.job_id])
