from __future__ import annotations

import sys
from types import ModuleType

import pytest

from xagent.config import default_config
from xagent.providers import OpenAICompatProvider, make_provider
from xagent.providers.types import ModelRequest


def test_make_provider_resolves_openai_compat_snapshot_from_config() -> None:
    config = default_config()
    config.agents.defaults.model = "custom-model"
    config.providers.openai_compat.api_key = "config-key"
    config.providers.openai_compat.api_base = "http://localhost:11434/v1"

    snapshot = make_provider(config)

    assert snapshot.model == "custom-model"
    assert snapshot.provider_name == "openai_compat"
    assert snapshot.api_base == "http://localhost:11434/v1"
    assert isinstance(snapshot.provider, OpenAICompatProvider)
    assert snapshot.provider._resolved_api_key() == "config-key"
    assert "config-key" not in snapshot.signature


def test_make_provider_rejects_unknown_provider() -> None:
    config = default_config()
    config.agents.defaults.provider = "anthropic"

    with pytest.raises(ValueError, match="Unsupported provider"):
        make_provider(config)


def test_missing_api_key_uses_no_key_and_ignores_environment(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "env-key")
    config = default_config()

    snapshot = make_provider(config)

    assert isinstance(snapshot.provider, OpenAICompatProvider)
    assert snapshot.provider._resolved_api_key() == "no-key"


def test_openai_compat_build_kwargs_merges_provider_options() -> None:
    provider = OpenAICompatProvider(
        api_key="key",
        api_base="https://example.test/v1",
        extra_headers={"X-Test": "yes"},
        extra_body={"metadata": {"tenant": "xagent"}},
        timeout_seconds=42,
    )
    request = ModelRequest(
        model="m",
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"type": "function", "function": {"name": "noop", "parameters": {}}}],
        temperature=0.2,
        max_tokens=10,
    )

    kwargs = provider._build_kwargs(request)

    assert kwargs["stream"] is True
    assert kwargs["stream_options"] == {"include_usage": True}
    assert kwargs["extra_headers"] == {"X-Test": "yes"}
    assert kwargs["extra_body"] == {"metadata": {"tenant": "xagent"}}
    assert kwargs["temperature"] == 0.2
    assert kwargs["max_tokens"] == 10


@pytest.mark.asyncio
async def test_openai_compat_stream_emits_core_events(monkeypatch) -> None:
    captured_init: dict[str, object] = {}
    captured_kwargs: dict[str, object] = {}

    class FakeStream:
        async def __aiter__(self):
            yield {"choices": [{"delta": {"content": "hello"}}]}
            yield {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": '{"path":"README.md"}',
                                    },
                                }
                            ]
                        }
                    }
                ]
            }
            yield {"usage": {"total_tokens": 7}, "choices": []}

    class FakeCompletions:
        async def create(self, **kwargs):
            captured_kwargs.update(kwargs)
            return FakeStream()

    class FakeChat:
        def __init__(self) -> None:
            self.completions = FakeCompletions()

    class FakeAsyncOpenAI:
        def __init__(self, **kwargs) -> None:
            captured_init.update(kwargs)
            self.chat = FakeChat()

    fake_openai = ModuleType("openai")
    fake_openai.AsyncOpenAI = FakeAsyncOpenAI
    monkeypatch.setitem(sys.modules, "openai", fake_openai)
    provider = OpenAICompatProvider(
        api_key=None,
        api_base="http://localhost:11434/v1",
        extra_body={"keep": True},
    )
    request = ModelRequest(model="m", messages=[{"role": "user", "content": "hi"}])

    events = [event async for event in provider.stream(request)]

    assert captured_init["api_key"] == "no-key"
    assert captured_init["base_url"] == "http://localhost:11434/v1"
    assert captured_kwargs["stream_options"] == {"include_usage": True}
    assert captured_kwargs["extra_body"] == {"keep": True}
    assert [event.kind for event in events] == [
        "text_delta",
        "tool_call_delta",
        "usage",
        "message_done",
    ]
    assert events[0].text == "hello"
    assert events[1].tool_call is not None
    assert events[2].usage == {"total_tokens": 7}
    assert events[-1].message is not None
    assert events[-1].message["tool_calls"][0]["function"]["name"] == "read_file"
