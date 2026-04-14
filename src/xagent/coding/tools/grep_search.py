from pathlib import Path
import re

from pydantic import BaseModel, Field

from xagent.coding.tools._ignore import iter_visible_entries
from xagent.coding.workspace import resolve_tool_path
from xagent.foundation.tools import Tool, ToolContext, ToolResult


class GrepSearchInput(BaseModel):
    pattern: str = Field(description="Text or regular expression to search for.")
    path: str = Field(default=".", description="Directory or file to search from, relative to the workspace root.")
    case_sensitive: bool = Field(default=False, description="Whether the match is case sensitive.")
    limit: int = Field(default=50, ge=1, le=500, description="Maximum number of matching lines to return.")


async def _grep_search(args: GrepSearchInput, ctx: ToolContext) -> ToolResult:
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
