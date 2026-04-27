from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from xagent.agent.paths import resolve_tool_path
from xagent.agent.tools.base import Tool, ToolContext, ToolResult


class ReadFileInput(BaseModel):
    path: str = Field(description="File path relative to the workspace root.")
    start_line: Optional[int] = Field(default=None, ge=1, description="First line to include.")
    end_line: Optional[int] = Field(default=None, ge=1, description="Last line to include.")


async def _read_file(args: ReadFileInput, ctx: ToolContext) -> ToolResult:
    """Read a file from the workspace, optionally with line ranges."""
    target = await resolve_tool_path(ctx, args.path, "read")
    if not target.exists():
        return ToolResult.fail(f"File not found: {args.path}", code="FILE_NOT_FOUND")
    if not target.is_file():
        return ToolResult.fail(f"Path is not a file: {args.path}", code="PATH_NOT_FILE")

    text = target.read_text(encoding="utf-8")
    lines = text.splitlines()

    start = (args.start_line - 1) if args.start_line else 0
    end = args.end_line if args.end_line else len(lines)
    if start < 0 or (args.start_line is not None and args.end_line is not None and args.end_line < args.start_line):
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


class WriteFileInput(BaseModel):
    path: str = Field(description="File path relative to the workspace root.")
    content: str = Field(description="Full file contents to write.")


async def _write_file(args: WriteFileInput, ctx: ToolContext) -> ToolResult:
    """Write full file contents inside the workspace."""
    target = await resolve_tool_path(ctx, args.path, "write")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(args.content, encoding="utf-8")
    return ToolResult.ok(
        f"Wrote {args.path}.",
        content=f"Wrote {args.path}",
        data={"path": args.path, "bytes": len(args.content.encode('utf-8'))},
    )


write_file_tool = Tool(
    name="write_file",
    description="Write full file contents inside the workspace.",
    input_model=WriteFileInput,
    handler=_write_file,
)


class StrReplaceInput(BaseModel):
    path: str = Field(description="File path relative to the workspace root.")
    old_text: str = Field(description="Exact text to replace.")
    new_text: str = Field(description="Replacement text.")
    count: int = Field(default=1, ge=1, description="Maximum number of matches to replace.")


async def _str_replace(args: StrReplaceInput, ctx: ToolContext) -> ToolResult:
    """Replace exact text inside one file in the workspace using a maximum replacement count."""
    target = await resolve_tool_path(ctx, args.path, "write")
    if not target.exists():
        return ToolResult.fail(f"File not found: {args.path}", code="FILE_NOT_FOUND")
    if not target.is_file():
        return ToolResult.fail(f"Path is not a file: {args.path}", code="PATH_NOT_FILE")

    text = target.read_text(encoding="utf-8")
    occurrences_found = text.count(args.old_text)
    if occurrences_found == 0:
        return ToolResult.fail("old_text was not found in the target file.", code="OLD_TEXT_NOT_FOUND")

    replacements = min(occurrences_found, args.count)
    updated = text.replace(args.old_text, args.new_text, args.count)

    target.write_text(updated, encoding="utf-8")
    return ToolResult.ok(
        f"Replaced text in {args.path} ({replacements} replacement{'s' if replacements != 1 else ''}).",
        content=f"Replaced text in {args.path} ({replacements} replacement{'s' if replacements != 1 else ''}).",
        data={
            "path": args.path,
            "occurrences_found": occurrences_found,
            "replacements": replacements,
            "count": args.count,
        },
    )


str_replace_tool = Tool(
    name="str_replace",
    description="Replace exact text inside one file in the workspace using a maximum replacement count.",
    input_model=StrReplaceInput,
    handler=_str_replace,
)


class FileInfoInput(BaseModel):
    path: str = Field(description="File or directory path relative to the workspace root.")


async def _file_info(args: FileInfoInput, ctx: ToolContext) -> ToolResult:
    """Show basic metadata for a file or directory in the workspace."""
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


class MkdirInput(BaseModel):
    path: str = Field(description="Directory path relative to the workspace root.")


async def _mkdir(args: MkdirInput, ctx: ToolContext) -> ToolResult:
    """Create a directory inside the workspace."""
    target = await resolve_tool_path(ctx, args.path, "write")
    target.mkdir(parents=True, exist_ok=True)
    return ToolResult.ok(
        f"Created directory {args.path}.",
        content=f"Created directory {args.path}",
        data={"path": args.path, "absolute_path": str(target)},
    )


mkdir_tool = Tool(
    name="mkdir",
    description="Create a directory inside the workspace.",
    input_model=MkdirInput,
    handler=_mkdir,
)


class MovePathInput(BaseModel):
    source: str = Field(description="Source path relative to the workspace root.")
    destination: str = Field(description="Destination path relative to the workspace root.")


async def _move_path(args: MovePathInput, ctx: ToolContext) -> ToolResult:
    """Move or rename a file or directory inside the workspace."""
    source = await resolve_tool_path(ctx, args.source, "write")
    destination = await resolve_tool_path(ctx, args.destination, "write")
    if not source.exists():
        return ToolResult.fail(f"Source path not found: {args.source}", code="SOURCE_NOT_FOUND")

    destination.parent.mkdir(parents=True, exist_ok=True)
    source.rename(destination)
    return ToolResult.ok(
        f"Moved {args.source} to {args.destination}.",
        content=f"Moved {args.source} to {args.destination}",
        data={"source": args.source, "destination": args.destination},
    )


move_path_tool = Tool(
    name="move_path",
    description="Move or rename a file or directory inside the workspace.",
    input_model=MovePathInput,
    handler=_move_path,
)
