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


def _sort_key(item: Dict[str, Any]) -> str:
    return str(item.get("started_at") or item.get("ended_at") or "")
