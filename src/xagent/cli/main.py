import typer

from xagent.cli.chat import chat_command
from xagent.cli.config_cmd import config_app
from xagent.cli.run import run_command

app = typer.Typer(help="XAgent CLI")
app.command("chat")(chat_command)
app.command("run")(run_command)
app.add_typer(config_app, name="config")


if __name__ == "__main__":
    app()
