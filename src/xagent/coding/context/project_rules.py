from pathlib import Path
from typing import Optional

from xagent.foundation.runtime.paths import find_project_root


def load_project_rules(start: Optional[Path] = None) -> Optional[str]:
    root = find_project_root(start)
    rules_path = root / "AGENTS.md"
    if not rules_path.exists():
        return None
    return rules_path.read_text(encoding="utf-8")
