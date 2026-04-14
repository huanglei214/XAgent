from pathlib import Path

from pydantic import BaseModel, Field

from xagent.coding.tools._ignore import is_visible_path
from xagent.coding.workspace import resolve_tool_path
from xagent.foundation.tools import Tool, ToolContext, ToolResult


class GlobSearchInput(BaseModel):
    pattern: str = Field(description="Glob pattern such as '**/*.py'.")
    path: str = Field(default=".", description="Directory to search from, relative to the workspace root.")
    limit: int = Field(default=100, ge=1, le=1000, description="Maximum number of matches.")


async def _glob_search(args: GlobSearchInput, ctx: ToolContext) -> ToolResult:
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
