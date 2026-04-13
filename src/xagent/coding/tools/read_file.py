from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from xagent.coding.workspace import resolve_workspace_path
from xagent.foundation.tools import Tool, ToolContext, ToolResult


class ReadFileInput(BaseModel):
    path: str = Field(description="File path relative to the workspace root.")
    start_line: Optional[int] = Field(default=None, ge=1, description="First line to include.")
    end_line: Optional[int] = Field(default=None, ge=1, description="Last line to include.")


async def _read_file(args: ReadFileInput, ctx: ToolContext) -> ToolResult:
    target = resolve_workspace_path(ctx.cwd, args.path)
    if not target.exists():
        return ToolResult(content=f"File not found: {args.path}", is_error=True)
    if not target.is_file():
        return ToolResult(content=f"Path is not a file: {args.path}", is_error=True)

    text = target.read_text(encoding="utf-8")
    lines = text.splitlines()

    start = (args.start_line - 1) if args.start_line else 0
    end = args.end_line if args.end_line else len(lines)
    if start < 0 or end < start:
        return ToolResult(content="Invalid line range requested.", is_error=True)

    selected = lines[start:end]
    numbered = [f"{index + start + 1:>4}: {line}" for index, line in enumerate(selected)]
    return ToolResult(content="\n".join(numbered) if numbered else "")


read_file_tool = Tool(
    name="read_file",
    description="Read a file from the workspace, optionally with line ranges.",
    input_model=ReadFileInput,
    handler=_read_file,
)
