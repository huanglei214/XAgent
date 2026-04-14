from __future__ import annotations

from xagent.agent.core.middleware import AgentMiddleware
from xagent.agent.core.runtime_events import emit_runtime_event


class ProjectRulesMiddleware(AgentMiddleware):
    def __init__(self, project_rules: str) -> None:
        self.project_rules = project_rules
        self.scope_count = project_rules.count("<agents_scope ")

    async def before_agent_run(self, *, agent, user_text: str) -> None:
        recorder = getattr(agent, "trace_recorder", None)
        payload = {
            "scope_count": self.scope_count,
            "char_count": len(self.project_rules),
        }
        if recorder is not None:
            recorder.emit("project_rules_loaded", payload=payload, tags={"scope_count": self.scope_count})
        await emit_runtime_event(agent, "project_rules_loaded", payload)

    async def before_model(self, *, agent, request):
        if not getattr(agent, "context_messages", None):
            return None
        payload = {
            "scope_count": self.scope_count,
            "context_message_count": len(agent.context_messages),
        }
        recorder = getattr(agent, "trace_recorder", None)
        if recorder is not None:
            recorder.emit(
                "project_rules_context_injected",
                payload=payload,
                tags={"scope_count": self.scope_count, "context_message_count": len(agent.context_messages)},
            )
        await emit_runtime_event(agent, "project_rules_context_injected", payload)
        return None
