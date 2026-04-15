from pydantic import BaseModel, Field

from xagent.foundation.runtime.workspace_paths import resolve_tool_path
from xagent.foundation.tools import Tool, ToolContext, ToolResult


class StrReplaceInput(BaseModel):
    path: str = Field(description="File path relative to the workspace root.")
    old_text: str = Field(description="Exact text to replace.")
    new_text: str = Field(description="Replacement text.")
    count: int = Field(default=1, ge=1, description="Maximum number of matches to replace.")


async def _str_replace(args: StrReplaceInput, ctx: ToolContext) -> ToolResult:
    target = await resolve_tool_path(ctx, args.path, "write")
    if not target.exists():
        return ToolResult.fail(f"File not found: {args.path}", code="FILE_NOT_FOUND")
    if not target.is_file():
        return ToolResult.fail(f"Path is not a file: {args.path}", code="PATH_NOT_FILE")

    text = target.read_text(encoding="utf-8")
    occurrences_found = text.count(args.old_text)
    if occurrences_found == 0:
        return ToolResult.fail("old_text was not found in the target file.", code="OLD_TEXT_NOT_FOUND")

    replacements = min(occurrences_found, args.count)
    updated = text.replace(args.old_text, args.new_text, args.count)

    target.write_text(updated, encoding="utf-8")
    return ToolResult.ok(
        f"Replaced text in {args.path} ({replacements} replacement{'s' if replacements != 1 else ''}).",
        content=f"Replaced text in {args.path} ({replacements} replacement{'s' if replacements != 1 else ''}).",
        data={
            "path": args.path,
            "occurrences_found": occurrences_found,
            "replacements": replacements,
            "count": args.count,
        },
    )


str_replace_tool = Tool(
    name="str_replace",
    description="Replace exact text inside one file in the workspace using a maximum replacement count.",
    input_model=StrReplaceInput,
    handler=_str_replace,
)
