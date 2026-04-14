import typer

from xagent.cli.commands.chat import chat_command
from xagent.cli.commands.config import config_app
from xagent.cli.commands.trace import trace_app

app = typer.Typer(help="XAgent CLI", invoke_without_command=True, no_args_is_help=False)
app.add_typer(config_app, name="config")
app.add_typer(trace_app, name="trace")


@app.callback()
def main_callback(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        chat_command()
        raise typer.Exit()


if __name__ == "__main__":
    app()
