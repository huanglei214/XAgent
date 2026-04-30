from __future__ import annotations

import asyncio

import pytest

from xagent.bus import InboundMessage, MessageBus, OutboundEvent
from xagent.cli import main as cli_main
from xagent.cli.factory import DEFAULT_CLI_SESSION_ID, build_agent, create_session
from xagent.config import default_config
from xagent.providers import ModelEvent
from xagent.session import SessionStore


def test_gateway_placeholder(capsys) -> None:
    assert cli_main.main(["gateway"]) == 0
    captured = capsys.readouterr()
    assert "reserved for future external channels" in captured.out


def test_root_without_args_shows_help(capsys) -> None:
    assert cli_main.main([]) == 0
    captured = capsys.readouterr()

    assert "Usage:" in captured.out
    assert "agent" in captured.out
    assert "gateway" in captured.out


def test_root_accepts_short_help_option(capsys) -> None:
    assert cli_main.main(["-h"]) == 0
    captured = capsys.readouterr()

    assert "Usage:" in captured.out
    assert "gateway" in captured.out


def test_agent_command_uses_message_resume_and_workspace_aliases(monkeypatch) -> None:
    seen: cli_main.AgentCliArgs | None = None

    def fake_main(args: cli_main.AgentCliArgs) -> int:
        nonlocal seen
        seen = args
        return 0

    monkeypatch.setattr(cli_main, "_main", fake_main)

    assert cli_main.main(["agent", "-m", "hello", "-r", "terminal-session", "-w", "/tmp/project"]) == 0

    assert seen == cli_main.AgentCliArgs(
        message="hello",
        resume="terminal-session",
        workspace="/tmp/project",
    )


def test_agent_command_supports_long_option_names(monkeypatch) -> None:
    seen: cli_main.AgentCliArgs | None = None

    def fake_main(args: cli_main.AgentCliArgs) -> int:
        nonlocal seen
        seen = args
        return 0

    monkeypatch.setattr(cli_main, "_main", fake_main)

    assert cli_main.main(
        [
            "agent",
            "--message",
            "hello",
            "--resume",
            "terminal-session",
            "--workspace",
            "/tmp/project",
        ]
    ) == 0

    assert seen == cli_main.AgentCliArgs(
        message="hello",
        resume="terminal-session",
        workspace="/tmp/project",
    )


def test_agent_help_lists_expected_options(capsys) -> None:
    assert cli_main.main(["agent", "--help"]) == 0
    captured = capsys.readouterr()

    assert "--message" in captured.out
    assert "--resume" in captured.out
    assert "--workspace" in captured.out


def test_agent_accepts_short_help_option(capsys) -> None:
    assert cli_main.main(["agent", "-h"]) == 0
    captured = capsys.readouterr()

    assert "--message" in captured.out
    assert "--resume" in captured.out
    assert "--workspace" in captured.out


def test_agent_control_c_exits_with_byebye(monkeypatch, capsys) -> None:
    def fake_main(args: cli_main.AgentCliArgs) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_main, "_main", fake_main)

    assert cli_main.main(["agent"]) == 0
    captured = capsys.readouterr()

    assert "byebye!" in captured.out


def test_shutdown_loop_cancels_pending_tasks() -> None:
    cancelled = False

    async def pending_forever() -> None:
        nonlocal cancelled
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            cancelled = True
            raise

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.create_task(pending_forever())
    loop.run_until_complete(asyncio.sleep(0))

    cli_main._shutdown_loop(loop)

    assert cancelled is True
    assert loop.is_closed()


def test_shutdown_loop_closes_async_generators() -> None:
    closed = False

    async def stream():
        nonlocal closed
        try:
            yield "chunk"
            await asyncio.sleep(60)
        finally:
            closed = True

    async def consume_one_chunk() -> None:
        generator = stream()
        await generator.__anext__()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(consume_one_chunk())

    cli_main._shutdown_loop(loop)

    assert closed is True
    assert loop.is_closed()


@pytest.mark.asyncio
async def test_dispatch_chat_publishes_outbound_without_printing(capsys) -> None:
    class FakeAgent:
        session = type("SessionStub", (), {"session_id": "cli:default"})()

        async def run(self, content, *, on_event):
            await on_event(ModelEvent.text_delta("hello"))
            return {"content": "hello"}

    bus = MessageBus()
    inbound = InboundMessage(content="hi", session_id="cli:default")
    await bus.publish_inbound(inbound)

    await cli_main._dispatch_chat_once(FakeAgent(), bus)  # type: ignore[arg-type]

    captured = capsys.readouterr()
    assert captured.out == ""
    delta = await bus.consume_outbound()
    final = await bus.consume_outbound()
    assert delta == OutboundEvent(kind="delta", content="hello", inbound_id=inbound.id)
    assert final.kind == "final"
    assert final.content == "hello"
    assert final.inbound_id == inbound.id


@pytest.mark.asyncio
async def test_render_outbound_consumes_events(capsys) -> None:
    bus = MessageBus()
    await bus.publish_outbound(OutboundEvent(kind="delta", content="hel", inbound_id="in-1"))
    await bus.publish_outbound(OutboundEvent(kind="delta", content="lo", inbound_id="in-1"))
    await bus.publish_outbound(OutboundEvent(kind="final", content="hello", inbound_id="in-1"))

    await cli_main._render_outbound_once(bus, "in-1")

    captured = capsys.readouterr()
    assert captured.out == "hello\n"


@pytest.mark.asyncio
async def test_render_outbound_prints_errors(capsys) -> None:
    bus = MessageBus()
    await bus.publish_outbound(OutboundEvent(kind="error", content="boom", inbound_id="in-1"))

    await cli_main._render_outbound_once(bus, "in-1")

    captured = capsys.readouterr()
    assert captured.err == "\nError: boom\n"


def test_build_agent_uses_provider_factory_model(tmp_path) -> None:
    config = default_config()
    config.agents.defaults.model = "factory-model"
    session = SessionStore(tmp_path / "sessions").create(workspace_path=tmp_path)

    agent = build_agent(config=config, session=session)

    assert agent.model == "factory-model"


def test_build_agent_uses_trace_model_events_config(tmp_path) -> None:
    config = default_config()
    config.trace.model_events = True
    session = SessionStore(tmp_path / "sessions").create(workspace_path=tmp_path)

    agent = build_agent(config=config, session=session)

    assert agent.trace_model_events is True


def test_create_session_defaults_to_cli_default_and_reuses_it(tmp_path) -> None:
    config = default_config()
    config.workspace.sessions_path = str(tmp_path / "sessions")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    first = create_session(config=config, workspace_path=workspace)
    first.append_message({"role": "user", "content": "hello"})
    second = create_session(config=config, workspace_path=workspace)

    assert first.session_id == DEFAULT_CLI_SESSION_ID
    assert second.session_id == DEFAULT_CLI_SESSION_ID
    assert second.path == first.path
    assert second.read_records()[1]["message"] == {"role": "user", "content": "hello"}


def test_create_session_resume_creates_missing_session_id(tmp_path) -> None:
    config = default_config()
    config.workspace.sessions_path = str(tmp_path / "sessions")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    created = create_session(config=config, workspace_path=workspace, resume="cli:experiment")
    reopened = create_session(config=config, workspace_path=workspace, resume="cli:experiment")

    assert created.session_id == "cli:experiment"
    assert reopened.path == created.path
