from pydantic import BaseModel, Field

from xagent.coding.workspace import resolve_workspace_path
from xagent.foundation.tools import Tool, ToolContext, ToolResult


class WriteFileInput(BaseModel):
    path: str = Field(description="File path relative to the workspace root.")
    content: str = Field(description="Full file contents to write.")


async def _write_file(args: WriteFileInput, ctx: ToolContext) -> ToolResult:
    target = resolve_workspace_path(ctx.cwd, args.path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(args.content, encoding="utf-8")
    return ToolResult(content=f"Wrote {args.path}")


write_file_tool = Tool(
    name="write_file",
    description="Write full file contents inside the workspace.",
    input_model=WriteFileInput,
    handler=_write_file,
)
