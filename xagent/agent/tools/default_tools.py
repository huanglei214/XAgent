from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from xagent.agent.permissions import Approver
from xagent.agent.tools.files import ApplyPatchTool, ReadFileTool
from xagent.agent.tools.interaction import AskUserTool
from xagent.agent.tools.registry import ToolRegistry
from xagent.agent.tools.search import SearchTool
from xagent.agent.tools.shell import ShellPolicy, ShellTool
from xagent.agent.tools.web import HttpRequestTool


def build_default_tools(
    *,
    workspace: Path,
    approver: Approver,
    shell_policy: ShellPolicy | None = None,
    ask_user: Callable[[str], str] | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ReadFileTool(workspace))
    registry.register(SearchTool(workspace))
    registry.register(ApplyPatchTool(workspace, approver))
    registry.register(ShellTool(workspace, approver, shell_policy=shell_policy))
    registry.register(AskUserTool(ask_user or (lambda question: input(question + " "))))
    registry.register(HttpRequestTool(approver))
    return registry
