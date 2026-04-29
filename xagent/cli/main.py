from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Sequence

from xagent.bus import InboundMessage, MessageBus, OutboundEvent
from xagent.cli.factory import build_agent, create_session, resolve_workspace
from xagent.config import ensure_config
from xagent.providers import ModelEvent


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "gateway":
        print("agent gateway is reserved for future external channels.")
        return 0
    args = build_parser().parse_args(argv)
    return asyncio.run(_main_async(args))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent")
    parser.add_argument("-m", "--message", help="Run a one-shot message.")
    parser.add_argument("-r", "--resume", help="Resume a session by directory name.")
    parser.add_argument(
        "-w",
        "--workspace",
        help="Workspace path. Defaults to ~/.xagent/workspace/files.",
    )
    return parser


async def _main_async(args: argparse.Namespace) -> int:
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
