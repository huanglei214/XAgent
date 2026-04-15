from __future__ import annotations

from pathlib import Path
from typing import Optional

from xagent.agent.session import SessionLoadMetadata, SessionStore, SessionSummary
from xagent.foundation.messages import Message


class EpisodicMemory:
    def __init__(self, store: SessionStore) -> None:
        self.store = store

    @classmethod
    def for_cwd(cls, cwd: str | Path) -> "EpisodicMemory":
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
