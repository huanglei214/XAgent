from __future__ import annotations

import inspect
import json
import queue
import threading
import time
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Callable, Optional

import typer

from xagent.agent import Agent
from xagent.agent.memory import create_runtime_memory
from xagent.agent.policies import ApprovalMiddleware, ApprovalStore
from xagent.agent.runtime import (
    ChannelManager,
    SessionRouter,
    SessionRuntime,
    SessionRuntimeManager,
    create_workspace_agent,
)
from xagent.channel.trace_channel import TraceChannel
from xagent.cli.tui.render import print_error, print_info, print_panel, print_tool_use
from xagent.cli.config import load_config, resolve_default_model
from xagent.provider.types import ToolUsePart, message_text
from xagent.provider import create_provider
from xagent.agent.core.loop import AgentAborted
from xagent.agent.traces import TraceMiddleware
from xagent.bus.messages import InboundMessage, OutboundMessage
from xagent.bus.queue import MessageBus


def build_runtime_agent(
    cwd: str,
    ask_user_question: Optional[Callable] = None,
    approval_prompt_fn: Optional[Callable[[str], Any]] = None,
) -> Agent:
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
    agent: Agent,
    session_id: Optional[str] = None,
    *,
    cwd: Optional[str] = None,
    message_bus: Optional[MessageBus] = None,
    memory_bundle=None,
):
    message_bus = message_bus or MessageBus()
    runtime_cwd = cwd or str(agent.cwd)
    runtime_memory = memory_bundle or create_runtime_memory(runtime_cwd, agent=agent)
    resolved_session_id = session_id or runtime_memory.episodic.new_session_id()
    runtime = SessionRuntime(
        session_id=resolved_session_id,
        turn_runner=partial(run_agent_turn_stream, agent),
        agent=agent,
        memory=runtime_memory,
        message_bus=message_bus,
    )
    return message_bus, runtime


def _compat_managed_outbound_message(
    message: OutboundMessage,
) -> Optional[OutboundMessage]:
    """把新 outbound 事件兼容映射为 gateway/channel 仍在消费的旧 kind 语义。"""
    metadata = dict(message.metadata or {})
    event = metadata.get("_event")
    if event == "turn_start":
        return None
    if event == "text_delta":
        return OutboundMessage(
            kind="delta",
            correlation_id=message.correlation_id,
            session_id=message.session_id,
            session_key=message.session_key,
            source=message.source,
            channel=message.channel,
            chat_id=message.chat_id,
            content=message.content,
            reply_to=message.reply_to,
            error=message.error,
            media=list(message.media),
            metadata={
                key: value
                for key, value in metadata.items()
                if not str(key).startswith("_")
            },
        )
    if event == "tool_use":
        return OutboundMessage(
            kind="tool_called",
            correlation_id=message.correlation_id,
            session_id=message.session_id,
            session_key=message.session_key,
            source=message.source,
            channel=message.channel,
            chat_id=message.chat_id,
            content=message.content,
            reply_to=message.reply_to,
            error=message.error,
            media=list(message.media),
            metadata={
                key: value
                for key, value in metadata.items()
                if not str(key).startswith("_")
            },
        )
    if event == "tool_result":
        compat_metadata = {
            key: value
            for key, value in metadata.items()
            if not str(key).startswith("_")
        }
        result = compat_metadata.get("result")
        content = str(getattr(result, "content", message.content) or message.content)
        is_error = bool(getattr(result, "is_error", compat_metadata.get("is_error", False)))
        compat_metadata["is_error"] = is_error
        return OutboundMessage(
            kind="tool_finished",
            correlation_id=message.correlation_id,
            session_id=message.session_id,
            session_key=message.session_key,
            source=message.source,
            channel=message.channel,
            chat_id=message.chat_id,
            content=content,
            reply_to=message.reply_to,
            error=content if is_error else None,
            media=list(message.media),
            metadata=compat_metadata,
        )
    return message


class ManagerFacingRuntimeAdapter:
    """面向 CLI/gateway/channel 的轻量运行时适配器。

    它直接包裹 ``SessionRuntimeManager``，仅在两处补充兼容逻辑：
    1. ``send_and_wait()`` 映射到 manager 的 ``send_inbound_and_wait()``；
    2. ``open_response_stream()`` 把新 outbound 事件转换成旧的
       ``delta/tool_called/tool_finished`` 语义，供 Feishu/gateway 继续消费。
    其余 manager 能力通过 ``__getattr__`` 透明透传，避免继续依赖
    历史 boundary 兼容壳。
    """

    def __init__(self, *, manager: SessionRuntimeManager) -> None:
        """保存底层 manager 实例。"""
        self.manager = manager

    def close(self) -> None:
        """关闭底层 manager。"""
        self.manager.close()

    def send_and_wait(
        self,
        message: InboundMessage,
        *,
        timeout_seconds: float = 30.0,
    ) -> OutboundMessage:
        """同步发送一条 inbound 并等待 terminal outbound。"""
        return self.manager.send_inbound_and_wait(
            message,
            timeout_seconds=timeout_seconds,
        )

    def open_response_stream(
        self,
        message: InboundMessage,
        *,
        terminal_only: bool = False,
    ) -> tuple["queue.Queue[OutboundMessage]", Callable[[], None]]:
        """打开一个兼容旧 kind 语义的响应流。"""
        raw_queue, raw_unsubscribe = self.manager.open_outbound_stream(
            message,
            terminal_only=terminal_only,
        )
        outbound_queue: "queue.Queue[OutboundMessage]" = queue.Queue()
        stop_event = threading.Event()

        def _pump() -> None:
            try:
                while not stop_event.is_set():
                    try:
                        outbound = raw_queue.get(timeout=0.1)
                    except queue.Empty:
                        continue
                    compat = _compat_managed_outbound_message(outbound)
                    if compat is None:
                        continue
                    outbound_queue.put_nowait(compat)
                    if compat.kind in {"completed", "failed"}:
                        return
            finally:
                raw_unsubscribe()

        worker = threading.Thread(
            target=_pump,
            name=f"xagent-manager-adapter-{message.correlation_id[:8]}",
            daemon=True,
        )
        worker.start()

        def _unsubscribe() -> None:
            stop_event.set()
            raw_unsubscribe()

        return outbound_queue, _unsubscribe

    def __getattr__(self, name: str) -> Any:
        """把其余 manager API 透明透传给调用方。"""
        return getattr(self.manager, name)


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
    return ManagerFacingRuntimeAdapter(manager=manager)


@dataclass
class RuntimeStack:
    """聚合新总线栈上所有关键组件的容器。

    由 :func:`build_runtime_stack` 构造；调用方通过 ``start()`` 启动
    ``SessionRouter`` 和 ``ChannelManager`` 的后台任务，通过 ``stop()``
    做优雅收敛。``channel_manager.send_and_wait`` 是单次请求的对外入口。
    """

    agent: Any
    message_bus: MessageBus
    channel_manager: ChannelManager
    session_router: SessionRouter
    runtime: SessionRuntime

    async def start(self) -> None:
        """启动 router / channel_manager 的 dispatch 任务。"""
        await self.session_router.start()
        await self.channel_manager.start()

    async def stop(self) -> None:
        """停止 router / channel_manager，并等待 runtime 后台任务收敛。"""
        try:
            await self.session_router.stop()
        finally:
            try:
                await self.channel_manager.stop()
            finally:
                await self.runtime.wait_for_background_tasks()


def build_runtime_stack(
    agent,
    *,
    session_id: Optional[str] = None,
    cwd: Optional[str] = None,
    memory_bundle=None,
) -> RuntimeStack:
    """组装单会话 nanobot 风格总线栈。

    - 新建 ``MessageBus`` 作为 inbound/outbound FIFO 队列；
    - 构造 ``SessionRuntime``（只绑定 ``message_bus``）；
    - ``SessionRouter`` 注册固定 resolver（始终返回本 session_id）和 provider（返回唯一 runtime 实例）；
    - ``ChannelManager`` 绑定 ``MessageBus``，此处不注册任何 ``BaseChannel``——
      CLI run 只需要 per-request fan-out（``send_and_wait``），不需要持续转发到外部通道。
    """
    message_bus = MessageBus()
    runtime_memory = memory_bundle or create_runtime_memory(
        cwd or str(agent.cwd), agent=agent
    )
    resolved_session_id = session_id or runtime_memory.episodic.new_session_id()
    runtime = SessionRuntime(
        session_id=resolved_session_id,
        turn_runner=partial(run_agent_turn_stream, agent),
        agent=agent,
        memory=runtime_memory,
        message_bus=message_bus,
    )

    def _resolver(session_key: str) -> str:
        # 单会话场景：无论 session_key 为何，都路由到同一个 runtime。
        return runtime.session_id

    async def _provider(resolved_session_id: str) -> SessionRuntime:
        return runtime

    router = SessionRouter(bus=message_bus, resolver=_resolver, provider=_provider)
    channel_manager = ChannelManager(message_bus)
    channel_manager.register_channel(
        TraceChannel(
            message_bus,
            recorder_getter=lambda: getattr(agent, "trace_recorder", None)
            or getattr(agent, "last_trace_recorder", None),
        )
    )

    return RuntimeStack(
        agent=agent,
        message_bus=message_bus,
        channel_manager=channel_manager,
        session_router=router,
        runtime=runtime,
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
