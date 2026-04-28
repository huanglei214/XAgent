from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator


IGNORED_DIRECTORY_NAMES = {
    ".git",
    ".mypy_cache",
    ".omx",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    ".xagent",
    "__pycache__",
    "node_modules",
}


def iter_visible_entries(target: Path, *, recursive: bool) -> Iterator[Path]:
    """遍历目标路径下的可见条目，跳过被忽略的目录。"""
    if not target.is_dir():
        return

    allow_all = target.name in IGNORED_DIRECTORY_NAMES

    if not recursive:
        for item in sorted(target.iterdir()):
            if not allow_all and item.name in IGNORED_DIRECTORY_NAMES:
                continue
            yield item
        return

    for current_root, dir_names, file_names in os.walk(target):
        current = Path(current_root)
        dir_names[:] = sorted(name for name in dir_names if allow_all or name not in IGNORED_DIRECTORY_NAMES)
        for dir_name in dir_names:
            yield current / dir_name
        for file_name in sorted(file_names):
            yield current / file_name


def is_visible_path(path: Path, traversal_root: Path) -> bool:
    """判断给定路径是否可见（不包含被忽略的目录部分）。"""
    if traversal_root.name in IGNORED_DIRECTORY_NAMES:
        return True
    relative = path.resolve().relative_to(traversal_root.resolve())
    return not any(part in IGNORED_DIRECTORY_NAMES for part in relative.parts)
