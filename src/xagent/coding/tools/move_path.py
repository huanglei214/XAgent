from pydantic import BaseModel, Field

from xagent.coding.workspace import resolve_workspace_path
from xagent.foundation.tools import Tool, ToolContext, ToolResult


class MovePathInput(BaseModel):
    source: str = Field(description="Source path relative to the workspace root.")
    destination: str = Field(description="Destination path relative to the workspace root.")


async def _move_path(args: MovePathInput, ctx: ToolContext) -> ToolResult:
    source = resolve_workspace_path(ctx.cwd, args.source)
    destination = resolve_workspace_path(ctx.cwd, args.destination)
    if not source.exists():
        return ToolResult(content=f"Source path not found: {args.source}", is_error=True)

    destination.parent.mkdir(parents=True, exist_ok=True)
    source.rename(destination)
    return ToolResult(content=f"Moved {args.source} to {args.destination}")


move_path_tool = Tool(
    name="move_path",
    description="Move or rename a file or directory inside the workspace.",
    input_model=MovePathInput,
    handler=_move_path,
)
