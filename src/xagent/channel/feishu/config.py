from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from xagent.channel.models import GroupIngressMode
from xagent.cli.config.env import load_project_env


def _read_csv_env(name: str) -> tuple[str, ...]:
    raw = os.getenv(name, "")
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return tuple(values)


def _read_bool_env(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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
    def from_env(cls, start: str | Path | None = None) -> "FeishuConfig":
        load_project_env(Path(start) if start is not None else None)
        app_id = os.getenv("FEISHU_APP_ID", "").strip()
        app_secret = os.getenv("FEISHU_APP_SECRET", "").strip()
        if not app_id:
            raise ValueError("FEISHU_APP_ID is required.")
        if not app_secret:
            raise ValueError("FEISHU_APP_SECRET is required.")
        mode = os.getenv("FEISHU_GROUP_MODE", GroupIngressMode.MENTION_ONLY.value).strip().lower()
        try:
            group_mode = GroupIngressMode(mode)
        except ValueError as exc:
            raise ValueError("FEISHU_GROUP_MODE must be 'mention_only' or 'all_text'.") from exc
        return cls(
            app_id=app_id,
            app_secret=app_secret,
            api_base_url=os.getenv("FEISHU_API_BASE_URL", "https://open.feishu.cn").strip() or "https://open.feishu.cn",
            bot_open_id=os.getenv("FEISHU_BOT_OPEN_ID", "").strip() or None,
            group_mode=group_mode,
            allow_all=_read_bool_env("FEISHU_ALLOW_ALL", default=False),
            allowed_user_ids=_read_csv_env("FEISHU_ALLOWED_USER_IDS"),
            allowed_chat_ids=_read_csv_env("FEISHU_ALLOWED_CHAT_IDS"),
            reconnect_initial_seconds=float(os.getenv("FEISHU_RECONNECT_INITIAL_SECONDS", "1.0")),
            reconnect_cap_seconds=float(os.getenv("FEISHU_RECONNECT_CAP_SECONDS", "30.0")),
            partial_emit_chars=int(os.getenv("FEISHU_PARTIAL_EMIT_CHARS", "32")),
            deny_message=os.getenv("FEISHU_DENY_MESSAGE", "Access denied.").strip() or "Access denied.",
        )
