from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from xagent.agent.prompts import PromptRenderer
from xagent.agent.runner import (
    AgentRunner,
    AgentRunSpec,
    EventSink,
    call_model,
)
from xagent.agent.tools.registry import ToolRegistry
from xagent.providers.types import ModelRequest, Provider
from xagent.session import Session


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

    async def run(self, user_text: str, *, on_event: EventSink | None = None) -> dict[str, Any]:
        self.session.append_message({"role": "user", "content": user_text})
        await self._maybe_compact()

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
        return [
            {"role": "system", "content": self._render_system_prompt()},
            *self.session.read_model_messages(),
        ]

    async def _maybe_compact(self) -> None:
        if self.session.approximate_context_size() <= self.context_char_threshold:
            return
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
        if summary:
            self.session.append_summary(summary)

    def _render_system_prompt(self) -> str:
        return self.prompt_renderer.render(
            "system.md",
            agent_name="XAgent",
            workspace_path=str(self.session.workspace_path),
            session_id=self.session.session_id,
            model=self.model,
        )
