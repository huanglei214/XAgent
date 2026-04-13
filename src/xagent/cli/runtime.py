import json
from pathlib import Path
from time import perf_counter
from typing import Callable

import typer

from xagent.cli.render import print_error, print_info, print_panel, print_tool_use, print_warning
from xagent.coding import create_coding_agent
from xagent.coding.approvals import ApprovalStore, requires_approval
from xagent.config.env import load_project_env
from xagent.config.loader import load_config, resolve_default_model
from xagent.foundation.messages import ToolUsePart, message_text
from xagent.providers import create_provider


def build_runtime_agent(cwd: str):
    load_project_env(Path(cwd))
    config = load_config()
    model_config = resolve_default_model(config)
    provider = create_provider(model_config)
    approval_store = ApprovalStore(cwd)
    agent = create_coding_agent(provider=provider, model=model_config.name, cwd=cwd)
    agent.approval_store = approval_store
    agent.approval_handler = make_approval_handler(approval_store)
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
    started = perf_counter()
    message = await agent.run(prompt, on_tool_use=render_tool_use)
    return message, perf_counter() - started


def make_approval_handler(approval_store: ApprovalStore) -> Callable[[ToolUsePart], bool]:
    def _handler(tool_use: ToolUsePart) -> bool:
        if not requires_approval(tool_use.name):
            return True
        if approval_store.is_allowed(tool_use.name):
            return True

        print_warning(f"Approval required for {tool_use.name}")
        prompt = (
            f"Allow {tool_use.name} with input {json.dumps(tool_use.input, ensure_ascii=False)}? "
            "[y]es/[n]o/[a]lways"
        )

        while True:
            decision = typer.prompt(prompt, default="n").strip().lower()
            if decision in {"y", "yes"}:
                return True
            if decision in {"n", "no"}:
                return False
            if decision in {"a", "always"}:
                approval_store.allow_tool(tool_use.name)
                print_info(f"Persisted approval for {tool_use.name} in this project.")
                return True
            print_warning("Please enter y, n, or a.")

    return _handler


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
