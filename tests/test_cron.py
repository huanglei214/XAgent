from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from xagent.agent.interactions import InteractionContext
from xagent.agent.permissions import SessionApprover
from xagent.agent.tools.cron import CronTool
from xagent.agent.tools.default_tools import build_default_tools
from xagent.bus import MessageBus
from xagent.config import CronPermissionConfig
from xagent.cron import CronFile, CronSchedule, CronService, CronTarget, CronTask


class TrackingApprover:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed
        self.calls: list[tuple[str, str, str]] = []

    async def require(self, action: str, target: str, *, summary: str = "") -> bool:
        self.calls.append((action, target, summary))
        return self.allowed


def fixed_now(value: datetime):
    def _now(timezone_name: str) -> datetime:
        return value.astimezone(ZoneInfo(timezone_name))

    return _now


def make_service(tmp_path, now: datetime | None = None) -> CronService:
    return CronService(
        tasks_path=tmp_path / "cron" / "tasks.json",
        default_timezone="Asia/Shanghai",
        poll_interval_seconds=0.1,
        now_fn=fixed_now(now or datetime(2026, 5, 13, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai"))),
    )


def make_task(*, next_run_at: str, enabled: bool = True) -> CronTask:
    return CronTask(
        id="daily_ai_news",
        enabled=enabled,
        description="每天早上 9 点收集 AI 新闻",
        schedule=CronSchedule(expression="0 9 * * *", timezone="Asia/Shanghai"),
        instruction="帮我收集今天最新的 AI 热门新闻",
        target=CronTarget(channel="lark", chat_id="oc_xxx"),
        session_id="cron:daily_ai_news",
        created_at="2026-05-13T08:00:00+08:00",
        updated_at="2026-05-13T08:00:00+08:00",
        next_run_at=next_run_at,
    )


def test_cron_service_initializes_tasks_file(tmp_path) -> None:
    service = make_service(tmp_path)

    assert service.list_tasks() == []
    assert (tmp_path / "cron" / "tasks.json").exists()


def test_default_registry_can_include_cron_tool_schema(tmp_path) -> None:
    registry = build_default_tools(
        workspace=tmp_path,
        approver=SessionApprover(default_allow=True),
        cron_service=make_service(tmp_path),
        cron_permission=CronPermissionConfig(default="allow"),
        ask_user=lambda question: "answer",
    )

    schema = next(item for item in registry.openai_tools() if item["function"]["name"] == "cron")
    task_properties = schema["function"]["parameters"]["properties"]["task"]["properties"]

    assert schema["function"]["parameters"]["properties"]["action"]["enum"] == [
        "list",
        "create",
        "update",
        "delete",
    ]
    assert "cron" in task_properties
    assert "instruction" in task_properties
    assert "target" in task_properties


def test_cron_service_create_update_delete_task(tmp_path) -> None:
    service = make_service(tmp_path)

    created = service.create_task(
        {
            "id": "daily_ai_news",
            "description": "每天早上 9 点收集 AI 新闻",
            "cron": "0 9 * * *",
            "instruction": "帮我收集今天最新的 AI 热门新闻",
            "target": {"channel": "lark", "chat_id": "oc_xxx"},
        }
    )
    updated = service.update_task("daily_ai_news", {"cron": "0 8 * * *", "enabled": False})
    service.delete_task("daily_ai_news")

    assert created.id == "daily_ai_news"
    assert created.session_id == "cron:daily_ai_news"
    assert created.next_run_at == "2026-05-13T09:00:00+08:00"
    assert updated.enabled is False
    assert updated.schedule.expression == "0 8 * * *"
    assert service.list_tasks() == []


def test_cron_service_update_does_not_rewrite_description_implicitly(tmp_path) -> None:
    service = make_service(tmp_path)
    service.create_task(
        {
            "id": "daily_news",
            "description": "每日早上10点推送新闻",
            "cron": "0 10 * * *",
            "instruction": "推送新闻",
            "target": {"channel": "lark", "chat_id": "oc_xxx"},
        }
    )

    updated = service.update_task("daily_news", {"cron": "0 15 * * *"})

    assert updated.schedule.expression == "0 15 * * *"
    assert updated.description == "每日早上10点推送新闻"


def test_cron_service_update_respects_explicit_description_patch(tmp_path) -> None:
    service = make_service(tmp_path)
    service.create_task(
        {
            "id": "daily_news",
            "description": "每日早上10点推送新闻",
            "cron": "0 10 * * *",
            "instruction": "推送新闻",
            "target": {"channel": "lark", "chat_id": "oc_xxx"},
        }
    )

    updated = service.update_task(
        "daily_news",
        {"cron": "0 15 * * *", "description": "每天下午15点推送新闻"},
    )

    assert updated.description == "每天下午15点推送新闻"


def test_cron_service_rejects_invalid_cron_expression(tmp_path) -> None:
    service = make_service(tmp_path)

    with pytest.raises(ValueError, match="Invalid cron expression"):
        service.create_task(
            {
                "id": "bad",
                "cron": "not cron",
                "instruction": "run",
                "target": {"channel": "lark", "chat_id": "oc_xxx"},
            }
        )


@pytest.mark.asyncio
async def test_cron_service_tick_publishes_due_task_with_cron_session(tmp_path) -> None:
    service = make_service(tmp_path, datetime(2026, 5, 13, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    service.save_file(
        CronFile(tasks=[make_task(next_run_at="2026-05-13T09:00:00+08:00")])
    )
    bus = MessageBus()

    await service.tick(bus)

    inbound = await bus.consume_inbound()
    task = service.list_tasks()[0]
    assert inbound.channel == "lark"
    assert inbound.chat_id == "oc_xxx"
    assert inbound.sender_id == "cron:daily_ai_news"
    assert inbound.session_id == "cron:daily_ai_news"
    assert inbound.metadata["cron"] is True
    assert inbound.metadata["task_id"] == "daily_ai_news"
    assert task.last_triggered_at == "2026-05-13T09:00:00+08:00"
    assert task.next_run_at == "2026-05-14T09:00:00+08:00"


@pytest.mark.asyncio
async def test_cron_service_tick_ignores_disabled_and_future_tasks(tmp_path) -> None:
    service = make_service(tmp_path, datetime(2026, 5, 13, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    service.save_file(
        CronFile(
            tasks=[
                make_task(next_run_at="2026-05-13T09:00:00+08:00"),
                make_task(next_run_at="2026-05-13T07:00:00+08:00", enabled=False),
            ]
        )
    )
    bus = MessageBus()

    await service.tick(bus)

    assert bus.inbound.empty()


def test_cron_service_skip_missed_tasks_recomputes_future_run(tmp_path) -> None:
    service = make_service(tmp_path, datetime(2026, 5, 13, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai")))
    service.save_file(
        CronFile(tasks=[make_task(next_run_at="2026-05-13T09:00:00+08:00")])
    )

    service._skip_missed_tasks()

    assert service.list_tasks()[0].next_run_at == "2026-05-14T09:00:00+08:00"


@pytest.mark.asyncio
async def test_cron_tool_create_uses_current_lark_context_and_permission(tmp_path) -> None:
    service = make_service(tmp_path)
    approver = TrackingApprover()
    context = InteractionContext(
        bus=MessageBus(),
        channel="lark",
        chat_id="oc_xxx",
        sender_id="ou_user",
        session_id="lark:oc_xxx",
    )
    tool = CronTool(
        service=service,
        approver=approver,
        permission=CronPermissionConfig(default="ask"),
        current_context=lambda: context,
    )

    result = await tool.execute(
        "create",
        task={
            "id": "daily_ai_news",
            "description": "每天早上 9 点收集 AI 新闻",
            "cron": "0 9 * * *",
            "instruction": "帮我收集今天最新的 AI 热门新闻",
        },
    )

    task = service.list_tasks()[0]
    assert result.is_error is False
    assert approver.calls[0][0] == "cron.create"
    assert approver.calls[0][1] == "lark:oc_xxx"
    assert task.target.channel == "lark"
    assert task.target.chat_id == "oc_xxx"
    assert task.target.reply_to == "ou_user"


@pytest.mark.asyncio
async def test_cron_tool_list_does_not_require_permission(tmp_path) -> None:
    service = make_service(tmp_path)
    service.create_task(
        {
            "id": "daily_ai_news",
            "cron": "0 9 * * *",
            "instruction": "run",
            "target": {"channel": "lark", "chat_id": "oc_xxx"},
        }
    )
    approver = TrackingApprover(allowed=False)
    tool = CronTool(
        service=service,
        approver=approver,
        permission=CronPermissionConfig(default="deny"),
        current_context=lambda: (_ for _ in ()).throw(RuntimeError("no context")),
    )

    result = await tool.execute("list")

    assert result.is_error is False
    assert "daily_ai_news" in result.content
    assert approver.calls == []


@pytest.mark.asyncio
async def test_cron_tool_rejects_cli_context_without_explicit_external_target(tmp_path) -> None:
    service = make_service(tmp_path)
    context = InteractionContext(
        bus=MessageBus(),
        channel="cli",
        chat_id="default",
        sender_id="user",
        session_id="cli:default",
    )
    tool = CronTool(
        service=service,
        approver=TrackingApprover(),
        permission=CronPermissionConfig(default="allow"),
        current_context=lambda: context,
    )

    result = await tool.execute(
        "create",
        task={
            "id": "daily_ai_news",
            "cron": "0 9 * * *",
            "instruction": "run",
        },
    )

    assert result.is_error is True
    assert "cannot target cli" in result.content


@pytest.mark.asyncio
async def test_cron_tool_delete_missing_task_returns_error(tmp_path) -> None:
    tool = CronTool(
        service=make_service(tmp_path),
        approver=TrackingApprover(),
        permission=CronPermissionConfig(default="allow"),
        current_context=lambda: (_ for _ in ()).throw(RuntimeError("no context")),
    )

    result = await tool.execute("delete", task_id="missing")

    assert result.is_error is True
    assert "does not exist" in result.content
