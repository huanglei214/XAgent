from __future__ import annotations

import asyncio
import urllib.request
from collections.abc import Callable
from pathlib import Path

from xagent.agent.permissions import Approver
from xagent.agent.tools.base import Tool, ToolResult, tool
from xagent.agent.tools.fs import resolve_under
from xagent.agent.tools.registry import ToolRegistry


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
    name="search",
    description="Search file names and text content in the workspace.",
    read_only=True,
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Text to search for."},
            "max_results": {"type": "integer", "default": 50},
        },
        "required": ["query"],
    },
)
class SearchTool(Tool):
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    async def execute(self, query: str, max_results: int = 50) -> ToolResult:
        matches: list[str] = []
        for path in self.workspace.rglob("*"):
            if len(matches) >= max_results:
                break
            if not path.is_file():
                continue
            rel = path.relative_to(self.workspace).as_posix()
            if query.lower() in rel.lower():
                matches.append(f"{rel}: filename match")
                continue
            try:
                for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                    if query.lower() in line.lower():
                        matches.append(f"{rel}:{lineno}: {line}")
                        break
            except UnicodeDecodeError:
                continue
        return ToolResult.ok("\n".join(matches) if matches else "No matches.")


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


@tool(
    name="shell",
    description="Run a shell command in the workspace.",
    exclusive=True,
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "timeout_seconds": {"type": "integer", "default": 60},
        },
        "required": ["command"],
    },
)
class ShellTool(Tool):
    def __init__(self, cwd: Path, approver: Approver) -> None:
        self.cwd = cwd
        self.approver = approver

    async def execute(self, command: str, timeout_seconds: int = 60) -> ToolResult:
        await _require(self.approver, "command", self.cwd.as_posix(), summary=command)
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=self.cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            return ToolResult.fail(f"Command timed out after {timeout_seconds}s.")
        content = (
            f"exit_code={process.returncode}\n"
            f"stdout:\n{stdout.decode(errors='replace')}\n"
            f"stderr:\n{stderr.decode(errors='replace')}"
        )
        return ToolResult(content=content, is_error=process.returncode != 0)


@tool(
    name="ask_user",
    description="Ask the user a short clarification question.",
    exclusive=True,
    parameters={
        "type": "object",
        "properties": {"question": {"type": "string"}},
        "required": ["question"],
    },
)
class AskUserTool(Tool):
    def __init__(self, ask: Callable[[str], str]) -> None:
        self.ask = ask

    async def execute(self, question: str) -> ToolResult:
        return ToolResult.ok(self.ask(question))


@tool(
    name="http_request",
    description="Make a basic HTTP request.",
    exclusive=True,
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "method": {"type": "string", "default": "GET"},
            "body": {"type": ["string", "null"], "default": None},
            "timeout_seconds": {"type": "integer", "default": 20},
        },
        "required": ["url"],
    },
)
class HttpRequestTool(Tool):
    def __init__(self, approver: Approver) -> None:
        self.approver = approver

    async def execute(
        self,
        url: str,
        method: str = "GET",
        body: str | None = None,
        timeout_seconds: int = 20,
    ) -> ToolResult:
        await _require(self.approver, "network", url, summary=f"{method.upper()} {url}")

        def run() -> str:
            data = body.encode("utf-8") if body is not None else None
            request = urllib.request.Request(url, method=method.upper(), data=data)
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return response.read().decode("utf-8", errors="replace")

        return ToolResult.ok(await asyncio.to_thread(run))


async def _require(approver: Approver, action: str, target: str, *, summary: str) -> None:
    allowed = await approver.require(action, target, summary=summary)
    if not allowed:
        raise PermissionError(f"Denied {action} for {target}")


def build_default_tools(
    *,
    workspace: Path,
    approver: Approver,
    ask_user: Callable[[str], str] | None = None,
) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ReadFileTool(workspace))
    registry.register(SearchTool(workspace))
    registry.register(ApplyPatchTool(workspace, approver))
    registry.register(ShellTool(workspace, approver))
    registry.register(AskUserTool(ask_user or (lambda question: input(question + " "))))
    registry.register(HttpRequestTool(approver))
    return registry
