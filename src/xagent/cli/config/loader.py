from pathlib import Path
from typing import Any, Dict, Optional

from xagent.cli.config.schema import AppConfig, ModelConfig
from xagent.foundation.runtime.paths import ensure_config_dir, get_config_file


def config_exists() -> bool:
    return get_config_file().exists()


def load_config() -> AppConfig:
    config_path = get_config_file()
    if not config_path.exists():
        raise FileNotFoundError("XAgent config not found. Run `xagent config init` first.")

    try:
        data = _parse_config_yaml(config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Invalid config YAML in {config_path}: {exc}") from exc

    return AppConfig.model_validate(data)


def save_config(config: AppConfig) -> Path:
    ensure_config_dir()
    config_path = get_config_file()
    config_path.write_text(_dump_config(config), encoding="utf-8")
    return config_path


def resolve_default_model(config: AppConfig) -> ModelConfig:
    for model in config.models:
        if model.name == config.default_model:
            return model
    raise ValueError(f"Default model '{config.default_model}' was not found.")


def add_model(config: AppConfig, model: ModelConfig, *, make_default: bool = False) -> AppConfig:
    if any(existing.name == model.name for existing in config.models):
        raise ValueError(f"Model '{model.name}' already exists.")
    models = [*config.models, model]
    default_model = model.name if make_default else config.default_model
    return AppConfig(default_model=default_model, models=models)


def remove_model(config: AppConfig, model_name: str) -> AppConfig:
    remaining = [model for model in config.models if model.name != model_name]
    if len(remaining) == len(config.models):
        raise ValueError(f"Model '{model_name}' is not defined in config.")
    if not remaining:
        raise ValueError("Cannot remove the last configured model.")
    default_model = config.default_model
    if default_model == model_name:
        default_model = remaining[0].name
    return AppConfig(default_model=default_model, models=remaining)


def set_default_model_name(config: AppConfig, model_name: str) -> AppConfig:
    if not any(model.name == model_name for model in config.models):
        raise ValueError(f"Model '{model_name}' is not defined in config.")
    return AppConfig(default_model=model_name, models=config.models)


def default_base_url(provider: str) -> str:
    if provider == "ark":
        return "https://ark.cn-beijing.volces.com/api/v3"
    if provider == "anthropic":
        return "https://api.anthropic.com"
    return "https://api.openai.com/v1"


def default_api_key_env(provider: str) -> str:
    if provider == "ark":
        return "ARK_API_KEY"
    if provider == "anthropic":
        return "ANTHROPIC_API_KEY"
    return "OPENAI_API_KEY"


def _dump_config(config: AppConfig) -> str:
    return dump_config_yaml(config)


def dump_config_yaml(config: AppConfig) -> str:
    lines = [f'default_model: "{_quote_yaml(config.default_model)}"', "models:"]
    for model in config.models:
        lines.append(f'  - name: "{_quote_yaml(model.name)}"')
        lines.append(f'    provider: "{_quote_yaml(model.provider)}"')
        if model.base_url:
            lines.append(f'    base_url: "{_quote_yaml(model.base_url)}"')
        lines.append(f'    api_key_env: "{_quote_yaml(model.api_key_env)}"')
    return "\n".join(lines) + "\n"


def _quote_yaml(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _parse_config_yaml(raw: str) -> Dict[str, Any]:
    data: Dict[str, Any] = {"models": []}
    current_model: Optional[Dict[str, str]] = None
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
            raise ValueError(f"Unexpected YAML line: {original_line}")

        key, value = _split_yaml_key_value(stripped)
        current_model[key] = _parse_yaml_scalar(value)

    return data


def _split_yaml_key_value(line: str) -> tuple[str, str]:
    if ":" not in line:
        raise ValueError(f"Expected key/value pair, got: {line}")
    key, value = line.split(":", 1)
    return key.strip(), value.strip()


def _parse_yaml_scalar(value: str) -> str:
    if not value:
        return ""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        inner = value[1:-1]
        return inner.replace('\\"', '"').replace("\\\\", "\\")
    return value
