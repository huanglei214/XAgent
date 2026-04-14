import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from xagent.foundation.runtime.paths import get_trace_index_file


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
