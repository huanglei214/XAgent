from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from xagent.agent.commands import CommandRouter
from xagent.agent.interactions import ChatApprover, InteractionBroker, InteractionContext
from xagent.agent.loop import Agent
from xagent.agent.memory import MemoryStore
from xagent.agent.permissions import Approver
from xagent.agent.tools import build_default_tools
from xagent.agent.tools.shell import ShellPolicy
from xagent.bus import InboundMessage, MessageBus, OutboundEvent, StreamKind, StreamState
from xagent.config import AppConfig
from xagent.providers import ModelEvent, make_provider
from xagent.session import Session, SessionStore, resolve_session_id


@dataclass
class AgentLoop:
    config: AppConfig
    workspace_path: Path
    approver: Approver | None = None
    memory_store: MemoryStore | None = None
    interaction_broker: InteractionBroker = field(default_factory=InteractionBroker)
    command_router: CommandRouter = field(default_factory=CommandRouter)
    _sessions: dict[str, Session] = field(default_factory=dict)
    _agents: dict[str, Agent] = field(default_factory=dict)
    _session_queues: dict[str, asyncio.Queue[InboundMessage]] = field(default_factory=dict)
    _session_workers: dict[str, asyncio.Task[None]] = field(default_factory=dict)

    def session_id_for(self, inbound: InboundMessage) -> str:
        return resolve_session_id(
            channel=inbound.channel,
            chat_id=inbound.chat_id,
            session_id=inbound.session_id,
        )

    def session_for(self, inbound: InboundMessage) -> Session:
        session_id = self.session_id_for(inbound)
        session = self._sessions.get(session_id)
        if session is None:
            store = SessionStore(self.config.sessions_path)
            session = store.open_for_chat(
                workspace_path=self.workspace_path,
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                session_id=inbound.session_id,
            )
            self._sessions[session_id] = session
        return session

    def agent_for(self, inbound: InboundMessage) -> Agent:
        session = self.session_for(inbound)
        agent = self._agents.get(session.session_id)
        if agent is None:
            snapshot = make_provider(self.config)
            approver = self.approver or ChatApprover(self.interaction_broker)
            tools = build_default_tools(
                workspace=session.workspace_path,
                approver=approver,
                shell_policy=ShellPolicy.from_config(self.config.permissions.shell),
                web_config=self.config.tools.web,
                web_permission=self.config.permissions.web,
                ask_user=self.interaction_broker.ask_user,
            )
            agent = Agent(
                provider=snapshot.provider,
                model=snapshot.model,
                session=session,
                tools=tools,
                temperature=self.config.agents.defaults.temperature,
                max_tokens=self.config.agents.defaults.max_tokens,
                max_steps=self.config.limits.max_steps,
                max_duration_seconds=self.config.limits.max_duration_seconds,
                max_repeated_tool_calls=self.config.limits.max_repeated_tool_calls,
                context_char_threshold=self.config.limits.context_char_threshold,
                trace_model_events=self.config.trace.model_events,
                memory_store=self.memory_store,
                inject_user_memory=self.config.memory.inject_user,
                inject_soul_memory=self.config.memory.inject_soul,
                inject_workspace_memory=self.config.memory.inject_workspace,
            )
            self._agents[session.session_id] = agent
        return agent

    async def dispatch_once(self, bus: MessageBus) -> None:
        inbound = await bus.consume_inbound()
        session_id = self.session_id_for(inbound)
        if self.interaction_broker.accept_reply(session_id=session_id, inbound=inbound):
            return
        await self._handle_inbound(bus, inbound, session_id=session_id)

    async def _handle_inbound(
        self,
        bus: MessageBus,
        inbound: InboundMessage,
        *,
        session_id: str,
    ) -> None:
        stream_id = uuid4().hex

        async def publish_event(event: ModelEvent) -> None:
            if event.kind == "text_delta":
                await bus.publish_outbound(
                    OutboundEvent(
                        content=event.text,
                        channel=inbound.channel,
                        chat_id=inbound.chat_id,
                        reply_to=inbound.sender_id,
                        session_id=session_id,
                        stream=StreamState(kind=StreamKind.DELTA, stream_id=stream_id),
                    )
                )

        context = InteractionContext(
            bus=bus,
            channel=inbound.channel,
            chat_id=inbound.chat_id,
            sender_id=inbound.sender_id,
            session_id=session_id,
        )
        try:
            with self.interaction_broker.activate(context):
                agent = self.agent_for(inbound)
                await self._dispatch_to_agent(
                    bus,
                    inbound,
                    agent,
                    stream_id=stream_id,
                    publish_event=publish_event,
                )
        except Exception as exc:  # noqa: BLE001 - agent failures become outbound errors
            await bus.publish_outbound(
                OutboundEvent(
                    content=str(exc),
                    channel=inbound.channel,
                    chat_id=inbound.chat_id,
                    reply_to=inbound.sender_id,
                    session_id=session_id,
                    stream=StreamState(kind=StreamKind.END, stream_id=stream_id),
                    metadata={"error": True},
                )
            )

    async def _dispatch_to_agent(
        self,
        bus: MessageBus,
        inbound: InboundMessage,
        agent: Agent,
        *,
        stream_id: str,
        publish_event: Callable[[ModelEvent], Awaitable[None] | None],
    ) -> None:
        command = self.command_router.parse(inbound.content)
        if command is not None:
            if command.name == "dream":
                await bus.publish_outbound(
                    OutboundEvent(
                        content="dreaming...",
                        channel=inbound.channel,
                        chat_id=inbound.chat_id,
                        reply_to=inbound.sender_id,
                        session_id=agent.session.session_id,
                        stream=StreamState(kind=StreamKind.END, stream_id=uuid4().hex),
                    )
                )
                await self.command_router.execute(command, agent)
                await bus.publish_outbound(
                    OutboundEvent(
                        content="dream done.",
                        channel=inbound.channel,
                        chat_id=inbound.chat_id,
                        reply_to=inbound.sender_id,
                        session_id=agent.session.session_id,
                        stream=StreamState(kind=StreamKind.END, stream_id=uuid4().hex),
                    )
                )
                return
            await bus.publish_outbound(
                OutboundEvent(
                    content=command.message or self.command_router.help_text(),
                    channel=inbound.channel,
                    chat_id=inbound.chat_id,
                    reply_to=inbound.sender_id,
                    session_id=agent.session.session_id,
                    stream=StreamState(kind=StreamKind.END, stream_id=stream_id),
                )
            )
            return
        final = await agent.run(
            f"[sender:{inbound.sender_id}] {inbound.content}",
            on_event=publish_event,
        )
        await bus.publish_outbound(
            OutboundEvent(
                content=str(final.get("content") or ""),
                channel=inbound.channel,
                chat_id=inbound.chat_id,
                reply_to=inbound.sender_id,
                session_id=agent.session.session_id,
                stream=StreamState(kind=StreamKind.END, stream_id=stream_id),
            )
        )

    async def run(self, bus: MessageBus) -> None:
        try:
            while True:
                inbound = await bus.consume_inbound()
                session_id = self.session_id_for(inbound)
                if self.interaction_broker.accept_reply(session_id=session_id, inbound=inbound):
                    continue
                queue = self._queue_for(session_id)
                await queue.put(inbound)
                worker = self._session_workers.get(session_id)
                if worker is None or worker.done():
                    self._session_workers[session_id] = asyncio.create_task(
                        self._run_session_worker(bus, session_id, queue)
                    )
        finally:
            for worker in self._session_workers.values():
                if not worker.done():
                    worker.cancel()
            await asyncio.gather(*self._session_workers.values(), return_exceptions=True)

    def _queue_for(self, session_id: str) -> asyncio.Queue[InboundMessage]:
        queue = self._session_queues.get(session_id)
        if queue is None:
            queue = asyncio.Queue()
            self._session_queues[session_id] = queue
        return queue

    async def _run_session_worker(
        self,
        bus: MessageBus,
        session_id: str,
        queue: asyncio.Queue[InboundMessage],
    ) -> None:
        while True:
            inbound = await queue.get()
            try:
                await self._handle_inbound(bus, inbound, session_id=session_id)
            finally:
                queue.task_done()
