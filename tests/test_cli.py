from __future__ import annotations

import asyncio

import pytest

from xagent.bus import InboundMessage, MessageBus, OutboundEvent, StreamKind, StreamState
from xagent.cli import agent as cli_agent
from xagent.cli import gateway as cli_gateway
from xagent.cli import main as cli_main
from xagent.cli.agent import build_agent
from xagent.config import default_config
from xagent.session import SessionStore


def test_gateway_without_enabled_channels_returns_hint(monkeypatch, tmp_path, capsys) -> None:
    config = default_config()
    config.workspace.default_path = str(tmp_path / "workspace")
    config.workspace.sessions_path = str(tmp_path / "sessions")
    monkeypatch.setattr(cli_gateway, "ensure_config", lambda *, interactive: config)

    assert cli_main.main(["gateway"]) == 1
    captured = capsys.readouterr()
    assert "No channels enabled" in captured.out


def test_gateway_builds_channel_manager_and_runtime(monkeypatch, tmp_path, capsys) -> None:
    config = default_config()
    config.workspace.default_path = str(tmp_path / "workspace")
    config.workspace.sessions_path = str(tmp_path / "sessions")
    bus_seen: MessageBus | None = None
    channels_seen = None
    runtime_seen = None
    manager_seen = None
    fake_channels = {"lark": object()}

    class FakeRuntime:
        def __init__(self, *, config, workspace_path) -> None:
            self.config = config
            self.workspace_path = workspace_path

    class FakeManager:
        def __init__(self, *, bus, channels) -> None:
            nonlocal bus_seen, channels_seen
            bus_seen = bus
            channels_seen = channels

    async def fake_run_gateway(*, runtime, manager, bus) -> int:
        nonlocal runtime_seen, manager_seen
        runtime_seen = runtime
        manager_seen = manager
        assert bus is bus_seen
        return 0

    monkeypatch.setattr(cli_gateway, "ensure_config", lambda *, interactive: config)
    monkeypatch.setattr(cli_gateway, "build_channels", lambda config, bus: fake_channels)
    monkeypatch.setattr(cli_gateway, "AgentRuntime", FakeRuntime)
    monkeypatch.setattr(cli_gateway, "ChannelManager", FakeManager)
    monkeypatch.setattr(cli_gateway, "_run_gateway", fake_run_gateway)

    assert cli_main.main(["gateway"]) == 0
    captured = capsys.readouterr()

    assert "xagent gateway started." in captured.out
    assert isinstance(bus_seen, MessageBus)
    assert channels_seen is fake_channels
    assert isinstance(runtime_seen, FakeRuntime)
    assert isinstance(manager_seen, FakeManager)


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
    seen: dict[str, str | None] | None = None

    def fake_run_agent_command(
        *,
        message: str | None = None,
        resume: str | None = None,
        workspace: str | None = None,
    ) -> int:
        nonlocal seen
        seen = {
            "message": message,
            "resume": resume,
            "workspace": workspace,
        }
        return 0

    monkeypatch.setattr(cli_agent, "_run_agent_command", fake_run_agent_command)

    assert cli_main.main(["agent", "-m", "hello", "-r", "cli-session", "-w", "/tmp/project"]) == 0

    assert seen == {
        "message": "hello",
        "resume": "cli-session",
        "workspace": "/tmp/project",
    }


def test_agent_command_supports_long_option_names(monkeypatch) -> None:
    seen: dict[str, str | None] | None = None

    def fake_run_agent_command(
        *,
        message: str | None = None,
        resume: str | None = None,
        workspace: str | None = None,
    ) -> int:
        nonlocal seen
        seen = {
            "message": message,
            "resume": resume,
            "workspace": workspace,
        }
        return 0

    monkeypatch.setattr(cli_agent, "_run_agent_command", fake_run_agent_command)

    assert cli_main.main(
        [
            "agent",
            "--message",
            "hello",
            "--resume",
            "cli-session",
            "--workspace",
            "/tmp/project",
        ]
    ) == 0

    assert seen == {
        "message": "hello",
        "resume": "cli-session",
        "workspace": "/tmp/project",
    }


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
    def fake_run_agent_command(
        *,
        message: str | None = None,
        resume: str | None = None,
        workspace: str | None = None,
    ) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(cli_agent, "_run_agent_command", fake_run_agent_command)

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

    cli_agent._shutdown_loop(loop)

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

    cli_agent._shutdown_loop(loop)

    assert closed is True
    assert loop.is_closed()


@pytest.mark.asyncio
async def test_render_outbound_consumes_events(capsys) -> None:
    bus = MessageBus()
    await bus.publish_outbound(
        OutboundEvent(content="hel", stream=StreamState(kind=StreamKind.DELTA, stream_id="s1"))
    )
    await bus.publish_outbound(
        OutboundEvent(content="lo", stream=StreamState(kind=StreamKind.DELTA, stream_id="s1"))
    )
    await bus.publish_outbound(
        OutboundEvent(content="hello", stream=StreamState(kind=StreamKind.END, stream_id="s1"))
    )

    await cli_agent._render_outbound_once(bus)

    captured = capsys.readouterr()
    assert captured.out == "hello\n"


@pytest.mark.asyncio
async def test_render_outbound_prints_errors(capsys) -> None:
    bus = MessageBus()
    await bus.publish_outbound(
        OutboundEvent(
            content="boom",
            stream=StreamState(kind=StreamKind.END, stream_id="s1"),
            metadata={"error": True},
        )
    )

    await cli_agent._render_outbound_once(bus)

    captured = capsys.readouterr()
    assert captured.err == "\nError: boom\n"


def test_chat_uses_runtime_and_bus(monkeypatch, capsys) -> None:
    seen: InboundMessage | None = None

    class FakeRuntime:
        async def dispatch_once(self, bus: MessageBus) -> None:
            nonlocal seen
            seen = await bus.consume_inbound()
            await bus.publish_outbound(
                OutboundEvent(
                    channel=seen.channel,
                    chat_id=seen.chat_id,
                    reply_to=seen.sender_id,
                    session_id=seen.session_id,
                    stream=StreamState(kind=StreamKind.END, stream_id="s1"),
                )
            )

    inputs = iter(["hello", "exit"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(inputs))

    assert (
        cli_agent._chat(
            FakeRuntime(),  # type: ignore[arg-type]
            "cli:default",
            channel="cli",
            chat_id="default",
            sender_id="user",
        )
        == 0
    )

    captured = capsys.readouterr()
    assert "Type 'exit' or 'quit' to leave." in captured.out
    assert seen is not None
    assert seen.content == "hello"
    assert seen.channel == "cli"
    assert seen.chat_id == "default"
    assert seen.sender_id == "user"
    assert seen.session_id == "cli:default"


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


def test_build_agent_uses_shell_policy_config(tmp_path) -> None:
    config = default_config()
    config.permissions.shell.default = "deny"
    config.permissions.shell.blacklist = ["sudo"]
    session = SessionStore(tmp_path / "sessions").create(workspace_path=tmp_path)

    agent = build_agent(config=config, session=session)
    shell = agent.tools.get("shell")

    assert shell is not None
    assert getattr(shell, "shell_policy").default == "deny"
    assert getattr(shell, "shell_policy").blacklist == ("sudo",)


def test_build_agent_uses_web_tools_config(tmp_path) -> None:
    config = default_config()
    config.tools.web.enabled = False
    session = SessionStore(tmp_path / "sessions").create(workspace_path=tmp_path)

    agent = build_agent(config=config, session=session)

    assert agent.tools.get("web_fetch") is None
    assert agent.tools.get("web_search") is None


def test_build_agent_uses_web_permission_config(tmp_path) -> None:
    config = default_config()
    config.permissions.web.default = "deny"
    session = SessionStore(tmp_path / "sessions").create(workspace_path=tmp_path)

    agent = build_agent(config=config, session=session)
    web_fetch = agent.tools.get("web_fetch")

    assert web_fetch is not None
    assert getattr(web_fetch, "web_permission").default == "deny"
