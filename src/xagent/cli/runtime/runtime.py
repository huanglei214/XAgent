import json
from pathlib import Path
import time

import typer

from xagent.cli.ui.render import print_error, print_info, print_panel, print_tool_use
from xagent.coding import create_coding_agent
from xagent.coding.permissions import ApprovalStore
from xagent.coding.middleware import ApprovalMiddleware
from xagent.cli.config.env import load_project_env
from xagent.cli.config.loader import load_config, resolve_default_model
from xagent.foundation.messages import ToolUsePart, message_text
from xagent.community import create_provider
from xagent.agent.traces import TraceMiddleware


def build_runtime_agent(cwd: str):
    load_project_env(Path(cwd))
    config = load_config()
    model_config = resolve_default_model(config)
    provider = create_provider(model_config)
    approval_store = ApprovalStore(cwd)
    agent = create_coding_agent(
        provider=provider,
        model=model_config.name,
        cwd=cwd,
        middlewares=[
            TraceMiddleware(),
            ApprovalMiddleware(approval_store=approval_store, prompt_fn=lambda prompt: typer.prompt(prompt, default="n")),
        ],
    )
    agent.provider_name = model_config.provider
    agent.approval_store = approval_store
    return agent


def get_runtime_status(agent) -> str:
    model = getattr(agent, "model", "unknown")
    cwd = getattr(agent, "cwd", ".")
    messages = getattr(agent, "messages", [])
    tools = getattr(agent, "tools", [])
    approval_store = getattr(agent, "approval_store", None)
    allowed_tools = approval_store.allowed_tools if approval_store is not None else set()
    return (
        f"Model: {model}\n"
        f"Workspace: {Path(cwd).resolve().as_posix()}\n"
        f"Messages in memory: {len(messages)}\n"
        f"Tools available: {len(tools)}\n"
        f"Persistent approvals: {len(allowed_tools)}"
    )


def render_tool_use(tool_use: ToolUsePart) -> None:
    print_tool_use(tool_use.name, json.dumps(tool_use.input, ensure_ascii=False))


def render_final_message(message) -> None:
    final_text = message_text(message)
    if not final_text.strip():
        print_info("(no output)")
        return
    print_info(final_text)


async def run_agent_turn(agent, prompt: str):
    if not hasattr(agent, "middlewares"):
        agent.middlewares = []
    trace_added = False
    if not any(isinstance(middleware, TraceMiddleware) for middleware in agent.middlewares):
        agent.middlewares.append(TraceMiddleware())
        trace_added = True
    started = time.perf_counter()
    try:
        message = await agent.run(prompt, on_tool_use=render_tool_use)
        return message, time.perf_counter() - started
    except Exception as exc:
        duration = time.perf_counter() - started
        recorder = getattr(agent, "trace_recorder", None)
        if recorder is not None:
            stage = getattr(agent, "last_error_stage", None) or "runtime"
            recorder.record_state_snapshot(agent, "failure", extra={"error": str(exc)})
            recorder.finish_failure(error=str(exc), stage=stage, duration_seconds=duration)
            agent.trace_recorder = None
        raise
    finally:
        if trace_added:
            agent.middlewares = [m for m in agent.middlewares if not isinstance(m, TraceMiddleware)]


def format_runtime_error(exc: Exception) -> None:
    print_error(str(exc))


def render_turn_status(duration_seconds: float, agent) -> None:
    model = getattr(agent, "model", "unknown")
    messages = getattr(agent, "messages", [])
    print_panel(
        "Status",
        (
            f"Turn completed in {duration_seconds:.2f}s\n"
            f"Conversation messages: {len(messages)}\n"
            f"Model: {model}"
        ),
    )
