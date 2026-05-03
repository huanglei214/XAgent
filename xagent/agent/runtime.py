from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from xagent.agent.loop import Agent
from xagent.agent.permissions import Approver, CliApprover
from xagent.agent.tools import build_default_tools
from xagent.agent.tools.shell import ShellPolicy
from xagent.bus import InboundMessage, MessageBus, OutboundEvent, StreamKind, StreamState
from xagent.config import AppConfig
from xagent.providers import ModelEvent, make_provider
from xagent.session import Session, SessionStore, resolve_session_id


@dataclass
class AgentRuntime:
    config: AppConfig
    workspace_path: Path
    approver: Approver | None = None
    _sessions: dict[str, Session] = field(default_factory=dict)
    _agents: dict[str, Agent] = field(default_factory=dict)

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
            tools = build_default_tools(
                workspace=session.workspace_path,
                approver=self.approver or CliApprover(),
                shell_policy=ShellPolicy.from_config(self.config.permissions.shell),
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
            )
            self._agents[session.session_id] = agent
        return agent

    async def dispatch_once(self, bus: MessageBus) -> None:
        inbound = await bus.consume_inbound()
        session_id = self.session_id_for(inbound)
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

        try:
            agent = self.agent_for(inbound)
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

    async def run(self, bus: MessageBus) -> None:
        while True:
            await self.dispatch_once(bus)
