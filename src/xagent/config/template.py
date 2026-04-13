from pathlib import Path
from typing import Optional

from xagent.config.loader import dump_config_yaml
from xagent.config.paths import get_config_example_file
from xagent.config.schema import default_config


def ensure_config_example_file(start: Optional[Path] = None, force: bool = False) -> Path:
    example_path = get_config_example_file(start)
    if example_path.exists() and not force:
        return example_path

    example_path.write_text(_build_example_contents(), encoding="utf-8")
    return example_path


def _build_example_contents() -> str:
    return (
        "# Example XAgent project configuration\n"
        "# Copy relevant values into .xagent/config.yaml if you want a fresh local config.\n"
        + dump_config_yaml(default_config())
    )
