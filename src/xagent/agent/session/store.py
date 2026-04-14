from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple, Union
from uuid import uuid4

from xagent.foundation.messages import Message, TextPart, ToolResultPart, ToolUsePart, message_text
from xagent.foundation.runtime.paths import ensure_config_dir, get_session_file

CHECKPOINT_HEADER = "[session-checkpoint"
DEFAULT_CHECKPOINT_THRESHOLD = 20
DEFAULT_RECENT_WINDOW = 12
DEFAULT_MAX_CHECKPOINT_CHARS = 6000


@dataclass
class SessionLoadMetadata:
    restored_message_count: int
    recent_message_count: int
    checkpointed_message_count: int
    has_checkpoint: bool


class SessionStore:
    def __init__(
        self,
        cwd: Union[str, Path],
        checkpoint_threshold: int = DEFAULT_CHECKPOINT_THRESHOLD,
        recent_window: int = DEFAULT_RECENT_WINDOW,
        max_checkpoint_chars: int = DEFAULT_MAX_CHECKPOINT_CHARS,
    ) -> None:
        self.cwd = Path(cwd)
        self.path = get_session_file(self.cwd)
        self.checkpoint_threshold = checkpoint_threshold
        self.recent_window = recent_window
        self.max_checkpoint_chars = max(500, max_checkpoint_chars)

    def load_messages(self) -> List[Message]:
        _, messages = self.load_state()
        return messages

    def load_state(self) -> Tuple[str, List[Message]]:
        session_id, messages, _ = self.load_state_with_metadata()
        return session_id, messages

    def load_state_with_metadata(self) -> Tuple[str, List[Message], SessionLoadMetadata]:
        if not self.path.exists():
            return str(uuid4()), [], SessionLoadMetadata(0, 0, 0, False)

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return str(uuid4()), [], SessionLoadMetadata(0, 0, 0, False)

        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            session_id = str(uuid4())

        checkpoint_summary = payload.get("checkpoint_summary")
        checkpointed_message_count = payload.get("checkpointed_message_count", 0)
        recent_raw = payload.get("recent_messages")

        if checkpoint_summary is None and recent_raw is None:
            return self._load_legacy_state(session_id, payload)

        recent_messages = self._load_messages_list(recent_raw)
        restored_messages: List[Message] = []
        checkpoint_count = checkpointed_message_count if isinstance(checkpointed_message_count, int) else 0
        if isinstance(checkpoint_summary, str) and checkpoint_summary.strip():
            restored_messages.append(_build_checkpoint_message(checkpoint_summary, checkpoint_count))
        restored_messages.extend(recent_messages)
        metadata = SessionLoadMetadata(
            restored_message_count=len(restored_messages),
            recent_message_count=len(recent_messages),
            checkpointed_message_count=checkpoint_count,
            has_checkpoint=bool(restored_messages and _is_checkpoint_message(restored_messages[0])),
        )
        return session_id, restored_messages, metadata

    def save_messages(self, messages: List[Message], session_id: Optional[str] = None) -> Path:
        ensure_config_dir(self.cwd)
        checkpoint_summary, checkpointed_message_count, recent_messages = self._compact_messages(messages)
        payload = {
            "cwd": str(self.cwd.resolve()),
            "session_id": session_id or str(uuid4()),
            "message_count": checkpointed_message_count + len(recent_messages),
            "checkpointed_message_count": checkpointed_message_count,
            "recent_message_count": len(recent_messages),
            "checkpoint_summary": checkpoint_summary,
            "recent_messages": [message.model_dump(mode="json") for message in recent_messages],
        }
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return self.path

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()

    def _load_legacy_state(
        self,
        session_id: str,
        payload: dict,
    ) -> Tuple[str, List[Message], SessionLoadMetadata]:
        messages = self._load_messages_list(payload.get("messages"))
        metadata = SessionLoadMetadata(
            restored_message_count=len(messages),
            recent_message_count=len(messages),
            checkpointed_message_count=0,
            has_checkpoint=False,
        )
        return session_id, messages, metadata

    def _load_messages_list(self, raw_messages) -> List[Message]:
        if not isinstance(raw_messages, list):
            return []
        loaded: List[Message] = []
        for item in raw_messages:
            try:
                loaded.append(Message.model_validate(item))
            except Exception:
                continue
        return loaded

    def _compact_messages(self, messages: List[Message]) -> tuple[Optional[str], int, List[Message]]:
        normalized = list(messages)
        prior_summary = None
        prior_checkpoint_count = 0
        if normalized and _is_checkpoint_message(normalized[0]):
            prior_summary = _extract_checkpoint_summary(normalized[0])
            prior_checkpoint_count = _extract_checkpoint_count(normalized[0])
            normalized = normalized[1:]

        if not normalized:
            return prior_summary, prior_checkpoint_count, []

        if len(normalized) <= self.checkpoint_threshold and prior_summary is None:
            return None, 0, normalized

        if len(normalized) <= self.recent_window:
            recent_messages = normalized
            new_checkpoint_messages: List[Message] = []
        else:
            recent_messages = normalized[-self.recent_window :]
            new_checkpoint_messages = normalized[: -self.recent_window]

        summary_parts = []
        if prior_summary:
            summary_parts.append(prior_summary)
        if new_checkpoint_messages:
            summary_parts.append(_summarize_messages(new_checkpoint_messages))
        merged_summary = "\n".join(part for part in summary_parts if part).strip() or None
        if merged_summary:
            merged_summary = _truncate_checkpoint_summary(merged_summary, self.max_checkpoint_chars)
        checkpointed_message_count = prior_checkpoint_count + len(new_checkpoint_messages)
        return merged_summary, checkpointed_message_count, recent_messages


def _build_checkpoint_message(summary: str, checkpointed_message_count: int) -> Message:
    return Message(
        role="system",
        content=[TextPart(text=f"{CHECKPOINT_HEADER} count={checkpointed_message_count}]\n{summary}")],
    )


def _is_checkpoint_message(message: Message) -> bool:
    return message.role == "system" and message_text(message).startswith(CHECKPOINT_HEADER)


def _extract_checkpoint_summary(message: Message) -> str:
    text = message_text(message)
    _, _, summary = text.partition("]\n")
    return summary.strip()


def _extract_checkpoint_count(message: Message) -> int:
    text = message_text(message)
    match = re.match(r"^\[session-checkpoint count=(\d+)\]", text)
    if match:
        return int(match.group(1))
    summary = _extract_checkpoint_summary(message)
    return len([line for line in summary.splitlines() if line.strip().startswith(("U:", "A:", "T:"))])


def _summarize_messages(messages: List[Message]) -> str:
    lines = []
    for message in messages:
        rendered = _render_message_for_checkpoint(message)
        if rendered:
            lines.append(rendered)
    return "\n".join(lines)


def _render_message_for_checkpoint(message: Message) -> str:
    if message.role == "user":
        text = message_text(message).strip()
        return f"U: {text}" if text else ""
    if message.role == "assistant":
        parts = []
        text = message_text(message).strip()
        if text:
            parts.append(f"A: {text}")
        for part in message.content:
            if isinstance(part, ToolUsePart):
                parts.append(f"T: call {part.name} {json.dumps(part.input, ensure_ascii=False, sort_keys=True)}")
        return "\n".join(parts)
    if message.role == "tool":
        parts = []
        for part in message.content:
            if isinstance(part, ToolResultPart):
                prefix = "error" if part.is_error else "ok"
                content = part.content.strip()
                if len(content) > 200:
                    content = content[:197] + "..."
                parts.append(f"T: result {prefix} {content}")
        return "\n".join(parts)
    return ""


def _truncate_checkpoint_summary(summary: str, max_chars: int) -> str:
    if len(summary) <= max_chars:
        return summary
    suffix = summary[-(max_chars - 28) :]
    return "[earlier checkpoint omitted]\n" + suffix.lstrip()
