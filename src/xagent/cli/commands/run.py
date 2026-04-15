import asyncio
import inspect
from pathlib import Path

import typer

from xagent.cli.tui.render import print_error
from xagent.cli.runtime import (
    build_runtime_agent,
    build_session_runtime,
    format_runtime_error,
    render_final_message,
    render_turn_status,
)


def run_command(prompt: str = typer.Argument(..., help="Prompt to send to the model.")) -> None:
    try:
        asyncio.run(_run(prompt))
    except KeyboardInterrupt:
        print_error("Interrupted.")
        raise typer.Exit(code=130) from None


async def _run(prompt: str) -> None:
    session_runtime = None
    try:
        agent = build_runtime_agent(str(Path.cwd()))
        agent.runtime_mode = "run"
        _, session_runtime = build_session_runtime(agent, session_id="run", cwd=str(Path.cwd()))
    except Exception as exc:
        format_runtime_error(exc)
        raise typer.Exit(code=1) from exc

    try:
        turn_result = await session_runtime.publish_user_message(prompt, source="cli.run")
        wait_for_background_tasks = getattr(session_runtime, "wait_for_background_tasks", None)
        if callable(wait_for_background_tasks):
            maybe_wait = wait_for_background_tasks()
            if inspect.isawaitable(maybe_wait):
                await maybe_wait
    except Exception as exc:
        format_runtime_error(exc)
        raise typer.Exit(code=1) from exc
    finally:
        if session_runtime is not None:
            session_runtime.close()

    render_final_message(turn_result.message)
    render_turn_status(turn_result.duration_seconds, agent)
