import tempfile
import unittest
from pathlib import Path

from xagent.foundation.messages import Message, TextPart
from xagent.memory.session_store import SessionStore


class SessionStoreTests(unittest.TestCase):
    def test_save_and_load_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = SessionStore(root)
            messages = [
                Message(role="user", content=[TextPart(text="hello")]),
                Message(role="assistant", content=[TextPart(text="hi")]),
            ]

            store.save_messages(messages)
            loaded = store.load_messages()

        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded[0].role, "user")
        self.assertEqual(loaded[1].content[0].text, "hi")

    def test_clear_removes_session_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = SessionStore(root)
            store.save_messages([Message(role="user", content=[TextPart(text="hello")])])
            self.assertTrue(store.path.exists())

            store.clear()

        self.assertFalse(store.path.exists())
