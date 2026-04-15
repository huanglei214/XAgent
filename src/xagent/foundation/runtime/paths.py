from pathlib import Path
from typing import Optional


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
