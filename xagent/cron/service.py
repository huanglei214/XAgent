from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter  # type: ignore[import-untyped]

from xagent.bus import InboundMessage, MessageBus
from xagent.cron.model import CronFile, CronSchedule, CronTarget, CronTask

SUPPORTED_TARGET_CHANNELS = {"lark", "weixin"}


class CronService:
    def __init__(
        self,
        *,
        tasks_path: Path,
        default_timezone: str = "Asia/Shanghai",
        poll_interval_seconds: float = 30.0,
        now_fn: Callable[[str], datetime] | None = None,
    ) -> None:
        self.tasks_path = tasks_path.expanduser().resolve()
        self.default_timezone = default_timezone
        self.poll_interval_seconds = poll_interval_seconds
        self._now_fn = now_fn

    def list_tasks(self) -> list[CronTask]:
        return self.load_file().tasks

    def create_task(self, task_input: Mapping[str, Any]) -> CronTask:
        cron_file = self.load_file()
        now = self._now(self.default_timezone)
        task_id = _normalize_task_id(task_input.get("id"))
        if any(task.id == task_id for task in cron_file.tasks):
            raise ValueError(f"Cron task {task_id!r} already exists.")
        task = self._task_from_input(task_input, task_id=task_id, now=now)
        task.next_run_at = self._next_run_at(task, now).isoformat()
        cron_file.tasks.append(task)
        self.save_file(cron_file)
        return task

    def update_task(self, task_id: str, patch: Mapping[str, Any]) -> CronTask:
        cron_file = self.load_file()
        task = self._find_task(cron_file, task_id)
        now = self._now(task.schedule.timezone or self.default_timezone)
        schedule_changed = False

        if "enabled" in patch:
            task.enabled = bool(patch["enabled"])
        if "description" in patch and patch["description"] is not None:
            task.description = str(patch["description"])
        if "instruction" in patch and patch["instruction"] is not None:
            task.instruction = str(patch["instruction"])
        if "cron" in patch and patch["cron"] is not None:
            task.schedule.expression = str(patch["cron"])
            schedule_changed = True
        if "timezone" in patch and patch["timezone"] is not None:
            task.schedule.timezone = str(patch["timezone"])
            schedule_changed = True
        if "target" in patch and patch["target"] is not None:
            if not isinstance(patch["target"], dict):
                raise ValueError("Cron task target patch must be an object.")
            task.target = CronTarget.from_mapping(patch["target"])

        self._validate_task(task)
        task.updated_at = now.isoformat()
        if schedule_changed or (task.enabled and not task.next_run_at):
            task.next_run_at = self._next_run_at(task, now).isoformat()
        self.save_file(cron_file)
        return task

    def delete_task(self, task_id: str) -> None:
        cron_file = self.load_file()
        before = len(cron_file.tasks)
        cron_file.tasks = [task for task in cron_file.tasks if task.id != task_id]
        if len(cron_file.tasks) == before:
            raise ValueError(f"Cron task {task_id!r} does not exist.")
        self.save_file(cron_file)

    async def tick(self, bus: MessageBus) -> None:
        cron_file = self.load_file()
        changed = False
        for task in cron_file.tasks:
            if not task.enabled:
                continue
            now = self._now(task.schedule.timezone)
            try:
                self._validate_task(task)
                if task.next_run_at is None:
                    task.next_run_at = self._next_run_at(task, now).isoformat()
                    changed = True
                    continue
                if _parse_datetime(task.next_run_at, task.schedule.timezone) > now:
                    continue
                await bus.publish_inbound(
                    InboundMessage(
                        channel=task.target.channel,
                        chat_id=task.target.chat_id,
                        sender_id=f"cron:{task.id}",
                        session_id=task.session_id,
                        content=task.instruction,
                        metadata={
                            "cron": True,
                            "task_id": task.id,
                            "triggered_at": now.isoformat(),
                        },
                    )
                )
                task.last_triggered_at = now.isoformat()
                task.next_run_at = self._next_run_at(task, now).isoformat()
                task.last_error = None
            except Exception as exc:  # noqa: BLE001 - persist scheduler diagnostics
                task.last_error = f"{type(exc).__name__}: {exc}"
            task.updated_at = now.isoformat()
            changed = True
        if changed:
            self.save_file(cron_file)

    async def run(self, bus: MessageBus) -> None:
        self._skip_missed_tasks()
        while True:
            await self.tick(bus)
            await asyncio.sleep(self.poll_interval_seconds)

    def load_file(self) -> CronFile:
        if not self.tasks_path.exists():
            cron_file = CronFile()
            self.save_file(cron_file)
            return cron_file
        try:
            payload = json.loads(self.tasks_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid cron tasks file {self.tasks_path}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Invalid cron tasks file {self.tasks_path}: root must be an object.")
        return CronFile.from_mapping(payload)

    def save_file(self, cron_file: CronFile) -> None:
        self.tasks_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.tasks_path.with_name(f"{self.tasks_path.name}.tmp")
        content = json.dumps(cron_file.to_dict(), ensure_ascii=False, indent=2) + "\n"
        tmp_path.write_text(content, encoding="utf-8")
        tmp_path.replace(self.tasks_path)

    def _skip_missed_tasks(self) -> None:
        cron_file = self.load_file()
        changed = False
        for task in cron_file.tasks:
            if not task.enabled:
                continue
            now = self._now(task.schedule.timezone)
            try:
                self._validate_task(task)
                next_run_at = (
                    _parse_datetime(task.next_run_at, task.schedule.timezone)
                    if task.next_run_at
                    else None
                )
                if next_run_at is None or next_run_at <= now:
                    task.next_run_at = self._next_run_at(task, now).isoformat()
                    task.updated_at = now.isoformat()
                    changed = True
            except Exception as exc:  # noqa: BLE001 - persist scheduler diagnostics
                task.last_error = f"{type(exc).__name__}: {exc}"
                task.updated_at = now.isoformat()
                changed = True
        if changed:
            self.save_file(cron_file)

    def _task_from_input(
        self,
        task_input: Mapping[str, Any],
        *,
        task_id: str,
        now: datetime,
    ) -> CronTask:
        target_payload = task_input.get("target")
        if not isinstance(target_payload, dict):
            target_payload = {}
        timezone_name = str(task_input.get("timezone") or self.default_timezone)
        task = CronTask(
            id=task_id,
            enabled=bool(task_input.get("enabled", True)),
            description=str(task_input.get("description") or ""),
            schedule=CronSchedule(
                type="cron",
                expression=str(task_input.get("cron") or ""),
                timezone=timezone_name,
            ),
            instruction=str(task_input.get("instruction") or ""),
            target=CronTarget.from_mapping(target_payload),
            session_id=f"cron:{task_id}",
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
        )
        self._validate_task(task)
        return task

    def _validate_task(self, task: CronTask) -> None:
        if not task.id:
            raise ValueError("Cron task id is required.")
        if task.schedule.type != "cron":
            raise ValueError("Only cron schedules are supported.")
        _zoneinfo(task.schedule.timezone)
        if not task.schedule.expression or not croniter.is_valid(task.schedule.expression):
            raise ValueError(f"Invalid cron expression: {task.schedule.expression!r}")
        if not task.instruction.strip():
            raise ValueError("Cron task instruction is required.")
        if task.target.channel not in SUPPORTED_TARGET_CHANNELS:
            raise ValueError("Cron task target.channel must be 'lark' or 'weixin'.")
        if not task.target.chat_id:
            raise ValueError("Cron task target.chat_id is required.")
        expected_session_id = f"cron:{task.id}"
        if task.session_id != expected_session_id:
            task.session_id = expected_session_id

    def _next_run_at(self, task: CronTask, base: datetime) -> datetime:
        tz = _zoneinfo(task.schedule.timezone)
        base_in_tz = base.astimezone(tz)
        next_run = croniter(task.schedule.expression, base_in_tz).get_next(datetime)
        if next_run.tzinfo is None:
            next_run = next_run.replace(tzinfo=tz)
        return next_run.astimezone(tz)

    def _find_task(self, cron_file: CronFile, task_id: str) -> CronTask:
        for task in cron_file.tasks:
            if task.id == task_id:
                return task
        raise ValueError(f"Cron task {task_id!r} does not exist.")

    def _now(self, timezone_name: str) -> datetime:
        if self._now_fn is not None:
            value = self._now_fn(timezone_name)
            if value.tzinfo is None:
                return value.replace(tzinfo=_zoneinfo(timezone_name))
            return value.astimezone(_zoneinfo(timezone_name))
        return datetime.now(_zoneinfo(timezone_name))


def _normalize_task_id(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return f"cron_{uuid4().hex[:8]}"
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip("-_.")
    if not normalized:
        return f"cron_{uuid4().hex[:8]}"
    return normalized[:80]


def _parse_datetime(value: str, timezone_name: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    tz = _zoneinfo(timezone_name)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=tz)
    return parsed.astimezone(tz)


def _zoneinfo(timezone_name: str) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Invalid timezone: {timezone_name!r}") from exc
