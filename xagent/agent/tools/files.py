from __future__ import annotations

from pathlib import Path

from xagent.agent.permissions import Approver
from xagent.agent.tools.base import Tool, ToolResult, tool
from xagent.agent.tools.paths import resolve_under


@tool(
    name="read_file",
    description="Read a text file from the workspace.",
    read_only=True,
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Path relative to the workspace."},
            "offset": {"type": "integer", "description": "1-based line offset.", "default": 1},
            "limit": {"type": "integer", "description": "Maximum number of lines.", "default": 2000},
        },
        "required": ["path"],
    },
)
class ReadFileTool(Tool):
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    async def execute(self, path: str, offset: int = 1, limit: int = 2000) -> ToolResult:
        target = resolve_under(self.workspace, path)
        lines = target.read_text(encoding="utf-8").splitlines()
        start = max(offset - 1, 0)
        selected = lines[start : start + max(limit, 0)]
        numbered = [f"{start + idx + 1}: {line}" for idx, line in enumerate(selected)]
        return ToolResult.ok("\n".join(numbered) if numbered else "")


@tool(
    name="apply_patch",
    description="Replace text in a workspace file.",
    exclusive=True,
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old": {"type": "string", "description": "Existing text to replace."},
            "new": {"type": "string", "description": "Replacement text."},
            "replace_all": {"type": "boolean", "default": False},
        },
        "required": ["path", "old", "new"],
    },
)
class ApplyPatchTool(Tool):
    def __init__(self, workspace: Path, approver: Approver) -> None:
        self.workspace = workspace
        self.approver = approver

    async def execute(
        self,
        path: str,
        old: str,
        new: str,
        replace_all: bool = False,
    ) -> ToolResult:
        target = resolve_under(self.workspace, path)
        await _require(self.approver, "file_write", target.as_posix(), summary=f"Replace text in {path}")
        text = target.read_text(encoding="utf-8") if target.exists() else ""
        if old not in text:
            return ToolResult.fail(f"Could not find text to replace in {path}.")
        count = -1 if replace_all else 1
        target.write_text(text.replace(old, new, count), encoding="utf-8")
        return ToolResult.ok(f"Updated {path}.")


async def _require(approver: Approver, action: str, target: str, *, summary: str) -> None:
    allowed = await approver.require(action, target, summary=summary)
    if not allowed:
        raise PermissionError(f"Denied {action} for {target}")
