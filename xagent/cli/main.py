from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from typing import Annotated, Sequence

import click
import typer
from typer.main import get_command

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
    raise typer.Exit(
        asyncio.run(
            _main_async(AgentCliArgs(message=message, resume=resume, workspace=workspace))
        )
    )


@app.command("gateway")
def gateway_command() -> None:
    typer.echo("xagent gateway is reserved for future external channels.")


async def _main_async(args: AgentCliArgs) -> int:
    config = ensure_config(interactive=True)
    workspace_path = resolve_workspace(config, args.workspace)
    workspace_path.mkdir(parents=True, exist_ok=True)
    session = create_session(config=config, workspace_path=workspace_path, resume=args.resume)
    agent = build_agent(config=config, session=session)
    print(f"Session: {session.session_id}")
    print(f"Workspace: {session.workspace_path}")
    if args.message is not None:
        final = await agent.run(args.message, on_event=_print_event)
        if final.get("content"):
            print()
        return 0
    await _chat(agent, session.session_id)
    return 0


async def _chat(agent, session_id: str) -> None:
    bus = MessageBus()
    print("Type 'exit' or 'quit' to leave.")
    while True:
        try:
            text = input("> ")
        except EOFError:
            print()
            return
        if text.strip().lower() in {"exit", "quit"}:
            return
        if not text.strip():
            continue
        inbound = InboundMessage(content=text, session_id=session_id)
        await bus.publish_inbound(inbound)
        await _dispatch_chat_once(agent, bus)


async def _dispatch_chat_once(agent, bus: MessageBus) -> None:
    inbound = await bus.consume_inbound()

    async def publish_event(event: ModelEvent) -> None:
        if event.kind == "text_delta":
            await bus.publish_outbound(
                OutboundEvent(kind="delta", content=event.text, inbound_id=inbound.id)
            )
            print(event.text, end="", flush=True)

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
        print()
    except Exception as exc:  # noqa: BLE001 - CLI should show failures plainly
        await bus.publish_outbound(OutboundEvent(kind="error", content=str(exc), inbound_id=inbound.id))
        print(f"\nError: {exc}", file=sys.stderr)


def _print_event(event: ModelEvent) -> None:
    if event.kind == "text_delta":
        print(event.text, end="", flush=True)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
