import asyncio
import unittest

from xagent.agent.runtime.scheduler import JobScheduler
from xagent.bus.queue import MessageBus


class JobSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_scheduler_publishes_inbound_after_delay(self) -> None:
        bus = MessageBus()
        scheduler = JobScheduler(bus=bus)

        job = await scheduler.schedule_once(
            session_id="session-1",
            text="hello",
            delay_seconds=0.01,
            source="scheduler-test",
        )
        await scheduler.wait_for_job(job.job_id)

        inbound = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)
        self.assertEqual(inbound.content, "hello")
        self.assertEqual(inbound.source, "scheduler-test")
        self.assertEqual(inbound.channel, "scheduler")
        self.assertEqual(inbound.chat_id, "session-1")
        self.assertEqual(inbound.correlation_id, job.job_id)
        self.assertEqual(inbound.session_key, "session-1")
        self.assertEqual(inbound.metadata.get("job_id"), job.job_id)

    async def test_scheduler_passes_requested_skill_name(self) -> None:
        bus = MessageBus()
        scheduler = JobScheduler(bus=bus)

        job = await scheduler.schedule_once(
            session_id="session-1",
            text="hello",
            delay_seconds=0.0,
            requested_skill_name="my-skill",
            source="scheduler-test",
        )
        await scheduler.wait_for_job(job.job_id)

        inbound = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)
        self.assertEqual(inbound.requested_skill_name, "my-skill")
