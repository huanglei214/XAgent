import typer

from xagent.cli.config import (
    add_model,
    config_exists,
    default_base_url,
    load_config,
    remove_model,
    save_config,
    set_default_model_name,
)
from xagent.cli.config import ModelConfig, default_config
from xagent.cli.config import ensure_config_example_file
from xagent.cli.tui.render import print_error, print_info

config_app = typer.Typer(help="Manage XAgent configuration.")
model_app = typer.Typer(help="Manage configured models.")
config_app.add_typer(model_app, name="model")


@config_app.command("init")
def init_config(force: bool = typer.Option(False, help="Overwrite an existing config file.")) -> None:
    config_path = None
    example_path = ensure_config_example_file(force=force)

    if config_exists() and not force:
        print_info("Config already exists. Re-run with --force to overwrite it.")
    else:
        config_path = save_config(default_config())
        print_info(f"Wrote XAgent config to {config_path}")

    print_info(f"Ensured config example file at {example_path}")
    print_info("Set models[].api_key and update the endpoint id in .xagent/config.yaml before running `xagent`.")


@config_app.command("show")
def show_config() -> None:
    try:
        config = load_config()
    except FileNotFoundError as exc:
        print_error(str(exc))
        raise typer.Exit(code=1) from exc

    print_info(config.model_dump_json(indent=2))


@config_app.command("set-default")
def set_default_model(model_name: str = typer.Argument(..., help="Configured model name.")) -> None:
    try:
        config = load_config()
    except FileNotFoundError as exc:
        print_error(str(exc))
        raise typer.Exit(code=1) from exc

    updated = set_default_model_name(config, model_name)
    save_config(updated)
    print_info(f"Default model set to {model_name}.")


@model_app.command("list")
def list_models() -> None:
    try:
        config = load_config()
    except FileNotFoundError as exc:
        print_error(str(exc))
        raise typer.Exit(code=1) from exc

    for model in config.models:
        marker = "*" if model.name == config.default_model else " "
        key_status = "set" if model.api_key else "missing"
        print_info(
            f"{marker} {model.name} | provider={model.provider} | base_url={model.base_url or '-'} | api_key={key_status}"
        )


@model_app.command("add")
def add_model_command(
    name: str = typer.Argument(..., help="Model name or endpoint id."),
    provider: str = typer.Option(..., "--provider", help="Provider type: openai, anthropic, or ark."),
    base_url: str = typer.Option("", "--base-url", help="Provider base URL. Uses provider default when omitted."),
    api_key: str = typer.Option("", "--api-key", help="API key stored in .xagent/config.yaml."),
    make_default: bool = typer.Option(False, "--default", help="Set the new model as default."),
) -> None:
    try:
        config = load_config()
    except FileNotFoundError as exc:
        print_error(str(exc))
        raise typer.Exit(code=1) from exc

    provider = provider.strip().lower()
    if provider not in {"openai", "anthropic", "ark"}:
        print_error(f"Unsupported provider '{provider}'. Use openai, anthropic, or ark.")
        raise typer.Exit(code=1)

    model = ModelConfig(
        name=name,
        provider=provider,
        base_url=base_url or default_base_url(provider),
        api_key=api_key,
    )
    updated = add_model(config, model, make_default=make_default)
    save_config(updated)
    print_info(f"Added model {name}.")


@model_app.command("remove")
def remove_model_command(model_name: str = typer.Argument(..., help="Configured model name.")) -> None:
    try:
        config = load_config()
    except FileNotFoundError as exc:
        print_error(str(exc))
        raise typer.Exit(code=1) from exc

    updated = remove_model(config, model_name)
    save_config(updated)
    print_info(f"Removed model {model_name}. Default is now {updated.default_model}.")


@model_app.command("set-default")
def set_default_model_command(model_name: str = typer.Argument(..., help="Configured model name.")) -> None:
    try:
        config = load_config()
    except FileNotFoundError as exc:
        print_error(str(exc))
        raise typer.Exit(code=1) from exc

    updated = set_default_model_name(config, model_name)
    save_config(updated)
    print_info(f"Default model set to {model_name}.")
