from __future__ import annotations

from pathlib import Path
from typing import Set

from xagent.agent.core.middleware import AgentMiddleware
from xagent.foundation.messages import ToolResultPart, ToolUsePart
from xagent.foundation.runtime.workspace_paths import resolve_workspace_path


FILE_INSPECTION_TOOLS = {"read_file", "file_info"}
PATH_DISCOVERY_TOOLS = {"list_files", "glob_search", "grep_search"}
GUARDED_WRITE_TOOLS = {"write_file", "str_replace", "apply_patch", "move_path"}


class EditGuardrailsMiddleware(AgentMiddleware):
    def __init__(self) -> None:
        self.inspected_paths: Set[str] = set()
        self.discovered_paths: Set[str] = set()

    async def before_tool(self, *, agent, tool_use: ToolUsePart) -> ToolResultPart | None:
        if tool_use.name not in GUARDED_WRITE_TOOLS:
            return None

        try:
            if tool_use.name == "move_path":
                source = self._resolve(agent.cwd, tool_use.input.get("source"))
                if source.exists() and not self._has_seen(source):
                    return self._deny(
                        tool_use,
                        f"Read or inspect {tool_use.input.get('source')} before moving it. "
                        "Use read_file or file_info first.",
                    )
                return None

            path = self._resolve(agent.cwd, tool_use.input.get("path"))
        except Exception as exc:
            return self._deny(tool_use, str(exc))

        if tool_use.name == "write_file" and not path.exists():
            return None

        if path.exists() and not self._has_seen(path):
            return self._deny(
                tool_use,
                f"Inspect {tool_use.input.get('path')} before modifying it. "
                "Use read_file or file_info first.",
            )
        return None

    async def after_tool(self, *, agent, tool_use: ToolUsePart, result: ToolResultPart) -> None:
        if result.is_error:
            return

        if tool_use.name in FILE_INSPECTION_TOOLS:
            try:
                path = self._resolve(agent.cwd, tool_use.input.get("path"))
            except Exception:
                return
            self.inspected_paths.add(str(path))
            return

        if tool_use.name in PATH_DISCOVERY_TOOLS:
            try:
                path = self._resolve(agent.cwd, tool_use.input.get("path", "."))
            except Exception:
                return
            self.discovered_paths.add(str(path))

    def _has_seen(self, path: Path) -> bool:
        key = str(path)
        if key in self.inspected_paths:
            return True
        return str(path.parent) in self.discovered_paths

    def _resolve(self, cwd: str, raw_path) -> Path:
        if not isinstance(raw_path, str) or not raw_path:
            raise ValueError("Tool input is missing a valid path.")
        return resolve_workspace_path(cwd, raw_path)

    def _deny(self, tool_use: ToolUsePart, message: str) -> ToolResultPart:
        return ToolResultPart(
            tool_use_id=tool_use.id,
            content=message,
            is_error=True,
        )
