from __future__ import annotations

import inspect
from pathlib import Path
from typing import Optional

from xagent.bus.errors import WorkspaceEscapeError


def find_project_root(start: Optional[Path] = None) -> Path:
    current = (start or Path.cwd()).resolve()
    markers = (".git", "pyproject.toml", ".xagent")
    for candidate in [current, *current.parents]:
        if any((candidate / marker).exists() for marker in markers):
            return candidate
    return current


def get_config_dir(start: Optional[Path] = None) -> Path:
    return find_project_root(start) / ".xagent"


def get_config_file(start: Optional[Path] = None) -> Path:
    return get_config_dir(start) / "config.yaml"


def get_env_file(start: Optional[Path] = None) -> Path:
    return find_project_root(start) / ".env"


def get_config_example_file(start: Optional[Path] = None) -> Path:
    return find_project_root(start) / "config.example.yaml"


def get_chat_history_file(start: Optional[Path] = None) -> Path:
    return get_config_dir(start) / "chat-history.txt"


def get_approvals_file(start: Optional[Path] = None) -> Path:
    return get_config_dir(start) / "approvals.json"


def get_session_file(start: Optional[Path] = None) -> Path:
    return get_config_dir(start) / "session.json"


def get_sessions_dir(start: Optional[Path] = None) -> Path:
    return get_config_dir(start) / "sessions"


def get_semantic_memory_file(start: Optional[Path] = None) -> Path:
    return get_config_dir(start) / "semantic-memory.json"


def get_scheduler_jobs_file(start: Optional[Path] = None) -> Path:
    return get_config_dir(start) / "scheduler-jobs.json"


def get_scheduler_history_file(start: Optional[Path] = None) -> Path:
    return get_config_dir(start) / "scheduler-history.jsonl"


def get_traces_dir(start: Optional[Path] = None) -> Path:
    return get_config_dir(start) / "traces"


def get_trace_index_file(start: Optional[Path] = None) -> Path:
    return get_traces_dir(start) / "index.json"


def get_trace_artifacts_dir(start: Optional[Path] = None) -> Path:
    return get_traces_dir(start) / "artifacts"


def ensure_config_dir(start: Optional[Path] = None) -> Path:
    config_dir = get_config_dir(start)
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def resolve_workspace_path(cwd: str, target: str = ".") -> Path:
    """Resolve a path relative to the workspace root, raising WorkspaceEscapeError if it escapes."""
    root = Path(cwd).resolve()
    candidate = (root / target).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise WorkspaceEscapeError(f"Path '{target}' escapes the workspace root.") from exc
    return candidate


async def resolve_tool_path(ctx, target: str = ".", access_kind: str = "read") -> Path:
    """Resolve a tool path, allowing external paths with approval."""
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
