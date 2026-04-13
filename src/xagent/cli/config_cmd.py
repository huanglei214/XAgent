import typer

from xagent.cli.render import print_error, print_info
from xagent.config.loader import config_exists, load_config, save_config
from xagent.config.schema import AppConfig, default_config

config_app = typer.Typer(help="Manage XAgent configuration.")


@config_app.command("init")
def init_config(force: bool = typer.Option(False, help="Overwrite an existing config file.")) -> None:
    if config_exists() and not force:
        print_info("Config already exists. Re-run with --force to overwrite it.")
        return

    config_path = save_config(default_config())
    print_info(f"Wrote XAgent config to {config_path}")
    print_info("Set ARK_API_KEY and update the endpoint id in the config before running `xagent run`.")


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

    if not any(model.name == model_name for model in config.models):
        print_error(f"Model '{model_name}' is not defined in config.")
        raise typer.Exit(code=1)

    updated = AppConfig(default_model=model_name, models=config.models)
    save_config(updated)
    print_info(f"Default model set to {model_name}.")
