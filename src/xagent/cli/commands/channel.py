from __future__ import annotations

import logging
from pathlib import Path

import typer

from xagent.agent.runtime import SessionRuntimeManager
from xagent.agent.runtime.channel_bridge import ChannelRuntimeBridge
from xagent.channel.feishu import FeishuChannelAdapter, FeishuConfig
from xagent.cli.config.loader import load_config
from xagent.cli.runtime import build_runtime_agent, build_session_runtime
from xagent.cli.tui.render import print_error, print_info

channel_app = typer.Typer(help="Run XAgent channel ingress adapters.")
feishu_app = typer.Typer(help="Run the Feishu long-connection adapter.")
channel_app.add_typer(feishu_app, name="feishu")


def _configure_logging(log_level: str) -> None:
    """Configure process-wide logging for CLI channel commands."""
    level = getattr(logging, log_level.upper(), logging.WARNING)
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(
            level=level,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
        )
        return
    root_logger.setLevel(level)


@feishu_app.command("serve")
def serve_feishu_channel() -> None:
    cwd = str(Path.cwd())
    try:
        config = FeishuConfig.from_env(cwd)
        app_config = load_config()
    except Exception as exc:
        print_error(str(exc))
        raise typer.Exit(code=1) from exc

    _configure_logging(app_config.log_level)

    manager = SessionRuntimeManager(
        cwd=cwd,
        agent_factory=lambda: build_runtime_agent(cwd, approval_prompt_fn=lambda _: "n"),
        runtime_factory=build_session_runtime,
    )
    bridge = ChannelRuntimeBridge(cwd=cwd, manager=manager)
    adapter = FeishuChannelAdapter(bridge=bridge, config=config)
    print_info("Feishu channel listening via long connection")
    try:
        adapter.serve_forever()
    except KeyboardInterrupt:
        print_error("Interrupted.")
        raise typer.Exit(code=130) from None
    except Exception as exc:
        print_error(str(exc))
        raise typer.Exit(code=1) from exc
    finally:
        adapter.close()
        manager.close()
