from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from xagent.agent.interactions import InteractionContext
from xagent.agent.permissions import Approver
from xagent.agent.tools.base import Tool, ToolResult, tool
from xagent.config import CronPermissionConfig
from xagent.cron import CronService, CronTask


@tool(
    name="cron",
    description="Create, update, delete, or list scheduled cron tasks.",
    exclusive=True,
    parameters={
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "One of: list, create, update, delete.",
                "enum": ["list", "create", "update", "delete"],
            },
            "task_id": {
                "type": ["string", "null"],
                "description": "Cron task id for update/delete.",
                "default": None,
            },
            "task": {
                "type": ["object", "null"],
                "description": "Task payload for create.",
                "properties": {
                    "id": {
                        "type": ["string", "null"],
                        "description": "Optional task id. Defaults to cron_<8hex>.",
                    },
                    "enabled": {
                        "type": ["boolean", "null"],
                        "description": "Whether the task is active. Defaults to true.",
                    },
                    "description": {
                        "type": ["string", "null"],
                        "description": "Human-readable task description.",
                    },
                    "cron": {
                        "type": "string",
                        "description": "Standard cron expression, e.g. 0 9 * * *.",
                    },
                    "timezone": {
                        "type": ["string", "null"],
                        "description": "IANA timezone. Defaults to config cron.default_timezone.",
                    },
                    "instruction": {
                        "type": "string",
                        "description": "Instruction that will be sent to the agent when triggered.",
                    },
                    "target": {
                        "type": ["object", "null"],
                        "description": "Reply target. Omit in Lark/Weixin to use the current chat.",
                        "properties": {
                            "channel": {"type": ["string", "null"]},
                            "chat_id": {"type": ["string", "null"]},
                            "reply_to": {"type": ["string", "null"]},
                        },
                    },
                },
                "default": None,
            },
            "patch": {
                "type": ["object", "null"],
                "description": "Fields to update for update.",
                "properties": {
                    "enabled": {"type": ["boolean", "null"]},
                    "description": {"type": ["string", "null"]},
                    "cron": {
                        "type": ["string", "null"],
                        "description": "New cron expression. Also update description if the human-readable time changes.",
                    },
                    "timezone": {"type": ["string", "null"]},
                    "instruction": {"type": ["string", "null"]},
                    "target": {
                        "type": ["object", "null"],
                        "properties": {
                            "channel": {"type": ["string", "null"]},
                            "chat_id": {"type": ["string", "null"]},
                            "reply_to": {"type": ["string", "null"]},
                        },
                    },
                },
                "default": None,
            },
        },
        "required": ["action"],
    },
)
class CronTool(Tool):
    def __init__(
        self,
        *,
        service: CronService,
        approver: Approver,
        permission: CronPermissionConfig,
        current_context: Callable[[], InteractionContext],
    ) -> None:
        self.service = service
        self.approver = approver
        self.permission = permission
        self.current_context = current_context

    async def execute(
        self,
        action: str,
        task_id: str | None = None,
        task: dict[str, Any] | None = None,
        patch: dict[str, Any] | None = None,
    ) -> ToolResult:
        action = action.strip().lower()
        try:
            if action == "list":
                return self._list()
            if action == "create":
                return await self._create(task)
            if action == "update":
                return await self._update(task_id, patch)
            if action == "delete":
                return await self._delete(task_id)
        except ValueError as exc:
            return ToolResult.fail(str(exc))
        return ToolResult.fail("Unsupported cron action. Use list, create, update, or delete.")

    def _list(self) -> ToolResult:
        tasks = self.service.list_tasks()
        payload = {"tasks": [_task_summary(task) for task in tasks]}
        return ToolResult.ok(
            json.dumps(payload, ensure_ascii=False, indent=2),
            data=payload,
        )

    async def _create(self, task: dict[str, Any] | None) -> ToolResult:
        if task is None:
            return ToolResult.fail("cron create requires a task object.")
        task_input = dict(task)
        target_error = self._fill_target(task_input)
        if target_error is not None:
            return ToolResult.fail(target_error)
        target = task_input["target"]
        allowed = await self._require_permission(
            "cron.create",
            f"{target['channel']}:{target['chat_id']}",
            summary=_permission_summary(task_input),
        )
        if not allowed:
            return ToolResult.fail("Cron create denied.")
        created = self.service.create_task(task_input)
        return ToolResult.ok(
            f"Created cron task {created.id}. next_run_at={created.next_run_at}",
            data=created.to_dict(),
        )

    async def _update(self, task_id: str | None, patch: dict[str, Any] | None) -> ToolResult:
        if not task_id:
            return ToolResult.fail("cron update requires task_id.")
        if patch is None:
            return ToolResult.fail("cron update requires patch.")
        patch_input = dict(patch)
        if isinstance(patch_input.get("target"), dict):
            target_error = self._fill_target(patch_input, target_key="target", allow_current=True)
            if target_error is not None:
                return ToolResult.fail(target_error)
        existing = self._find_existing(task_id)
        if existing is None:
            return ToolResult.fail(f"Cron task {task_id!r} does not exist.")
        allowed = await self._require_permission(
            "cron.update",
            f"{existing.target.channel}:{existing.target.chat_id}",
            summary=f"Update cron task {task_id}: {json.dumps(patch_input, ensure_ascii=False)[:300]}",
        )
        if not allowed:
            return ToolResult.fail("Cron update denied.")
        updated = self.service.update_task(task_id, patch_input)
        return ToolResult.ok(
            f"Updated cron task {updated.id}. next_run_at={updated.next_run_at}",
            data=updated.to_dict(),
        )

    async def _delete(self, task_id: str | None) -> ToolResult:
        if not task_id:
            return ToolResult.fail("cron delete requires task_id.")
        existing = self._find_existing(task_id)
        if existing is None:
            return ToolResult.fail(f"Cron task {task_id!r} does not exist.")
        allowed = await self._require_permission(
            "cron.delete",
            f"{existing.target.channel}:{existing.target.chat_id}",
            summary=f"Delete cron task {task_id}.",
        )
        if not allowed:
            return ToolResult.fail("Cron delete denied.")
        self.service.delete_task(task_id)
        return ToolResult.ok(f"Deleted cron task {task_id}.")

    def _fill_target(
        self,
        payload: dict[str, Any],
        *,
        target_key: str = "target",
        allow_current: bool = True,
    ) -> str | None:
        target = payload.get(target_key)
        if target is None:
            target = {}
        if not isinstance(target, dict):
            return "cron target must be an object."
        target = dict(target)
        context = self._context_or_none()
        if allow_current and context is not None:
            if target.get("channel") in {None, "", "current"}:
                target["channel"] = context.channel
            if target.get("chat_id") in {None, "", "current"} and target.get("channel") == context.channel:
                target["chat_id"] = context.chat_id
            if (
                "reply_to" not in target
                and target.get("channel") == context.channel
                and target.get("chat_id") == context.chat_id
            ):
                target["reply_to"] = context.sender_id
        if not target.get("channel") or not target.get("chat_id"):
            return (
                "cron target is required. Create it from Lark/Weixin, or provide "
                "target.channel and target.chat_id explicitly."
            )
        if target.get("channel") == "cli":
            return "Cron tasks cannot target cli. Use lark or weixin target explicitly."
        payload[target_key] = target
        return None

    def _context_or_none(self) -> InteractionContext | None:
        try:
            return self.current_context()
        except RuntimeError:
            return None

    async def _require_permission(self, action: str, target: str, *, summary: str) -> bool:
        if self.permission.default == "allow":
            return True
        if self.permission.default == "deny":
            return False
        return await self.approver.require(action, target, summary=summary)

    def _find_existing(self, task_id: str) -> CronTask | None:
        for task in self.service.list_tasks():
            if task.id == task_id:
                return task
        return None


def _task_summary(task: CronTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "enabled": task.enabled,
        "description": task.description,
        "cron": task.schedule.expression,
        "timezone": task.schedule.timezone,
        "target": task.target.to_dict(),
        "session_id": task.session_id,
        "last_triggered_at": task.last_triggered_at,
        "next_run_at": task.next_run_at,
        "last_error": task.last_error,
    }


def _permission_summary(task: dict[str, Any]) -> str:
    target = task.get("target") or {}
    return "\n".join(
        [
            f"cron: {task.get('cron')}",
            f"timezone: {task.get('timezone') or 'default'}",
            f"target: {target.get('channel')}:{target.get('chat_id')}",
            f"instruction: {str(task.get('instruction') or '')[:300]}",
        ]
    )
