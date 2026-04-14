from pydantic import BaseModel, Field

from xagent.coding.workspace import resolve_tool_path
from xagent.foundation.tools import Tool, ToolContext, ToolResult


class ApplyPatchInput(BaseModel):
    path: str = Field(description="File path relative to the workspace root.")
    old_text: str = Field(description="Exact text to replace.")
    new_text: str = Field(description="Replacement text.")
    replace_all: bool = Field(default=False, description="Replace all matches instead of the first match.")


async def _apply_patch(args: ApplyPatchInput, ctx: ToolContext) -> ToolResult:
    target = await resolve_tool_path(ctx, args.path, "write")
    if not target.exists():
        return ToolResult.fail(f"File not found: {args.path}", code="FILE_NOT_FOUND")
    if not target.is_file():
        return ToolResult.fail(f"Path is not a file: {args.path}", code="PATH_NOT_FILE")

    text = target.read_text(encoding="utf-8")
    if args.old_text not in text:
        return ToolResult.fail("old_text was not found in the target file.", code="OLD_TEXT_NOT_FOUND")

    if args.replace_all:
        updated = text.replace(args.old_text, args.new_text)
        replacements = text.count(args.old_text)
    else:
        updated = text.replace(args.old_text, args.new_text, 1)
        replacements = 1

    target.write_text(updated, encoding="utf-8")
    return ToolResult.ok(
        f"Applied patch to {args.path} ({replacements} replacement{'s' if replacements != 1 else ''}).",
        content=f"Applied patch to {args.path} ({replacements} replacement{'s' if replacements != 1 else ''}).",
        data={"path": args.path, "replacements": replacements},
    )


apply_patch_tool = Tool(
    name="apply_patch",
    description="Apply a targeted text replacement to a file in the workspace.",
    input_model=ApplyPatchInput,
    handler=_apply_patch,
)
