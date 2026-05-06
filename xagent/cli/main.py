from __future__ import annotations

import sys
from collections.abc import Sequence

import click
import typer
from typer.main import get_command

from xagent.cli.agent import agent_command
from xagent.cli.channels import channels_app
from xagent.cli.gateway import gateway_command


app = typer.Typer(
    add_completion=False,
    invoke_without_command=True,
    no_args_is_help=False,
    context_settings={"help_option_names": ["-h", "--help"]},
    rich_markup_mode=None,
    pretty_exceptions_enable=False,
)
app.command("agent")(agent_command)
app.add_typer(channels_app, name="channels")
app.command("gateway")(gateway_command)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = get_command(app)
    try:
        result = command.main(
            args=args,
            prog_name="xagent",
            standalone_mode=False,
        )
    except click.exceptions.Exit as exc:
        return int(exc.exit_code)
    except click.ClickException as exc:
        exc.show()
        return exc.exit_code
    except click.Abort:
        click.echo("Aborted!", err=True)
        return 1
    return result if isinstance(result, int) else 0


@app.callback()
def root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        typer.echo(ctx.get_help())
        raise typer.Exit(0)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
