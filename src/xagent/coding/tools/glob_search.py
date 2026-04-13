from pathlib import Path

from pydantic import BaseModel, Field

from xagent.coding.workspace import resolve_workspace_path
from xagent.foundation.tools import Tool, ToolContext, ToolResult


class GlobSearchInput(BaseModel):
    pattern: str = Field(description="Glob pattern such as '**/*.py'.")
    path: str = Field(default=".", description="Directory to search from, relative to the workspace root.")
    limit: int = Field(default=100, ge=1, le=1000, description="Maximum number of matches.")


async def _glob_search(args: GlobSearchInput, ctx: ToolContext) -> ToolResult:
    root = Path(ctx.cwd).resolve()
    target = resolve_workspace_path(ctx.cwd, args.path)
    if not target.exists():
        return ToolResult(content=f"Path not found: {args.path}", is_error=True)

    matches = []
    for item in target.glob(args.pattern):
        matches.append(item.resolve().relative_to(root).as_posix())
        if len(matches) >= args.limit:
            break

    if not matches:
        return ToolResult(content=f"No matches for pattern '{args.pattern}' under {args.path}")
    return ToolResult(content="\n".join(matches))


glob_search_tool = Tool(
    name="glob_search",
    description="Search the workspace using a glob pattern.",
    input_model=GlobSearchInput,
    handler=_glob_search,
)
