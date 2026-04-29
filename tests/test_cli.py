from __future__ import annotations

from xagent.cli.factory import build_agent
from xagent.cli import main as cli_main
from xagent.config import default_config
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

    async def fake_main(args: cli_main.AgentCliArgs) -> int:
        nonlocal seen
        seen = args
        return 0

    monkeypatch.setattr(cli_main, "_main_async", fake_main)

    assert cli_main.main(["agent", "-m", "hello", "-r", "terminal-session", "-w", "/tmp/project"]) == 0

    assert seen == cli_main.AgentCliArgs(
        message="hello",
        resume="terminal-session",
        workspace="/tmp/project",
    )


def test_agent_command_supports_long_option_names(monkeypatch) -> None:
    seen: cli_main.AgentCliArgs | None = None

    async def fake_main(args: cli_main.AgentCliArgs) -> int:
        nonlocal seen
        seen = args
        return 0

    monkeypatch.setattr(cli_main, "_main_async", fake_main)

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


def test_build_agent_uses_provider_factory_model(tmp_path) -> None:
    config = default_config()
    config.agents.defaults.model = "factory-model"
    session = SessionStore(tmp_path / "sessions").create(workspace_path=tmp_path)

    agent = build_agent(config=config, session=session)

    assert agent.model == "factory-model"
