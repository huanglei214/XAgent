import os
from json import dumps
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from xagent.cli.config import ModelConfig
from xagent.bus.types import Message, TextPart, ToolResultPart, ToolUsePart
from xagent.bus.types import ModelRequest
from xagent.provider.openai import OpenAIChatProvider, _to_openai_messages


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = iter(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._chunks)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _FakeCompletions:
    async def create(self, **_: object):
        return _FakeStream(
            [
                SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content="Hello"))]
                ),
                SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content=" world"))]
                ),
            ]
        )


class _FakeNonStreamingCompletions:
    async def create(self, **_: object):
        message = SimpleNamespace(
            content="Final answer",
            tool_calls=[
                SimpleNamespace(
                    id="call_1",
                    function=SimpleNamespace(name="read_file", arguments=dumps({"path": "README.md"})),
                )
            ],
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class _FakeChat:
    def __init__(self):
        self.completions = _FakeCompletions()


class _FakeClient:
    def __init__(self, **_: object):
        self.chat = _FakeChat()


class _FakeClientComplete:
    def __init__(self, **_: object):
        self.chat = SimpleNamespace(completions=_FakeNonStreamingCompletions())


class OpenAIProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_text_yields_chunks(self) -> None:
        config = ModelConfig(
            name="ep-test",
            provider="ark",
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            api_key_env="ARK_API_KEY",
        )
        request = ModelRequest(
            model=config.name,
            messages=[Message(role="user", content=[TextPart(text="hello")])],
        )

        with patch.dict(os.environ, {"ARK_API_KEY": "test-key"}, clear=False):
            with patch("xagent.provider.openai.AsyncOpenAI", _FakeClient):
                provider = OpenAIChatProvider(config)
                parts = [part async for part in provider.stream_text(request)]

        self.assertEqual(parts, ["Hello", " world"])
        self.assertEqual(provider.provider_name, "ark")

    def test_missing_api_key_raises(self) -> None:
        config = ModelConfig(
            name="ep-test",
            provider="ark",
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            api_key_env="ARK_API_KEY",
        )
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ValueError):
                OpenAIChatProvider(config)

    def test_openai_message_conversion(self) -> None:
        request = ModelRequest(
            model="gpt-4o-mini",
            messages=[
                Message(role="system", content=[TextPart(text="system")]),
                Message(role="user", content=[TextPart(text="user")]),
                Message(
                    role="assistant",
                    content=[
                        TextPart(text="checking"),
                        ToolUsePart(id="call_1", name="read_file", input={"path": "README.md"}),
                    ],
                ),
                Message(
                    role="tool",
                    content=[ToolResultPart(tool_use_id="call_1", content="hello", is_error=False)],
                ),
            ],
            tools=[{"type": "function", "function": {"name": "read_file"}}],
        )
        converted = _to_openai_messages(request)
        self.assertEqual(
            converted,
            [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "user"},
                {
                    "role": "assistant",
                    "content": "checking",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {
                                "name": "read_file",
                                "arguments": dumps({"path": "README.md"}),
                            },
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call_1", "content": "hello"},
            ],
        )

    async def test_complete_parses_tool_calls(self) -> None:
        config = ModelConfig(
            name="ep-test",
            provider="ark",
            base_url="https://ark.cn-beijing.volces.com/api/v3",
            api_key_env="ARK_API_KEY",
        )

        with patch.dict(os.environ, {"ARK_API_KEY": "test-key"}, clear=False):
            with patch("xagent.provider.openai.AsyncOpenAI", _FakeClientComplete):
                provider = OpenAIChatProvider(config)
                message = await provider.complete(ModelRequest(model="ep-test", messages=[]))

        self.assertEqual(message.role, "assistant")
        self.assertEqual(message.content[0].text, "Final answer")
        self.assertEqual(message.content[1].name, "read_file")

    def test_openai_message_conversion_skips_tool_messages_without_tool_result(self) -> None:
        request = ModelRequest(
            model="gpt-4o-mini",
            messages=[
                Message(role="user", content=[TextPart(text="user")]),
                Message(role="tool", content=[TextPart(text="missing tool result")]),
            ],
        )

        converted = _to_openai_messages(request)

        self.assertEqual(converted, [{"role": "user", "content": "user"}])
