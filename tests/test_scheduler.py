import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from xagent.agent.memory import create_runtime_memory
from xagent.agent.runtime import SessionRuntime
from xagent.bus.events import Event, InMemoryMessageBus
from xagent.provider.types import Message, TextPart, message_text
from xagent.agent.runtime.scheduler import JobScheduler


class _SchedulerAgent:
    def __init__(self) -> None:
        self.messages = []
        self.requested_skill_name = None
        self.trace_session_id = None
        self.abort_calls = 0

    def clear_messages(self) -> None:
        self.messages.clear()

    def set_messages(self, messages) -> None:
        self.messages = list(messages)

    def set_requested_skill_name(self, requested_skill_name) -> None:
        self.requested_skill_name = requested_skill_name

    def abort(self) -> None:
        self.abort_calls += 1


class JobSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_scheduler_publishes_triggered_event_after_delay(self) -> None:
        bus = InMemoryMessageBus()
        events = []

        async def _capture(event: Event) -> None:
            events.append(event)

        bus.subscribe("*", _capture)
        scheduler = JobScheduler(bus=bus)

        job = await scheduler.schedule_once(
            session_id="session-1",
            text="hello",
            delay_seconds=0.01,
            source="test",
        )
        await scheduler.wait_for_job(job.job_id)

        self.assertEqual([event.topic for event in events], ["job.scheduled.triggered"])
        self.assertEqual(events[0].payload["text"], "hello")
        self.assertEqual(events[0].payload["job_id"], job.job_id)

    async def test_session_runtime_handles_scheduled_job_event(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = _SchedulerAgent()
            bus = InMemoryMessageBus()
            memory = create_runtime_memory(root, agent=agent)
            events = []

            async def _capture(event: Event) -> None:
                events.append(event.topic)

            bus.subscribe("*", _capture)

            async def _turn_runner(prompt, *, on_assistant_delta, on_tool_use, on_tool_result):
                reply_text = f"scheduled:{prompt}"
                agent.messages.extend(
                    [
                        Message(role="user", content=[TextPart(text=prompt)]),
                        Message(role="assistant", content=[TextPart(text=reply_text)]),
                    ]
                )
                return Message(role="assistant", content=[TextPart(text=reply_text)]), 0.02

            runtime = SessionRuntime(
                session_id="session-1",
                bus=bus,
                turn_runner=_turn_runner,
                agent=agent,
                memory=memory,
            )
            scheduler = JobScheduler(bus=bus)

            job = await scheduler.schedule_once(
                session_id="session-1",
                text="daily summary",
                delay_seconds=0.01,
                source="scheduler-test",
            )
            await scheduler.wait_for_job(job.job_id)
            await runtime.wait_for_background_tasks()

            self.assertIn("job.scheduled.triggered", events)
            self.assertIn("session.turn.requested", events)
            self.assertIn("session.turn.completed", events)
            self.assertEqual([message_text(message) for message in runtime.messages], ["daily summary", "scheduled:daily summary"])
