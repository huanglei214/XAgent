import asyncio
from pathlib import Path

import typer

from xagent.cli.render import print_error
from xagent.cli.runtime import (
    build_runtime_agent,
    format_runtime_error,
    render_final_message,
    render_turn_status,
    run_agent_turn,
)


def run_command(prompt: str = typer.Argument(..., help="Prompt to send to the model.")) -> None:
    try:
        asyncio.run(_run(prompt))
    except KeyboardInterrupt:
        print_error("Interrupted.")
        raise typer.Exit(code=130) from None


async def _run(prompt: str) -> None:
    try:
        agent = build_runtime_agent(str(Path.cwd()))
    except Exception as exc:
        format_runtime_error(exc)
        raise typer.Exit(code=1) from exc

    try:
        final_message, duration = await run_agent_turn(agent, prompt)
    except Exception as exc:
        format_runtime_error(exc)
        raise typer.Exit(code=1) from exc

    render_final_message(final_message)
    render_turn_status(duration, agent)
