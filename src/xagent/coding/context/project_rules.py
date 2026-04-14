from pathlib import Path
from typing import Optional

from xagent.foundation.runtime.paths import find_project_root


def load_project_rules(start: Optional[Path] = None) -> Optional[str]:
    current = (start or Path.cwd()).resolve()
    if current.is_file():
        current = current.parent

    root = find_project_root(current)
    segments = []
    for directory in _iter_rule_directories(root, current):
        rules_path = directory / "AGENTS.md"
        if not rules_path.exists():
            continue
        body = rules_path.read_text(encoding="utf-8").strip()
        if not body:
            continue
        relative = directory.relative_to(root)
        scope = "." if str(relative) == "." else relative.as_posix()
        segments.append(
            "\n".join(
                [
                    f"<agents_scope path=\"{scope}/AGENTS.md\">",
                    body,
                    "</agents_scope>",
                ]
            )
        )
    if not segments:
        return None
    return "\n\n".join(segments)


def _iter_rule_directories(root: Path, current: Path) -> list[Path]:
    if current == root:
        return [root]
    relative = current.relative_to(root)
    directories = [root]
    cursor = root
    for part in relative.parts:
        cursor = cursor / part
        directories.append(cursor)
    return directories
