from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from xagent.channel.models import GroupIngressMode


def _as_tuple(values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        return tuple(item.strip() for item in values.split(",") if item.strip())
    return tuple(str(item).strip() for item in values if str(item).strip())


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class FeishuConfig:
    app_id: str
    app_secret: str
    api_base_url: str = "https://open.feishu.cn"
    bot_open_id: str | None = None
    group_mode: GroupIngressMode = GroupIngressMode.MENTION_ONLY
    allow_all: bool = False
    allowed_user_ids: tuple[str, ...] = field(default_factory=tuple)
    allowed_chat_ids: tuple[str, ...] = field(default_factory=tuple)
    reconnect_initial_seconds: float = 1.0
    reconnect_cap_seconds: float = 30.0
    partial_emit_chars: int = 32
    deny_message: str = "Access denied."

    @classmethod
    def from_app_config(cls, app_config: Any) -> "FeishuConfig":
        settings = getattr(app_config, "feishu", None)
        if settings is None:
            raise ValueError("Feishu config is required in .xagent/config.yaml.")
        return cls.from_settings(settings)

    @classmethod
    def from_settings(cls, settings: Any) -> "FeishuConfig":
        app_id = str(getattr(settings, "app_id", "") or "").strip()
        app_secret = str(getattr(settings, "app_secret", "") or "").strip()
        if not app_id:
            raise ValueError("feishu.app_id is required in .xagent/config.yaml.")
        if not app_secret:
            raise ValueError("feishu.app_secret is required in .xagent/config.yaml.")
        mode = getattr(settings, "group_mode", GroupIngressMode.MENTION_ONLY.value)
        if isinstance(mode, GroupIngressMode):
            mode = mode.value
        mode = str(mode or GroupIngressMode.MENTION_ONLY.value).strip().lower()
        try:
            group_mode = GroupIngressMode(mode)
        except ValueError as exc:
            raise ValueError("feishu.group_mode must be 'mention_only' or 'all_text'.") from exc
        return cls(
            app_id=app_id,
            app_secret=app_secret,
            api_base_url=str(getattr(settings, "api_base_url", "") or "https://open.feishu.cn").strip()
            or "https://open.feishu.cn",
            bot_open_id=str(getattr(settings, "bot_open_id", "") or "").strip() or None,
            group_mode=group_mode,
            allow_all=_as_bool(getattr(settings, "allow_all", False)),
            allowed_user_ids=_as_tuple(getattr(settings, "allowed_user_ids", ())),
            allowed_chat_ids=_as_tuple(getattr(settings, "allowed_chat_ids", ())),
            reconnect_initial_seconds=float(getattr(settings, "reconnect_initial_seconds", 1.0)),
            reconnect_cap_seconds=float(getattr(settings, "reconnect_cap_seconds", 30.0)),
            partial_emit_chars=int(getattr(settings, "partial_emit_chars", 32)),
            deny_message=str(getattr(settings, "deny_message", "") or "Access denied.").strip()
            or "Access denied.",
        )
