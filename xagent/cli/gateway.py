from __future__ import annotations

import asyncio
from collections.abc import Mapping

import typer

from xagent.agent import AgentLoop
from xagent.bus import MessageBus
from xagent.channels import ChannelManager, build_channels
from xagent.cli.workspace import resolve_workspace_path
from xagent.config import ensure_config


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
    agent_loop = AgentLoop(config=config, workspace_path=workspace_path)
    manager = ChannelManager(bus=bus, channels=channels)
    typer.echo("xagent gateway started.")
    _print_channel_summary(channels)
    return asyncio.run(_run_gateway(agent_loop=agent_loop, manager=manager, bus=bus))


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
) -> int:
    tasks = [
        asyncio.create_task(manager.run()),
        asyncio.create_task(agent_loop.run(bus)),
    ]
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
