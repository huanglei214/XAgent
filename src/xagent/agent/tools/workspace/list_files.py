from pathlib import Path

from pydantic import BaseModel, Field

from xagent.agent.tools.workspace._ignore import iter_visible_entries
from xagent.foundation.runtime.workspace_paths import resolve_tool_path
from xagent.foundation.tools import Tool, ToolContext, ToolResult


class ListFilesInput(BaseModel):
    path: str = Field(default=".", description="Directory to inspect, relative to the workspace root.")
    recursive: bool = Field(default=False, description="Whether to recurse into nested directories.")
    max_entries: int = Field(default=200, ge=1, le=1000, description="Maximum number of entries to return.")


async def _list_files(args: ListFilesInput, ctx: ToolContext) -> ToolResult:
    target = await resolve_tool_path(ctx, args.path, "read")
    if not target.exists():
        return ToolResult.fail(f"Path not found: {args.path}", code="PATH_NOT_FOUND")
    if not target.is_dir():
        return ToolResult.fail(f"Path is not a directory: {args.path}", code="PATH_NOT_DIRECTORY")

    root = Path(ctx.cwd).resolve()
    entries = []

    for item in iter_visible_entries(target, recursive=args.recursive):
        relative = item.relative_to(root).as_posix()
        suffix = "/" if item.is_dir() else ""
        entries.append(relative + suffix)
        if len(entries) >= args.max_entries:
            break

    if not entries:
        return ToolResult.ok(
            f"No files found under {args.path}.",
            content=f"No files found under {args.path}",
            data={"entries": [], "truncated": False},
        )
    sorted_entries = sorted(entries)
    return ToolResult.ok(
        f"Found {len(sorted_entries)} entries under {args.path}.",
        content="\n".join(sorted_entries),
        data={"entries": sorted_entries, "truncated": len(sorted_entries) >= args.max_entries},
    )


list_files_tool = Tool(
    name="list_files",
    description="List files and directories inside the workspace.",
    input_model=ListFilesInput,
    handler=_list_files,
)
