import typer

from xagent.cli.commands.chat import chat_command
from xagent.cli.commands.config import config_app
from xagent.cli.commands.run import run_command
from xagent.cli.commands.trace import trace_app

app = typer.Typer(help="XAgent CLI")
app.command("chat")(chat_command)
app.command("run")(run_command)
app.add_typer(config_app, name="config")
app.add_typer(trace_app, name="trace")


if __name__ == "__main__":
    app()
