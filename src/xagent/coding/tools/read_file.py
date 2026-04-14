from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from xagent.coding.workspace import resolve_tool_path
from xagent.foundation.tools import Tool, ToolContext, ToolResult


class ReadFileInput(BaseModel):
    path: str = Field(description="File path relative to the workspace root.")
    start_line: Optional[int] = Field(default=None, ge=1, description="First line to include.")
    end_line: Optional[int] = Field(default=None, ge=1, description="Last line to include.")


async def _read_file(args: ReadFileInput, ctx: ToolContext) -> ToolResult:
    target = await resolve_tool_path(ctx, args.path, "read")
    if not target.exists():
        return ToolResult.fail(f"File not found: {args.path}", code="FILE_NOT_FOUND")
    if not target.is_file():
        return ToolResult.fail(f"Path is not a file: {args.path}", code="PATH_NOT_FILE")

    text = target.read_text(encoding="utf-8")
    lines = text.splitlines()

    start = (args.start_line - 1) if args.start_line else 0
    end = args.end_line if args.end_line else len(lines)
    if start < 0 or end < start:
        return ToolResult.fail("Invalid line range requested.", code="INVALID_LINE_RANGE")

    selected = lines[start:end]
    numbered = [f"{index + start + 1:>4}: {line}" for index, line in enumerate(selected)]
    return ToolResult.ok(
        f"Read {len(selected)} line(s) from {args.path}.",
        content="\n".join(numbered) if numbered else "",
        data={"path": args.path, "start_line": args.start_line, "end_line": args.end_line, "line_count": len(selected)},
    )


read_file_tool = Tool(
    name="read_file",
    description="Read a file from the workspace, optionally with line ranges.",
    input_model=ReadFileInput,
    handler=_read_file,
)
