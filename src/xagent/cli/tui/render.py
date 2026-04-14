from rich.console import Console
from rich.panel import Panel

console = Console()


def print_info(message: str) -> None:
    console.print(message)


def print_error(message: str) -> None:
    console.print(f"[red]{message}[/red]")


def stream_assistant_text(delta: str) -> None:
    console.print(delta, end="")


def finish_stream() -> None:
    console.print()


def print_tool_use(name: str, tool_input: str) -> None:
    console.print(f"[cyan]tool[/cyan] {name} {tool_input}")


def print_warning(message: str) -> None:
    console.print(f"[yellow]{message}[/yellow]")


def print_panel(title: str, body: str) -> None:
    console.print(Panel(body, title=title, expand=False))
