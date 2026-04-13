import unittest
from unittest.mock import patch

from typer.testing import CliRunner

from xagent.cli.main import app
from xagent.foundation.messages import Message, TextPart


class _FakePromptSession:
    def __init__(self, prompts):
        self._prompts = iter(prompts)

    async def prompt_async(self, prompt):
        value = next(self._prompts)
        if isinstance(value, BaseException):
            raise value
        return value


class _FakeAgent:
    def __init__(self):
        self.cleared = False
        self.calls = []
        self.messages = []

    def clear_messages(self):
        self.cleared = True
        self.messages = []

    def set_messages(self, messages):
        self.messages = list(messages)

    async def run(self, prompt, on_tool_use=None):
        self.calls.append(prompt)
        self.messages.append(Message(role="user", content=[TextPart(text=prompt)]))
        reply = Message(role="assistant", content=[TextPart(text=f"Echo: {prompt}")])
        self.messages.append(reply)
        return reply


class _FakeSessionStore:
    def __init__(self, restored=None):
        self.restored = restored or []
        self.saved_messages = None
        self.cleared = False

    def load_messages(self):
        return list(self.restored)

    def save_messages(self, messages):
        self.saved_messages = list(messages)

    def clear(self):
        self.cleared = True


class CliChatTests(unittest.TestCase):
    def test_chat_handles_help_clear_status_and_exit(self) -> None:
        runner = CliRunner()
        fake_agent = _FakeAgent()
        fake_session = _FakePromptSession(["/help", "/status", "/clear", "hello", "/exit"])
        fake_store = _FakeSessionStore()

        with patch("xagent.cli.chat.build_runtime_agent", return_value=fake_agent):
            with patch("xagent.cli.chat.create_prompt_session", return_value=fake_session):
                with patch("xagent.cli.chat.SessionStore", return_value=fake_store):
                    result = runner.invoke(app, ["chat"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("XAgent chat started", result.output)
        self.assertIn("Workspace:", result.output)
        self.assertIn("Cleared conversation history", result.output)
        self.assertIn("Echo: hello", result.output)
        self.assertIn("Turn completed in", result.output)
        self.assertIn("Bye.", result.output)
        self.assertTrue(fake_agent.cleared)
        self.assertTrue(fake_store.cleared)
        self.assertIsNotNone(fake_store.saved_messages)
        self.assertEqual(fake_agent.calls, ["hello"])

    def test_chat_restores_previous_session(self) -> None:
        runner = CliRunner()
        fake_agent = _FakeAgent()
        restored = [Message(role="user", content=[TextPart(text="previous")])]
        fake_session = _FakePromptSession(["/exit"])
        fake_store = _FakeSessionStore(restored=restored)

        with patch("xagent.cli.chat.build_runtime_agent", return_value=fake_agent):
            with patch("xagent.cli.chat.create_prompt_session", return_value=fake_session):
                with patch("xagent.cli.chat.SessionStore", return_value=fake_store):
                    result = runner.invoke(app, ["chat"])

        self.assertEqual(result.exit_code, 0)
        self.assertIn("Restored 1 messages", result.output)
        self.assertEqual(fake_agent.messages, restored)
