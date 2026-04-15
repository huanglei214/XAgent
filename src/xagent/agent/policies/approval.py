from __future__ import annotations

import inspect
import json
from typing import Callable

import typer

from xagent.agent.core.middleware import AgentMiddleware
from xagent.agent.policies.approval_store import ApprovalStore, describe_scoped_rule, requires_approval
from xagent.foundation.messages import ToolResultPart, ToolUsePart


class ApprovalMiddleware(AgentMiddleware):
    def __init__(self, approval_store: ApprovalStore, prompt_fn: Callable[[str], str] | None = None) -> None:
        self.approval_store = approval_store
        self.prompt_fn = prompt_fn or (lambda prompt: typer.prompt(prompt, default="n"))

    async def before_tool(self, *, agent, tool_use: ToolUsePart) -> ToolResultPart | None:
        if not requires_approval(tool_use.name):
            return None
        cwd = getattr(agent, "cwd", ".")
        if self.approval_store.is_allowed_tool_use(tool_use, cwd=cwd):
            return None

        recorder = getattr(agent, "trace_recorder", None)
        if recorder is not None:
            recorder.emit(
                "approval_requested",
                payload=tool_use.model_dump(mode="json"),
                tags={"tool_name": tool_use.name},
            )

        scope_description = describe_scoped_rule(tool_use, cwd=cwd)
        prompt = (
            f"Allow {tool_use.name} with input {json.dumps(tool_use.input, ensure_ascii=False)}? "
            f"[y]es once/[n]o/[s]cope 7d ({scope_description})/[a]llways tool"
        )

        while True:
            decision = self.prompt_fn(prompt)
            if inspect.isawaitable(decision):
                decision = await decision
            decision = str(decision).strip().lower()
            if decision in {"y", "yes"}:
                _record_approval_feedback(recorder, tool_use.name, "allow_once")
                return None
            if decision in {"s", "scope"}:
                rule = self.approval_store.allow_scoped_tool_use(tool_use, cwd=cwd)
                _record_approval_feedback(recorder, tool_use.name, "allow_scope")
                if rule is None:
                    return None
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
