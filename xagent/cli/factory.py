from __future__ import annotations

from pathlib import Path

from xagent.agent import Agent
from xagent.agent.permissions import Approver, CliApprover
from xagent.agent.tools import build_default_tools
from xagent.agent.tools.shell import ShellPolicy
from xagent.config import AppConfig
from xagent.providers import make_provider
from xagent.session import Session, SessionStore, resolve_session_id


DEFAULT_CLI_CHANNEL = "cli"
DEFAULT_CLI_CHAT_ID = "default"
DEFAULT_CLI_SENDER_ID = "user"
DEFAULT_CLI_SESSION_ID = resolve_session_id(
    channel=DEFAULT_CLI_CHANNEL,
    chat_id=DEFAULT_CLI_CHAT_ID,
)


def resolve_workspace(config: AppConfig, workspace: str | None) -> Path:
    if workspace:
        return Path(workspace).expanduser().resolve()
    return config.default_workspace_path


def create_session(
    *,
    config: AppConfig,
    workspace_path: Path,
    resume: str | None = None,
    channel: str = DEFAULT_CLI_CHANNEL,
    chat_id: str = DEFAULT_CLI_CHAT_ID,
) -> Session:
    store = SessionStore(config.sessions_path)
    session_id = resolve_session_id(
        channel=channel,
        chat_id=chat_id,
        session_id=resume,
    )
    return store.open_or_create(session_id, workspace_path=workspace_path)


def build_agent(
    *,
    config: AppConfig,
    session: Session,
    approver: Approver | None = None,
) -> Agent:
    snapshot = make_provider(config)
    active_approver = approver or CliApprover()
    tools = build_default_tools(
        workspace=session.workspace_path,
        approver=active_approver,
        shell_policy=ShellPolicy.from_config(config.permissions.shell),
    )
    return Agent(
        provider=snapshot.provider,
        model=snapshot.model,
        session=session,
        tools=tools,
        temperature=config.agents.defaults.temperature,
        max_tokens=config.agents.defaults.max_tokens,
        max_steps=config.limits.max_steps,
        max_duration_seconds=config.limits.max_duration_seconds,
        max_repeated_tool_calls=config.limits.max_repeated_tool_calls,
        context_char_threshold=config.limits.context_char_threshold,
        trace_model_events=config.trace.model_events,
    )
