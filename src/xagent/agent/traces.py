from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Dict, List, Optional, Union
from uuid import uuid4

from xagent.agent.core.middleware import AgentMiddleware
from xagent.agent.paths import (
    ensure_config_dir,
    get_trace_artifacts_dir,
    get_trace_index_file,
    get_traces_dir,
)
from xagent.provider.types import message_text


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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        self.artifacts_dir = get_trace_artifacts_dir(self.cwd) / self.trace_id
        ensure_config_dir(self.cwd)
        traces_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
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

    def write_artifact(self, name: str, payload: Any) -> Path:
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        path = self.artifacts_dir / name
        if isinstance(payload, str):
            path.write_text(payload, encoding="utf-8")
            return path
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return path

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

    def finish_cancelled(self, reason: str, duration_seconds: float) -> None:
        self.status = "cancelled"
        self.error = reason
        self.termination_reason = "aborted"
        self.ended_at = _utc_now()
        self.emit(
            "task_cancelled",
            payload={"error": reason, "duration_seconds": duration_seconds, "termination_reason": "aborted"},
            tags={"status": self.status, "termination_reason": "aborted"},
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


def load_trace_index(cwd: Union[str, Path]) -> List[Dict[str, Any]]:
    index_path = get_trace_index_file(Path(cwd))
    if not index_path.exists():
        return []
    try:
        data = json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def list_failed_traces(cwd: Union[str, Path], limit: int = 10) -> List[Dict[str, Any]]:
    items = [item for item in load_trace_index(cwd) if item.get("status") == "failed"]
    return sorted(items, key=_sort_key, reverse=True)[:limit]


def list_traces(
    cwd: Union[str, Path],
    limit: int = 20,
    session_id: Optional[str] = None,
    status: Optional[str] = None,
    termination_reason: Optional[str] = None,
    tool_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    items = load_trace_index(cwd)
    filtered = []
    for item in items:
        if session_id and item.get("session_id") != session_id:
            continue
        if status and item.get("status") != status:
            continue
        if termination_reason and item.get("termination_reason") != termination_reason:
            continue
        if tool_name and not _trace_uses_tool(item, tool_name):
            continue
        filtered.append(item)
    return sorted(filtered, key=_sort_key, reverse=True)[:limit]


def get_latest_trace(cwd: Union[str, Path]) -> Optional[Dict[str, Any]]:
    items = load_trace_index(cwd)
    if not items:
        return None
    return sorted(items, key=_sort_key, reverse=True)[0]


def get_trace_summary(cwd: Union[str, Path], trace_id: str) -> Optional[Dict[str, Any]]:
    for item in load_trace_index(cwd):
        if item.get("trace_id") == trace_id:
            return item
    return None


def load_trace_events(trace_file: Union[str, Path]) -> List[Dict[str, Any]]:
    path = Path(trace_file)
    if not path.exists():
        return []
    events: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except Exception:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def summarize_trace(summary: Dict[str, Any]) -> Dict[str, Any]:
    events = load_trace_events(summary.get("trace_file", ""))
    tool_stats: Dict[str, Dict[str, int]] = {}
    approval_decisions: Dict[str, int] = {}
    external_path_decisions: Dict[str, int] = {}
    step_count = 0

    for event in events:
        event_type = event.get("event_type")
        tags = event.get("tags", {}) or {}
        if event_type == "agent_step_started":
            step_count = max(step_count, int(tags.get("step", 0) or 0))
        if event_type == "tool_call_started":
            tool_name = str(tags.get("tool_name", ""))
            if tool_name:
                stats = tool_stats.setdefault(tool_name, {"started": 0, "success": 0, "error": 0})
                stats["started"] += 1
        if event_type == "tool_call_finished":
            tool_name = str(tags.get("tool_name", ""))
            if tool_name:
                stats = tool_stats.setdefault(tool_name, {"started": 0, "success": 0, "error": 0})
                if tags.get("status") == "error":
                    stats["error"] += 1
                else:
                    stats["success"] += 1
        if event_type == "approval_decided":
            decision = str((event.get("payload", {}) or {}).get("decision", "unknown"))
            approval_decisions[decision] = approval_decisions.get(decision, 0) + 1
        if event_type == "external_path_access_decided":
            decision = str((event.get("payload", {}) or {}).get("decision", "unknown"))
            external_path_decisions[decision] = external_path_decisions.get(decision, 0) + 1

    return {
        "step_count": step_count,
        "tool_stats": tool_stats,
        "approval_decisions": approval_decisions,
        "external_path_decisions": external_path_decisions,
        "restored_context": bool((summary.get("tags") or {}).get("session_restored")),
        "event_count": len(events),
    }


def summarize_sessions(cwd: Union[str, Path], limit: int = 20) -> List[Dict[str, Any]]:
    sessions: Dict[str, Dict[str, Any]] = {}
    for item in sorted(load_trace_index(cwd), key=_sort_key):
        session_id = str(item.get("session_id") or "")
        if not session_id:
            continue
        session = sessions.setdefault(
            session_id,
            {
                "session_id": session_id,
                "trace_count": 0,
                "started_at": item.get("started_at"),
                "last_activity": item.get("ended_at") or item.get("started_at"),
                "latest_status": item.get("status"),
                "latest_reason": item.get("termination_reason"),
                "reasons": {},
                "statuses": {},
                "tools": {},
                "restored_turns": 0,
                "timeline": [],
            },
        )
        session["trace_count"] += 1
        session["last_activity"] = item.get("ended_at") or item.get("started_at") or session["last_activity"]
        session["latest_status"] = item.get("status")
        session["latest_reason"] = item.get("termination_reason")
        reason = str(item.get("termination_reason") or "-")
        status = str(item.get("status") or "-")
        session["reasons"][reason] = session["reasons"].get(reason, 0) + 1
        session["statuses"][status] = session["statuses"].get(status, 0) + 1

        derived = summarize_trace(item)
        if derived.get("restored_context"):
            session["restored_turns"] += 1
        for tool_name, stats in derived.get("tool_stats", {}).items():
            aggregate = session["tools"].setdefault(tool_name, {"started": 0, "success": 0, "error": 0})
            aggregate["started"] += stats.get("started", 0)
            aggregate["success"] += stats.get("success", 0)
            aggregate["error"] += stats.get("error", 0)
        session["timeline"].append(
            {
                "trace_id": item.get("trace_id"),
                "started_at": item.get("started_at"),
                "status": item.get("status"),
                "reason": item.get("termination_reason"),
                "task_kind": item.get("task_kind"),
            }
        )

    ordered = sorted(sessions.values(), key=lambda item: str(item.get("last_activity") or ""), reverse=True)
    return ordered[:limit]


def _trace_uses_tool(summary: Dict[str, Any], tool_name: str) -> bool:
    for event in load_trace_events(summary.get("trace_file", "")):
        tags = event.get("tags", {}) or {}
        if str(tags.get("tool_name", "")) == tool_name:
            return True
    return False


def _sort_key(item: Dict[str, Any]) -> str:
    return str(item.get("started_at") or item.get("ended_at") or "")


class TraceMiddleware(AgentMiddleware):
    def __init__(self) -> None:
        self._started: float | None = None
        self._model_call_index = 0

    async def before_agent_run(self, *, agent, user_text: str) -> None:
        recorder = TraceRecorder(
            cwd=getattr(agent, "cwd", "."),
            mode=getattr(agent, "runtime_mode", "run"),
            model=getattr(agent, "model", "unknown"),
            provider=getattr(agent, "provider_name", "unknown"),
            task_kind=classify_task_kind(user_text),
            session_id=getattr(agent, "trace_session_id", None),
            tags={"session_restored": bool(getattr(agent, "messages", [])[:-1])},
        )
        agent.trace_recorder = recorder
        agent.last_trace_recorder = recorder
        self._started = perf_counter()
        self._model_call_index = 0
        recorder.emit("task_started", payload={"input": user_text}, tags={"status": "started"})
        recorder.emit("user_input", payload={"text": user_text})

    async def after_agent_run(self, *, agent, final_message) -> None:
        recorder = getattr(agent, "trace_recorder", None)
        if recorder is None:
            return
        duration = self._duration()
        output_text = message_text(final_message)
        if not output_text.strip():
            recorder.emit("assistant_output_empty", payload={"message_count": len(getattr(agent, "messages", []))})
        recorder.record_state_snapshot(agent, "post_turn")
        recorder.finish_success(
            output_text=output_text,
            duration_seconds=duration,
            termination_reason=getattr(agent, "last_termination_reason", None) or "completed",
        )
        agent.trace_recorder = None

    async def before_agent_step(self, *, agent, step: int) -> None:
        recorder = getattr(agent, "trace_recorder", None)
        if recorder is not None:
            recorder.emit("agent_step_started", payload={"step": step}, tags={"step": step})

    async def after_agent_step(self, *, agent, step: int) -> None:
        recorder = getattr(agent, "trace_recorder", None)
        if recorder is not None:
            recorder.emit(
                "agent_step_finished",
                payload={"step": step, "message_count": len(getattr(agent, "messages", []))},
                tags={"step": step},
            )

    async def before_model(self, *, agent, request):
        recorder = getattr(agent, "trace_recorder", None)
        if recorder is not None:
            self._model_call_index += 1
            artifact_path = recorder.write_artifact(
                f"step-{self._model_call_index}-request.json",
                request.model_dump(mode="json"),
            )
            recorder.emit(
                "model_request_artifact_written",
                payload={"artifact_path": str(artifact_path), "model_call_index": self._model_call_index},
                tags={"status": "written", "step": self._model_call_index},
            )
            recorder.emit("model_request", payload=request.model_dump(mode="json"))
        return request

    async def after_model(self, *, agent, assistant_message) -> None:
        recorder = getattr(agent, "trace_recorder", None)
        if recorder is not None:
            artifact_path = recorder.write_artifact(
                f"step-{self._model_call_index}-response.json",
                assistant_message.model_dump(mode="json"),
            )
            recorder.emit(
                "model_response_artifact_written",
                payload={"artifact_path": str(artifact_path), "model_call_index": self._model_call_index},
                tags={"status": "written", "step": self._model_call_index},
            )
            recorder.emit("model_response", payload=assistant_message.model_dump(mode="json"))

    async def before_tool(self, *, agent, tool_use):
        recorder = getattr(agent, "trace_recorder", None)
        if recorder is not None:
            recorder.emit(
                "tool_call_started",
                payload=tool_use.model_dump(mode="json"),
                tags={"tool_name": tool_use.name},
            )
        return None

    async def after_tool(self, *, agent, tool_use, result) -> None:
        recorder = getattr(agent, "trace_recorder", None)
        if recorder is not None:
            recorder.emit(
                "tool_call_finished",
                payload={
                    "tool_name": tool_use.name,
                    "tool_use_id": tool_use.id,
                    "result": result.model_dump(mode="json"),
                },
                tags={"tool_name": tool_use.name, "status": "error" if result.is_error else "success"},
            )

    def _duration(self) -> float:
        if self._started is None:
            return 0.0
        return perf_counter() - self._started
