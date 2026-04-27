from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from xagent.agent.paths import get_semantic_memory_file
from xagent.agent.session import SessionLoadMetadata, SessionStore, SessionSummary
from xagent.provider.types import Message


class WorkingMemory:
    """Holds transient per-turn state: active tools, requested skill, scratchpad."""

    def __init__(self, agent: Any = None) -> None:
        self.agent = agent
        self.active_tools: list[str] = []
        self.requested_skill_name: Optional[str] = None
        self.current_plan: Optional[str] = None
        self.scratchpad: dict[str, Any] = {}
        self._messages: list[Message] = []

    @property
    def messages(self) -> list[Message]:
        if self.agent is not None:
            return list(getattr(self.agent, "messages", []))
        return list(self._messages)

    def attach_agent(self, agent: Any) -> None:
        self.agent = agent

    def replace_messages(self, messages: list[Message]) -> None:
        if self.agent is not None:
            self.agent.clear_messages()
            self.agent.set_messages(messages)
            return
        self._messages = list(messages)

    def clear_messages(self) -> None:
        self.replace_messages([])

    def start_tool(self, tool_name: str) -> None:
        if tool_name not in self.active_tools:
            self.active_tools.append(tool_name)

    def finish_tool(self, tool_name: str) -> None:
        self.active_tools = [name for name in self.active_tools if name != tool_name]

    def clear_active_tools(self) -> None:
        self.active_tools = []

    def set_requested_skill_name(self, requested_skill_name: Optional[str]) -> None:
        self.requested_skill_name = requested_skill_name
        if self.agent is not None and hasattr(self.agent, "set_requested_skill_name"):
            self.agent.set_requested_skill_name(requested_skill_name)

    def set_current_plan(self, plan: Optional[str]) -> None:
        self.current_plan = plan

    def set_scratchpad_item(self, key: str, value: Any) -> None:
        self.scratchpad[key] = value

    def clear_turn_state(self) -> None:
        self.clear_active_tools()
        self.set_requested_skill_name(None)


class EpisodicMemory:
    """Persists and restores conversation sessions via SessionStore."""

    def __init__(self, store: SessionStore) -> None:
        self.store = store

    @classmethod
    def for_cwd(cls, cwd: str | Path) -> EpisodicMemory:
        return cls(SessionStore(cwd))

    def new_session_id(self) -> str:
        return self.store.new_session_id()

    def list_sessions(self, limit: int = 20) -> list[SessionSummary]:
        return self.store.list_sessions(limit=limit)

    def session_exists(self, session_id: str) -> bool:
        return self.store.session_exists(session_id)

    def save(self, session_id: str, messages: list[Message], *, compact: bool = True):
        return self.store.save_messages(messages, session_id=session_id, compact=compact)

    def restore(self, session_id: str) -> Optional[tuple[str, list[Message], SessionLoadMetadata]]:
        loaded_session_id, restored_messages, metadata = self.store.load_state_with_metadata(session_id=session_id)
        if not restored_messages and not self.store.session_exists(session_id):
            return None
        return loaded_session_id, restored_messages, metadata

    def clear(self, session_id: str) -> None:
        self.store.clear(session_id=session_id)


class SemanticMemory:
    """Key-value store backed by a JSON file, namespaced by category."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def get_namespace(self, namespace: str) -> dict[str, Any]:
        payload = self._load()
        values = payload.get(namespace, {})
        return dict(values) if isinstance(values, dict) else {}

    def get(self, namespace: str, key: str, default: Any = None) -> Any:
        return self.get_namespace(namespace).get(key, default)

    def set(self, namespace: str, key: str, value: Any) -> None:
        payload = self._load()
        values = payload.setdefault(namespace, {})
        if not isinstance(values, dict):
            values = {}
            payload[namespace] = values
        values[key] = value
        self._save(payload)

    def replace_namespace(self, namespace: str, values: dict[str, Any]) -> None:
        payload = self._load()
        payload[namespace] = dict(values)
        self._save(payload)

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _save(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


@dataclass
class RuntimeMemory:
    """Aggregate of all memory subsystems for a single runtime."""

    working: WorkingMemory
    episodic: EpisodicMemory
    semantic: SemanticMemory


def create_runtime_memory(
    cwd: str | Path,
    *,
    agent: Any = None,
    session_store: Optional[SessionStore] = None,
) -> RuntimeMemory:
    """Build a RuntimeMemory instance from a working directory."""
    resolved_cwd = Path(cwd)
    episodic_store = session_store or SessionStore(resolved_cwd)
    return RuntimeMemory(
        working=WorkingMemory(agent=agent),
        episodic=EpisodicMemory(episodic_store),
        semantic=SemanticMemory(get_semantic_memory_file(resolved_cwd)),
    )
