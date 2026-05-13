from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class CronSchedule:
    type: str = "cron"
    expression: str = ""
    timezone: str = "Asia/Shanghai"

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "CronSchedule":
        return cls(
            type=str(payload.get("type") or "cron"),
            expression=str(payload.get("expression") or ""),
            timezone=str(payload.get("timezone") or "Asia/Shanghai"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CronTarget:
    channel: str
    chat_id: str
    reply_to: str | None = None

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "CronTarget":
        return cls(
            channel=str(payload.get("channel") or ""),
            chat_id=str(payload.get("chat_id") or ""),
            reply_to=payload.get("reply_to"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CronTask:
    id: str
    enabled: bool
    description: str
    schedule: CronSchedule
    instruction: str
    target: CronTarget
    session_id: str
    created_at: str
    updated_at: str
    last_triggered_at: str | None = None
    next_run_at: str | None = None
    last_error: str | None = None

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "CronTask":
        schedule_payload = payload.get("schedule") or {}
        target_payload = payload.get("target") or {}
        if not isinstance(schedule_payload, dict):
            schedule_payload = {}
        if not isinstance(target_payload, dict):
            target_payload = {}
        return cls(
            id=str(payload.get("id") or ""),
            enabled=bool(payload.get("enabled", True)),
            description=str(payload.get("description") or ""),
            schedule=CronSchedule.from_mapping(schedule_payload),
            instruction=str(payload.get("instruction") or ""),
            target=CronTarget.from_mapping(target_payload),
            session_id=str(payload.get("session_id") or ""),
            created_at=str(payload.get("created_at") or ""),
            updated_at=str(payload.get("updated_at") or ""),
            last_triggered_at=payload.get("last_triggered_at"),
            next_run_at=payload.get("next_run_at"),
            last_error=payload.get("last_error"),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["schedule"] = self.schedule.to_dict()
        payload["target"] = self.target.to_dict()
        return payload


@dataclass
class CronFile:
    version: int = 1
    tasks: list[CronTask] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "CronFile":
        tasks_payload = payload.get("tasks") or []
        if not isinstance(tasks_payload, list):
            tasks_payload = []
        tasks = [
            CronTask.from_mapping(item)
            for item in tasks_payload
            if isinstance(item, dict)
        ]
        return cls(version=int(payload.get("version") or 1), tasks=tasks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "tasks": [task.to_dict() for task in self.tasks],
        }
