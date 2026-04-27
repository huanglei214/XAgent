from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, model_validator

from xagent.provider.types import ModelConfig
from xagent.agent.paths import (
    ensure_config_dir,
    get_config_example_file,
    get_config_file,
)


# ── schema ──────────────────────────────────────────────────────────────────


class FeishuAppConfig(BaseModel):
    app_id: str = ""
    app_secret: str = ""
    api_base_url: str = "https://open.feishu.cn"
    bot_open_id: Optional[str] = None
    group_mode: Literal["mention_only", "all_text"] = "mention_only"
    allow_all: bool = False
    allowed_user_ids: list[str] = Field(default_factory=list)
    allowed_chat_ids: list[str] = Field(default_factory=list)
    reconnect_initial_seconds: float = 1.0
    reconnect_cap_seconds: float = 30.0
    partial_emit_chars: int = 32
    deny_message: str = "Access denied."


class AppConfig(BaseModel):
    default_model: str
    max_model_calls: int = Field(default=100, ge=1, le=1000)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "WARNING"
    models: list[ModelConfig]
    feishu: FeishuAppConfig = Field(default_factory=FeishuAppConfig)

    @model_validator(mode="after")
    def validate_default_model(self) -> AppConfig:
        if not self.models:
            raise ValueError("Config must define at least one model.")
        model_names = {model.name for model in self.models}
        if len(model_names) != len(self.models):
            raise ValueError("Config contains duplicate model names.")
        if self.default_model not in model_names:
            raise ValueError(f"default_model '{self.default_model}' does not match any configured model.")
        return self


def default_config() -> AppConfig:
    """构建默认配置。"""
    model = ModelConfig(
        name="ep-your-ark-endpoint-id",
        provider="ark",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_key="",
    )
    return AppConfig(default_model=model.name, max_model_calls=100, log_level="WARNING", models=[model])


# ── loader ──────────────────────────────────────────────────────────────────


def config_exists() -> bool:
    """检查配置文件是否存在。"""
    return get_config_file().exists()


def load_config() -> AppConfig:
    """从配置文件加载 AppConfig。"""
    config_path = get_config_file()
    if not config_path.exists():
        raise FileNotFoundError("XAgent config not found. Run `xagent config init` first.")

    try:
        data = _parse_config_yaml(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Invalid config YAML in {config_path}: {exc}") from exc

    return AppConfig.model_validate(data)


def save_config(config: AppConfig) -> Path:
    """将 AppConfig 保存到配置文件。"""
    ensure_config_dir()
    config_path = get_config_file()
    config_path.write_text(_dump_config(config), encoding="utf-8")
    return config_path


def resolve_default_model(config: AppConfig) -> ModelConfig:
    """根据 default_model 名称查找对应的 ModelConfig。"""
    for model in config.models:
        if model.name == config.default_model:
            return model
    raise ValueError(f"Default model '{config.default_model}' was not found.")


def add_model(config: AppConfig, model: ModelConfig, *, make_default: bool = False) -> AppConfig:
    """向配置中添加一个新模型。"""
    if any(existing.name == model.name for existing in config.models):
        raise ValueError(f"Model '{model.name}' already exists.")
    models = [*config.models, model]
    default_model = model.name if make_default else config.default_model
    return AppConfig(
        default_model=default_model,
        max_model_calls=config.max_model_calls,
        log_level=config.log_level,
        models=models,
    )


def remove_model(config: AppConfig, model_name: str) -> AppConfig:
    """从配置中移除指定模型。"""
    remaining = [model for model in config.models if model.name != model_name]
    if len(remaining) == len(config.models):
        raise ValueError(f"Model '{model_name}' is not defined in config.")
    if not remaining:
        raise ValueError("Cannot remove the last configured model.")
    default_model = config.default_model
    if default_model == model_name:
        default_model = remaining[0].name
    return AppConfig(
        default_model=default_model,
        max_model_calls=config.max_model_calls,
        log_level=config.log_level,
        models=remaining,
    )


def set_default_model_name(config: AppConfig, model_name: str) -> AppConfig:
    """设置默认模型名称。"""
    if not any(model.name == model_name for model in config.models):
        raise ValueError(f"Model '{model_name}' is not defined in config.")
    return AppConfig(
        default_model=model_name,
        max_model_calls=config.max_model_calls,
        log_level=config.log_level,
        models=config.models,
    )


def default_base_url(provider: str) -> str:
    """根据 provider 返回默认 base_url。"""
    if provider == "ark":
        return "https://ark.cn-beijing.volces.com/api/v3"
    if provider == "anthropic":
        return "https://api.anthropic.com"
    return "https://api.openai.com/v1"


def _dump_config(config: AppConfig) -> str:
    """将 AppConfig 序列化为 YAML 字符串（内部委托）。"""
    return dump_config_yaml(config)


def dump_config_yaml(config: AppConfig) -> str:
    """将 AppConfig 序列化为 YAML 字符串。"""
    lines = [
        f'default_model: "{_quote_yaml(config.default_model)}"',
        f"max_model_calls: {config.max_model_calls}",
        "# Supported log levels: DEBUG, INFO, WARNING, ERROR, CRITICAL",
        f'log_level: "{config.log_level}"',
        "models:",
    ]
    for model in config.models:
        lines.append(f'  - name: "{_quote_yaml(model.name)}"')
        lines.append(f'    provider: "{_quote_yaml(model.provider)}"')
        if model.base_url:
            lines.append(f'    base_url: "{_quote_yaml(model.base_url)}"')
        lines.append(f'    api_key: "{_quote_yaml(model.api_key)}"')
    lines.extend(
        [
            "feishu:",
            f'  app_id: "{_quote_yaml(config.feishu.app_id)}"',
            f'  app_secret: "{_quote_yaml(config.feishu.app_secret)}"',
            f'  api_base_url: "{_quote_yaml(config.feishu.api_base_url)}"',
            f'  bot_open_id: "{_quote_yaml(config.feishu.bot_open_id or "")}"',
            f'  group_mode: "{_quote_yaml(config.feishu.group_mode)}"',
            f"  allow_all: {_dump_yaml_bool(config.feishu.allow_all)}",
            f"  allowed_user_ids: {_dump_yaml_list(config.feishu.allowed_user_ids)}",
            f"  allowed_chat_ids: {_dump_yaml_list(config.feishu.allowed_chat_ids)}",
            f"  reconnect_initial_seconds: {config.feishu.reconnect_initial_seconds}",
            f"  reconnect_cap_seconds: {config.feishu.reconnect_cap_seconds}",
            f"  partial_emit_chars: {config.feishu.partial_emit_chars}",
            f'  deny_message: "{_quote_yaml(config.feishu.deny_message)}"',
        ]
    )
    return "\n".join(lines) + "\n"


def _dump_yaml_bool(value: bool) -> str:
    return "true" if value else "false"


def _dump_yaml_list(values: list[str]) -> str:
    if not values:
        return "[]"
    return "[" + ", ".join(f'"{_quote_yaml(value)}"' for value in values) + "]"


def _quote_yaml(value: str) -> str:
    """对 YAML 字符串值进行转义处理。"""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _parse_config_yaml(raw: str) -> dict[str, Any]:
    """解析配置 YAML 文本为字典。"""
    data: dict[str, Any] = {"models": [], "feishu": {}}
    current_model: dict[str, Any] | None = None
    in_models = False
    in_feishu = False

    for original_line in raw.splitlines():
        stripped = original_line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if stripped == "models:":
            in_models = True
            in_feishu = False
            current_model = None
            continue
        if stripped == "feishu:":
            in_models = False
            in_feishu = True
            current_model = None
            continue

        if stripped.startswith("default_model:"):
            _, value = stripped.split(":", 1)
            data["default_model"] = _parse_yaml_scalar(value.strip())
            in_models = False
            in_feishu = False
            current_model = None
            continue

        if stripped.startswith("max_model_calls:"):
            _, value = stripped.split(":", 1)
            raw_value = _parse_yaml_scalar(value.strip())
            try:
                data["max_model_calls"] = int(raw_value)
            except ValueError as exc:
                raise ValueError(f"Invalid max_model_calls value: {raw_value}") from exc
            in_models = False
            in_feishu = False
            current_model = None
            continue

        if stripped.startswith("log_level:"):
            _, value = stripped.split(":", 1)
            data["log_level"] = _parse_yaml_scalar(value.strip()).upper()
            in_models = False
            in_feishu = False
            current_model = None
            continue

        if stripped.startswith("- "):
            if not in_models:
                raise ValueError("List item found before models: section")
            current_model = {}
            data["models"].append(current_model)
            rest = stripped[2:].strip()
            if rest:
                key, value = _split_yaml_key_value(rest)
                current_model[key] = _parse_yaml_scalar(value)
            continue

        if in_feishu:
            key, value = _split_yaml_key_value(stripped)
            data["feishu"][key] = _parse_yaml_scalar(value)
            continue

        if current_model is None:
            key, value = _split_yaml_key_value(stripped)
            data[key] = _parse_yaml_scalar(value)
            continue

        key, value = _split_yaml_key_value(stripped)
        current_model[key] = _parse_yaml_scalar(value)

    return data


def _split_yaml_key_value(line: str) -> tuple[str, str]:
    """将 YAML 行拆分为键值对。"""
    if ":" not in line:
        raise ValueError(f"Expected key/value pair, got: {line}")
    key, value = line.split(":", 1)
    return key.strip(), value.strip()


def _parse_yaml_scalar(value: str):
    """解析 YAML 标量值，去除引号并处理转义。"""
    if not value:
        return ""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        inner = value[1:-1]
        return inner.replace('\\"', '"').replace("\\\\", "\\")
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "none", "~"}:
        return None
    if value.startswith("[") and value.endswith("]"):
        try:
            parsed = ast.literal_eval(value)
        except (SyntaxError, ValueError) as exc:
            raise ValueError(f"Invalid inline list value: {value}") from exc
        if not isinstance(parsed, list):
            raise ValueError(f"Expected inline list value, got: {value}")
        return [str(item) for item in parsed]
    return value


# ── template ────────────────────────────────────────────────────────────────


def ensure_config_example_file(start: Path | None = None, force: bool = False) -> Path:
    """确保配置示例文件存在，不存在则创建。"""
    example_path = get_config_example_file(start)
    if example_path.exists() and not force:
        return example_path

    example_path.write_text(_build_example_contents(), encoding="utf-8")
    return example_path


def _build_example_contents() -> str:
    """构建配置示例文件内容。"""
    return (
        "# Example XAgent project configuration\n"
        "# Copy relevant values into .xagent/config.yaml if you want a fresh local config.\n"
        + dump_config_yaml(default_config())
    )
