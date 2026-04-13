from typing import Optional

from pathlib import Path

from xagent.config.paths import ensure_config_dir, get_config_file
from xagent.config.schema import AppConfig, ModelConfig

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised on Python < 3.11
    tomllib = None


def config_exists() -> bool:
    return get_config_file().exists()


def load_config() -> AppConfig:
    config_path = get_config_file()
    if not config_path.exists():
        raise FileNotFoundError("XAgent config not found. Run `xagent config init` first.")

    try:
        raw = config_path.read_text(encoding="utf-8")
        if tomllib is not None:
            data = tomllib.loads(raw)
        else:
            data = _parse_config_toml(raw)
    except Exception as exc:
        raise ValueError(f"Invalid config TOML in {config_path}: {exc}") from exc

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


def _quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _dump_config(config: AppConfig) -> str:
    lines = [f"default_model = {_quote(config.default_model)}", ""]
    for model in config.models:
        lines.append("[[models]]")
        lines.append(f"name = {_quote(model.name)}")
        lines.append(f"provider = {_quote(model.provider)}")
        if model.base_url:
            lines.append(f"base_url = {_quote(model.base_url)}")
        lines.append(f"api_key_env = {_quote(model.api_key_env)}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _parse_config_toml(raw: str) -> dict:
    data: dict[str, object] = {"models": []}
    current_model: Optional[dict[str, str]] = None

    for original_line in raw.splitlines():
        line = original_line.strip()
        if not line or line.startswith("#"):
            continue
        if line == "[[models]]":
            current_model = {}
            data["models"].append(current_model)
            continue
        if "=" not in line:
            raise ValueError(f"Unsupported TOML line: {original_line}")

        key, value = [item.strip() for item in line.split("=", 1)]
        parsed_value = _parse_toml_string(value)
        if current_model is not None:
            current_model[key] = parsed_value
        else:
            data[key] = parsed_value

    return data


def _parse_toml_string(value: str) -> str:
    if len(value) < 2 or not (value.startswith('"') and value.endswith('"')):
        raise ValueError(f"Expected quoted string, got: {value}")
    inner = value[1:-1]
    return inner.replace('\\"', '"').replace("\\\\", "\\")
