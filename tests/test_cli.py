from __future__ import annotations

from xagent.cli.factory import build_agent
from xagent.cli.main import build_parser, main
from xagent.config import default_config
from xagent.session import SessionStore


def test_gateway_placeholder(capsys) -> None:
    assert main(["gateway"]) == 0
    captured = capsys.readouterr()
    assert "reserved for future external channels" in captured.out


def test_cli_parser_uses_message_resume_and_workspace_aliases() -> None:
    args = build_parser().parse_args(
        ["-m", "hello", "-r", "terminal-session", "-w", "/tmp/project"]
    )

    assert args.message == "hello"
    assert args.resume == "terminal-session"
    assert args.workspace == "/tmp/project"


def test_cli_parser_supportes_long_option_names() -> None:
    args = build_parser().parse_args(
        [
            "--message",
            "hello",
            "--resume",
            "terminal-session",
            "--workspace",
            "/tmp/project",
        ]
    )

    assert args.message == "hello"
    assert args.resume == "terminal-session"
    assert args.workspace == "/tmp/project"


def test_build_agent_uses_provider_factory_model(tmp_path) -> None:
    config = default_config()
    config.agents.defaults.model = "factory-model"
    session = SessionStore(tmp_path / "sessions").create(workspace_path=tmp_path)

    agent = build_agent(config=config, session=session)

    assert agent.model == "factory-model"
