from __future__ import annotations

from pathlib import Path

from xagent.config import AppConfig


def resolve_workspace_path(config: AppConfig, workspace: str | None) -> Path:
    if workspace:
        return Path(workspace).expanduser().resolve()
    return config.default_workspace_path
