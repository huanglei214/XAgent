import unittest
from tempfile import TemporaryDirectory

from xagent.agent.compaction import AutoCompactService
from xagent.agent.memory import create_runtime_memory
from xagent.bus.queue import MessageBus
from xagent.provider.types import Message, TextPart, message_text


class _MemoryAgent:
    def __init__(self) -> None:
        self.messages = []
        self.requested_skill_name = None

    def clear_messages(self) -> None:
        self.messages.clear()

    def set_messages(self, messages) -> None:
        self.messages = list(messages)

    def set_requested_skill_name(self, requested_skill_name) -> None:
        self.requested_skill_name = requested_skill_name


class AutoCompactServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_compact_compacts_and_emits_events(self) -> None:
        with TemporaryDirectory() as tmp:
            agent = _MemoryAgent()
            memory = create_runtime_memory(tmp, agent=agent)
            memory.episodic.store.checkpoint_threshold = 3
            memory.episodic.store.recent_window = 2
            agent.messages = [
                Message(role="user", content=[TextPart(text="u1")]),
                Message(role="assistant", content=[TextPart(text="a1")]),
                Message(role="user", content=[TextPart(text="u2")]),
                Message(role="assistant", content=[TextPart(text="a2")]),
            ]
            bus = MessageBus()
            service = AutoCompactService(
                message_bus=bus,
                working_memory=memory.working,
                episodic_memory=memory.episodic,
                session_id_getter=lambda: "session-1",
                is_turn_active=lambda: False,
                message_threshold=3,
                token_threshold=None,
            )

            task = await service.request_if_needed()
            self.assertIsNotNone(task)
            await service.wait_for_all()
            events = []
            while not bus.outbound.empty():
                events.append(bus.outbound.get_nowait())

            self.assertEqual(
                [event.metadata["_event"] for event in events],
                ["compact_started", "compact_finished"],
            )
            self.assertTrue(events[-1].metadata["has_checkpoint"])
            self.assertEqual(memory.working.messages[0].role, "system")
            self.assertIn("[session-checkpoint", message_text(memory.working.messages[0]))

    async def test_auto_compact_skips_when_turn_active(self) -> None:
        with TemporaryDirectory() as tmp:
            agent = _MemoryAgent()
            memory = create_runtime_memory(tmp, agent=agent)
            agent.messages = [
                Message(role="user", content=[TextPart(text="u1")]),
                Message(role="assistant", content=[TextPart(text="a1")]),
                Message(role="user", content=[TextPart(text="u2")]),
                Message(role="assistant", content=[TextPart(text="a2")]),
            ]
            service = AutoCompactService(
                message_bus=MessageBus(),
                working_memory=memory.working,
                episodic_memory=memory.episodic,
                session_id_getter=lambda: "session-1",
                is_turn_active=lambda: True,
                message_threshold=3,
                token_threshold=None,
            )

            task = await service.request_if_needed()
            self.assertIsNotNone(task)
            self.assertFalse(await task)
            self.assertEqual([message_text(message) for message in memory.working.messages], ["u1", "a1", "u2", "a2"])
