from __future__ import annotations

import asyncio
import inspect
from time import perf_counter
from typing import Any

import httpx
import pytest

from xagent.agent.permissions import SessionApprover
from xagent.agent.tools import Tool, ToolRegistry, ToolResult, build_default_tools, tool
from xagent.agent.tools import registry as registry_module
from xagent.agent.tools.shell import ShellPolicy, ShellTool
from xagent.agent.tools.web import WebFetchTool, WebSearchTool
from xagent.config import TavilyWebConfig, WebPermissionConfig, WebToolsConfig


class TrackingApprover:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls: list[tuple[str, str, str]] = []

    async def require(self, action: str, target: str, *, summary: str = "") -> bool:
        self.calls.append((action, target, summary))
        return self.allowed


def test_tool_decorator_exports_openai_function_schema(tmp_path) -> None:
    registry = build_default_tools(
        workspace=tmp_path,
        approver=SessionApprover(default_allow=True),
        ask_user=lambda question: "answer",
    )

    read_schema = next(item for item in registry.openai_tools() if item["function"]["name"] == "read_file")

    assert read_schema["type"] == "function"
    assert read_schema["function"]["parameters"]["required"] == ["path"]


def test_default_tools_include_web_fetch_and_search_but_not_http_request(tmp_path) -> None:
    registry = build_default_tools(
        workspace=tmp_path,
        approver=SessionApprover(default_allow=True),
        ask_user=lambda question: "answer",
    )

    tool_names = {item["function"]["name"] for item in registry.openai_tools()}

    assert "web_fetch" in tool_names
    assert "web_search" in tool_names
    assert "http_request" not in tool_names


def test_web_tools_can_be_disabled_in_default_registry(tmp_path) -> None:
    registry = build_default_tools(
        workspace=tmp_path,
        approver=SessionApprover(default_allow=True),
        web_config=WebToolsConfig(enabled=False),
        ask_user=lambda question: "answer",
    )

    tool_names = {item["function"]["name"] for item in registry.openai_tools()}

    assert "web_fetch" not in tool_names
    assert "web_search" not in tool_names


def test_tool_import_surfaces_stay_stable() -> None:
    from xagent.agent.tools import build_default_tools as exported_build_default_tools
    from xagent.agent.tools.registry import ToolRegistry as RegistryOnly
    from xagent.agent.tools.shell import ShellPolicy as ExportedShellPolicy
    from xagent.agent.tools.shell import ShellTool as ExportedShellTool

    assert exported_build_default_tools is build_default_tools
    assert RegistryOnly is ToolRegistry
    assert ExportedShellPolicy is ShellPolicy
    assert ExportedShellTool is ShellTool


def test_registry_module_does_not_import_concrete_tools() -> None:
    source = inspect.getsource(registry_module)

    assert "xagent.agent.tools.files" not in source
    assert "xagent.agent.tools.search" not in source
    assert "xagent.agent.tools.shell" not in source
    assert "xagent.agent.tools.web" not in source
    assert "xagent.agent.tools.interaction" not in source


@pytest.mark.asyncio
async def test_read_and_patch_tools_use_injected_workspace_and_approver(tmp_path) -> None:
    (tmp_path / "note.txt").write_text("hello old", encoding="utf-8")
    registry = build_default_tools(
        workspace=tmp_path,
        approver=SessionApprover(default_allow=True),
        ask_user=lambda question: "answer",
    )

    read = registry.prepare(
        {
            "id": "call_read",
            "function": {"name": "read_file", "arguments": '{"path": "note.txt"}'},
        }
    )
    patch = registry.prepare(
        {
            "id": "call_patch",
            "function": {
                "name": "apply_patch",
                "arguments": '{"path": "note.txt", "old": "old", "new": "new"}',
            },
        }
    )

    read_result = await registry.execute(read)
    patch_result = await registry.execute(patch)

    assert "hello old" in read_result.result.content
    assert patch_result.result.is_error is False
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "hello new"


@pytest.mark.asyncio
async def test_argument_parse_error_returns_tool_error() -> None:
    registry = ToolRegistry()
    prepared = registry.prepare(
        {
            "id": "call_bad",
            "function": {"name": "missing", "arguments": "{not-json"},
        }
    )

    execution = await registry.execute(prepared)

    assert execution.result.is_error is True
    assert "Could not parse arguments" in execution.result.content


@tool(
    name="slow_read",
    description="Slow read-only tool.",
    read_only=True,
    parameters={"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]},
)
class SlowReadTool(Tool):
    def __init__(self, seen: list[str]) -> None:
        self.seen = seen

    async def execute(self, value: str) -> ToolResult:
        await asyncio.sleep(0.2)
        self.seen.append(value)
        return ToolResult.ok(value)


@tool(
    name="slow_write",
    description="Slow exclusive tool.",
    exclusive=True,
    parameters={"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]},
)
class SlowWriteTool(Tool):
    def __init__(self, seen: list[str]) -> None:
        self.seen = seen

    async def execute(self, value: str) -> ToolResult:
        await asyncio.sleep(0.2)
        self.seen.append(value)
        return ToolResult.ok(value)


@pytest.mark.asyncio
async def test_read_only_tools_parallelize_but_exclusive_tools_serialize() -> None:
    seen: list[str] = []
    registry = ToolRegistry()
    registry.register(SlowReadTool(seen))
    registry.register(SlowWriteTool(seen))
    calls: list[dict[str, Any]] = [
        {"id": "r1", "function": {"name": "slow_read", "arguments": '{"value": "a"}'}},
        {"id": "r2", "function": {"name": "slow_read", "arguments": '{"value": "b"}'}},
        {"id": "w1", "function": {"name": "slow_write", "arguments": '{"value": "c"}'}},
        {"id": "w2", "function": {"name": "slow_write", "arguments": '{"value": "d"}'}},
    ]

    started = perf_counter()
    await registry.execute_many([registry.prepare(call) for call in calls])
    elapsed = perf_counter() - started

    assert elapsed < 0.7
    assert seen[-2:] == ["c", "d"]


def test_shell_policy_allows_common_read_only_commands() -> None:
    policy = ShellPolicy()

    assert policy.match_blacklist("ls") is None
    assert policy.match_blacklist("pwd") is None
    assert policy.match_blacklist("rg foo") is None


@pytest.mark.parametrize(
    ("command", "rule"),
    [
        ("rm -rf tmp", "rm"),
        ("sudo ls", "sudo"),
        ("curl https://example.com", "curl"),
        ("npm install", "npm install"),
        ("uv pip install requests", "uv pip install"),
        ("echo hi > file", ">"),
    ],
)
def test_shell_policy_blocks_blacklisted_commands(command: str, rule: str) -> None:
    assert ShellPolicy().match_blacklist(command) == rule


def test_shell_policy_does_not_treat_quoted_argument_as_command() -> None:
    assert ShellPolicy().match_blacklist('echo "rm"') is None


@pytest.mark.asyncio
async def test_shell_tool_default_allow_does_not_call_approver(tmp_path) -> None:
    approver = TrackingApprover()
    tool = ShellTool(tmp_path, approver, shell_policy=ShellPolicy(default="allow"))

    result = await tool.execute("pwd")

    assert result.is_error is False
    assert str(tmp_path) in result.content
    assert approver.calls == []


@pytest.mark.asyncio
async def test_shell_tool_blacklist_returns_error_without_execution_or_approval(tmp_path) -> None:
    approver = TrackingApprover()
    tool = ShellTool(tmp_path, approver, shell_policy=ShellPolicy(default="allow"))

    result = await tool.execute("rm -rf tmp")

    assert result.is_error is True
    assert "blacklist rule: rm" in result.content
    assert approver.calls == []


@pytest.mark.asyncio
async def test_shell_tool_default_ask_keeps_approver_flow(tmp_path) -> None:
    approver = TrackingApprover()
    tool = ShellTool(tmp_path, approver, shell_policy=ShellPolicy(default="ask"))

    result = await tool.execute("pwd")

    assert result.is_error is False
    assert approver.calls == [("command", tmp_path.as_posix(), "pwd")]


@pytest.mark.asyncio
async def test_web_fetch_uses_jina_without_api_key_and_truncates() -> None:
    approver = TrackingApprover()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "data": {
                    "url": "https://example.com",
                    "title": "Example",
                    "content": "abcdef",
                }
            },
        )

    tool = WebFetchTool(
        approver,
        WebToolsConfig(),
        transport=httpx.MockTransport(handler),
    )

    result = await tool.execute("https://example.com", max_chars=3)

    assert result.is_error is False
    assert "backend: jina" in result.content
    assert "title: Example" in result.content
    assert "truncated: true" in result.content
    assert result.data["content"] == "abc"
    assert result.data["truncated"] is True
    assert seen[0].url == "https://r.jina.ai/https://example.com"
    assert "Authorization" not in seen[0].headers
    assert approver.calls == []


@pytest.mark.asyncio
async def test_web_fetch_sends_jina_api_key_when_configured() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"url": "https://example.com", "content": "ok"})

    config = WebToolsConfig()
    config.jina.api_key = "jina-key"
    tool = WebFetchTool(
        TrackingApprover(),
        config,
        transport=httpx.MockTransport(handler),
    )

    result = await tool.execute("https://example.com")

    assert result.is_error is False
    assert seen[0].headers["Authorization"] == "Bearer jina-key"


@pytest.mark.asyncio
async def test_web_fetch_http_error_returns_tool_error() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(429)

    tool = WebFetchTool(
        TrackingApprover(),
        WebToolsConfig(),
        transport=httpx.MockTransport(handler),
    )

    result = await tool.execute("https://example.com")

    assert result.is_error is True
    assert "web_fetch failed" in result.content
    assert "HTTPStatusError: 429" in result.content
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_web_fetch_falls_back_to_direct_get_when_jina_fails() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "r.jina.ai":
            return httpx.Response(504, request=request)
        return httpx.Response(
            200,
            headers={"content-type": "text/plain"},
            text="Wuhan: sunny, 25C",
            request=request,
        )

    tool = WebFetchTool(
        TrackingApprover(),
        WebToolsConfig(),
        transport=httpx.MockTransport(handler),
    )

    result = await tool.execute("https://weather.example/wuhan")

    assert result.is_error is False
    assert "backend: direct" in result.content
    assert "fallback_from: jina" in result.content
    assert "Wuhan: sunny, 25C" in result.content
    assert result.data["backend"] == "direct"
    assert result.data["fallback_from"] == "jina"
    assert seen[0].url == "https://r.jina.ai/https://weather.example/wuhan"
    assert seen[1].url == "https://weather.example/wuhan"
    assert "Mozilla/5.0" in seen[1].headers["User-Agent"]
    assert seen[1].headers["Accept-Language"].startswith("zh-CN")


@pytest.mark.asyncio
async def test_web_fetch_direct_get_formats_json_when_jina_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "r.jina.ai":
            return httpx.Response(504, request=request)
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"city": "武汉", "temp": 25},
            request=request,
        )

    tool = WebFetchTool(
        TrackingApprover(),
        WebToolsConfig(),
        transport=httpx.MockTransport(handler),
    )

    result = await tool.execute("https://weather.example/api/wuhan")

    assert result.is_error is False
    assert '"city": "武汉"' in result.content
    assert '"temp": 25' in result.content
    assert result.data["backend"] == "direct"


@pytest.mark.asyncio
async def test_web_fetch_direct_get_extracts_gb18030_html_when_jina_fails() -> None:
    html = (
        '<html><head><meta charset="gb2312"><title>武汉天气</title></head>'
        "<body><h1>今日天气</h1><script>ignore()</script><p>晴，25℃</p></body></html>"
    ).encode("gb18030")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "r.jina.ai":
            return httpx.Response(504, request=request)
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=html,
            request=request,
        )

    tool = WebFetchTool(
        TrackingApprover(),
        WebToolsConfig(),
        transport=httpx.MockTransport(handler),
    )

    result = await tool.execute("https://weather.example/wuhan")

    assert result.is_error is False
    assert "title: 武汉天气" in result.content
    assert "今日天气" in result.content
    assert "晴，25℃" in result.content
    assert "ignore()" not in result.content


@pytest.mark.asyncio
async def test_web_fetch_rejects_non_http_urls_before_network() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, text="should not happen", request=request)

    tool = WebFetchTool(
        TrackingApprover(),
        WebToolsConfig(),
        transport=httpx.MockTransport(handler),
    )

    result = await tool.execute("file:///etc/passwd")

    assert result.is_error is True
    assert "absolute http/https URLs" in result.content
    assert called is False


@pytest.mark.asyncio
async def test_web_fetch_direct_get_rejects_binary_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "r.jina.ai":
            return httpx.Response(504, request=request)
        return httpx.Response(
            200,
            headers={"content-type": "image/png"},
            content=b"\x89PNG\r\n\x1a\n",
            request=request,
        )

    tool = WebFetchTool(
        TrackingApprover(),
        WebToolsConfig(),
        transport=httpx.MockTransport(handler),
    )

    result = await tool.execute("https://example.com/image.png")

    assert result.is_error is True
    assert "Unsupported direct GET content type: image/png" in result.content


@pytest.mark.asyncio
async def test_web_fetch_direct_get_truncates_large_text_when_jina_fails() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "r.jina.ai":
            return httpx.Response(504, request=request)
        return httpx.Response(
            200,
            headers={"content-type": "text/plain; charset=utf-8"},
            text="abcdef",
            request=request,
        )

    tool = WebFetchTool(
        TrackingApprover(),
        WebToolsConfig(),
        transport=httpx.MockTransport(handler),
    )

    result = await tool.execute("https://example.com/large.txt", max_chars=3)

    assert result.is_error is False
    assert "truncated: true" in result.content
    assert result.data["content"] == "abc"
    assert result.data["truncated"] is True


@pytest.mark.asyncio
async def test_web_fetch_ask_denial_happens_before_network_request() -> None:
    called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, text="should not happen")

    tool = WebFetchTool(
        TrackingApprover(allowed=False),
        WebToolsConfig(),
        WebPermissionConfig(default="ask"),
        transport=httpx.MockTransport(handler),
    )

    result = await tool.execute("https://example.com")

    assert result.is_error is True
    assert "Denied web request" in result.content
    assert called is False


@pytest.mark.asyncio
async def test_web_fetch_deny_policy_skips_approver_and_network() -> None:
    called = False
    approver = TrackingApprover()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, text="should not happen")

    tool = WebFetchTool(
        approver,
        WebToolsConfig(),
        WebPermissionConfig(default="deny"),
        transport=httpx.MockTransport(handler),
    )

    result = await tool.execute("https://example.com")

    assert result.is_error is True
    assert "permissions.web.default=deny" in result.content
    assert approver.calls == []
    assert called is False


@pytest.mark.asyncio
async def test_web_search_prefers_tavily_when_api_key_is_configured() -> None:
    calls: list[tuple[str, str, int]] = []

    class FakeTavily:
        def __init__(self, api_key: str) -> None:
            self.api_key = api_key

        def search(self, *, query: str, max_results: int) -> dict[str, Any]:
            calls.append((self.api_key, query, max_results))
            return {
                "results": [
                    {
                        "title": "Tavily Result",
                        "url": "https://example.com/tavily",
                        "content": "from tavily",
                        "score": 0.9,
                    }
                ]
            }

    config = WebToolsConfig(tavily=TavilyWebConfig(api_key="tvly-key"))
    approver = TrackingApprover()
    tool = WebSearchTool(
        approver,
        config,
        tavily_client_factory=FakeTavily,
    )

    result = await tool.execute("xagent web tools", max_results=3)

    assert result.is_error is False
    assert "backend: tavily" in result.content
    assert "Tavily Result" in result.content
    assert result.data["backend"] == "tavily"
    assert calls == [("tvly-key", "xagent web tools", 3)]
    assert approver.calls == []


@pytest.mark.asyncio
async def test_web_search_falls_back_to_duckduckgo_without_tavily_key() -> None:
    calls: list[tuple[float, str, int, str]] = []

    class FakeDDGS:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        def text(self, query: str, *, max_results: int, backend: str) -> list[dict[str, str]]:
            calls.append((self.timeout, query, max_results, backend))
            return [
                {
                    "title": "DDG Result",
                    "href": "https://example.com/ddg",
                    "body": "from duckduckgo",
                }
            ]

    approver = TrackingApprover()
    tool = WebSearchTool(
        approver,
        WebToolsConfig(timeout_seconds=9),
        ddgs_factory=FakeDDGS,
    )

    result = await tool.execute("xagent web tools", max_results=2)

    assert result.is_error is False
    assert "backend: duckduckgo" in result.content
    assert "DDG Result" in result.content
    assert result.data["backend"] == "duckduckgo"
    assert calls == [(9, "xagent web tools", 2, "duckduckgo")]
    assert approver.calls == []


@pytest.mark.asyncio
async def test_web_search_ask_policy_calls_approver_before_searching() -> None:
    calls: list[str] = []

    class FakeDDGS:
        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        def text(self, query: str, *, max_results: int, backend: str) -> list[dict[str, str]]:
            calls.append(query)
            return []

    approver = TrackingApprover()
    tool = WebSearchTool(
        approver,
        WebToolsConfig(),
        WebPermissionConfig(default="ask"),
        ddgs_factory=FakeDDGS,
    )

    result = await tool.execute("xagent web tools")

    assert result.is_error is False
    assert calls == ["xagent web tools"]
    assert approver.calls == [
        ("web", "web_search:xagent web tools", "web_search via duckduckgo")
    ]


@pytest.mark.asyncio
async def test_web_search_without_backend_returns_error_without_approval() -> None:
    config = WebToolsConfig(search_backend="tavily")
    approver = TrackingApprover()
    tool = WebSearchTool(approver, config)

    result = await tool.execute("xagent web tools")

    assert result.is_error is True
    assert "No web search backend" in result.content
    assert approver.calls == []
