from __future__ import annotations

import json
from typing import Callable

import typer

from xagent.agent.core.middleware import AgentMiddleware
from xagent.coding.permissions.store import ApprovalStore, requires_approval
from xagent.foundation.messages import ToolResultPart, ToolUsePart


class ApprovalMiddleware(AgentMiddleware):
    def __init__(self, approval_store: ApprovalStore, prompt_fn: Callable[[str], str] | None = None) -> None:
        self.approval_store = approval_store
        self.prompt_fn = prompt_fn or (lambda prompt: typer.prompt(prompt, default="n"))

    async def before_tool(self, *, agent, tool_use: ToolUsePart) -> ToolResultPart | None:
        if not requires_approval(tool_use.name):
            return None
        if self.approval_store.is_allowed(tool_use.name):
            return None

        recorder = getattr(agent, "trace_recorder", None)
        if recorder is not None:
            recorder.emit(
                "approval_requested",
                payload=tool_use.model_dump(mode="json"),
                tags={"tool_name": tool_use.name},
            )

        prompt = (
            f"Allow {tool_use.name} with input {json.dumps(tool_use.input, ensure_ascii=False)}? "
            "[y]es/[n]o/[a]lways"
        )

        while True:
            decision = self.prompt_fn(prompt).strip().lower()
            if decision in {"y", "yes"}:
                _record_approval_feedback(recorder, tool_use.name, "allow_once")
                return None
            if decision in {"a", "always"}:
                self.approval_store.allow_tool(tool_use.name)
                _record_approval_feedback(recorder, tool_use.name, "allow_always")
                return None
            if decision in {"n", "no"}:
                _record_approval_feedback(recorder, tool_use.name, "deny")
                return ToolResultPart(
                    tool_use_id=tool_use.id,
                    content=f"Execution denied for tool '{tool_use.name}'.",
                    is_error=True,
                )


def _record_approval_feedback(recorder, tool_name: str, decision: str) -> None:
    if recorder is None:
        return
    recorder.emit(
        "approval_decided",
        payload={"tool_name": tool_name, "decision": decision},
        tags={"tool_name": tool_name, "feedback_type": "approval"},
    )
    recorder.emit(
        "user_feedback",
        payload={"kind": "approval", "decision": decision, "tool_name": tool_name},
        tags={"tool_name": tool_name},
    )
