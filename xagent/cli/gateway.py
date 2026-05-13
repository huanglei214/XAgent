from __future__ import annotations

import asyncio
from collections.abc import Mapping

import typer

from xagent.agent import AgentLoop
from xagent.agent.memory import MemoryStore
from xagent.bus import MessageBus
from xagent.channels import ChannelManager, build_channels
from xagent.cli.workspace import resolve_workspace_path
from xagent.config import AppConfig, ensure_config
from xagent.cron import CronService


def gateway_command() -> None:
    try:
        exit_code = _gateway()
    except KeyboardInterrupt:
        typer.echo("\nbyebye!")
        raise typer.Exit(0) from None
    raise typer.Exit(exit_code)


def _gateway() -> int:
    config = ensure_config(interactive=True)
    workspace_path = resolve_workspace_path(config, None)
    workspace_path.mkdir(parents=True, exist_ok=True)
    bus = MessageBus()
    channels = build_channels(config, bus)
    if not channels:
        typer.echo(
            "No channels enabled. Enable channels.lark.enabled or channels.weixin.enabled "
            "in ~/.xagent/config.yaml."
        )
        return 1
    cron_service = _build_cron_service(config) if config.cron.enabled else None
    agent_loop = AgentLoop(
        config=config,
        workspace_path=workspace_path,
        memory_store=MemoryStore() if config.memory.enabled else None,
        cron_service=cron_service,
    )
    manager = ChannelManager(bus=bus, channels=channels)
    typer.echo("xagent gateway started.")
    _print_channel_summary(channels)
    if cron_service is not None:
        typer.echo(f"Cron: enabled ({config.cron_tasks_path})")
    return asyncio.run(
        _run_gateway(
            agent_loop=agent_loop,
            manager=manager,
            bus=bus,
            cron_service=cron_service,
        )
    )


def _print_channel_summary(channels: Mapping[str, object]) -> None:
    typer.echo("Channels:")
    for name, channel in channels.items():
        describe = getattr(channel, "describe", None)
        summary = describe() if callable(describe) else name
        typer.echo(f"  - {summary}")


async def _run_gateway(
    *,
    agent_loop: AgentLoop,
    manager: ChannelManager,
    bus: MessageBus,
    cron_service: CronService | None = None,
) -> int:
    tasks = [
        asyncio.create_task(manager.run()),
        asyncio.create_task(agent_loop.run(bus)),
    ]
    if cron_service is not None:
        tasks.append(asyncio.create_task(cron_service.run(bus)))
    try:
        done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in done:
            task.result()
        return 0
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _build_cron_service(config: AppConfig) -> CronService:
    return CronService(
        tasks_path=config.cron_tasks_path,
        default_timezone=config.cron.default_timezone,
        poll_interval_seconds=config.cron.poll_interval_seconds,
    )
