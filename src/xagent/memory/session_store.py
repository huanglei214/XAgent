import json
from pathlib import Path
from typing import List, Union

from xagent.config.paths import ensure_config_dir, get_session_file
from xagent.foundation.messages import Message


class SessionStore:
    def __init__(self, cwd: Union[str, Path]) -> None:
        self.cwd = Path(cwd)
        self.path = get_session_file(self.cwd)

    def load_messages(self) -> List[Message]:
        if not self.path.exists():
            return []

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return []

        messages = payload.get("messages", [])
        if not isinstance(messages, list):
            return []

        loaded: List[Message] = []
        for item in messages:
            try:
                loaded.append(Message.model_validate(item))
            except Exception:
                continue
        return loaded

    def save_messages(self, messages: List[Message]) -> Path:
        ensure_config_dir(self.cwd)
        payload = {
            "cwd": str(self.cwd.resolve()),
            "message_count": len(messages),
            "messages": [message.model_dump(mode="json") for message in messages],
        }
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return self.path

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()
