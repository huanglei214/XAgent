from pydantic import BaseModel, Field

from xagent.coding.workspace import resolve_tool_path
from xagent.foundation.tools import Tool, ToolContext, ToolResult


class MkdirInput(BaseModel):
    path: str = Field(description="Directory path relative to the workspace root.")


async def _mkdir(args: MkdirInput, ctx: ToolContext) -> ToolResult:
    target = await resolve_tool_path(ctx, args.path, "write")
    target.mkdir(parents=True, exist_ok=True)
    return ToolResult(content=f"Created directory {args.path}")


mkdir_tool = Tool(
    name="mkdir",
    description="Create a directory inside the workspace.",
    input_model=MkdirInput,
    handler=_mkdir,
)
