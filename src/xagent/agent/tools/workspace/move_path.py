from pydantic import BaseModel, Field

from xagent.foundation.runtime.workspace_paths import resolve_tool_path
from xagent.foundation.tools import Tool, ToolContext, ToolResult


class MovePathInput(BaseModel):
    source: str = Field(description="Source path relative to the workspace root.")
    destination: str = Field(description="Destination path relative to the workspace root.")


async def _move_path(args: MovePathInput, ctx: ToolContext) -> ToolResult:
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
