from pathlib import Path

import typer

import asyncio

from xagent.cli.tui.render import print_error, print_info
from xagent.cli.tui.tui import run_tui


def chat_command() -> None:
    try:
        asyncio.run(run_tui(str(Path.cwd())))
    except KeyboardInterrupt:
        print_error("Interrupted.")
        raise typer.Exit(code=130) from None
