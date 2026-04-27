import unittest
from tempfile import TemporaryDirectory

from xagent.agent.memory import create_runtime_memory
from xagent.agent.runtime import SessionRuntime
from xagent.bus.events import InMemoryMessageBus
from xagent.bus.types import Message, TextPart


class _ManagerAgent:
    def __init__(self) -> None:
        self.messages = []
        self.requested_skill_name = None
        self.trace_session_id = None
        self.abort_calls = 0
        self.cwd = "."
        self.model = "manager-test"

    def clear_messages(self) -> None:
        self.messages.clear()

    def set_messages(self, messages) -> None:
        self.messages = list(messages)

    def set_requested_skill_name(self, requested_skill_name) -> None:
        self.requested_skill_name = requested_skill_name

    def abort(self) -> None:
        self.abort_calls += 1


def _build_test_runtime(agent, *, session_id=None, cwd=None, bus=None):
    memory = create_runtime_memory(cwd or ".", agent=agent)
    message_bus = bus or InMemoryMessageBus()

    async def _turn_runner(prompt, *, on_assistant_delta, on_tool_use, on_tool_result):
        reply_text = f"managed:{prompt}"
        agent.messages.extend(
            [
                Message(role="user", content=[TextPart(text=prompt)]),
                Message(role="assistant", content=[TextPart(text=reply_text)]),
            ]
        )
        return Message(role="assistant", content=[TextPart(text=reply_text)]), 0.03

    runtime = SessionRuntime(
        session_id=session_id or memory.episodic.new_session_id(),
        bus=message_bus,
        turn_runner=_turn_runner,
        agent=agent,
        memory=memory,
    )
    return message_bus, runtime


class SessionRuntimeManagerTests(unittest.TestCase):
    def test_manager_resolves_and_reuses_session_key_routes(self) -> None:
        from xagent.agent.runtime.manager import SessionRuntimeManager

        with TemporaryDirectory() as tmp:
            manager = SessionRuntimeManager(
                cwd=tmp,
                agent_factory=_ManagerAgent,
                runtime_factory=_build_test_runtime,
            )
            try:
                session_id = manager.resolve_session_id("feishu:user:user-1")
                resolved_again = manager.resolve_session_id("feishu:user:user-1")

                self.assertEqual(resolved_again, session_id)
                self.assertIsNotNone(manager.get_session_status(session_id))
            finally:
                manager.close()

    def test_manager_creates_sends_and_lists_sessions(self) -> None:
        from xagent.agent.runtime.manager import SessionRuntimeManager

        with TemporaryDirectory() as tmp:
            manager = SessionRuntimeManager(
                cwd=tmp,
                agent_factory=_ManagerAgent,
                runtime_factory=_build_test_runtime,
            )
            try:
                session_id = manager.create_session()
                response = manager.send_message(session_id, "hello")

                self.assertEqual(response["session_id"], session_id)
                self.assertEqual(response["text"], "managed:hello")
                self.assertEqual(manager.get_session_status(session_id)["message_count"], 2)
                self.assertEqual(
                    [item["text"] for item in manager.get_session_messages(session_id)],
                    ["hello", "managed:hello"],
                )
                self.assertEqual(manager.list_sessions()[0]["session_id"], session_id)
            finally:
                manager.close()

    def test_manager_schedules_job_into_existing_session(self) -> None:
        from xagent.agent.runtime.manager import SessionRuntimeManager

        with TemporaryDirectory() as tmp:
            manager = SessionRuntimeManager(
                cwd=tmp,
                agent_factory=_ManagerAgent,
                runtime_factory=_build_test_runtime,
            )
            try:
                session_id = manager.create_session()
                job = manager.schedule_message(session_id, "daily summary", delay_seconds=0.01)
                self.assertEqual(job["session_id"], session_id)
                self.assertEqual(job["text"], "daily summary")

                response = manager.wait_for_job(job["job_id"])
                self.assertEqual(response["session_id"], session_id)
                self.assertEqual(response["text"], "managed:daily summary")
                self.assertEqual(manager.get_session_status(session_id)["message_count"], 2)
            finally:
                manager.close()

    def test_manager_persistent_jobs_support_pause_update_and_history(self) -> None:
        from xagent.agent.runtime.manager import SessionRuntimeManager

        with TemporaryDirectory() as tmp:
            manager = SessionRuntimeManager(
                cwd=tmp,
                agent_factory=_ManagerAgent,
                runtime_factory=_build_test_runtime,
            )
            try:
                session_id = manager.create_session()
                job = manager.add_cron_job(
                    session_id,
                    "nightly summary",
                    cron_expression="*/5 * * * *",
                    retry_enabled=True,
                    retry_delay_seconds=30,
                    retry_backoff_multiplier=2.0,
                    max_retries=2,
                )
                paused = manager.pause_job(job["job_id"])
                self.assertFalse(paused["enabled"])

                updated = manager.update_job(job["job_id"], text="updated summary", enabled=True)
                self.assertTrue(updated["enabled"])
                self.assertEqual(updated["text"], "updated summary")
                self.assertEqual(updated["retry_backoff_multiplier"], 2.0)

                history = manager.list_job_history(job_id=job["job_id"])
                self.assertEqual(history, [])

                removed = manager.remove_job(job["job_id"])
                self.assertTrue(removed)
            finally:
                manager.close()
