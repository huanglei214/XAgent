from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from xagent.bus.types import ModelConfig
from xagent.foundation.runtime.paths import (
    ensure_config_dir,
    get_config_example_file,
    get_config_file,
    get_env_file,
)


# ── schema ──────────────────────────────────────────────────────────────────


class AppConfig(BaseModel):
    default_model: str
    max_model_calls: int = Field(default=100, ge=1, le=1000)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "WARNING"
    models: list[ModelConfig]

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
        api_key_env="ARK_API_KEY",
    )
    return AppConfig(default_model=model.name, max_model_calls=100, log_level="WARNING", models=[model])


# ── env ─────────────────────────────────────────────────────────────────────


def ensure_env_file(start: Path | None = None, force: bool = False) -> Path:
    """确保 .env 文件存在，不存在则创建。"""
    env_path = get_env_file(start)
    if env_path.exists() and not force:
        return env_path

    env_path.write_text(_default_env_contents(), encoding="utf-8")
    return env_path


def load_project_env(start: Path | None = None, override: bool = False) -> dict[str, str]:
    """加载项目 .env 文件到环境变量。"""
    env_path = get_env_file(start)
    if not env_path.exists():
        return {}

    values = _parse_env(env_path.read_text(encoding="utf-8"))
    for key, value in values.items():
        if override or key not in os.environ:
            os.environ[key] = value
    return values


def _default_env_contents() -> str:
    """返回默认 .env 文件内容。"""
    return (
        "# Project-local environment variables for XAgent\n"
        "ARK_API_KEY=\n"
        "OPENAI_API_KEY=\n"
        "ANTHROPIC_API_KEY=\n"
        "FEISHU_APP_ID=\n"
        "FEISHU_APP_SECRET=\n"
        "FEISHU_API_BASE_URL=https://open.feishu.cn\n"
        "FEISHU_BOT_OPEN_ID=\n"
        "FEISHU_GROUP_MODE=mention_only\n"
        "FEISHU_ALLOW_ALL=false\n"
        "FEISHU_ALLOWED_USER_IDS=\n"
        "FEISHU_ALLOWED_CHAT_IDS=\n"
    )


def _parse_env(raw: str) -> dict[str, str]:
    """解析 .env 文件内容为键值对。"""
    values: dict[str, str] = {}
    for original_line in raw.splitlines():
        line = original_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        values[key] = value
    return values


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


def default_api_key_env(provider: str) -> str:
    """根据 provider 返回默认 api_key_env。"""
    if provider == "ark":
        return "ARK_API_KEY"
    if provider == "anthropic":
        return "ANTHROPIC_API_KEY"
    return "OPENAI_API_KEY"


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
        lines.append(f'    api_key_env: "{_quote_yaml(model.api_key_env)}"')
    return "\n".join(lines) + "\n"


def _quote_yaml(value: str) -> str:
    """对 YAML 字符串值进行转义处理。"""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _parse_config_yaml(raw: str) -> dict[str, Any]:
    """解析配置 YAML 文本为字典。"""
    data: dict[str, Any] = {"models": []}
    current_model: dict[str, str] | None = None
    in_models = False

    for original_line in raw.splitlines():
        stripped = original_line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        if stripped == "models:":
            in_models = True
            current_model = None
            continue

        if stripped.startswith("default_model:"):
            _, value = stripped.split(":", 1)
            data["default_model"] = _parse_yaml_scalar(value.strip())
            continue

        if stripped.startswith("max_model_calls:"):
            _, value = stripped.split(":", 1)
            raw_value = _parse_yaml_scalar(value.strip())
            try:
                data["max_model_calls"] = int(raw_value)
            except ValueError as exc:
                raise ValueError(f"Invalid max_model_calls value: {raw_value}") from exc
            continue

        if stripped.startswith("log_level:"):
            _, value = stripped.split(":", 1)
            data["log_level"] = _parse_yaml_scalar(value.strip()).upper()
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


def _parse_yaml_scalar(value: str) -> str:
    """解析 YAML 标量值，去除引号并处理转义。"""
    if not value:
        return ""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        inner = value[1:-1]
        return inner.replace('\\"', '"').replace("\\\\", "\\")
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
