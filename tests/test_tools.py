from __future__ import annotations

import asyncio
import inspect
from time import perf_counter
from typing import Any

import pytest

from xagent.agent.permissions import SessionApprover
from xagent.agent.tools import Tool, ToolRegistry, ToolResult, build_default_tools, tool
from xagent.agent.tools import registry as registry_module
from xagent.agent.tools.shell import ShellPolicy, ShellTool


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
