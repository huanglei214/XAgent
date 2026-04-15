import tempfile
import unittest
from pathlib import Path

from xagent.agent.memory import EpisodicMemory, SemanticMemory, WorkingMemory, create_runtime_memory
from xagent.foundation.messages import Message, TextPart, message_text


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


class WorkingMemoryTests(unittest.TestCase):
    def test_working_memory_tracks_messages_tools_and_requested_skill(self) -> None:
        agent = _MemoryAgent()
        memory = WorkingMemory(agent=agent)

        memory.replace_messages([Message(role="user", content=[TextPart(text="hello")])])
        memory.start_tool("calendar.lookup")
        memory.start_tool("calendar.lookup")
        memory.finish_tool("calendar.lookup")
        memory.set_requested_skill_name("calendar")
        memory.set_current_plan("draft itinerary")
        memory.set_scratchpad_item("timezone", "Asia/Shanghai")

        self.assertEqual([message_text(message) for message in memory.messages], ["hello"])
        self.assertEqual(memory.active_tools, [])
        self.assertEqual(memory.requested_skill_name, "calendar")
        self.assertEqual(agent.requested_skill_name, "calendar")
        self.assertEqual(memory.current_plan, "draft itinerary")
        self.assertEqual(memory.scratchpad["timezone"], "Asia/Shanghai")

        memory.clear_messages()
        memory.clear_turn_state()
        self.assertEqual(memory.messages, [])
        self.assertIsNone(memory.requested_skill_name)
        self.assertEqual(memory.active_tools, [])


class EpisodicMemoryTests(unittest.TestCase):
    def test_episodic_memory_wraps_session_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            memory = EpisodicMemory.for_cwd(root)
            session_id = memory.new_session_id()
            memory.save(
                session_id,
                [
                    Message(role="user", content=[TextPart(text="hello")]),
                    Message(role="assistant", content=[TextPart(text="hi")]),
                ],
            )

            restored = memory.restore(session_id)
            self.assertIsNotNone(restored)
            self.assertEqual(restored[0], session_id)
            self.assertEqual([message_text(message) for message in restored[1]], ["hello", "hi"])
            self.assertTrue(memory.session_exists(session_id))
            self.assertEqual(memory.list_sessions(limit=1)[0].session_id, session_id)


class SemanticMemoryTests(unittest.TestCase):
    def test_semantic_memory_persists_namespaced_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "semantic-memory.json"
            memory = SemanticMemory(path)
            memory.set("preferences", "language", "zh-CN")
            memory.set("facts", "home_city", "Shanghai")

            reloaded = SemanticMemory(path)
            self.assertEqual(reloaded.get("preferences", "language"), "zh-CN")
            self.assertEqual(reloaded.get("facts", "home_city"), "Shanghai")
            self.assertEqual(reloaded.get_namespace("preferences"), {"language": "zh-CN"})


class RuntimeMemoryFactoryTests(unittest.TestCase):
    def test_create_runtime_memory_wires_all_three_layers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            agent = _MemoryAgent()
            memory = create_runtime_memory(tmp, agent=agent)

            self.assertIsNotNone(memory.working)
            self.assertIsNotNone(memory.episodic)
            self.assertIsNotNone(memory.semantic)
