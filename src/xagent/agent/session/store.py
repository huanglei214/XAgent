from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from time import time
from typing import List, Optional, Tuple, Union
from uuid import uuid4

from xagent.foundation.messages import Message, TextPart, ToolResultPart, ToolUsePart, message_text
from xagent.foundation.runtime.paths import ensure_config_dir, get_session_file, get_sessions_dir

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


@dataclass
class SessionSummary:
    session_id: str
    saved_at: float
    message_count: int
    recent_message_count: int
    checkpointed_message_count: int
    preview: str
    is_latest: bool
    created_at: float = 0.0
    branch: str = "-"


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
        self.sessions_dir = get_sessions_dir(self.cwd)
        self.checkpoint_threshold = checkpoint_threshold
        self.recent_window = recent_window
        self.max_checkpoint_chars = max(500, max_checkpoint_chars)

    def new_session_id(self) -> str:
        return str(uuid4())

    def load_messages(self) -> List[Message]:
        _, messages = self.load_state()
        return messages

    def load_state(self, session_id: Optional[str] = None) -> Tuple[str, List[Message]]:
        session_id, messages, _ = self.load_state_with_metadata(session_id=session_id)
        return session_id, messages

    def load_state_with_metadata(
        self,
        session_id: Optional[str] = None,
    ) -> Tuple[str, List[Message], SessionLoadMetadata]:
        target_session_id = session_id or self._read_latest_session_id()
        if target_session_id:
            loaded = self._load_session_payload(self._session_path(target_session_id))
            if loaded is not None:
                return self._decode_state_payload(loaded, fallback_session_id=target_session_id)

        legacy_payload = self._read_legacy_payload()
        if legacy_payload is not None:
            fallback_id = session_id or legacy_payload.get("session_id") or str(uuid4())
            return self._decode_state_payload(legacy_payload, fallback_session_id=fallback_id)

        return session_id or str(uuid4()), [], SessionLoadMetadata(0, 0, 0, False)

    def save_messages(self, messages: List[Message], session_id: Optional[str] = None, *, compact: bool = True) -> Path:
        ensure_config_dir(self.cwd)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        resolved_session_id = session_id or str(uuid4())
        existing_payload = self._load_session_payload(self._session_path(resolved_session_id))
        created_at = existing_payload.get("created_at") if isinstance(existing_payload, dict) else None
        if not isinstance(created_at, (int, float)):
            created_at = time()

        branch = _detect_git_branch(self.cwd)
        if not branch and isinstance(existing_payload, dict):
            existing_branch = existing_payload.get("branch")
            if isinstance(existing_branch, str) and existing_branch.strip():
                branch = existing_branch.strip()

        if compact:
            checkpoint_summary, checkpointed_message_count, recent_messages = self._compact_messages(messages)
        else:
            checkpoint_summary = None
            checkpointed_message_count = 0
            recent_messages = list(messages)
        payload = {
            "cwd": str(self.cwd.resolve()),
            "session_id": resolved_session_id,
            "created_at": float(created_at),
            "saved_at": time(),
            "branch": branch or "-",
            "message_count": checkpointed_message_count + len(recent_messages),
            "checkpointed_message_count": checkpointed_message_count,
            "recent_message_count": len(recent_messages),
            "checkpoint_summary": checkpoint_summary,
            "recent_messages": [message.model_dump(mode="json") for message in recent_messages],
        }
        session_path = self._session_path(resolved_session_id)
        session_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        self._write_session_index(resolved_session_id)
        return session_path

    def session_exists(self, session_id: str) -> bool:
        if self._session_path(session_id).exists():
            return True
        legacy_payload = self._read_legacy_payload()
        if legacy_payload is None:
            return False
        return legacy_payload.get("session_id") == session_id

    def list_sessions(self, limit: int = 20) -> List[SessionSummary]:
        latest_session_id = self._read_latest_session_id()
        summaries: List[SessionSummary] = []

        if self.sessions_dir.exists():
            for path in self.sessions_dir.glob("*.json"):
                payload = self._load_session_payload(path)
                if payload is None:
                    continue
                summary = self._build_session_summary(payload, latest_session_id=latest_session_id)
                if summary is not None:
                    summaries.append(summary)

        if not summaries:
            legacy_payload = self._read_legacy_payload()
            if legacy_payload is not None:
                summary = self._build_session_summary(legacy_payload, latest_session_id=latest_session_id)
                if summary is not None:
                    summaries.append(summary)

        summaries.sort(key=lambda item: (item.saved_at, item.session_id), reverse=True)
        return summaries[:limit]

    def clear(self, session_id: Optional[str] = None) -> None:
        target_session_id = session_id or self._read_latest_session_id()
        if target_session_id:
            session_path = self._session_path(target_session_id)
            if session_path.exists():
                session_path.unlink()
        if not target_session_id or self._read_latest_session_id() == target_session_id:
            self._clear_session_index()

    def _read_legacy_payload(self) -> Optional[dict]:
        if not self.path.exists():
            return None
        payload = self._load_session_payload(self.path)
        if payload is None:
            return None
        if "latest_session_id" in payload and "recent_messages" not in payload and "messages" not in payload:
            return None
        return payload

    def _load_session_payload(self, path: Path) -> Optional[dict]:
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    def _read_latest_session_id(self) -> Optional[str]:
        payload = self._load_session_payload(self.path)
        if payload is None:
            return None
        latest_session_id = payload.get("latest_session_id")
        if isinstance(latest_session_id, str) and latest_session_id:
            return latest_session_id
        return None

    def _write_session_index(self, session_id: str) -> None:
        payload = {
            "cwd": str(self.cwd.resolve()),
            "latest_session_id": session_id,
            "updated_at": time(),
        }
        self.path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    def _clear_session_index(self) -> None:
        if self.path.exists():
            self.path.unlink()

    def _session_path(self, session_id: str) -> Path:
        return self.sessions_dir / f"{session_id}.json"

    def _decode_state_payload(
        self,
        payload: dict,
        fallback_session_id: str,
    ) -> Tuple[str, List[Message], SessionLoadMetadata]:
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            session_id = fallback_session_id

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

    def _build_session_summary(
        self,
        payload: dict,
        latest_session_id: Optional[str],
    ) -> Optional[SessionSummary]:
        session_id = payload.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            return None

        message_count = payload.get("message_count")
        if not isinstance(message_count, int):
            recent_messages = self._load_messages_list(payload.get("recent_messages") or payload.get("messages"))
            message_count = len(recent_messages)

        recent_message_count = payload.get("recent_message_count")
        if not isinstance(recent_message_count, int):
            recent_message_count = len(self._load_messages_list(payload.get("recent_messages")))

        checkpointed_message_count = payload.get("checkpointed_message_count")
        if not isinstance(checkpointed_message_count, int):
            checkpointed_message_count = max(0, message_count - recent_message_count)

        saved_at = payload.get("saved_at")
        if not isinstance(saved_at, (int, float)):
            saved_at = 0.0

        created_at = payload.get("created_at")
        if not isinstance(created_at, (int, float)):
            created_at = saved_at

        branch = payload.get("branch")
        if not isinstance(branch, str) or not branch.strip():
            branch = "-"

        return SessionSummary(
            session_id=session_id,
            saved_at=float(saved_at),
            message_count=message_count,
            recent_message_count=recent_message_count,
            checkpointed_message_count=checkpointed_message_count,
            preview=_build_session_preview(payload, self._load_messages_list),
            is_latest=session_id == latest_session_id,
            created_at=float(created_at),
            branch=branch.strip(),
        )

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


def _build_session_preview(payload: dict, load_messages_list) -> str:
    recent_messages = load_messages_list(payload.get("recent_messages") or payload.get("messages"))
    for message in reversed(recent_messages):
        preview = _preview_for_message(message)
        if preview:
            return preview

    checkpoint_summary = payload.get("checkpoint_summary")
    if isinstance(checkpoint_summary, str) and checkpoint_summary.strip():
        for line in reversed(checkpoint_summary.splitlines()):
            normalized = line.strip()
            if not normalized:
                continue
            normalized = re.sub(r"^[UAT]:\s*", "", normalized)
            if normalized:
                return _truncate_preview(normalized)

    return "(empty session)"


def _preview_for_message(message: Message) -> str:
    if message.role == "system" and _is_checkpoint_message(message):
        return ""
    text = message_text(message).strip()
    if not text:
        return ""
    return _truncate_preview(text.replace("\n", " "))


def _truncate_preview(text: str, max_chars: int = 80) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3].rstrip() + "..."


def _detect_git_branch(cwd: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
            timeout=1,
        )
    except Exception:
        return ""

    if result.returncode != 0:
        return ""

    branch = result.stdout.strip()
    if not branch:
        return ""
    if branch == "HEAD":
        return "detached"
    return branch
