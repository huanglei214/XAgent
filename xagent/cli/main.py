from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from typing import Annotated, Sequence

import click
import typer
from typer.main import get_command

from xagent.agent import Agent
from xagent.bus import InboundMessage, MessageBus, OutboundEvent
from xagent.cli.factory import build_agent, create_session, resolve_workspace
from xagent.config import ensure_config
from xagent.providers import ModelEvent


app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
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
    if not args:
        with click.Context(command, info_name="xagent") as context:
            click.echo(command.get_help(context))
        return 0
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
    typer.echo("xagent gateway is reserved for future external channels.")


def _main(args: AgentCliArgs) -> int:
    config = ensure_config(interactive=True)
    workspace_path = resolve_workspace(config, args.workspace)
    workspace_path.mkdir(parents=True, exist_ok=True)
    session = create_session(config=config, workspace_path=workspace_path, resume=args.resume)
    agent = build_agent(config=config, session=session)
    print(f"Session: {session.session_id}")
    print(f"Workspace: {session.workspace_path}")
    if args.message is not None:
        return asyncio.run(_run_once(agent, args.message))
    return _chat(agent, session.session_id)


async def _run_once(agent: Agent, message: str) -> int:
    final = await agent.run(message, on_event=_print_event)
    if final.get("content"):
        print()
    return 0


def _chat(agent: Agent, session_id: str) -> int:
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
            inbound = InboundMessage(content=text, session_id=session_id)
            loop.run_until_complete(bus.publish_inbound(inbound))
            loop.run_until_complete(_dispatch_chat_once(agent, bus))
            loop.run_until_complete(_render_outbound_once(bus, inbound.id))
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


async def _dispatch_chat_once(agent: Agent, bus: MessageBus) -> None:
    inbound = await bus.consume_inbound()

    async def publish_event(event: ModelEvent) -> None:
        if event.kind == "text_delta":
            await bus.publish_outbound(
                OutboundEvent(kind="delta", content=event.text, inbound_id=inbound.id)
            )

    try:
        final = await agent.run(inbound.content, on_event=publish_event)
        await bus.publish_outbound(
            OutboundEvent(
                kind="final",
                content=str(final.get("content") or ""),
                session_id=agent.session.session_id,
                inbound_id=inbound.id,
            )
        )
    except Exception as exc:  # noqa: BLE001 - CLI should show failures plainly
        await bus.publish_outbound(OutboundEvent(kind="error", content=str(exc), inbound_id=inbound.id))


async def _render_outbound_once(bus: MessageBus, inbound_id: str) -> None:
    while True:
        event = await bus.consume_outbound()
        if event.inbound_id != inbound_id:
            continue
        if event.kind == "delta":
            print(event.content, end="", flush=True)
            continue
        if event.kind == "final":
            print()
            return
        if event.kind == "error":
            print(f"\nError: {event.content}", file=sys.stderr)
            return


def _print_event(event: ModelEvent) -> None:
    if event.kind == "text_delta":
        print(event.text, end="", flush=True)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
