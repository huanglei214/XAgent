import asyncio
import inspect
from pathlib import Path

import typer

from xagent.agent.runtime import TurnResult
from xagent.bus.messages import InboundMessage
from xagent.bus.types import Message, TextPart
from xagent.cli.tui.render import print_error
from xagent.cli.runtime import (
    build_local_runtime_boundary,
    build_runtime_agent,
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
    runtime_boundary = None
    try:
        agent = build_runtime_agent(str(Path.cwd()))
        agent.runtime_mode = "run"
        runtime_boundary = build_local_runtime_boundary(agent, session_id="run", cwd=str(Path.cwd()))
    except Exception as exc:
        format_runtime_error(exc)
        raise typer.Exit(code=1) from exc

    try:
        inbound = InboundMessage(
            content=prompt,
            source="cli.run",
            channel="cli",
            sender_id="cli",
            chat_id="run",
        )
        outbound = await runtime_boundary.submit_and_wait(inbound)
        if outbound.kind == "failed":
            raise RuntimeError(outbound.error or "Runtime execution failed.")
        turn_result = TurnResult(
            message=outbound.metadata.get("message")
            or Message(role="assistant", content=[TextPart(text=outbound.content)]),
            duration_seconds=float(outbound.metadata.get("duration_seconds") or 0.0),
        )
        wait_for_background_tasks = getattr(runtime_boundary, "wait_for_background_tasks", None)
        if callable(wait_for_background_tasks):
            maybe_wait = wait_for_background_tasks()
            if inspect.isawaitable(maybe_wait):
                await maybe_wait
    except Exception as exc:
        format_runtime_error(exc)
        raise typer.Exit(code=1) from exc
    finally:
        if runtime_boundary is not None:
            runtime_boundary.close()

    render_final_message(turn_result.message)
    render_turn_status(turn_result.duration_seconds, agent)
