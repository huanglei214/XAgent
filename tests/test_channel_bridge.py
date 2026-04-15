import unittest
from tempfile import TemporaryDirectory

from xagent.agent.memory import create_runtime_memory
from xagent.agent.runtime import SessionRuntime
from xagent.agent.runtime.channel_bridge import ChannelRuntimeBridge
from xagent.channel.models import ChannelConversationKey
from xagent.foundation.events import InMemoryMessageBus
from xagent.foundation.messages import Message, TextPart


class _BridgeAgent:
    def __init__(self) -> None:
        self.messages = []
        self.requested_skill_name = None
        self.trace_session_id = None
        self.abort_calls = 0
        self.cwd = "."
        self.model = "bridge-test"

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
        on_assistant_delta(Message(role="assistant", content=[TextPart(text="thinking...")]))
        reply_text = f"bridge:{prompt}"
        agent.messages.extend(
            [
                Message(role="user", content=[TextPart(text=prompt)]),
                Message(role="assistant", content=[TextPart(text=reply_text)]),
            ]
        )
        return Message(role="assistant", content=[TextPart(text=reply_text)]), 0.05

    runtime = SessionRuntime(
        session_id=session_id or memory.episodic.new_session_id(),
        bus=message_bus,
        turn_runner=_turn_runner,
        agent=agent,
        memory=memory,
    )
    return message_bus, runtime


class _Sink:
    def __init__(self) -> None:
        self.texts = []
        self.completed = []
        self.errors = []

    def on_text(self, text: str) -> None:
        self.texts.append(text)

    def on_complete(self, text: str) -> None:
        self.completed.append(text)

    def on_error(self, error: str) -> None:
        self.errors.append(error)


class ChannelRuntimeBridgeTests(unittest.TestCase):
    def test_bridge_dispatches_text_and_reuses_session_mapping(self) -> None:
        from xagent.agent.runtime.manager import SessionRuntimeManager

        with TemporaryDirectory() as tmp:
            manager = SessionRuntimeManager(
                cwd=tmp,
                agent_factory=_BridgeAgent,
                runtime_factory=_build_test_runtime,
            )
            bridge = ChannelRuntimeBridge(cwd=tmp, manager=manager)
            sink = _Sink()
            conversation_key = ChannelConversationKey(channel="feishu", scope="chat", value="chat-1")
            try:
                session_id = bridge.dispatch_text(conversation_key, "hello", sink, source="test.bridge")
                self.assertEqual(sink.texts, ["thinking..."])
                self.assertEqual(sink.completed, ["bridge:hello"])
                self.assertEqual(sink.errors, [])

                second_session_id = bridge.resolve_session_id(conversation_key)
                self.assertEqual(second_session_id, session_id)
            finally:
                manager.close()
