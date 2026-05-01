from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from typing import Annotated, Sequence

import click
import typer
from typer.main import get_command

from xagent.agent import Agent, AgentRuntime
from xagent.bus import InboundMessage, MessageBus, StreamKind
from xagent.channels import ChannelManager, build_channels
from xagent.cli.factory import (
    DEFAULT_CLI_CHANNEL,
    DEFAULT_CLI_CHAT_ID,
    DEFAULT_CLI_SENDER_ID,
    build_agent,
    create_session,
    resolve_workspace,
)
from xagent.config import ensure_config
from xagent.providers import ModelEvent


app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    no_args_is_help=False,
    context_settings={"help_option_names": ["-h", "--help"]},
    rich_markup_mode=None,
    pretty_exceptions_enable=False,
)


@dataclass(frozen=True)
class AgentCliArgs:
    message: str | None = None
    resume: str | None = None
    workspace: str | None = None


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = get_command(app)
    try:
        result = command.main(
            args=args,
            prog_name="xagent",
            standalone_mode=False,
        )
    except click.exceptions.Exit as exc:
        return int(exc.exit_code)
    except click.ClickException as exc:
        exc.show()
        return exc.exit_code
    except click.Abort:
        click.echo("Aborted!", err=True)
        return 1
    return result if isinstance(result, int) else 0


@app.callback()
def root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(0)


@app.command("agent")
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
    args = AgentCliArgs(message=message, resume=resume, workspace=workspace)
    try:
        exit_code = _main(args)
    except KeyboardInterrupt:
        typer.echo("\nbyebye!")
        raise typer.Exit(0) from None
    raise typer.Exit(exit_code)


@app.command("gateway")
def gateway_command() -> None:
    try:
        exit_code = _gateway()
    except KeyboardInterrupt:
        typer.echo("\nbyebye!")
        raise typer.Exit(0) from None
    raise typer.Exit(exit_code)


def _main(args: AgentCliArgs) -> int:
    config = ensure_config(interactive=True)
    workspace_path = resolve_workspace(config, args.workspace)
    workspace_path.mkdir(parents=True, exist_ok=True)
    session = create_session(config=config, workspace_path=workspace_path, resume=args.resume)
    print(f"Session: {session.session_id}")
    print(f"Workspace: {session.workspace_path}")
    if args.message is not None:
        agent = build_agent(config=config, session=session)
        return asyncio.run(_run_once(agent, args.message))
    runtime = AgentRuntime(config=config, workspace_path=workspace_path)
    return _chat(
        runtime,
        session.session_id,
        channel=DEFAULT_CLI_CHANNEL,
        chat_id=DEFAULT_CLI_CHAT_ID,
        sender_id=DEFAULT_CLI_SENDER_ID,
    )


def _gateway() -> int:
    config = ensure_config(interactive=True)
    workspace_path = resolve_workspace(config, None)
    workspace_path.mkdir(parents=True, exist_ok=True)
    bus = MessageBus()
    channels = build_channels(config, bus)
    if not channels:
        typer.echo(
            "No channels enabled. Enable channels.lark.enabled in ~/.xagent/config.yaml."
        )
        return 1
    runtime = AgentRuntime(config=config, workspace_path=workspace_path)
    manager = ChannelManager(bus=bus, channels=channels)
    typer.echo("xagent gateway started.")
    return asyncio.run(_run_gateway(runtime=runtime, manager=manager, bus=bus))


async def _run_gateway(
    *,
    runtime: AgentRuntime,
    manager: ChannelManager,
    bus: MessageBus,
) -> int:
    tasks = [
        asyncio.create_task(manager.run()),
        asyncio.create_task(runtime.run(bus)),
    ]
    try:
        done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_EXCEPTION)
        for task in done:
            task.result()
        return 0
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


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


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
