from pathlib import Path
from typing import NamedTuple, Optional

from pydantic import BaseModel, Field

from xagent.coding.tools._unified_diff import (
    PatchApplyError,
    PatchParseError,
    apply_file_patch,
    parse_unified_diff,
    summarize_paths,
)
from xagent.coding.workspace import resolve_tool_path
from xagent.foundation.tools import Tool, ToolContext, ToolResult


class ApplyPatchInput(BaseModel):
    patch: str = Field(description="Unified diff patch with ---/+++ file headers and @@ hunks.")


class PendingPatchWrite(NamedTuple):
    source: Optional[Path]
    target: Optional[Path]
    updated_text: Optional[str]


async def _apply_patch(args: ApplyPatchInput, ctx: ToolContext) -> ToolResult:
    try:
        file_patches = parse_unified_diff(args.patch)
    except PatchParseError as exc:
        return ToolResult.fail(str(exc), code="INVALID_PATCH")

    pending_writes: list[PendingPatchWrite] = []

    try:
        for file_patch in file_patches:
            source = await resolve_tool_path(ctx, file_patch.old_path, "write") if file_patch.old_path else None
            target = await resolve_tool_path(ctx, file_patch.new_path, "write") if file_patch.new_path else None
            effective_target = target or source

            if effective_target is None:
                return ToolResult.fail("Patch file is missing a target path.", code="INVALID_PATCH")

            if source is None:
                if target is not None and target.exists():
                    return ToolResult.fail(f"Target already exists: {file_patch.new_path}", code="PATCH_TARGET_EXISTS")
                original_text = ""
            else:
                if not source.exists():
                    return ToolResult.fail(f"File not found: {file_patch.old_path}", code="FILE_NOT_FOUND")
                if not source.is_file():
                    return ToolResult.fail(f"Path is not a file: {file_patch.old_path}", code="PATH_NOT_FILE")
                original_text = source.read_text(encoding="utf-8")

            if target is not None and target.exists() and not target.is_file():
                return ToolResult.fail(f"Path is not a file: {file_patch.new_path}", code="PATH_NOT_FILE")

            updated_text = apply_file_patch(original_text, file_patch)
            pending_writes.append(
                PendingPatchWrite(
                    source=source,
                    target=target,
                    updated_text=updated_text if file_patch.new_path is not None else None,
                )
            )
    except PatchApplyError as exc:
        return ToolResult.fail(str(exc), code="PATCH_MISMATCH")

    for pending in pending_writes:
        if pending.updated_text is None:
            if pending.source is not None:
                pending.source.unlink()
            continue
        if pending.target is None:
            continue
        pending.target.parent.mkdir(parents=True, exist_ok=True)
        pending.target.write_text(pending.updated_text, encoding="utf-8")
        if pending.source is not None and pending.target != pending.source and pending.source.exists():
            pending.source.unlink()

    unique_paths = summarize_paths(file_patches)
    summary = f"Applied patch to {len(unique_paths)} file{'s' if len(unique_paths) != 1 else ''}."
    return ToolResult.ok(
        summary,
        content="\n".join([summary, *unique_paths]),
        data={"paths": unique_paths, "file_count": len(unique_paths)},
    )


apply_patch_tool = Tool(
    name="apply_patch",
    description="Apply a strict unified diff patch to one or more files in the workspace.",
    input_model=ApplyPatchInput,
    handler=_apply_patch,
)
