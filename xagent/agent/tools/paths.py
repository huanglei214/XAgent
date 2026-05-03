from __future__ import annotations

from pathlib import Path


def resolve_under(root: Path, path: str) -> Path:
    root = root.expanduser().resolve()
    target = (root / path).expanduser().resolve()
    if target != root and root not in target.parents:
        raise ValueError(f"Path escapes workspace: {path}")
    return target
