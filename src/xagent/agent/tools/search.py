from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

from xagent.agent.paths import resolve_tool_path
from xagent.agent.tools.base import Tool, ToolContext, ToolResult
from xagent.agent.tools.ignore import is_visible_path, iter_visible_entries


class ListFilesInput(BaseModel):
    path: str = Field(default=".", description="Directory to inspect, relative to the workspace root.")
    recursive: bool = Field(default=False, description="Whether to recurse into nested directories.")
    max_entries: int = Field(default=200, ge=1, le=1000, description="Maximum number of entries to return.")


async def _list_files(args: ListFilesInput, ctx: ToolContext) -> ToolResult:
    """List files and directories inside the workspace."""
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


class GlobSearchInput(BaseModel):
    pattern: str = Field(description="Glob pattern such as '**/*.py'.")
    path: str = Field(default=".", description="Directory to search from, relative to the workspace root.")
    limit: int = Field(default=100, ge=1, le=1000, description="Maximum number of matches.")


async def _glob_search(args: GlobSearchInput, ctx: ToolContext) -> ToolResult:
    """Search the workspace using a glob pattern."""
    root = Path(ctx.cwd).resolve()
    target = await resolve_tool_path(ctx, args.path, "read")
    if not target.exists():
        return ToolResult.fail(f"Path not found: {args.path}", code="PATH_NOT_FOUND")

    matches = []
    for item in target.glob(args.pattern):
        if not is_visible_path(item, target):
            continue
        matches.append(item.resolve().relative_to(root).as_posix())
        if len(matches) >= args.limit:
            break

    if not matches:
        return ToolResult.ok(
            f"No matches for pattern '{args.pattern}' under {args.path}.",
            content=f"No matches for pattern '{args.pattern}' under {args.path}",
            data={"matches": [], "truncated": False},
        )
    return ToolResult.ok(
        f"Found {len(matches)} path(s) for pattern '{args.pattern}'.",
        content="\n".join(matches),
        data={"matches": matches, "truncated": len(matches) >= args.limit},
    )


glob_search_tool = Tool(
    name="glob_search",
    description="Search the workspace using a glob pattern.",
    input_model=GlobSearchInput,
    handler=_glob_search,
)


class GrepSearchInput(BaseModel):
    pattern: str = Field(description="Text or regular expression to search for.")
    path: str = Field(default=".", description="Directory or file to search from, relative to the workspace root.")
    case_sensitive: bool = Field(default=False, description="Whether the match is case sensitive.")
    limit: int = Field(default=50, ge=1, le=500, description="Maximum number of matching lines to return.")


async def _grep_search(args: GrepSearchInput, ctx: ToolContext) -> ToolResult:
    """Search file contents in the workspace using a regex or plain text pattern."""
    root = Path(ctx.cwd).resolve()
    target = await resolve_tool_path(ctx, args.path, "read")
    if not target.exists():
        return ToolResult.fail(f"Path not found: {args.path}", code="PATH_NOT_FOUND")

    flags = 0 if args.case_sensitive else re.IGNORECASE
    try:
        pattern = re.compile(args.pattern, flags)
    except re.error as exc:
        return ToolResult.fail(f"Invalid regex pattern: {exc}", code="INVALID_REGEX")

    if target.is_file():
        files = [target]
    else:
        files = [item for item in iter_visible_entries(target, recursive=True) if item.is_file()]
    matches = []

    for file_path in files:
        try:
            text = file_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        for line_number, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                rel = file_path.resolve().relative_to(root).as_posix()
                matches.append(f"{rel}:{line_number}: {line}")
                if len(matches) >= args.limit:
                    return ToolResult.ok(
                        f"Found {len(matches)} matches for pattern '{args.pattern}'.",
                        content="\n".join(matches),
                        data={"matches": matches, "truncated": True},
                    )

    if not matches:
        return ToolResult.ok(
            f"No matches for pattern '{args.pattern}' under {args.path}.",
            content=f"No matches for pattern '{args.pattern}' under {args.path}",
            data={"matches": [], "truncated": False},
        )
    return ToolResult.ok(
        f"Found {len(matches)} matches for pattern '{args.pattern}'.",
        content="\n".join(matches),
        data={"matches": matches, "truncated": False},
    )


grep_search_tool = Tool(
    name="grep_search",
    description="Search file contents in the workspace using a regex or plain text pattern.",
    input_model=GrepSearchInput,
    handler=_grep_search,
)
