import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Union
from uuid import uuid4

from xagent.foundation.runtime.paths import ensure_config_dir, get_trace_index_file, get_traces_dir


def classify_task_kind(prompt: str) -> str:
    lowered = prompt.lower()
    if any(word in lowered for word in ["fix", "debug", "bug", "error", "traceback"]):
        return "debug"
    if any(word in lowered for word in ["write", "edit", "update", "modify", "change", "implement", "create"]):
        return "edit"
    if any(word in lowered for word in ["review", "analyze", "audit"]):
        return "review"
    if any(word in lowered for word in ["read", "find", "search", "list", "show", "summarize", "explain"]):
        return "read"
    return "general"


class TraceRecorder:
    def __init__(
        self,
        cwd: Union[str, Path],
        mode: str,
        model: str,
        provider: str,
        task_kind: str,
        session_id: Optional[str] = None,
        tags: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.cwd = Path(cwd)
        self.mode = mode
        self.model = model
        self.provider = provider
        self.task_kind = task_kind
        self.session_id = session_id or str(uuid4())
        self.trace_id = str(uuid4())
        self.turn_id = self.trace_id
        self.started_at = _utc_now()
        self.ended_at: Optional[str] = None
        self.status = "running"
        self.error: Optional[str] = None
        self.termination_reason: Optional[str] = None
        self.base_tags = {
            "mode": mode,
            "provider": provider,
            "model": model,
            "task_kind": task_kind,
            **(tags or {}),
        }
        traces_dir = get_traces_dir(self.cwd)
        ensure_config_dir(self.cwd)
        traces_dir.mkdir(parents=True, exist_ok=True)
        self.path = traces_dir / f"{self.trace_id}.ndjson"

    def emit(
        self,
        event_type: str,
        payload: Dict[str, Any],
        tags: Optional[Dict[str, Any]] = None,
        parent_event_id: Optional[str] = None,
    ) -> str:
        event_id = str(uuid4())
        event = {
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "event_id": event_id,
            "parent_event_id": parent_event_id,
            "timestamp": _utc_now(),
            "event_type": event_type,
            "tags": {**self.base_tags, **(tags or {})},
            "payload": payload,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        return event_id

    def record_state_snapshot(self, agent, name: str, extra: Optional[Dict[str, Any]] = None) -> None:
        payload = {
            "name": name,
            "cwd": str(Path(getattr(agent, "cwd", self.cwd)).resolve()),
            "message_count": len(getattr(agent, "messages", [])),
            "tool_count": len(getattr(agent, "tools", [])),
        }
        if extra:
            payload.update(extra)
        self.emit("state_snapshot", payload=payload)

    def finish_success(self, output_text: str, duration_seconds: float, termination_reason: str = "completed") -> None:
        self.status = "success"
        self.termination_reason = termination_reason
        self.ended_at = _utc_now()
        self.emit(
            "task_finished",
            payload={
                "output_text": output_text,
                "duration_seconds": duration_seconds,
                "termination_reason": termination_reason,
            },
            tags={"status": self.status, "termination_reason": termination_reason},
        )
        self._update_index(output_text=output_text)

    def finish_failure(
        self,
        error: str,
        stage: str,
        duration_seconds: float,
        termination_reason: Optional[str] = None,
    ) -> None:
        self.status = "failed"
        self.error = error
        self.termination_reason = termination_reason
        self.ended_at = _utc_now()
        self.emit(
            "task_failed",
            payload={
                "error": error,
                "failure_stage": stage,
                "duration_seconds": duration_seconds,
                "termination_reason": termination_reason,
            },
            tags={
                "status": self.status,
                "failure_stage": stage,
                **({"termination_reason": termination_reason} if termination_reason else {}),
            },
        )
        self._update_index()

    def _update_index(self, output_text: Optional[str] = None) -> None:
        index_path = get_trace_index_file(self.cwd)
        index_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            existing = json.loads(index_path.read_text(encoding="utf-8")) if index_path.exists() else []
        except Exception:
            existing = []
        if not isinstance(existing, list):
            existing = []

        summary = {
            "trace_id": self.trace_id,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "mode": self.mode,
            "provider": self.provider,
            "model": self.model,
            "task_kind": self.task_kind,
            "status": self.status,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "trace_file": str(self.path),
            "error": self.error,
            "termination_reason": self.termination_reason,
            "output_preview": (output_text or "")[:400],
            "tags": self.base_tags,
        }

        existing = [item for item in existing if item.get("trace_id") != self.trace_id]
        existing.append(summary)
        index_path.write_text(json.dumps(existing, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
