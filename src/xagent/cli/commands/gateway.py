from __future__ import annotations

from pathlib import Path

import typer

from xagent.cli.runtime import build_managed_runtime_boundary
from xagent.cli.tui.render import print_error, print_info
from xagent.gateway.http import GatewayHTTPServer

gateway_app = typer.Typer(help="Run the HTTP gateway.")


@gateway_app.command("serve")
def serve_gateway(
    host: str = typer.Option("127.0.0.1", help="Host interface to bind."),
    port: int = typer.Option(8000, help="TCP port to bind."),
) -> None:
    cwd = str(Path.cwd())
    manager = build_managed_runtime_boundary(cwd, approval_prompt_fn=lambda _: "n")
    server = GatewayHTTPServer((host, port), manager)
    actual_host, actual_port = server.server_address[:2]
    print_info(f"Gateway listening on http://{actual_host!s}:{actual_port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print_error("Interrupted.")
        raise typer.Exit(code=130) from None
    finally:
        server.shutdown()
        server.server_close()
        manager.close()
