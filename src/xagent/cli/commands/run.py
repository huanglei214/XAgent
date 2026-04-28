import asyncio
from pathlib import Path

import typer

from xagent.agent.runtime import TurnResult
from xagent.bus.messages import InboundMessage
from xagent.provider.types import Message, TextPart
from xagent.cli.tui.render import print_error
from xagent.cli.runtime import (
    build_runtime_agent,
    build_runtime_stack,
    format_runtime_error,
    render_final_message,
    render_turn_status,
)


def run_command(prompt: str = typer.Argument(..., help="Prompt to send to the model.")) -> None:
    """`xagent run` 入口：一次性 prompt，使用新 ChannelManager 路径。"""
    try:
        asyncio.run(_run(prompt))
    except KeyboardInterrupt:
        print_error("Interrupted.")
        raise typer.Exit(code=130) from None


async def _run(prompt: str) -> None:
    """构建新总线栈，submit 一次 inbound，等待 terminal outbound，然后渲染。"""
    stack = None
    try:
        agent = build_runtime_agent(str(Path.cwd()))
        agent.runtime_mode = "run"
        stack = build_runtime_stack(agent, session_id="run", cwd=str(Path.cwd()))
        await stack.start()
    except Exception as exc:
        format_runtime_error(exc)
        if stack is not None:
            await stack.stop()
        raise typer.Exit(code=1) from exc

    try:
        inbound = InboundMessage(
            content=prompt,
            source="cli.run",
            channel="cli",
            sender_id="cli",
            chat_id="run",
        )
        outbound = await stack.channel_manager.send_and_wait(inbound)
        if outbound.kind == "failed":
            raise RuntimeError(outbound.error or "Runtime execution failed.")
        # 成功分支：从 terminal outbound.metadata 还原业务层 TurnResult。
        turn_result = TurnResult(
            message=outbound.metadata.get("message")
            or Message(role="assistant", content=[TextPart(text=outbound.content)]),
            duration_seconds=float(outbound.metadata.get("duration_seconds") or 0.0),
        )
    except Exception as exc:
        format_runtime_error(exc)
        await stack.stop()
        raise typer.Exit(code=1) from exc

    await stack.stop()
    render_final_message(turn_result.message)
    render_turn_status(turn_result.duration_seconds, agent)
