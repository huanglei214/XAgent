from __future__ import annotations

import getpass
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]


DEFAULT_SHELL_BLACKLIST: tuple[str, ...] = (
    "rm",
    "rmdir",
    "unlink",
    "shred",
    "dd",
    "mkfs",
    "chmod",
    "chown",
    "chgrp",
    "sudo",
    "su",
    "kill",
    "killall",
    "pkill",
    "curl",
    "wget",
    "npx",
    "npm install",
    "npm i",
    "pnpm add",
    "yarn add",
    "pip install",
    "pip3 install",
    "uv add",
    "uv pip install",
    "brew install",
    "apt install",
    "apt-get install",
    "go get",
    "cargo install",
    "gem install",
    ">",
    ">>",
    ">|",
    "&>",
    "2>",
    "2>>",
)


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
class ShellPermissionConfig:
    default: str = "allow"
    blacklist: list[str] = field(default_factory=lambda: list(DEFAULT_SHELL_BLACKLIST))

    def __post_init__(self) -> None:
        if self.default not in {"allow", "ask", "deny"}:
            raise ValueError("permissions.shell.default must be 'allow', 'ask', or 'deny'")


@dataclass
class WebPermissionConfig:
    default: str = "allow"

    def __post_init__(self) -> None:
        if self.default not in {"allow", "ask", "deny"}:
            raise ValueError("permissions.web.default must be 'allow', 'ask', or 'deny'")


@dataclass
class PermissionConfig:
    shell: ShellPermissionConfig = field(default_factory=ShellPermissionConfig)
    web: WebPermissionConfig = field(default_factory=WebPermissionConfig)


@dataclass
class TraceConfig:
    raw_model_io: bool = False
    model_events: bool = False


@dataclass
class JinaWebConfig:
    api_key: str | None = None
    reader_base_url: str = "https://r.jina.ai"


@dataclass
class TavilyWebConfig:
    api_key: str | None = None


@dataclass
class DuckDuckGoWebConfig:
    enabled: bool = True


@dataclass
class WebToolsConfig:
    enabled: bool = True
    fetch_backend: str = "jina"
    search_backend: str = "auto"
    timeout_seconds: float = 30.0
    max_fetch_chars: int = 20_000
    max_search_results: int = 5
    jina: JinaWebConfig = field(default_factory=JinaWebConfig)
    tavily: TavilyWebConfig = field(default_factory=TavilyWebConfig)
    duckduckgo: DuckDuckGoWebConfig = field(default_factory=DuckDuckGoWebConfig)

    def __post_init__(self) -> None:
        if self.fetch_backend not in {"jina"}:
            raise ValueError("tools.web.fetch_backend must be 'jina'")
        if self.search_backend not in {"auto", "tavily", "duckduckgo"}:
            raise ValueError("tools.web.search_backend must be 'auto', 'tavily', or 'duckduckgo'")


@dataclass
class ToolsConfig:
    web: WebToolsConfig = field(default_factory=WebToolsConfig)


@dataclass
class AgentLimitsConfig:
    max_steps: int = 50
    max_duration_seconds: float = 600.0
    max_repeated_tool_calls: int = 3
    context_char_threshold: int = 120_000


@dataclass
class MemoryConfig:
    enabled: bool = True
    inject_user: bool = True
    inject_soul: bool = True
    inject_workspace: bool = True


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
    reactions_enabled: bool = True
    working_reaction: str = "OnIt"
    done_reaction: str = "DONE"

    def __post_init__(self) -> None:
        if self.domain not in {"feishu", "lark"}:
            raise ValueError("channels.lark.domain must be 'feishu' or 'lark'")


@dataclass
class WeixinChannelConfig:
    enabled: bool = False
    allow_from: list[str] = field(default_factory=list)
    base_url: str = "https://ilinkai.weixin.qq.com"
    route_tag: str | None = None
    token: str | None = None
    state_dir: str | None = None
    poll_timeout_seconds: int = 35

    def __post_init__(self) -> None:
        if not isinstance(self.allow_from, list):
            raise ValueError("channels.weixin.allow_from must be a list")
        self.allow_from = [str(item) for item in self.allow_from]
        if self.poll_timeout_seconds <= 0:
            raise ValueError("channels.weixin.poll_timeout_seconds must be greater than 0")


@dataclass
class ChannelsConfig:
    lark: LarkChannelConfig = field(default_factory=LarkChannelConfig)
    weixin: WeixinChannelConfig = field(default_factory=WeixinChannelConfig)


@dataclass
class AppConfig:
    agents: AgentsConfig = field(default_factory=AgentsConfig)
    providers: ProvidersConfig = field(default_factory=ProvidersConfig)
    workspace: WorkspaceConfig = field(default_factory=WorkspaceConfig)
    permissions: PermissionConfig = field(default_factory=PermissionConfig)
    trace: TraceConfig = field(default_factory=TraceConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    limits: AgentLimitsConfig = field(default_factory=AgentLimitsConfig)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
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
    weixin_payload = (
        channels_payload.get("weixin", {})
        if isinstance(channels_payload, dict)
        else {}
    )
    if not isinstance(weixin_payload, dict):
        weixin_payload = {}
    weixin_defaults = asdict(default.channels.weixin)
    weixin_values = {
        **weixin_defaults,
        **{key: value for key, value in weixin_payload.items() if key in weixin_defaults},
    }
    permissions_payload = payload.get("permissions", {})
    if not isinstance(permissions_payload, dict):
        permissions_payload = {}
    permission_values = _merge_known_fields(default.permissions, permissions_payload)
    shell_payload = permissions_payload.get("shell", {})
    if not isinstance(shell_payload, dict):
        shell_payload = {}
    permission_values["shell"] = ShellPermissionConfig(
        **_merge_known_fields(default.permissions.shell, shell_payload)
    )
    web_permission_payload = permissions_payload.get("web", {})
    if not isinstance(web_permission_payload, dict):
        web_permission_payload = {}
    permission_values["web"] = WebPermissionConfig(
        **_merge_known_fields(default.permissions.web, web_permission_payload)
    )
    tools_payload = payload.get("tools", {})
    if not isinstance(tools_payload, dict):
        tools_payload = {}
    tools_values = _merge_known_fields(default.tools, tools_payload)
    web_payload = tools_payload.get("web", {})
    if not isinstance(web_payload, dict):
        web_payload = {}
    web_values = _merge_known_fields(default.tools.web, web_payload)
    jina_payload = web_payload.get("jina", {})
    tavily_payload = web_payload.get("tavily", {})
    duckduckgo_payload = web_payload.get("duckduckgo", {})
    if not isinstance(jina_payload, dict):
        jina_payload = {}
    if not isinstance(tavily_payload, dict):
        tavily_payload = {}
    if not isinstance(duckduckgo_payload, dict):
        duckduckgo_payload = {}
    web_values["jina"] = JinaWebConfig(
        **_merge_known_fields(default.tools.web.jina, jina_payload)
    )
    web_values["tavily"] = TavilyWebConfig(
        **_merge_known_fields(default.tools.web.tavily, tavily_payload)
    )
    web_values["duckduckgo"] = DuckDuckGoWebConfig(
        **_merge_known_fields(default.tools.web.duckduckgo, duckduckgo_payload)
    )
    tools_values["web"] = WebToolsConfig(**web_values)
    trace_payload = payload.get("trace", {})
    if not isinstance(trace_payload, dict):
        trace_payload = {}

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
        permissions=PermissionConfig(**permission_values),
        trace=TraceConfig(**_merge_known_fields(default.trace, trace_payload)),
        tools=ToolsConfig(**tools_values),
        limits=AgentLimitsConfig(**{**asdict(default.limits), **payload.get("limits", {})}),
        memory=MemoryConfig(**{**asdict(default.memory), **payload.get("memory", {})}),
        channels=ChannelsConfig(
            lark=LarkChannelConfig(**lark_values),
            weixin=WeixinChannelConfig(**weixin_values),
        ),
    )


def _merge_known_fields(default: Any, payload: dict[str, Any]) -> dict[str, Any]:
    values = asdict(default)
    return {**values, **{key: value for key, value in payload.items() if key in values}}
