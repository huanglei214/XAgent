import inspect
import json
import time
from functools import partial
from pathlib import Path
from typing import Any, Callable, Optional

import typer

from xagent.agent.memory import create_runtime_memory
from xagent.agent.policies import ApprovalMiddleware, ApprovalStore
from xagent.agent.runtime import (
    LocalRuntimeBoundary,
    ManagedRuntimeBoundary,
    SessionRuntime,
    SessionRuntimeManager,
    create_workspace_agent,
)
from xagent.cli.tui.render import print_error, print_info, print_panel, print_tool_use
from xagent.cli.config.env import load_project_env
from xagent.cli.config.loader import load_config, resolve_default_model
from xagent.foundation.messages import ToolUsePart, message_text
from xagent.community import create_provider
from xagent.agent.core.loop import AgentAborted
from xagent.agent.traces import TraceMiddleware
from xagent.foundation.events import InMemoryMessageBus


def build_runtime_agent(
    cwd: str,
    ask_user_question: Optional[Callable] = None,
    approval_prompt_fn: Optional[Callable[[str], Any]] = None,
):
    load_project_env(Path(cwd))
    config = load_config()
    model_config = resolve_default_model(config)
    provider = create_provider(model_config)
    approval_store = ApprovalStore(cwd)
    agent = create_workspace_agent(
        provider=provider,
        model=model_config.name,
        cwd=cwd,
        max_steps=config.max_model_calls,
        ask_user_question=ask_user_question,
        middlewares=[
            TraceMiddleware(),
            ApprovalMiddleware(
                approval_store=approval_store,
                prompt_fn=approval_prompt_fn or (lambda prompt: typer.prompt(prompt, default="n")),
            ),
        ],
    )
    agent.provider_name = model_config.provider
    agent.approval_store = approval_store
    agent.request_path_access = make_external_path_approval_handler(
        recorder_getter=lambda: getattr(agent, "trace_recorder", None)
    )
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


def build_session_runtime(
    agent,
    session_id: Optional[str] = None,
    *,
    cwd: Optional[str] = None,
    bus: Optional[InMemoryMessageBus] = None,
    memory_bundle=None,
):
    message_bus = bus or InMemoryMessageBus()
    runtime_memory = memory_bundle or create_runtime_memory(cwd or getattr(agent, "cwd", "."), agent=agent)
    resolved_session_id = session_id or runtime_memory.episodic.new_session_id()
    runtime = SessionRuntime(
        session_id=resolved_session_id,
        bus=message_bus,
        turn_runner=partial(run_agent_turn_stream, agent),
        agent=agent,
        memory=runtime_memory,
    )
    return message_bus, runtime


def build_local_runtime_boundary(
    agent,
    session_id: Optional[str] = None,
    *,
    cwd: Optional[str] = None,
    bus: Optional[InMemoryMessageBus] = None,
    memory_bundle=None,
):
    _, runtime = build_session_runtime(
        agent,
        session_id=session_id,
        cwd=cwd,
        bus=bus,
        memory_bundle=memory_bundle,
    )
    return LocalRuntimeBoundary(runtime=runtime)


def build_managed_runtime_boundary(
    cwd: str,
    *,
    approval_prompt_fn: Optional[Callable[[str], Any]] = None,
):
    manager = SessionRuntimeManager(
        cwd=cwd,
        agent_factory=lambda: build_runtime_agent(cwd, approval_prompt_fn=approval_prompt_fn),
        runtime_factory=build_session_runtime,
    )
    return ManagedRuntimeBoundary(manager=manager)


def render_tool_use(tool_use: ToolUsePart) -> None:
    print_tool_use(tool_use.name, json.dumps(tool_use.input, ensure_ascii=False))


def render_final_message(message) -> None:
    final_text = message_text(message)
    if not final_text.strip():
        print_info("(no output)")
        return
    print_info(final_text)


async def run_agent_turn(agent, prompt: str):
    return await run_agent_turn_stream(agent, prompt)


async def run_agent_turn_stream(agent, prompt: str, on_assistant_delta=None, on_tool_use=None, on_tool_result=None):
    if not hasattr(agent, "middlewares"):
        agent.middlewares = []
    trace_added = False
    if not any(isinstance(middleware, TraceMiddleware) for middleware in agent.middlewares):
        agent.middlewares.append(TraceMiddleware())
        trace_added = True
    started = time.perf_counter()
    try:
        def _tool_use(tool_use):
            if on_tool_use:
                on_tool_use(tool_use)
            else:
                render_tool_use(tool_use)

        message = await agent.run(
            prompt,
            on_tool_use=_tool_use,
            on_tool_result=on_tool_result,
            on_assistant_delta=on_assistant_delta,
        )
        return message, time.perf_counter() - started
    except AgentAborted as exc:
        duration = time.perf_counter() - started
        recorder = getattr(agent, "trace_recorder", None)
        if recorder is not None:
            recorder.record_state_snapshot(agent, "cancelled", extra={"error": str(exc)})
            recorder.finish_cancelled(reason=str(exc), duration_seconds=duration)
            agent.trace_recorder = None
        raise
    except Exception as exc:
        duration = time.perf_counter() - started
        recorder = getattr(agent, "trace_recorder", None)
        if recorder is not None:
            stage = getattr(agent, "last_error_stage", None) or "runtime"
            recorder.record_state_snapshot(agent, "failure", extra={"error": str(exc)})
            recorder.finish_failure(
                error=str(exc),
                stage=stage,
                duration_seconds=duration,
                termination_reason=getattr(agent, "last_termination_reason", None),
            )
            agent.trace_recorder = None
        raise
    finally:
        if trace_added:
            agent.middlewares = [m for m in agent.middlewares if not isinstance(m, TraceMiddleware)]


def format_runtime_error(exc: Exception) -> None:
    print_error(str(exc))


def make_external_path_approval_handler(
    prompt_fn: Optional[Callable[[str], Any]] = None,
    recorder_getter: Optional[Callable[[], Any]] = None,
):
    prompt = prompt_fn or (
        lambda text: typer.confirm(
            text,
            default=False,
        )
    )

    async def _handler(path: str, access_kind: str) -> bool:
        recorder = recorder_getter() if recorder_getter is not None else None
        if recorder is not None:
            recorder.emit(
                "external_path_access_requested",
                payload={"path": path, "access_kind": access_kind},
                tags={"access_kind": access_kind},
            )

        decision = prompt(f"Allow {access_kind} access outside the workspace for '{path}'? [y/N]")
        if inspect.isawaitable(decision):
            decision = await decision
        allowed = _normalize_confirmation(decision)

        if recorder is not None:
            recorder.emit(
                "external_path_access_decided",
                payload={
                    "path": path,
                    "access_kind": access_kind,
                    "decision": "allow" if allowed else "deny",
                },
                tags={"access_kind": access_kind, "status": "allowed" if allowed else "denied"},
            )
        return allowed

    return _handler


def _normalize_confirmation(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"y", "yes", "true", "1", "allow", "allowed"}


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
