import os
from pathlib import Path
from typing import Dict, Optional

from xagent.foundation.runtime.paths import get_env_file


def ensure_env_file(start: Optional[Path] = None, force: bool = False) -> Path:
    env_path = get_env_file(start)
    if env_path.exists() and not force:
        return env_path

    env_path.write_text(_default_env_contents(), encoding="utf-8")
    return env_path


def load_project_env(start: Optional[Path] = None, override: bool = False) -> Dict[str, str]:
    env_path = get_env_file(start)
    if not env_path.exists():
        return {}

    values = _parse_env(env_path.read_text(encoding="utf-8"))
    for key, value in values.items():
        if override or key not in os.environ:
            os.environ[key] = value
    return values


def _default_env_contents() -> str:
    return (
        "# Project-local environment variables for XAgent\n"
        "ARK_API_KEY=\n"
        "OPENAI_API_KEY=\n"
        "ANTHROPIC_API_KEY=\n"
    )


def _parse_env(raw: str) -> Dict[str, str]:
    values: Dict[str, str] = {}
    for original_line in raw.splitlines():
        line = original_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values
