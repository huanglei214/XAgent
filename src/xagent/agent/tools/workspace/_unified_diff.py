from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?: .*)?$")
_PATCH_METADATA_PREFIXES = (
    "diff --git ",
    "index ",
    "new file mode ",
    "deleted file mode ",
    "similarity index ",
    "rename from ",
    "rename to ",
)


@dataclass(frozen=True)
class HunkLine:
    kind: str
    text: str
    no_newline: bool = False


@dataclass(frozen=True)
class PatchHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[HunkLine]


@dataclass(frozen=True)
class FilePatch:
    old_path: str | None
    new_path: str | None
    hunks: list[PatchHunk]


class PatchParseError(ValueError):
    pass


class PatchApplyError(ValueError):
    pass


def parse_unified_diff(patch: str) -> list[FilePatch]:
    lines = patch.splitlines()
    index = 0
    files: list[FilePatch] = []

    while index < len(lines):
        line = lines[index]
        if not line:
            index += 1
            continue
        if line.startswith(_PATCH_METADATA_PREFIXES):
            index += 1
            continue
        if not line.startswith("--- "):
            raise PatchParseError(f"Unexpected patch line: {line}")

        old_path = _normalize_patch_path(_extract_header_path(line, "--- "))
        index += 1
        if index >= len(lines) or not lines[index].startswith("+++ "):
            raise PatchParseError("Expected '+++' header after '---' header.")
        new_path = _normalize_patch_path(_extract_header_path(lines[index], "+++ "))
        index += 1

        hunks: list[PatchHunk] = []
        while index < len(lines):
            current = lines[index]
            if not current:
                index += 1
                continue
            if current.startswith(_PATCH_METADATA_PREFIXES):
                index += 1
                continue
            if current.startswith("--- "):
                break
            if not current.startswith("@@ "):
                raise PatchParseError(f"Expected hunk header, got: {current}")

            hunk, index = _parse_hunk(lines, index)
            hunks.append(hunk)

        if not hunks:
            raise PatchParseError("Patch file is missing hunks.")
        files.append(FilePatch(old_path=old_path, new_path=new_path, hunks=hunks))

    if not files:
        raise PatchParseError("Patch is empty.")
    return files


def apply_file_patch(original_text: str, file_patch: FilePatch) -> str:
    original_lines = _split_text_lines(original_text)
    output_lines: list[tuple[str, bool]] = []
    source_index = 0

    for hunk in file_patch.hunks:
        start_index = 0 if hunk.old_start == 0 else hunk.old_start - 1
        if start_index < source_index:
            raise PatchApplyError("Patch hunks overlap or are out of order.")
        if start_index > len(original_lines):
            raise PatchApplyError("Patch hunk starts beyond the end of the file.")

        output_lines.extend(original_lines[source_index:start_index])
        cursor = start_index

        for line in hunk.lines:
            if line.kind == " ":
                _assert_line_matches(original_lines, cursor, line, "context")
                output_lines.append(original_lines[cursor])
                cursor += 1
                continue
            if line.kind == "-":
                _assert_line_matches(original_lines, cursor, line, "delete")
                cursor += 1
                continue
            output_lines.append((line.text, not line.no_newline))

        source_index = cursor

    output_lines.extend(original_lines[source_index:])
    return _join_lines(output_lines)


def _parse_hunk(lines: list[str], start_index: int) -> tuple[PatchHunk, int]:
    header = lines[start_index]
    match = _HUNK_HEADER_RE.match(header)
    if match is None:
        raise PatchParseError(f"Invalid hunk header: {header}")

    old_start = int(match.group(1))
    old_count = int(match.group(2) or "1")
    new_start = int(match.group(3))
    new_count = int(match.group(4) or "1")
    index = start_index + 1
    hunk_lines: list[HunkLine] = []

    while index < len(lines):
        line = lines[index]
        if line.startswith("@@ ") or line.startswith("--- "):
            break
        if line.startswith(_PATCH_METADATA_PREFIXES):
            break
        if line == r"\ No newline at end of file":
            if not hunk_lines:
                raise PatchParseError("No-newline marker must follow a hunk line.")
            previous = hunk_lines[-1]
            hunk_lines[-1] = HunkLine(kind=previous.kind, text=previous.text, no_newline=True)
            index += 1
            continue
        if not line or line[0] not in {" ", "+", "-"}:
            raise PatchParseError(f"Invalid hunk line: {line}")
        hunk_lines.append(HunkLine(kind=line[0], text=line[1:]))
        index += 1

    counted_old = sum(1 for line in hunk_lines if line.kind in {" ", "-"})
    counted_new = sum(1 for line in hunk_lines if line.kind in {" ", "+"})
    if counted_old != old_count or counted_new != new_count:
        raise PatchParseError(
            "Hunk line counts do not match the header "
            f"(expected old/new {old_count}/{new_count}, got {counted_old}/{counted_new})."
        )

    return (
        PatchHunk(
            old_start=old_start,
            old_count=old_count,
            new_start=new_start,
            new_count=new_count,
            lines=hunk_lines,
        ),
        index,
    )


def _extract_header_path(line: str, prefix: str) -> str:
    raw_path = line[len(prefix) :]
    if "\t" in raw_path:
        raw_path = raw_path.split("\t", 1)[0]
    return raw_path.strip()


def _normalize_patch_path(path: str) -> str | None:
    if path == "/dev/null":
        return None
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path


def _assert_line_matches(lines: list[tuple[str, bool]], index: int, expected: HunkLine, kind: str) -> None:
    if index >= len(lines):
        raise PatchApplyError(f"Patch {kind} line is beyond the end of the file.")
    actual_text, actual_has_newline = lines[index]
    if actual_text != expected.text:
        raise PatchApplyError(
            f"Patch {kind} mismatch at line {index + 1}: expected {expected.text!r}, got {actual_text!r}."
        )
    if expected.no_newline and actual_has_newline:
        raise PatchApplyError(f"Patch {kind} mismatch at line {index + 1}: expected no trailing newline.")


def _join_lines(lines: list[tuple[str, bool]]) -> str:
    if not lines:
        return ""
    return "".join(text + ("\n" if has_newline else "") for text, has_newline in lines)


def _split_text_lines(text: str) -> list[tuple[str, bool]]:
    if text == "":
        return []

    raw_lines = text.splitlines(keepends=True)
    if not raw_lines:
        return []

    lines: list[tuple[str, bool]] = []
    for raw_line in raw_lines:
        if raw_line.endswith("\n"):
            lines.append((raw_line[:-1], True))
        else:
            lines.append((raw_line, False))
    return lines


def summarize_paths(file_patches: list[FilePatch]) -> list[str]:
    paths: list[str] = []
    for file_patch in file_patches:
        path = file_patch.new_path or file_patch.old_path
        if path is not None:
            paths.append(path)
    return paths
