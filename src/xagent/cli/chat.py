import asyncio
from pathlib import Path

import typer

from xagent.cli.prompt import create_prompt_session
from xagent.cli.render import print_error, print_info
from xagent.cli.runtime import (
    build_runtime_agent,
    format_runtime_error,
    get_runtime_status,
    render_final_message,
    render_turn_status,
    run_agent_turn,
)
from xagent.memory.session_store import SessionStore


def chat_command() -> None:
    try:
        asyncio.run(_chat())
    except KeyboardInterrupt:
        print_error("Interrupted.")
        raise typer.Exit(code=130) from None


async def _chat() -> None:
    cwd = str(Path.cwd())
    try:
        agent = build_runtime_agent(cwd)
    except Exception as exc:
        format_runtime_error(exc)
        raise typer.Exit(code=1) from exc

    session_store = SessionStore(cwd)
    restored_messages = session_store.load_messages()
    if restored_messages:
        agent.set_messages(restored_messages)

    session = create_prompt_session(cwd)
    print_info("XAgent chat started. Use /help for commands.")
    if restored_messages:
        print_info(f"Restored {len(restored_messages)} messages from the previous session.")
    print_info(get_runtime_status(agent))

    while True:
        try:
            user_input = await session.prompt_async("xagent> ")
        except EOFError:
            print_info("Bye.")
            return

        command = user_input.strip()
        if not command:
            continue

        if command in {"/exit", "/quit"}:
            session_store.save_messages(agent.messages)
            print_info("Bye.")
            return

        if command == "/help":
            _print_chat_help()
            continue

        if command == "/clear":
            agent.clear_messages()
            session_store.clear()
            print_info("Cleared conversation history.")
            continue

        if command == "/status":
            print_info(get_runtime_status(agent))
            continue

        try:
            message, duration = await run_agent_turn(agent, command)
        except Exception as exc:
            format_runtime_error(exc)
            continue

        render_final_message(message)
        render_turn_status(duration, agent)
        session_store.save_messages(agent.messages)


def _print_chat_help() -> None:
    print_info("/help  Show chat commands")
    print_info("/clear Clear in-memory conversation history")
    print_info("/status Show current session status")
    print_info("/exit  Exit chat mode")
