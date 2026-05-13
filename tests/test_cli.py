from __future__ import annotations

import asyncio
import threading

import pytest

from xagent.bus import InboundMessage, MessageBus, OutboundEvent, StreamKind, StreamState
from xagent.cli import agent as cli_agent
from xagent.cli import channels as cli_channels
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
    agent_loop_seen = None
    manager_seen = None
    cron_service_seen = None
    fake_channels = {"lark": object()}

    class FakeAgentLoop:
        def __init__(self, *, config, workspace_path, memory_store=None, cron_service=None) -> None:
            self.config = config
            self.workspace_path = workspace_path
            self.memory_store = memory_store
            self.cron_service = cron_service

    class FakeManager:
        def __init__(self, *, bus, channels) -> None:
            nonlocal bus_seen, channels_seen
            bus_seen = bus
            channels_seen = channels

    async def fake_run_gateway(*, agent_loop, manager, bus, cron_service=None) -> int:
        nonlocal agent_loop_seen, manager_seen, cron_service_seen
        agent_loop_seen = agent_loop
        manager_seen = manager
        cron_service_seen = cron_service
        assert bus is bus_seen
        return 0

    monkeypatch.setattr(cli_gateway, "ensure_config", lambda *, interactive: config)
    monkeypatch.setattr(cli_gateway, "build_channels", lambda config, bus: fake_channels)
    monkeypatch.setattr(cli_gateway, "AgentLoop", FakeAgentLoop)
    monkeypatch.setattr(cli_gateway, "ChannelManager", FakeManager)
    monkeypatch.setattr(cli_gateway, "_run_gateway", fake_run_gateway)

    assert cli_main.main(["gateway"]) == 0
    captured = capsys.readouterr()

    assert "xagent gateway started." in captured.out
    assert "Channels:" in captured.out
    assert "  - lark" in captured.out
    assert "Cron: enabled" in captured.out
    assert isinstance(bus_seen, MessageBus)
    assert channels_seen is fake_channels
    assert isinstance(agent_loop_seen, FakeAgentLoop)
    assert isinstance(manager_seen, FakeManager)
    assert cron_service_seen is not None


def test_gateway_does_not_start_cron_when_disabled(monkeypatch, tmp_path, capsys) -> None:
    config = default_config()
    config.cron.enabled = False
    config.workspace.default_path = str(tmp_path / "workspace")
    config.workspace.sessions_path = str(tmp_path / "sessions")
    fake_channels = {"lark": object()}
    seen_cron_service = object()

    class FakeAgentLoop:
        def __init__(self, **kwargs) -> None:
            pass

    class FakeManager:
        def __init__(self, **kwargs) -> None:
            pass

    async def fake_run_gateway(*, cron_service=None, **kwargs) -> int:
        nonlocal seen_cron_service
        seen_cron_service = cron_service
        return 0

    monkeypatch.setattr(cli_gateway, "ensure_config", lambda *, interactive: config)
    monkeypatch.setattr(cli_gateway, "build_channels", lambda config, bus: fake_channels)
    monkeypatch.setattr(cli_gateway, "AgentLoop", FakeAgentLoop)
    monkeypatch.setattr(cli_gateway, "ChannelManager", FakeManager)
    monkeypatch.setattr(cli_gateway, "_run_gateway", fake_run_gateway)

    assert cli_main.main(["gateway"]) == 0

    captured = capsys.readouterr()
    assert "Cron: enabled" not in captured.out
    assert seen_cron_service is None


def test_root_without_args_shows_help(capsys) -> None:
    assert cli_main.main([]) == 0
    captured = capsys.readouterr()

    assert "Usage:" in captured.out
    assert "agent" in captured.out
    assert "channels" in captured.out
    assert "gateway" in captured.out


def test_root_accepts_short_help_option(capsys) -> None:
    assert cli_main.main(["-h"]) == 0
    captured = capsys.readouterr()

    assert "Usage:" in captured.out
    assert "channels" in captured.out
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


def test_channels_login_weixin_invokes_channel_login(monkeypatch) -> None:
    seen: dict[str, object] | None = None

    def fake_run_channels_login(*, channel_name: str, force: bool = False) -> int:
        nonlocal seen
        seen = {"channel_name": channel_name, "force": force}
        return 0

    monkeypatch.setattr(cli_channels, "_run_channels_login", fake_run_channels_login)

    assert cli_main.main(["channels", "login", "weixin"]) == 0

    assert seen == {"channel_name": "weixin", "force": False}


def test_channels_login_weixin_force_invokes_channel_login(monkeypatch) -> None:
    seen: dict[str, object] | None = None

    def fake_run_channels_login(*, channel_name: str, force: bool = False) -> int:
        nonlocal seen
        seen = {"channel_name": channel_name, "force": force}
        return 0

    monkeypatch.setattr(cli_channels, "_run_channels_login", fake_run_channels_login)

    assert cli_main.main(["channels", "login", "weixin", "--force"]) == 0

    assert seen == {"channel_name": "weixin", "force": True}


def test_channels_login_unknown_channel_returns_error(capsys) -> None:
    assert cli_channels._run_channels_login(channel_name="unknown") == 1

    captured = capsys.readouterr()
    assert "Unknown channel" in captured.out


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


@pytest.mark.asyncio
async def test_render_outbound_drains_command_messages(capsys) -> None:
    bus = MessageBus()
    await bus.publish_outbound(
        OutboundEvent(content="dreaming...", stream=StreamState(kind=StreamKind.END, stream_id="s1"))
    )
    await bus.publish_outbound(
        OutboundEvent(content="dream done.", stream=StreamState(kind=StreamKind.END, stream_id="s2"))
    )

    await cli_agent._render_outbound_once(bus)

    captured = capsys.readouterr()
    assert captured.out == "dreaming...\ndream done.\n"


def test_chat_uses_runtime_and_bus(monkeypatch, capsys) -> None:
    seen: InboundMessage | None = None
    answered = threading.Event()

    class FakeAgentLoop:
        async def run(self, bus: MessageBus) -> None:
            nonlocal seen
            while True:
                seen = await bus.consume_inbound()
                await bus.publish_outbound(
                    OutboundEvent(
                        content="ok",
                        channel=seen.channel,
                        chat_id=seen.chat_id,
                        reply_to=seen.sender_id,
                        session_id=seen.session_id,
                        stream=StreamState(kind=StreamKind.END, stream_id="s1"),
                    )
                )
                answered.set()

    inputs = iter(["hello", "exit"])

    def fake_input(prompt: str) -> str:
        value = next(inputs)
        if value == "exit":
            answered.wait(timeout=1)
        return value

    monkeypatch.setattr("builtins.input", fake_input)

    assert (
        cli_agent._chat(
            FakeAgentLoop(),  # type: ignore[arg-type]
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


def test_build_agent_registers_cron_tool_when_enabled(tmp_path) -> None:
    config = default_config()
    config.cron.tasks_path = str(tmp_path / "cron" / "tasks.json")
    session = SessionStore(tmp_path / "sessions").create(workspace_path=tmp_path)

    agent = build_agent(config=config, session=session)

    assert agent.tools.get("cron") is not None


def test_build_agent_omits_cron_tool_when_disabled(tmp_path) -> None:
    config = default_config()
    config.cron.enabled = False
    session = SessionStore(tmp_path / "sessions").create(workspace_path=tmp_path)

    agent = build_agent(config=config, session=session)

    assert agent.tools.get("cron") is None
