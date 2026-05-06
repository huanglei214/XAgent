from __future__ import annotations

import asyncio
from typing import Annotated

import typer

from xagent.bus import MessageBus
from xagent.channels import WeixinChannel
from xagent.config import ensure_config

channels_app = typer.Typer(
    add_completion=False,
    context_settings={"help_option_names": ["-h", "--help"]},
    rich_markup_mode=None,
)


@channels_app.command("login")
def channels_login_command(
    channel_name: Annotated[str, typer.Argument(help="Channel name, e.g. weixin.")],
    force: Annotated[
        bool,
        typer.Option("-f", "--force", help="Force QR login and replace saved state."),
    ] = False,
) -> None:
    try:
        exit_code = _run_channels_login(channel_name=channel_name, force=force)
    except KeyboardInterrupt:
        typer.echo("\nbyebye!")
        raise typer.Exit(0) from None
    raise typer.Exit(exit_code)


def _run_channels_login(*, channel_name: str, force: bool = False) -> int:
    if channel_name != "weixin":
        typer.echo("Unknown channel. Supported login channel: weixin.")
        return 1
    config = ensure_config(interactive=True)
    channel = WeixinChannel(config=config.channels.weixin, bus=MessageBus())
    success = asyncio.run(channel.login(force=force))
    if success:
        typer.echo("Weixin login successful.")
        return 0
    typer.echo("Weixin login failed.")
    return 1
