from __future__ import annotations

import getpass
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]


def xagent_home() -> Path:
    """Return the user-level XAgent home directory."""

    configured = os.environ.get("XAGENT_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path.home() / ".xagent"


@dataclass
class AgentDefaultsConfig:
    model: str = "gpt-4o-mini"
    provider: str = "openai_compat"
    temperature: float | None = None
    max_tokens: int | None = None


@dataclass
class AgentsConfig:
    defaults: AgentDefaultsConfig = field(default_factory=AgentDefaultsConfig)


@dataclass
class OpenAICompatProviderConfig:
    api_key: str | None = None
    api_base: str | None = None
    extra_headers: dict[str, str] = field(default_factory=dict)
    extra_body: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: float = 120.0


@dataclass
class ProvidersConfig:
    openai_compat: OpenAICompatProviderConfig = field(
        default_factory=OpenAICompatProviderConfig
    )


@dataclass
class WorkspaceConfig:
    default_path: str = "~/.xagent/workspace/files"
    sessions_path: str = "~/.xagent/workspace/sessions"


@dataclass
class PermissionConfig:
    remember: str = "session"
    read_default: str = "allow"
    write_default: str = "ask"
    command_default: str = "ask"
    network_default: str = "ask"


@dataclass
class TraceConfig:
    raw_model_io: bool = False
    model_events: bool = False
    context_threshold_ratio: float = 0.70


@dataclass
class ToolsConfig:
    enabled: list[str] = field(default_factory=list)


@dataclass
class AgentLimitsConfig:
    max_steps: int = 50
    max_duration_seconds: float = 600.0
    max_repeated_tool_calls: int = 3
    context_char_threshold: int = 120_000


@dataclass
class LarkChannelConfig:
    enabled: bool = False
    app_id: str | None = None
    app_secret: str | None = None
    verification_token: str | None = None
    encrypt_key: str | None = None
    domain: str = "feishu"
    require_mention: bool = True
    strip_mention: bool = True
    auto_reconnect: bool = True
    log_level: str = "info"

    def __post_init__(self) -> None:
        if self.domain not in {"feishu", "lark"}:
            raise ValueError("channels.lark.domain must be 'feishu' or 'lark'")


@dataclass
class ChannelsConfig:
    lark: LarkChannelConfig = field(default_factory=LarkChannelConfig)


@dataclass
class AppConfig:
    agents: AgentsConfig = field(default_factory=AgentsConfig)
    providers: ProvidersConfig = field(default_factory=ProvidersConfig)
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    permissions: PermissionConfig = field(default_factory=PermissionConfig)
    trace: TraceConfig = field(default_factory=TraceConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    limits: AgentLimitsConfig = field(default_factory=AgentLimitsConfig)
    channels: ChannelsConfig = field(default_factory=ChannelsConfig)

    @property
    def default_workspace_path(self) -> Path:
        return _expand_path(self.workspace.default_path)

    @property
    def sessions_path(self) -> Path:
        return _expand_path(self.workspace.sessions_path)


def _expand_path(value: str) -> Path:
    return Path(os.path.expandvars(value)).expanduser().resolve()


def default_config() -> AppConfig:
    home = xagent_home()
    return AppConfig(
        workspace=WorkspaceConfig(
            default_path=str(home / "workspace" / "files"),
            sessions_path=str(home / "workspace" / "sessions"),
        )
    )


def ensure_app_home(config: AppConfig | None = None) -> None:
    config = config or default_config()
    xagent_home().mkdir(parents=True, exist_ok=True)
    config.default_workspace_path.mkdir(parents=True, exist_ok=True)
    config.sessions_path.mkdir(parents=True, exist_ok=True)


def config_path() -> Path:
    return xagent_home() / "config.yaml"


def save_config(config: AppConfig, path: Path | None = None) -> Path:
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(config)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    ensure_app_home(config)
    return path


def load_config(path: Path | None = None) -> AppConfig:
    path = path or config_path()
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return _config_from_mapping(payload)


def ensure_config(*, interactive: bool = False) -> AppConfig:
    path = config_path()
    if path.exists():
        config = load_config(path)
        ensure_app_home(config)
        return config

    config = default_config()
    if interactive:
        _interactive_fill(config)
    save_config(config, path)
    return config


def _interactive_fill(config: AppConfig) -> None:
    if not os.isatty(0):
        return
    model_name = input(f"Model name [{config.agents.defaults.model}]: ").strip()
    if model_name:
        config.agents.defaults.model = model_name
    provider = config.providers.openai_compat
    api_key = getpass.getpass("OpenAI-compatible API key [empty/no-key]: ").strip()
    if api_key:
        provider.api_key = api_key
    base_url = input("OpenAI-compatible base URL [default]: ").strip()
    if base_url:
        provider.api_base = base_url


def _config_from_mapping(payload: dict[str, Any]) -> AppConfig:
    default = default_config()
    agents_payload = payload.get("agents", {})
    defaults_payload = agents_payload.get("defaults", {}) if isinstance(agents_payload, dict) else {}
    providers_payload = payload.get("providers", {})
    openai_payload = (
        providers_payload.get("openai_compat", {})
        if isinstance(providers_payload, dict)
        else {}
    )
    if not isinstance(openai_payload, dict):
        openai_payload = {}
    openai_defaults = asdict(default.providers.openai_compat)
    openai_values = {
        **openai_defaults,
        **{key: value for key, value in openai_payload.items() if key in openai_defaults},
    }
    channels_payload = payload.get("channels", {})
    lark_payload = (
        channels_payload.get("lark", {})
        if isinstance(channels_payload, dict)
        else {}
    )
    if not isinstance(lark_payload, dict):
        lark_payload = {}
    lark_defaults = asdict(default.channels.lark)
    lark_values = {
        **lark_defaults,
        **{key: value for key, value in lark_payload.items() if key in lark_defaults},
    }
    return AppConfig(
        agents=AgentsConfig(
            defaults=AgentDefaultsConfig(
                **{**asdict(default.agents.defaults), **defaults_payload}
            )
        ),
        providers=ProvidersConfig(
            openai_compat=OpenAICompatProviderConfig(
                **openai_values
            )
        ),
        workspace=WorkspaceConfig(
            **{**asdict(default.workspace), **payload.get("workspace", {})}
        ),
        permissions=PermissionConfig(
            **{**asdict(default.permissions), **payload.get("permissions", {})}
        ),
        trace=TraceConfig(**{**asdict(default.trace), **payload.get("trace", {})}),
        tools=ToolsConfig(**{**asdict(default.tools), **payload.get("tools", {})}),
        limits=AgentLimitsConfig(**{**asdict(default.limits), **payload.get("limits", {})}),
        channels=ChannelsConfig(
            lark=LarkChannelConfig(**lark_values)
        ),
    )
