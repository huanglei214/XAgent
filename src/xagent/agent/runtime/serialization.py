from __future__ import annotations

from typing import Any, Optional

from xagent.bus.events import Event
from xagent.provider.types import Message, message_text
from xagent.agent.runtime.scheduler import ScheduledJobRecord, ScheduledJobHistoryEntry


def serialize_message(message: Message) -> dict[str, Any]:
    """Serialize a Message to a JSON-friendly dict with a computed text field."""
    payload = message.model_dump(mode="json")
    payload["text"] = message_text(message)
    return payload


def serialize_job(job: ScheduledJobRecord) -> dict[str, Any]:
    """Serialize a ScheduledJobRecord to a JSON-friendly dict."""
    return {
        "job_id": job.job_id,
        "session_id": job.session_id,
        "text": job.text,
        "schedule_type": job.schedule_type,
        "cron_expression": job.cron_expression,
        "run_at": job.run_at,
        "next_run_at": job.next_run_at,
        "requested_skill_name": job.requested_skill_name,
        "enabled": job.enabled,
        "last_run_at": job.last_run_at,
        "last_error": job.last_error,
        "last_result_text": job.last_result_text,
        "retry_enabled": job.retry_enabled,
        "retry_delay_seconds": job.retry_delay_seconds,
        "retry_backoff_multiplier": job.retry_backoff_multiplier,
        "max_retries": job.max_retries,
        "retry_count": job.retry_count,
        "created_at": job.created_at,
        "updated_at": job.updated_at,
    }


def serialize_job_history(entry: ScheduledJobHistoryEntry) -> dict[str, Any]:
    """Serialize a ScheduledJobHistoryEntry to a JSON-friendly dict."""
    return {
        "history_id": entry.history_id,
        "job_id": entry.job_id,
        "session_id": entry.session_id,
        "status": entry.status,
        "text": entry.text,
        "source": entry.source,
        "recorded_at": entry.recorded_at,
        "result_text": entry.result_text,
        "error_text": entry.error_text,
        "attempt": entry.attempt,
    }


def serialize_event(event: Event) -> dict[str, Any]:
    """Serialize an Event to a JSON-friendly dict with recursively jsonable payload."""
    return {
        "event_id": event.event_id,
        "topic": event.topic,
        "session_id": event.session_id,
        "source": event.source,
        "created_at": event.created_at,
        "payload": to_jsonable(event.payload),
    }


def to_jsonable(value: Any) -> Any:
    """Recursively convert a value to a JSON-serializable representation."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [to_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): to_jsonable(item) for key, item in value.items()}
    if hasattr(value, "model_dump"):
        try:
            return to_jsonable(value.model_dump(mode="json"))
        except TypeError:
            return to_jsonable(value.model_dump())
    return str(value)


def build_turn_response(
    runtime: Any,
    final_message: Message,
    duration_seconds: Optional[float],
    *,
    job_id: Optional[str] = None,
) -> dict[str, Any]:
    """Build the response dict for a completed agent turn."""
    payload = {
        "session_id": runtime.session_id,
        "message": serialize_message(final_message),
        "text": message_text(final_message),
        "status": build_status(runtime),
    }
    if duration_seconds is not None:
        payload["duration_seconds"] = duration_seconds
    if job_id is not None:
        payload["job_id"] = job_id
    return payload


def build_status(runtime: Any) -> dict[str, Any]:
    """Build a status summary dict for a runtime instance."""
    working_memory = getattr(runtime, "working_memory", None)
    return {
        "session_id": runtime.session_id,
        "message_count": len(runtime.messages),
        "active_tools": list(getattr(working_memory, "active_tools", [])) if working_memory is not None else [],
        "requested_skill_name": getattr(working_memory, "requested_skill_name", None)
        if working_memory is not None
        else None,
        "current_plan": getattr(working_memory, "current_plan", None) if working_memory is not None else None,
        "scratchpad_keys": sorted(getattr(working_memory, "scratchpad", {}).keys())
        if working_memory is not None
        else [],
    }
