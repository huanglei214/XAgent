from __future__ import annotations

import asyncio
import sys
from typing import Annotated

import typer

from xagent.agent import Agent, AgentRuntime
from xagent.agent.permissions import Approver, CliApprover
from xagent.agent.tools import build_default_tools
from xagent.agent.tools.shell import ShellPolicy
from xagent.bus import InboundMessage, MessageBus, StreamKind
from xagent.cli.workspace import resolve_workspace_path
from xagent.config import AppConfig, ensure_config
from xagent.providers import ModelEvent, make_provider
from xagent.session import Session, SessionStore


DEFAULT_CLI_CHANNEL = "cli"
DEFAULT_CLI_CHAT_ID = "default"
DEFAULT_CLI_SENDER_ID = "user"


def build_agent(
    *,
    config: AppConfig,
    session: Session,
    approver: Approver | None = None,
) -> Agent:
    snapshot = make_provider(config)
    active_approver = approver or CliApprover()
    tools = build_default_tools(
        workspace=session.workspace_path,
        approver=active_approver,
        shell_policy=ShellPolicy.from_config(config.permissions.shell),
        web_config=config.tools.web,
        web_permission=config.permissions.web,
    )
    return Agent(
        provider=snapshot.provider,
        model=snapshot.model,
        session=session,
        tools=tools,
        temperature=config.agents.defaults.temperature,
        max_tokens=config.agents.defaults.max_tokens,
        max_steps=config.limits.max_steps,
        max_duration_seconds=config.limits.max_duration_seconds,
        max_repeated_tool_calls=config.limits.max_repeated_tool_calls,
        context_char_threshold=config.limits.context_char_threshold,
        trace_model_events=config.trace.model_events,
    )


def agent_command(
    message: Annotated[
        str | None,
        typer.Option("-m", "--message", help="Run a one-shot message."),
    ] = None,
    resume: Annotated[
        str | None,
        typer.Option("-r", "--resume", help="Resume a session by directory name."),
    ] = None,
    workspace: Annotated[
        str | None,
        typer.Option(
            "-w",
            "--workspace",
            help="Workspace path. Defaults to ~/.xagent/workspace/files.",
        ),
    ] = None,
) -> None:
    try:
        exit_code = _run_agent_command(
            message=message,
            resume=resume,
            workspace=workspace,
        )
    except KeyboardInterrupt:
        typer.echo("\nbyebye!")
        raise typer.Exit(0) from None
    raise typer.Exit(exit_code)


def _run_agent_command(
    *,
    message: str | None = None,
    resume: str | None = None,
    workspace: str | None = None,
) -> int:
    config = ensure_config(interactive=True)
    workspace_path = resolve_workspace_path(config, workspace)
    workspace_path.mkdir(parents=True, exist_ok=True)
    session = SessionStore(config.sessions_path).open_for_chat(
        workspace_path=workspace_path,
        channel=DEFAULT_CLI_CHANNEL,
        chat_id=DEFAULT_CLI_CHAT_ID,
        session_id=resume,
    )
    print(f"Session: {session.session_id}")
    print(f"Workspace: {session.workspace_path}")
    if message is not None:
        agent = build_agent(config=config, session=session)
        return asyncio.run(_run_once(agent, message))
    runtime = AgentRuntime(config=config, workspace_path=workspace_path)
    return _chat(
        runtime,
        session.session_id,
        channel=DEFAULT_CLI_CHANNEL,
        chat_id=DEFAULT_CLI_CHAT_ID,
        sender_id=DEFAULT_CLI_SENDER_ID,
    )


async def _run_once(agent: Agent, message: str) -> int:
    final = await agent.run(message, on_event=_print_event)
    if final.get("content"):
        print()
    return 0


def _chat(
    runtime: AgentRuntime,
    session_id: str,
    *,
    channel: str,
    chat_id: str,
    sender_id: str,
) -> int:
    bus = MessageBus()
    loop = asyncio.new_event_loop()
    print("Type 'exit' or 'quit' to leave.")
    try:
        asyncio.set_event_loop(loop)
        while True:
            try:
                text = input("> ")
            except EOFError:
                print()
                return 0
            if text.strip().lower() in {"exit", "quit"}:
                return 0
            if not text.strip():
                continue
            inbound = InboundMessage(
                content=text,
                channel=channel,
                chat_id=chat_id,
                sender_id=sender_id,
                session_id=session_id,
            )
            loop.run_until_complete(bus.publish_inbound(inbound))
            loop.run_until_complete(runtime.dispatch_once(bus))
            loop.run_until_complete(_render_outbound_once(bus))
    finally:
        _shutdown_loop(loop)


def _shutdown_loop(loop: asyncio.AbstractEventLoop) -> None:
    if loop.is_closed():
        return
    pending = [task for task in asyncio.all_tasks(loop) if not task.done()]
    for task in pending:
        task.cancel()
    if pending:
        loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
    loop.run_until_complete(loop.shutdown_asyncgens())
    asyncio.set_event_loop(None)
    loop.close()


async def _render_outbound_once(bus: MessageBus) -> None:
    printed_delta = False
    while True:
        event = await bus.consume_outbound()
        if event.stream is not None and event.stream.kind == StreamKind.DELTA:
            print(event.content, end="", flush=True)
            printed_delta = True
            continue
        if event.metadata.get("error"):
            print(f"\nError: {event.content}", file=sys.stderr)
            return
        if event.stream is None or event.stream.kind == StreamKind.END:
            if event.content and not printed_delta:
                print(event.content, end="", flush=True)
            print()
            return


def _print_event(event: ModelEvent) -> None:
    if event.kind == "text_delta":
        print(event.text, end="", flush=True)
