from datetime import datetime

from pydantic import BaseModel, Field

from xagent.coding.workspace import resolve_tool_path
from xagent.foundation.tools import Tool, ToolContext, ToolResult


class FileInfoInput(BaseModel):
    path: str = Field(description="File or directory path relative to the workspace root.")


async def _file_info(args: FileInfoInput, ctx: ToolContext) -> ToolResult:
    target = await resolve_tool_path(ctx, args.path, "read")
    if not target.exists():
        return ToolResult.fail(f"Path not found: {args.path}", code="PATH_NOT_FOUND")

    stat = target.stat()
    lines = [
        f"path: {args.path}",
        f"type: {'directory' if target.is_dir() else 'file'}",
        f"size_bytes: {stat.st_size}",
        f"modified_at: {datetime.fromtimestamp(stat.st_mtime).isoformat()}",
        f"absolute_path: {target}",
    ]
    return ToolResult.ok(
        f"Loaded file info for {args.path}.",
        content="\n".join(lines),
        data={
            "path": args.path,
            "type": "directory" if target.is_dir() else "file",
            "size_bytes": stat.st_size,
            "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            "absolute_path": str(target),
        },
    )


file_info_tool = Tool(
    name="file_info",
    description="Show basic metadata for a file or directory in the workspace.",
    input_model=FileInfoInput,
    handler=_file_info,
)
