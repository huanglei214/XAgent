from pathlib import Path

from pydantic import BaseModel, Field

from xagent.coding.workspace import resolve_workspace_path
from xagent.foundation.tools import Tool, ToolContext, ToolResult


class ListFilesInput(BaseModel):
    path: str = Field(default=".", description="Directory to inspect, relative to the workspace root.")
    recursive: bool = Field(default=False, description="Whether to recurse into nested directories.")
    max_entries: int = Field(default=200, ge=1, le=1000, description="Maximum number of entries to return.")


async def _list_files(args: ListFilesInput, ctx: ToolContext) -> ToolResult:
    target = resolve_workspace_path(ctx.cwd, args.path)
    if not target.exists():
        return ToolResult(content=f"Path not found: {args.path}", is_error=True)
    if not target.is_dir():
        return ToolResult(content=f"Path is not a directory: {args.path}", is_error=True)

    root = Path(ctx.cwd).resolve()
    iterator = target.rglob("*") if args.recursive else target.iterdir()
    entries = []

    for item in iterator:
        relative = item.relative_to(root).as_posix()
        suffix = "/" if item.is_dir() else ""
        entries.append(relative + suffix)
        if len(entries) >= args.max_entries:
            break

    if not entries:
        return ToolResult(content=f"No files found under {args.path}")
    return ToolResult(content="\n".join(sorted(entries)))


list_files_tool = Tool(
    name="list_files",
    description="List files and directories inside the workspace.",
    input_model=ListFilesInput,
    handler=_list_files,
)
