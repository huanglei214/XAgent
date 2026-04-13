from pathlib import Path


def resolve_workspace_path(cwd: str, target: str = ".") -> Path:
    root = Path(cwd).resolve()
    candidate = (root / target).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Path '{target}' escapes the workspace root.") from exc
    return candidate
