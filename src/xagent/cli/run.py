import asyncio
import json
from pathlib import Path

import typer

from xagent.cli.render import print_error, print_info, print_tool_use
from xagent.config.loader import load_config, resolve_default_model
from xagent.coding import create_coding_agent
from xagent.foundation.messages import message_text
from xagent.providers import create_provider


def run_command(prompt: str = typer.Argument(..., help="Prompt to send to the model.")) -> None:
    try:
        asyncio.run(_run(prompt))
    except KeyboardInterrupt:
        print_error("Interrupted.")
        raise typer.Exit(code=130) from None


async def _run(prompt: str) -> None:
    try:
        config = load_config()
        model_config = resolve_default_model(config)
        provider = create_provider(model_config)
    except Exception as exc:
        print_error(str(exc))
        raise typer.Exit(code=1) from exc

    try:
        agent = create_coding_agent(provider=provider, model=model_config.name, cwd=str(Path.cwd()))
        final_message = await agent.run(
            prompt,
            on_tool_use=lambda tool_use: print_tool_use(tool_use.name, json.dumps(tool_use.input, ensure_ascii=False)),
        )
    except Exception as exc:
        print_error(str(exc))
        raise typer.Exit(code=1) from exc

    final_text = message_text(final_message)
    if not final_text.strip():
        print_info("(no output)")
        return

    print_info(final_text)
