from __future__ import annotations

import json

from xagent.config import ensure_config, xagent_home
from xagent.session import SessionStore


def test_ensure_config_creates_user_level_layout(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("XAGENT_HOME", str(tmp_path / "home"))

    config = ensure_config(interactive=False)

    assert xagent_home() == tmp_path / "home"
    assert (tmp_path / "home" / "config.yaml").exists()
    assert config.default_workspace_path == tmp_path / "home" / "workspace" / "files"
    assert config.sessions_path == tmp_path / "home" / "workspace" / "sessions"
    assert config.default_workspace_path.is_dir()
    assert config.sessions_path.is_dir()
    assert config.channels == {}
    assert config.agents.defaults.model == "gpt-4o-mini"
    assert config.agents.defaults.provider == "openai_compat"
    assert config.providers.openai_compat.api_key_env == "OPENAI_API_KEY"
    config_text = (tmp_path / "home" / "config.yaml").read_text(encoding="utf-8")
    assert "agents:" in config_text
    assert "providers:" in config_text


def test_session_package_writes_meta_messages_trace_and_artifacts(tmp_path) -> None:
    sessions = SessionStore(tmp_path / "sessions")
    workspace = tmp_path / "project"
    workspace.mkdir()

    session = sessions.create(workspace_path=workspace, source="terminal", external_id="local")
    session.append_message({"role": "user", "content": "hello"})
    session.append_trace("example", {"ok": True})

    assert session.path.name == "terminal-local"
    assert session.artifacts_path.is_dir()
    records = [json.loads(line) for line in session.messages_path.read_text().splitlines()]
    assert records[0]["type"] == "meta"
    assert records[0]["workspace_path"] == str(workspace.resolve())
    assert records[1]["message"] == {"role": "user", "content": "hello"}
    trace = [json.loads(line) for line in session.trace_path.read_text().splitlines()]
    assert trace[1]["type"] == "example"
    assert trace[1]["ok"] is True


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
