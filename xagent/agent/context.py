from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AgentPaths:
    workspace: Path
    session: Path
    artifacts: Path
