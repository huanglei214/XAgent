from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from xagent.agent.interactions import InteractionContext
from xagent.agent.permissions import Approver
from xagent.agent.tools.cron import CronTool
from xagent.agent.tools.files import ApplyPatchTool, ReadFileTool
from xagent.agent.tools.interaction import AskUserTool
from xagent.agent.tools.registry import ToolRegistry
from xagent.agent.tools.search import SearchTool
from xagent.agent.tools.shell import ShellPolicy, ShellTool
from xagent.agent.tools.web import WebFetchTool, WebSearchTool
from xagent.config import CronPermissionConfig, WebPermissionConfig, WebToolsConfig
from xagent.cron import CronService


def build_default_tools(
    *,
    workspace: Path,
    approver: Approver,
    shell_policy: ShellPolicy | None = None,
    web_config: WebToolsConfig | None = None,
    web_permission: WebPermissionConfig | None = None,
    cron_service: CronService | None = None,
    cron_permission: CronPermissionConfig | None = None,
    current_context: Callable[[], InteractionContext] | None = None,
    ask_user: Callable[[str], str | Awaitable[str]] | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ReadFileTool(workspace))
    registry.register(SearchTool(workspace))
    registry.register(ApplyPatchTool(workspace, approver))
    registry.register(ShellTool(workspace, approver, shell_policy=shell_policy))
    registry.register(AskUserTool(ask_user or (lambda question: input(question + " "))))
    active_web_config = web_config or WebToolsConfig()
    active_web_permission = web_permission or WebPermissionConfig()
    if active_web_config.enabled:
        registry.register(WebFetchTool(approver, active_web_config, active_web_permission))
        registry.register(WebSearchTool(approver, active_web_config, active_web_permission))
    if cron_service is not None:
        registry.register(
            CronTool(
                service=cron_service,
                approver=approver,
                permission=cron_permission or CronPermissionConfig(),
                current_context=current_context or _missing_context,
            )
        )
    return registry


def _missing_context() -> Any:
    raise RuntimeError("No active interaction context is available.")
