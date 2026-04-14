import inspect
from pathlib import Path


class WorkspaceEscapeError(ValueError):
    pass


def resolve_workspace_path(cwd: str, target: str = ".") -> Path:
    root = Path(cwd).resolve()
    candidate = (root / target).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise WorkspaceEscapeError(f"Path '{target}' escapes the workspace root.") from exc
    return candidate


async def resolve_tool_path(ctx, target: str = ".", access_kind: str = "read") -> Path:
    root = Path(ctx.cwd).resolve()
    candidate = (root / target).resolve()
    try:
        candidate.relative_to(root)
        return candidate
    except ValueError:
        pass

    if _is_allowed_external_path(candidate, getattr(ctx, "allowed_external_paths", set())):
        return candidate

    handler = getattr(ctx, "request_path_access", None)
    if handler is None:
        raise WorkspaceEscapeError(f"Path '{target}' escapes the workspace root.")

    decision = handler(str(candidate), access_kind)
    if inspect.isawaitable(decision):
        decision = await decision
    if decision:
        ctx.allowed_external_paths.add(str(candidate))
        return candidate
    raise WorkspaceEscapeError(f"Access denied for path '{target}' outside the workspace root.")


def _is_allowed_external_path(candidate: Path, allowed_paths: set[str]) -> bool:
    resolved = str(candidate)
    if resolved in allowed_paths:
        return True
    for path in allowed_paths:
        try:
            candidate.relative_to(Path(path))
            return True
        except Exception:
            continue
    return False
