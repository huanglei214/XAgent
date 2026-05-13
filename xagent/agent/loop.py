from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any

from xagent.agent.memory import MemoryBundle, MemoryStore
from xagent.agent.prompts import PromptRenderer
from xagent.agent.runner import (
    AgentError,
    AgentRunner,
    AgentRunSpec,
    EventSink,
    call_model,
)
from xagent.agent.tools.registry import ToolRegistry
from xagent.providers.types import ModelRequest, Provider
from xagent.session import Session, local_now

RETAINED_USER_TURNS = 4


@dataclass
class Agent:
    provider: Provider
    model: str
    session: Session
    tools: ToolRegistry
    prompt_renderer: PromptRenderer = field(default_factory=PromptRenderer)
    temperature: float | None = None
    max_tokens: int | None = None
    max_steps: int = 50
    max_duration_seconds: float = 600.0
    max_repeated_tool_calls: int = 3
    context_char_threshold: int = 120_000
    trace_model_events: bool = False
    memory_store: MemoryStore | None = None
    inject_user_memory: bool = True
    inject_soul_memory: bool = True
    inject_workspace_memory: bool = True

    async def run(self, user_text: str, *, on_event: EventSink | None = None) -> dict[str, Any]:
        user_message = {"role": "user", "content": user_text}
        await self.compact(additional_messages=[user_message])
        self.session.append_message(user_message)

        runner = AgentRunner(self.provider)
        result = await runner.run(self._build_run_spec(on_event=on_event))
        return result.final_message

    def _build_run_spec(self, *, on_event: EventSink | None = None) -> AgentRunSpec:
        return AgentRunSpec(
            model=self.model,
            messages=self._build_messages(),
            tools=self.tools,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            max_steps=self.max_steps,
            max_duration_seconds=self.max_duration_seconds,
            max_repeated_tool_calls=self.max_repeated_tool_calls,
            empty_retry_message=self.prompt_renderer.render("empty_retry.md"),
            metadata={
                "session_id": self.session.session_id,
                "workspace_path": str(self.session.workspace_path),
            },
            trace_model_events=self.trace_model_events,
            on_event=on_event,
            on_trace=self.session.append_trace,
            on_message=self.session.append_message,
        )

    def _build_messages(self) -> list[dict[str, Any]]:
        model_messages = self._attach_runtime_context(self.session.read_model_messages())
        return [
            {"role": "system", "content": self._render_system_prompt()},
            *model_messages,
        ]

    def _attach_runtime_context(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """把本轮运行时元信息附加到当前 user message，不写入 session 历史。"""

        runtime_context = self._render_runtime_context()
        if not messages or messages[-1].get("role") != "user":
            return [*messages, {"role": "user", "content": runtime_context}]

        updated = [*messages]
        current = dict(updated[-1])
        content = current.get("content")
        if isinstance(content, list):
            current["content"] = [{"type": "text", "text": runtime_context}, *content]
        elif isinstance(content, str):
            current["content"] = f"{runtime_context}\n\n{content}" if content else runtime_context
        else:
            current["content"] = runtime_context if content is None else f"{runtime_context}\n\n{content}"
        updated[-1] = current
        return updated

    async def compact(
        self,
        *,
        force: bool = False,
        additional_messages: list[dict[str, Any]] | None = None,
    ) -> bool:
        context_size = self.session.approximate_context_size()
        if additional_messages:
            context_size += sum(
                len(json.dumps(message, ensure_ascii=False)) for message in additional_messages
            )
        if not force and context_size <= self.context_char_threshold:
            return False
        messages_until_index = self.session.latest_message_record_index()
        compact_state = self.session.read_session_state().get("compact", {})
        compacted_until = int(compact_state.get("messages_until_index") or 0)
        if messages_until_index <= compacted_until:
            return False
        messages = self.session.read_model_messages()
        request = ModelRequest(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": self.prompt_renderer.render("summary.md"),
                },
                *messages,
            ],
            tools=[],
            metadata={"session_id": self.session.session_id, "purpose": "compaction"},
        )
        summary_message = await call_model(
            self.provider,
            request,
            on_trace=self.session.append_trace,
            trace_model_events=self.trace_model_events,
        )
        summary = str(summary_message.get("content") or "").strip()
        if not summary:
            summary = await self._retry_compaction(messages)
        if summary:
            retained_from_index = self.session.recent_user_turn_start_index(
                messages_until_index,
                user_turns=RETAINED_USER_TURNS,
            )
            self.session.append_summary(
                summary,
                messages_until_index=messages_until_index,
                retained_from_index=retained_from_index,
                previous_summary_id=compact_state.get("latest_summary_id"),
            )
            return True
        self.session.append_trace(
            "compaction_skipped",
            {"reason": "empty_summary", "forced": force},
        )
        if force:
            raise AgentError("Compaction produced an empty summary.")
        return False

    async def _retry_compaction(self, messages: list[dict[str, Any]]) -> str:
        request = ModelRequest(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": self.prompt_renderer.render("summary.md"),
                },
                *messages,
                {
                    "role": "user",
                    "content": "The previous compaction response was empty. Return a concise non-empty summary.",
                },
            ],
            tools=[],
            metadata={"session_id": self.session.session_id, "purpose": "compaction_retry"},
        )
        summary_message = await call_model(
            self.provider,
            request,
            on_trace=self.session.append_trace,
            trace_model_events=self.trace_model_events,
        )
        return str(summary_message.get("content") or "").strip()

    def _render_system_prompt(self) -> str:
        memory = self._load_memory_bundle()
        return self.prompt_renderer.render(
            "system.md",
            agent_name="XAgent",
            workspace_path=str(self.session.workspace_path),
            session_id=self.session.session_id,
            model=self.model,
            memory=memory,
        )

    def _render_runtime_context(self) -> str:
        current_datetime = local_now()
        current_date, _, current_time = current_datetime.partition("T")
        return self.prompt_renderer.render(
            "runtime_context.md",
            current_date=current_date,
            current_time=current_time[:5],
            timezone="Asia/Shanghai",
        )

    def _load_memory_bundle(self) -> MemoryBundle:
        if self.memory_store is None:
            return MemoryBundle.empty(self.session.workspace_path)
        memory = self.memory_store.load_bundle(self.session.workspace_path)
        return replace(
            memory,
            soul=memory.soul if self.inject_soul_memory else "",
            user=memory.user if self.inject_user_memory else "",
            workspace=memory.workspace if self.inject_workspace_memory else "",
        )
