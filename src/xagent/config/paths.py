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
    return get_config_dir(start) / "config.toml"


def ensure_config_dir(start: Optional[Path] = None) -> Path:
    config_dir = get_config_dir(start)
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir
