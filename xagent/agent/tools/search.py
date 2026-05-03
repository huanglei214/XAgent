from __future__ import annotations

from pathlib import Path

from xagent.agent.tools.base import Tool, ToolResult, tool


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
