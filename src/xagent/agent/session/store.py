import json
from pathlib import Path
from typing import List, Optional, Tuple, Union
from uuid import uuid4

from xagent.foundation.runtime.paths import ensure_config_dir, get_session_file
from xagent.foundation.messages import Message


class SessionStore:
    def __init__(self, cwd: Union[str, Path]) -> None:
        self.cwd = Path(cwd)
        self.path = get_session_file(self.cwd)

    def load_messages(self) -> List[Message]:
        _, messages = self.load_state()
        return messages

    def load_state(self) -> Tuple[str, List[Message]]:
        if not self.path.exists():
            return str(uuid4()), []

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return str(uuid4()), []

        messages = payload.get("messages", [])
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            session_id = str(uuid4())
        if not isinstance(messages, list):
            return session_id, []

        loaded: List[Message] = []
        for item in messages:
            try:
                loaded.append(Message.model_validate(item))
            except Exception:
                continue
        return session_id, loaded

    def save_messages(self, messages: List[Message], session_id: Optional[str] = None) -> Path:
        ensure_config_dir(self.cwd)
        payload = {
            "cwd": str(self.cwd.resolve()),
            "session_id": session_id or str(uuid4()),
            "message_count": len(messages),
            "messages": [message.model_dump(mode="json") for message in messages],
        }
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return self.path

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()
