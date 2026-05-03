from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sanitize_id(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.:-]+", "-", value).strip("-:")
    return cleaned or uuid4().hex[:8]


def new_session_id(channel: str = "cli", chat_id: str | None = None) -> str:
    if chat_id:
        return session_id_from_chat(channel, chat_id)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"{sanitize_id(channel)}-{stamp}-{uuid4().hex[:6]}"


def session_id_from_chat(channel: str, chat_id: str) -> str:
    return f"{sanitize_id(channel)}:{sanitize_id(chat_id)}"


def resolve_session_id(
    *,
    channel: str,
    chat_id: str,
    session_id: str | None = None,
) -> str:
    if session_id:
        return sanitize_id(session_id)
    return session_id_from_chat(channel, chat_id)


@dataclass
class Session:
    session_id: str
    path: Path
    workspace_path: Path

    @property
    def messages_path(self) -> Path:
        return self.path / "messages.jsonl"

    @property
    def trace_path(self) -> Path:
        return self.path / "trace.jsonl"

    @property
    def artifacts_path(self) -> Path:
        return self.path / "artifacts"

    def append_message(self, message: dict[str, Any]) -> None:
        self._append_jsonl(self.messages_path, {"type": "message", "at": utc_now(), "message": message})

    def append_summary(self, content: str) -> None:
        self._append_jsonl(self.messages_path, {"type": "summary", "at": utc_now(), "content": content})

    def append_trace(self, kind: str, payload: dict[str, Any]) -> None:
        self._append_jsonl(self.trace_path, {"type": kind, "at": utc_now(), **payload})

    def read_records(self) -> list[dict[str, Any]]:
        return list(self._iter_jsonl(self.messages_path))

    def read_model_messages(self) -> list[dict[str, Any]]:
        records = self.read_records()
        latest_summary_index = -1
        latest_summary = None
        for idx, record in enumerate(records):
            if record.get("type") == "summary":
                latest_summary_index = idx
                latest_summary = str(record.get("content") or "")

        messages: list[dict[str, Any]] = []
        if latest_summary:
            messages.append(
                {
                    "role": "system",
                    "content": "Conversation summary:\n" + latest_summary,
                }
            )
        for record in records[latest_summary_index + 1 :]:
            if record.get("type") == "message" and isinstance(record.get("message"), dict):
                messages.append(record["message"])
        return messages

    def approximate_context_size(self) -> int:
        total = 0
        for message in self.read_model_messages():
            total += len(json.dumps(message, ensure_ascii=False))
        return total

    @staticmethod
    def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    @staticmethod
    def _iter_jsonl(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        records: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
        return records


class SessionStore:
    def __init__(self, sessions_path: Path) -> None:
        self.sessions_path = sessions_path
        self.sessions_path.mkdir(parents=True, exist_ok=True)

    def create(
        self,
        *,
        workspace_path: Path,
        channel: str = "cli",
        chat_id: str | None = None,
    ) -> Session:
        session_id = new_session_id(channel=channel, chat_id=chat_id)
        return self._initialize(session_id=session_id, workspace_path=workspace_path)

    def open_or_create(self, session_id: str, *, workspace_path: Path) -> Session:
        safe_session_id = sanitize_id(session_id)
        path = self.sessions_path / safe_session_id
        messages_path = path / "messages.jsonl"
        if messages_path.exists():
            return self.open(safe_session_id)
        if path.exists():
            raise ValueError(f"Session directory {path} exists but has no messages.jsonl")
        return self._initialize(
            session_id=safe_session_id,
            workspace_path=workspace_path,
            ensure_unique=False,
        )

    def open_for_chat(
        self,
        *,
        workspace_path: Path,
        channel: str,
        chat_id: str,
        session_id: str | None = None,
    ) -> Session:
        resolved_session_id = resolve_session_id(
            channel=channel,
            chat_id=chat_id,
            session_id=session_id,
        )
        return self.open_or_create(resolved_session_id, workspace_path=workspace_path)

    def open(self, session_id: str) -> Session:
        path = self.sessions_path / sanitize_id(session_id)
        messages_path = path / "messages.jsonl"
        if not messages_path.exists():
            raise KeyError(session_id)
        meta = self._read_meta(messages_path)
        workspace_path = Path(meta.get("workspace_path") or ".").expanduser().resolve()
        return Session(session_id=path.name, path=path, workspace_path=workspace_path)

    def _initialize(
        self,
        *,
        session_id: str,
        workspace_path: Path,
        ensure_unique: bool = True,
    ) -> Session:
        path = self.sessions_path / sanitize_id(session_id)
        suffix = 1
        original = path
        while ensure_unique and path.exists():
            suffix += 1
            path = original.with_name(f"{original.name}-{suffix}")
        if path.exists():
            raise FileExistsError(path)
        path.mkdir(parents=True)
        (path / "artifacts").mkdir()
        session = Session(session_id=path.name, path=path, workspace_path=workspace_path.resolve())
        meta = {
            "type": "meta",
            "session_id": session.session_id,
            "created_at": utc_now(),
            "workspace_path": str(session.workspace_path),
        }
        Session._append_jsonl(session.messages_path, meta)
        Session._append_jsonl(
            session.trace_path,
            {"type": "meta", "at": utc_now(), "session_id": session.session_id},
        )
        return session

    @staticmethod
    def _read_meta(path: Path) -> dict[str, Any]:
        records = Session._iter_jsonl(path)
        if not records or records[0].get("type") != "meta":
            raise ValueError(f"Session file {path} is missing a meta record")
        return records[0]
