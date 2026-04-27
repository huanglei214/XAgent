import unittest
from types import SimpleNamespace
from unittest.mock import patch

from xagent.cli.config import ModelConfig
from xagent.provider.anthropic import (
    AnthropicProvider,
    _from_anthropic_message,
    _to_anthropic_request_kwargs,
)
from xagent.bus.types import Message, ModelRequest, TextPart, ToolResultPart, ToolUsePart


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


class _FakeMessagesAPI:
    def __init__(self, response):
        self._response = response
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._response


class _FakeClient:
    def __init__(self, response):
        self.messages = _FakeMessagesAPI(response)


class AnthropicProviderTests(unittest.IsolatedAsyncioTestCase):
    def test_to_anthropic_request_kwargs_converts_system_tools_and_tool_results(self) -> None:
        request = ModelRequest(
            model="claude-3-7-sonnet-latest",
            messages=[
                Message(role="system", content=[TextPart(text="core system")]),
                Message(role="user", content=[TextPart(text="read the file")]),
                Message(
                    role="assistant",
                    content=[
                        TextPart(text="checking"),
                        ToolUsePart(id="toolu_1", name="read_file", input={"path": "README.md"}),
                    ],
                ),
                Message(
                    role="tool",
                    content=[
                        ToolResultPart(tool_use_id="toolu_1", content="README contents", is_error=False),
                    ],
                ),
                Message(role="system", content=[TextPart(text="follow repo rules")]),
            ],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "description": "Read a file",
                        "parameters": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                            "required": ["path"],
                        },
                    },
                }
            ],
        )

        kwargs = _to_anthropic_request_kwargs(request)

        self.assertEqual(kwargs["system"], "core system\n\nfollow repo rules")
        self.assertEqual(
            kwargs["tools"],
            [
                {
                    "name": "read_file",
                    "description": "Read a file",
                    "input_schema": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                }
            ],
        )
        self.assertEqual(
            kwargs["messages"],
            [
                {"role": "user", "content": [{"type": "text", "text": "read the file"}]},
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "checking"},
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "read_file",
                            "input": {"path": "README.md"},
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_1",
                            "content": "README contents",
                            "is_error": False,
                        }
                    ],
                },
            ],
        )

    def test_from_anthropic_message_parses_text_and_tool_use_blocks(self) -> None:
        message = SimpleNamespace(
            content=[
                SimpleNamespace(type="text", text="Final answer"),
                SimpleNamespace(type="tool_use", id="toolu_1", name="read_file", input={"path": "README.md"}),
            ]
        )

        parsed = _from_anthropic_message(message)

        self.assertEqual(parsed.role, "assistant")
        self.assertEqual(parsed.content[0].text, "Final answer")
        self.assertEqual(parsed.content[1].name, "read_file")
        self.assertEqual(parsed.content[1].input, {"path": "README.md"})

    async def test_complete_passes_tools_and_system_and_parses_tool_use(self) -> None:
        response = SimpleNamespace(
            content=[
                SimpleNamespace(type="text", text="I will inspect it."),
                SimpleNamespace(type="tool_use", id="toolu_1", name="read_file", input={"path": "README.md"}),
            ]
        )
        fake_client = _FakeClient(response)
        config = ModelConfig(
            name="claude-3-7-sonnet-latest",
            provider="anthropic",
            base_url="https://api.anthropic.com",
            api_key="test-key",
        )
        request = ModelRequest(
            model=config.name,
            messages=[Message(role="system", content=[TextPart(text="system prompt")])],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "description": "Read a file",
                        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
                    },
                }
            ],
        )

        with patch("xagent.provider.anthropic.AsyncAnthropic", lambda **_: fake_client):
            provider = AnthropicProvider(config)
            message = await provider.complete(request)

        self.assertEqual(message.content[0].text, "I will inspect it.")
        self.assertEqual(message.content[1].name, "read_file")
        self.assertEqual(fake_client.messages.calls[0]["system"], "system prompt")
        self.assertEqual(fake_client.messages.calls[0]["tools"][0]["name"], "read_file")

    async def test_stream_complete_emits_text_then_tool_snapshot(self) -> None:
        stream = _FakeStream(
            [
                SimpleNamespace(type="content_block_start", index=0, content_block=SimpleNamespace(type="text", text="")),
                SimpleNamespace(type="content_block_delta", index=0, delta=SimpleNamespace(type="text_delta", text="Hel")),
                SimpleNamespace(type="content_block_delta", index=0, delta=SimpleNamespace(type="text_delta", text="lo")),
                SimpleNamespace(
                    type="content_block_start",
                    index=1,
                    content_block=SimpleNamespace(type="tool_use", id="toolu_1", name="read_file", input={}),
                ),
                SimpleNamespace(
                    type="content_block_delta",
                    index=1,
                    delta=SimpleNamespace(type="input_json_delta", partial_json='{"path":"REA'),
                ),
                SimpleNamespace(
                    type="content_block_delta",
                    index=1,
                    delta=SimpleNamespace(type="input_json_delta", partial_json='DME.md"}'),
                ),
                SimpleNamespace(type="content_block_stop", index=1),
            ]
        )
        fake_client = _FakeClient(stream)
        config = ModelConfig(
            name="claude-3-7-sonnet-latest",
            provider="anthropic",
            base_url="https://api.anthropic.com",
            api_key="test-key",
        )

        with patch("xagent.provider.anthropic.AsyncAnthropic", lambda **_: fake_client):
            provider = AnthropicProvider(config)
            snapshots = [
                snapshot
                async for snapshot in provider.stream_complete(ModelRequest(model=config.name, messages=[]))
            ]

        self.assertEqual([snapshot.content[0].text for snapshot in snapshots[:2]], ["Hel", "Hello"])
        self.assertEqual(snapshots[-1].content[0].text, "Hello")
        self.assertEqual(snapshots[-1].content[1].name, "read_file")
        self.assertEqual(snapshots[-1].content[1].input, {"path": "README.md"})
