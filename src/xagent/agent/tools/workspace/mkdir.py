from pydantic import BaseModel, Field

from xagent.foundation.runtime.workspace_paths import resolve_tool_path
from xagent.foundation.tools import Tool, ToolContext, ToolResult


class MkdirInput(BaseModel):
    path: str = Field(description="Directory path relative to the workspace root.")


async def _mkdir(args: MkdirInput, ctx: ToolContext) -> ToolResult:
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
