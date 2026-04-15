from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from xagent.agent.memory.episodic import EpisodicMemory
from xagent.agent.memory.semantic import SemanticMemory
from xagent.agent.memory.working import WorkingMemory
from xagent.agent.session import SessionStore
from xagent.foundation.runtime.paths import get_semantic_memory_file


@dataclass
class RuntimeMemory:
    working: WorkingMemory
    episodic: EpisodicMemory
    semantic: SemanticMemory


def create_runtime_memory(
    cwd: str | Path,
    *,
    agent: Any = None,
    session_store: Optional[SessionStore] = None,
) -> RuntimeMemory:
    resolved_cwd = Path(cwd)
    episodic_store = session_store or SessionStore(resolved_cwd)
    return RuntimeMemory(
        working=WorkingMemory(agent=agent),
        episodic=EpisodicMemory(episodic_store),
        semantic=SemanticMemory(get_semantic_memory_file(resolved_cwd)),
    )
