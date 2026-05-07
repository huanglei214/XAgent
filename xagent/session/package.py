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
    def summary_path(self) -> Path:
        return self.path / "summary.jsonl"

    @property
    def session_state_path(self) -> Path:
        return self.path / "session_state.json"

    @property
    def artifacts_path(self) -> Path:
        return self.path / "artifacts"

    def append_message(self, message: dict[str, Any]) -> None:
        self._append_jsonl(self.messages_path, {"type": "message", "at": utc_now(), "message": message})

    def append_summary(
        self,
        content: str,
        *,
        messages_until_index: int | None = None,
        previous_summary_id: str | None = None,
        kind: str = "context",
    ) -> dict[str, Any]:
        compact = self.read_session_state().get("compact", {})
        summary_id = f"sum_{uuid4().hex}"
        if messages_until_index is None:
            messages_until_index = self.latest_message_record_index()
        if previous_summary_id is None:
            previous_summary_id = compact.get("latest_summary_id")
        record = {
            "type": "summary",
            "summary_id": summary_id,
            "kind": kind,
            "created_at": utc_now(),
            "covers": {
                "messages_until_index": messages_until_index,
                "previous_summary_id": previous_summary_id,
            },
            "content": content,
        }
        self._append_jsonl(self.summary_path, record)
        self.write_session_state(
            {
                "compact": {
                    "messages_until_index": messages_until_index,
                    "latest_summary_id": summary_id,
                }
            }
        )
        return record

    def append_trace(self, kind: str, payload: dict[str, Any]) -> None:
        self._append_jsonl(self.trace_path, {"type": kind, "at": utc_now(), **payload})

    def read_records(self) -> list[dict[str, Any]]:
        return list(self._iter_jsonl(self.messages_path))

    def read_summary_records(self) -> list[dict[str, Any]]:
        return [
            record
            for record in self._iter_jsonl(self.summary_path)
            if record.get("type") == "summary"
        ]

    def read_session_state(self) -> dict[str, Any]:
        if not self.session_state_path.exists():
            return self.default_session_state()
        return json.loads(self.session_state_path.read_text(encoding="utf-8"))

    def write_session_state(self, state: dict[str, Any]) -> None:
        self.session_state_path.parent.mkdir(parents=True, exist_ok=True)
        self.session_state_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def read_model_messages(self) -> list[dict[str, Any]]:
        state = self.read_session_state().get("compact", {})
        latest_summary_id = state.get("latest_summary_id")
        compacted_until = int(state.get("messages_until_index") or 0)
        latest_summary = self.summary_by_id(str(latest_summary_id)) if latest_summary_id else None
        if latest_summary:
            return [
                {
                    "role": "system",
                    "content": "Conversation summary:\n" + str(latest_summary.get("content") or ""),
                },
                *self.messages_after_record_index(compacted_until),
            ]
        return self._read_model_messages_compat()

    def _read_model_messages_compat(self) -> list[dict[str, Any]]:
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

    def latest_message_record_index(self) -> int:
        latest = 0
        for idx, record in enumerate(self.read_records()):
            if record.get("type") == "message":
                latest = idx
        return latest

    def messages_after_record_index(self, record_index: int) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        for idx, record in enumerate(self.read_records()):
            if idx <= record_index:
                continue
            if record.get("type") == "message" and isinstance(record.get("message"), dict):
                messages.append(record["message"])
        return messages

    def summary_by_id(self, summary_id: str) -> dict[str, Any] | None:
        for record in self.read_summary_records():
            if record.get("summary_id") == summary_id:
                return record
        return None

    def summary_records_after(self, summary_id: str | None) -> list[dict[str, Any]]:
        records = self.read_summary_records()
        if not summary_id:
            return records
        for idx, record in enumerate(records):
            if record.get("summary_id") == summary_id:
                return records[idx + 1 :]
        return records

    @staticmethod
    def default_session_state() -> dict[str, Any]:
        return {"compact": {"messages_until_index": 0, "latest_summary_id": None}}

    def ensure_sidecar_files(self) -> None:
        self.artifacts_path.mkdir(parents=True, exist_ok=True)
        if not self.summary_path.exists():
            self._append_jsonl(self.summary_path, {"type": "meta", "at": utc_now()})
        if not self.session_state_path.exists():
            self.write_session_state(self.default_session_state())

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
        session = Session(session_id=path.name, path=path, workspace_path=workspace_path)
        session.ensure_sidecar_files()
        return session

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
        Session._append_jsonl(session.summary_path, {"type": "meta", "at": utc_now()})
        session.write_session_state(Session.default_session_state())
        return session

    @staticmethod
    def _read_meta(path: Path) -> dict[str, Any]:
        records = Session._iter_jsonl(path)
        if not records or records[0].get("type") != "meta":
            raise ValueError(f"Session file {path} is missing a meta record")
        return records[0]
