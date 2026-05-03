from __future__ import annotations

import json

import pytest

from xagent.config import (
    DEFAULT_SHELL_BLACKLIST,
    LarkChannelConfig,
    ensure_config,
    load_config,
    xagent_home,
)
from xagent.session import SessionStore, resolve_session_id, session_id_from_chat


def test_ensure_config_creates_user_level_layout(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XAGENT_HOME", str(tmp_path / "home"))

    config = ensure_config(interactive=False)

    assert xagent_home() == tmp_path / "home"
    assert (tmp_path / "home" / "config.yaml").exists()
    assert config.default_workspace_path == tmp_path / "home" / "workspace" / "files"
    assert config.sessions_path == tmp_path / "home" / "workspace" / "sessions"
    assert config.default_workspace_path.is_dir()
    assert config.sessions_path.is_dir()
    assert config.channels.lark.enabled is False
    assert config.channels.lark.app_id is None
    assert config.channels.lark.app_secret is None
    assert config.agents.defaults.model == "gpt-4o-mini"
    assert config.agents.defaults.provider == "openai_compat"
    assert config.providers.openai_compat.api_key is None
    assert config.trace.raw_model_io is False
    assert config.trace.model_events is False
    assert config.permissions.shell.default == "allow"
    assert config.permissions.shell.blacklist == list(DEFAULT_SHELL_BLACKLIST)
    config_text = (tmp_path / "home" / "config.yaml").read_text(encoding="utf-8")
    assert "agents:" in config_text
    assert "providers:" in config_text
    assert "permissions:" in config_text
    assert "shell:" in config_text
    assert "default: allow" in config_text
    assert "- rm" in config_text
    assert "command_default" not in config_text
    assert "channels:" in config_text
    assert "lark:" in config_text
    assert "raw_model_io: false" in config_text
    assert "model_events: false" in config_text


def test_lark_channel_config_loads_explicit_values(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
channels:
  lark:
    enabled: true
    app_id: cli_explicit
    app_secret: secret_explicit
    verification_token: vt
    encrypt_key: ek
    domain: lark
    require_mention: false
    strip_mention: false
    auto_reconnect: false
    log_level: debug
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.channels.lark == LarkChannelConfig(
        enabled=True,
        app_id="cli_explicit",
        app_secret="secret_explicit",
        verification_token="vt",
        encrypt_key="ek",
        domain="lark",
        require_mention=False,
        strip_mention=False,
        auto_reconnect=False,
        log_level="debug",
    )


def test_lark_channel_config_rejects_unknown_domain() -> None:
    with pytest.raises(ValueError, match="channels.lark.domain"):
        LarkChannelConfig(domain="unknown")


def test_lark_channel_config_ignores_removed_env_fields(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
channels:
  lark:
    app_id_env: OLD_LARK_APP_ID
    app_secret_env: OLD_LARK_APP_SECRET
    verification_token_env: OLD_LARK_VERIFICATION_TOKEN
    encrypt_key_env: OLD_LARK_ENCRYPT_KEY
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.channels.lark.app_id is None
    assert config.channels.lark.app_secret is None


def test_provider_config_ignores_removed_env_field(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
providers:
  openai_compat:
    api_key_env: OLD_OPENAI_API_KEY
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.providers.openai_compat.api_key is None


def test_shell_permission_config_loads_custom_blacklist(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
permissions:
  shell:
    default: ask
    blacklist:
      - rm
      - npm install
      - ">"
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.permissions.shell.default == "ask"
    assert config.permissions.shell.blacklist == ["rm", "npm install", ">"]


def test_shell_permission_config_rejects_unknown_default(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
permissions:
  shell:
    default: maybe
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="permissions.shell.default"):
        load_config(path)


def test_session_package_writes_meta_messages_trace_and_artifacts(tmp_path) -> None:
    sessions = SessionStore(tmp_path / "sessions")
    workspace = tmp_path / "project"
    workspace.mkdir()

    session = sessions.create(workspace_path=workspace, channel="cli", chat_id="local")
    session.append_message({"role": "user", "content": "hello"})
    session.append_trace("example", {"ok": True})

    assert session.path.name == "cli:local"
    assert session.artifacts_path.is_dir()
    records = [json.loads(line) for line in session.messages_path.read_text().splitlines()]
    assert records[0]["type"] == "meta"
    assert records[0]["workspace_path"] == str(workspace.resolve())
    assert records[1]["message"] == {"role": "user", "content": "hello"}
    trace = [json.loads(line) for line in session.trace_path.read_text().splitlines()]
    assert trace[1]["type"] == "example"
    assert trace[1]["ok"] is True


def test_channel_chat_session_identity_and_explicit_override() -> None:
    assert session_id_from_chat("cli", "default") == "cli:default"
    assert resolve_session_id(channel="lark", chat_id="chat_1") == "lark:chat_1"
    assert (
        resolve_session_id(
            channel="lark",
            chat_id="chat_1",
            session_id="manual:session",
        )
        == "manual:session"
    )


def test_session_open_or_create_reuses_fixed_session_id(tmp_path) -> None:
    sessions = SessionStore(tmp_path / "sessions")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    first = sessions.open_or_create("cli:default", workspace_path=workspace)
    first.append_message({"role": "user", "content": "hello"})
    second = sessions.open_or_create("cli:default", workspace_path=tmp_path / "other")

    assert first.session_id == "cli:default"
    assert second.session_id == "cli:default"
    assert second.path == first.path
    assert second.workspace_path == workspace.resolve()
    assert second.read_records()[1]["message"] == {"role": "user", "content": "hello"}


def test_session_summary_becomes_model_visible_system_message(tmp_path) -> None:
    sessions = SessionStore(tmp_path / "sessions")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = sessions.create(workspace_path=workspace)
    session.append_message({"role": "user", "content": "old"})
    session.append_summary("Important state")
    session.append_message({"role": "user", "content": "new"})

    messages = session.read_model_messages()

    assert messages == [
        {"role": "system", "content": "Conversation summary:\nImportant state"},
        {"role": "user", "content": "new"},
    ]
