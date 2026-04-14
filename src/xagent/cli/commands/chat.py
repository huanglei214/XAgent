from pathlib import Path
from typing import Optional

import typer

import asyncio

from xagent.cli.tui.render import print_error
from xagent.cli.tui.tui import run_tui


def chat_command(*, resume: bool = False, resume_session_id: Optional[str] = None) -> None:
    try:
        asyncio.run(run_tui(str(Path.cwd()), resume=resume, resume_session_id=resume_session_id))
    except KeyboardInterrupt:
        print_error("Interrupted.")
        raise typer.Exit(code=130) from None


def resume_command(session_id: Optional[str] = None) -> None:
    chat_command(resume=True, resume_session_id=session_id)
