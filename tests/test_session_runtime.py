import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from xagent.agent.memory import create_runtime_memory
from xagent.agent.runtime import SessionRuntime
from xagent.agent.session import SessionStore
from xagent.bus.events import Event, InMemoryMessageBus
from xagent.bus.types import Message, TextPart, ToolResultPart, ToolUsePart, message_text


class _RuntimeAgent:
    def __init__(self) -> None:
        self.messages = []
        self.trace_session_id = None
        self.requested_skill_name = None
        self.abort_calls = 0

    def clear_messages(self) -> None:
        self.messages.clear()

    def set_messages(self, messages) -> None:
        self.messages = list(messages)

    def set_requested_skill_name(self, requested_skill_name) -> None:
        self.requested_skill_name = requested_skill_name

    def abort(self) -> None:
        self.abort_calls += 1


class SessionRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_emits_event_flow_and_returns_turn_result(self) -> None:
        bus = InMemoryMessageBus()
        events = []

        async def _capture(event: Event) -> None:
            events.append(event)

        bus.subscribe("*", _capture)

        async def _turn_runner(prompt, *, on_assistant_delta, on_tool_use, on_tool_result):
            tool_use = ToolUsePart(id="call-1", name="echo_tool", input={"value": prompt})
            on_assistant_delta(Message(role="assistant", content=[TextPart(text="thinking")]))
            on_tool_use(tool_use)
            on_tool_result(tool_use, ToolResultPart(tool_use_id="call-1", content="ok", is_error=False))
            return Message(role="assistant", content=[TextPart(text="done")]), 0.25

        runtime = SessionRuntime(
            session_id="session-1",
            bus=bus,
            turn_runner=_turn_runner,
        )

        result = await runtime.publish_user_message("hello", source="test")

        self.assertEqual(message_text(result.message), "done")
        self.assertEqual(result.duration_seconds, 0.25)
        self.assertEqual(
            [event.topic for event in events],
            [
                "user.message.received",
                "session.turn.requested",
                "assistant.delta",
                "tool.called",
                "tool.finished",
                "session.turn.completed",
            ],
        )
        self.assertEqual(events[2].payload["text"], "thinking")
        self.assertEqual(events[3].payload["tool_name"], "echo_tool")
        self.assertFalse(events[4].payload["is_error"])

    async def test_runtime_emits_failed_event_and_raises(self) -> None:
        bus = InMemoryMessageBus()
        events = []

        async def _capture(event: Event) -> None:
            events.append(event)

        bus.subscribe("*", _capture)

        async def _turn_runner(prompt, *, on_assistant_delta, on_tool_use, on_tool_result):
            raise RuntimeError(f"failed: {prompt}")

        runtime = SessionRuntime(
            session_id="session-1",
            bus=bus,
            turn_runner=_turn_runner,
        )

        with self.assertRaisesRegex(RuntimeError, "failed: hello"):
            await runtime.publish_user_message("hello", source="test")

        self.assertEqual(
            [event.topic for event in events],
            [
                "user.message.received",
                "session.turn.requested",
                "session.turn.failed",
            ],
        )
        self.assertEqual(events[-1].payload["error"], "failed: hello")

    async def test_runtime_manages_session_lifecycle_with_store(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = _RuntimeAgent()
            store = SessionStore(root)
            bus = InMemoryMessageBus()
            runtime = SessionRuntime(
                session_id="session-1",
                bus=bus,
                turn_runner=lambda *args, **kwargs: None,
                agent=agent,
                session_store=store,
            )

            agent.messages = [Message(role="user", content=[TextPart(text="first session")])]
            original_path = runtime.save_session()
            self.assertIsNotNone(original_path)
            self.assertTrue(original_path.exists())

            new_session_id = runtime.start_new_session()
            self.assertNotEqual(new_session_id, "session-1")
            self.assertEqual(runtime.session_id, new_session_id)
            self.assertEqual(agent.trace_session_id, new_session_id)
            self.assertEqual(agent.messages, [])

            agent.messages = [Message(role="assistant", content=[TextPart(text="second session")])]
            runtime.save_session()

            restored = runtime.restore_session("session-1")
            self.assertIsNotNone(restored)
            self.assertEqual(restored.session_id, "session-1")
            self.assertEqual(message_text(agent.messages[0]), "first session")

            runtime.clear_session()
            self.assertEqual(agent.messages, [])
            self.assertFalse(store.session_exists("session-1"))

    async def test_runtime_applies_requested_skill_and_saves_on_success(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = _RuntimeAgent()
            store = SessionStore(root)
            bus = InMemoryMessageBus()

            async def _turn_runner(prompt, *, on_assistant_delta, on_tool_use, on_tool_result):
                self.assertEqual(agent.requested_skill_name, "calendar")
                agent.messages.extend(
                    [
                        Message(role="user", content=[TextPart(text=prompt)]),
                        Message(role="assistant", content=[TextPart(text="done")]),
                    ]
                )
                return Message(role="assistant", content=[TextPart(text="done")]), 0.1

            runtime = SessionRuntime(
                session_id="session-1",
                bus=bus,
                turn_runner=_turn_runner,
                agent=agent,
                session_store=store,
            )

            result = await runtime.publish_user_message(
                "schedule it",
                source="test",
                requested_skill_name="calendar",
            )

            self.assertEqual(message_text(result.message), "done")
            self.assertIsNone(agent.requested_skill_name)
            loaded_session_id, loaded_messages, _ = store.load_state_with_metadata(session_id="session-1")
            self.assertEqual(loaded_session_id, "session-1")
            self.assertEqual([message_text(message) for message in loaded_messages], ["schedule it", "done"])

    async def test_runtime_abort_delegates_to_agent(self) -> None:
        runtime = SessionRuntime(
            session_id="session-1",
            bus=InMemoryMessageBus(),
            turn_runner=lambda *args, **kwargs: None,
            agent=_RuntimeAgent(),
        )

        runtime.abort()

        self.assertEqual(runtime.agent.abort_calls, 1)

    async def test_runtime_auto_compacts_after_successful_turn(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = _RuntimeAgent()
            memory = create_runtime_memory(root, agent=agent)
            memory.episodic.store.checkpoint_threshold = 3
            memory.episodic.store.recent_window = 2

            async def _turn_runner(prompt, *, on_assistant_delta, on_tool_use, on_tool_result):
                agent.messages.extend(
                    [
                        Message(role="user", content=[TextPart(text="old user")]),
                        Message(role="assistant", content=[TextPart(text="old assistant")]),
                        Message(role="user", content=[TextPart(text=prompt)]),
                        Message(role="assistant", content=[TextPart(text="done")]),
                    ]
                )
                return Message(role="assistant", content=[TextPart(text="done")]), 0.1

            bus = InMemoryMessageBus()
            events = []

            async def _capture(event: Event) -> None:
                events.append(event.topic)

            bus.subscribe("*", _capture)
            runtime = SessionRuntime(
                session_id="session-1",
                bus=bus,
                turn_runner=_turn_runner,
                agent=agent,
                memory=memory,
            )

            await runtime.publish_user_message("new user", source="test")
            await runtime.wait_for_background_tasks()

            self.assertIn("memory.compaction.requested", events)
            self.assertIn("memory.compaction.completed", events)
            self.assertEqual(agent.messages[0].role, "system")
            self.assertIn("[session-checkpoint", message_text(agent.messages[0]))
