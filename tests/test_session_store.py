import tempfile
import unittest
from pathlib import Path

from xagent.agent.session import SessionStore
from xagent.foundation.messages import Message, TextPart


class SessionStoreTests(unittest.TestCase):
    def test_save_and_load_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = SessionStore(root)
            messages = [
                Message(role="user", content=[TextPart(text="hello")]),
                Message(role="assistant", content=[TextPart(text="hi")]),
            ]

            store.save_messages(messages, session_id="session-1")
            session_id, loaded, metadata = store.load_state_with_metadata()

        self.assertEqual(session_id, "session-1")
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded[0].role, "user")
        self.assertEqual(loaded[1].content[0].text, "hi")
        self.assertFalse(metadata.has_checkpoint)
        self.assertEqual(metadata.recent_message_count, 2)

    def test_long_session_compacts_into_checkpoint_and_recent_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = SessionStore(root, checkpoint_threshold=6, recent_window=4)
            messages = [
                Message(role="user", content=[TextPart(text=f"user {index}")]) if index % 2 == 0
                else Message(role="assistant", content=[TextPart(text=f"assistant {index}")])
                for index in range(10)
            ]

            store.save_messages(messages, session_id="session-1")
            session_id, loaded, metadata = store.load_state_with_metadata()

        self.assertEqual(session_id, "session-1")
        self.assertTrue(metadata.has_checkpoint)
        self.assertEqual(metadata.checkpointed_message_count, 6)
        self.assertEqual(metadata.recent_message_count, 4)
        self.assertEqual(len(loaded), 5)
        self.assertEqual(loaded[0].role, "system")
        self.assertIn("[session-checkpoint count=6]", loaded[0].content[0].text)
        self.assertIn("user 0", loaded[0].content[0].text)
        self.assertEqual(loaded[-1].content[0].text, "assistant 9")

    def test_saving_restored_checkpoint_rolls_forward_without_nesting(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = SessionStore(root, checkpoint_threshold=6, recent_window=4)
            original = [
                Message(role="user", content=[TextPart(text=f"user {index}")]) if index % 2 == 0
                else Message(role="assistant", content=[TextPart(text=f"assistant {index}")])
                for index in range(10)
            ]
            store.save_messages(original, session_id="session-1")

            session_id, restored, _ = store.load_state_with_metadata()
            restored.extend(
                [
                    Message(role="user", content=[TextPart(text="new user")]),
                    Message(role="assistant", content=[TextPart(text="new assistant")]),
                ]
            )
            store.save_messages(restored, session_id=session_id)
            _, loaded, metadata = store.load_state_with_metadata()

        self.assertEqual(metadata.checkpointed_message_count, 8)
        self.assertEqual(metadata.recent_message_count, 4)
        self.assertEqual(len([message for message in loaded if message.role == "system"]), 1)
        self.assertIn("[session-checkpoint count=8]", loaded[0].content[0].text)
        self.assertEqual(loaded[-2].content[0].text, "new user")
        self.assertEqual(loaded[-1].content[0].text, "new assistant")

    def test_clear_removes_session_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = SessionStore(root)
            store.save_messages([Message(role="user", content=[TextPart(text="hello")])])
            self.assertTrue(store.path.exists())

            store.clear()

        self.assertFalse(store.path.exists())
