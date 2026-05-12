from __future__ import annotations

import json

import pytest

from xagent.config import (
    DEFAULT_SHELL_BLACKLIST,
    LarkChannelConfig,
    WebPermissionConfig,
    WebToolsConfig,
    WeixinChannelConfig,
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
    assert config.channels.lark.reactions_enabled is True
    assert config.channels.lark.working_reaction == "OnIt"
    assert config.channels.lark.done_reaction == "DONE"
    assert config.channels.weixin.enabled is False
    assert config.channels.weixin.allow_from == []
    assert config.channels.weixin.token is None
    assert config.channels.weixin.poll_timeout_seconds == 35
    assert config.agents.defaults.model == "gpt-4o-mini"
    assert config.agents.defaults.provider == "openai_compat"
    assert config.providers.openai_compat.api_key is None
    assert config.trace.raw_model_io is False
    assert config.trace.model_events is False
    assert config.permissions.shell.default == "allow"
    assert config.permissions.shell.blacklist == list(DEFAULT_SHELL_BLACKLIST)
    assert config.permissions.web.default == "allow"
    assert config.memory.enabled is True
    assert config.memory.inject_user is True
    assert config.memory.inject_soul is True
    assert config.memory.inject_workspace is True
    assert config.tools.web.enabled is True
    assert config.tools.web.fetch_backend == "jina"
    assert config.tools.web.search_backend == "auto"
    assert config.tools.web.jina.api_key is None
    assert config.tools.web.tavily.api_key is None
    assert config.tools.web.duckduckgo.enabled is True
    config_text = (tmp_path / "home" / "config.yaml").read_text(encoding="utf-8")
    assert "agents:" in config_text
    assert "providers:" in config_text
    assert "permissions:" in config_text
    assert "shell:" in config_text
    assert "web:" in config_text
    assert "default: allow" in config_text
    assert "- rm" in config_text
    assert "command_default" not in config_text
    assert "channels:" in config_text
    assert "lark:" in config_text
    assert "reactions_enabled: true" in config_text
    assert "working_reaction: OnIt" in config_text
    assert "done_reaction: DONE" in config_text
    assert "weixin:" in config_text
    assert "tools:" in config_text
    assert "memory:" in config_text
    assert "inject_workspace: true" in config_text
    assert "fetch_backend: jina" in config_text
    assert "search_backend: auto" in config_text
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
    reactions_enabled: false
    working_reaction: Thinking
    done_reaction: OK
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
        reactions_enabled=False,
        working_reaction="Thinking",
        done_reaction="OK",
    )


def test_lark_channel_config_rejects_unknown_domain() -> None:
    with pytest.raises(ValueError, match="channels.lark.domain"):
        LarkChannelConfig(domain="unknown")


def test_weixin_channel_config_loads_explicit_values(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
channels:
  weixin:
    enabled: true
    allow_from:
      - user_1
    base_url: https://weixin.example.test
    route_tag: route-a
    token: token-a
    state_dir: /tmp/weixin-state
    poll_timeout_seconds: 42
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.channels.weixin == WeixinChannelConfig(
        enabled=True,
        allow_from=["user_1"],
        base_url="https://weixin.example.test",
        route_tag="route-a",
        token="token-a",
        state_dir="/tmp/weixin-state",
        poll_timeout_seconds=42,
    )


def test_weixin_channel_config_rejects_invalid_poll_timeout() -> None:
    with pytest.raises(ValueError, match="channels.weixin.poll_timeout_seconds"):
        WeixinChannelConfig(poll_timeout_seconds=0)


def test_weixin_channel_config_rejects_non_list_allow_from() -> None:
    with pytest.raises(ValueError, match="channels.weixin.allow_from"):
        WeixinChannelConfig(allow_from="user_1")  # type: ignore[arg-type]


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


def test_config_ignores_removed_permission_and_trace_fields(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
permissions:
  remember: session
  read_default: allow
  write_default: ask
  network_default: ask
trace:
  context_threshold_ratio: 0.5
  model_events: true
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.permissions.shell.default == "allow"
    assert config.trace.model_events is True


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


def test_web_permission_config_loads_and_validates_default(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
permissions:
  web:
    default: ask
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.permissions.web.default == "ask"
    with pytest.raises(ValueError, match="permissions.web.default"):
        WebPermissionConfig(default="maybe")


def test_web_tools_config_loads_explicit_values_and_ignores_root_enabled(tmp_path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(
        """
tools:
  enabled:
    - old-global-field
  web:
    enabled: false
    fetch_backend: jina
    search_backend: duckduckgo
    timeout_seconds: 12
    max_fetch_chars: 1234
    max_search_results: 7
    jina:
      api_key: jina-key
      reader_base_url: https://reader.example.test
    tavily:
      api_key: tavily-key
    duckduckgo:
      enabled: false
""",
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.tools.web.enabled is False
    assert config.tools.web.fetch_backend == "jina"
    assert config.tools.web.search_backend == "duckduckgo"
    assert config.tools.web.timeout_seconds == 12
    assert config.tools.web.max_fetch_chars == 1234
    assert config.tools.web.max_search_results == 7
    assert config.tools.web.jina.api_key == "jina-key"
    assert config.tools.web.jina.reader_base_url == "https://reader.example.test"
    assert config.tools.web.tavily.api_key == "tavily-key"
    assert config.tools.web.duckduckgo.enabled is False


def test_web_tools_config_rejects_unknown_backends() -> None:
    with pytest.raises(ValueError, match="tools.web.fetch_backend"):
        WebToolsConfig(fetch_backend="direct")
    with pytest.raises(ValueError, match="tools.web.search_backend"):
        WebToolsConfig(search_backend="unknown")


def test_session_package_writes_meta_messages_trace_and_artifacts(tmp_path) -> None:
    sessions = SessionStore(tmp_path / "sessions")
    workspace = tmp_path / "project"
    workspace.mkdir()

    session = sessions.create(workspace_path=workspace, channel="cli", chat_id="local")
    session.append_message({"role": "user", "content": "hello"})
    session.append_trace("example", {"ok": True})

    assert session.path.name == "cli:local"
    assert session.artifacts_path.is_dir()
    assert session.summary_path.exists()
    assert session.session_state_path.exists()
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


def test_session_open_existing_package_creates_missing_sidecars(tmp_path) -> None:
    sessions = SessionStore(tmp_path / "sessions")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = sessions.open_or_create("cli:default", workspace_path=workspace)
    session.summary_path.unlink()
    session.session_state_path.unlink()

    reopened = sessions.open("cli:default")

    assert reopened.summary_path.exists()
    assert reopened.session_state_path.exists()


def test_session_open_for_chat_defaults_to_channel_chat_identity(tmp_path) -> None:
    sessions = SessionStore(tmp_path / "sessions")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    first = sessions.open_for_chat(
        workspace_path=workspace,
        channel="cli",
        chat_id="default",
    )
    first.append_message({"role": "user", "content": "hello"})
    second = sessions.open_for_chat(
        workspace_path=workspace,
        channel="cli",
        chat_id="default",
    )

    assert first.session_id == "cli:default"
    assert second.session_id == "cli:default"
    assert second.path == first.path
    assert second.read_records()[1]["message"] == {"role": "user", "content": "hello"}


def test_session_open_for_chat_explicit_session_id_wins(tmp_path) -> None:
    sessions = SessionStore(tmp_path / "sessions")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    created = sessions.open_for_chat(
        workspace_path=workspace,
        channel="cli",
        chat_id="default",
        session_id="cli:experiment",
    )
    reopened = sessions.open_for_chat(
        workspace_path=workspace,
        channel="cli",
        chat_id="default",
        session_id="cli:experiment",
    )

    assert created.session_id == "cli:experiment"
    assert reopened.path == created.path


def test_session_rejects_path_like_session_ids(tmp_path) -> None:
    sessions = SessionStore(tmp_path / "sessions")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    for session_id in {".", "..", "../outside", "nested/session", r"..\outside"}:
        with pytest.raises(ValueError, match="Invalid session id"):
            sessions.open_or_create(session_id, workspace_path=workspace)


def test_session_summary_jsonl_becomes_model_visible_system_message(tmp_path) -> None:
    sessions = SessionStore(tmp_path / "sessions")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = sessions.create(workspace_path=workspace)
    session.append_message({"role": "user", "content": "old"})
    summary = session.append_summary("Important state")
    session.append_message({"role": "user", "content": "new"})

    messages = session.read_model_messages()

    assert summary["type"] == "summary"
    assert summary["covers"]["messages_until_index"] == 1
    assert [record["type"] for record in session.read_records()] == ["meta", "message", "message"]
    assert session.read_session_state()["compact"]["latest_summary_id"] == summary["summary_id"]
    assert messages == [
        {"role": "system", "content": "Conversation summary:\nImportant state"},
        {"role": "user", "content": "new"},
    ]


def test_session_summary_can_retain_recent_user_turn_window(tmp_path) -> None:
    sessions = SessionStore(tmp_path / "sessions")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = sessions.create(workspace_path=workspace)
    for index in range(1, 7):
        session.append_message({"role": "user", "content": f"user {index}"})
        session.append_message({"role": "assistant", "content": f"assistant {index}"})
    retained_from = session.recent_user_turn_start_index(
        session.latest_message_record_index(),
        user_turns=4,
    )
    summary = session.append_summary(
        "Important state",
        retained_from_index=retained_from,
    )

    messages = session.read_model_messages()

    assert summary["covers"]["retained_from_index"] == 5
    assert session.read_session_state()["compact"]["retained_from_index"] == 5
    assert messages[0] == {"role": "system", "content": "Conversation summary:\nImportant state"}
    assert messages[1] == {"role": "user", "content": "user 3"}
    assert messages[-1] == {"role": "assistant", "content": "assistant 6"}


def test_session_compat_reads_legacy_summary_records(tmp_path) -> None:
    sessions = SessionStore(tmp_path / "sessions")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    session = sessions.create(workspace_path=workspace)
    session.append_message({"role": "user", "content": "old"})
    session._append_jsonl(session.messages_path, {"type": "summary", "content": "Legacy state"})
    session.append_message({"role": "user", "content": "new"})

    assert session.read_model_messages() == [
        {"role": "system", "content": "Conversation summary:\nLegacy state"},
        {"role": "user", "content": "new"},
    ]
