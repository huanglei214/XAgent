from __future__ import annotations

from time import perf_counter

from xagent.agent.core.middleware import AgentMiddleware
from xagent.foundation.messages import message_text
from xagent.agent.traces.recorder import TraceRecorder, classify_task_kind


class TraceMiddleware(AgentMiddleware):
    def __init__(self) -> None:
        self._started = None

    async def before_agent_run(self, *, agent, user_text: str) -> None:
        recorder = TraceRecorder(
            cwd=getattr(agent, "cwd", "."),
            mode=getattr(agent, "runtime_mode", "run"),
            model=getattr(agent, "model", "unknown"),
            provider=getattr(agent, "provider_name", "unknown"),
            task_kind=classify_task_kind(user_text),
            session_id=getattr(agent, "trace_session_id", None),
            tags={"session_restored": bool(getattr(agent, "messages", [])[:-1])},
        )
        agent.trace_recorder = recorder
        agent.last_trace_recorder = recorder
        self._started = perf_counter()
        recorder.emit("task_started", payload={"input": user_text}, tags={"status": "started"})
        recorder.emit("user_input", payload={"text": user_text})

    async def after_agent_run(self, *, agent, final_message) -> None:
        recorder = getattr(agent, "trace_recorder", None)
        if recorder is None:
            return
        duration = self._duration()
        output_text = message_text(final_message)
        if not output_text.strip():
            recorder.emit("assistant_output_empty", payload={"message_count": len(getattr(agent, "messages", []))})
        recorder.record_state_snapshot(agent, "post_turn")
        recorder.finish_success(
            output_text=output_text,
            duration_seconds=duration,
            termination_reason=getattr(agent, "last_termination_reason", None) or "completed",
        )
        agent.trace_recorder = None

    async def before_agent_step(self, *, agent, step: int) -> None:
        recorder = getattr(agent, "trace_recorder", None)
        if recorder is not None:
            recorder.emit("agent_step_started", payload={"step": step}, tags={"step": step})

    async def after_agent_step(self, *, agent, step: int) -> None:
        recorder = getattr(agent, "trace_recorder", None)
        if recorder is not None:
            recorder.emit(
                "agent_step_finished",
                payload={"step": step, "message_count": len(getattr(agent, "messages", []))},
                tags={"step": step},
            )

    async def before_model(self, *, agent, request):
        recorder = getattr(agent, "trace_recorder", None)
        if recorder is not None:
            recorder.emit("model_request", payload=request.model_dump(mode="json"))
        return request

    async def after_model(self, *, agent, assistant_message) -> None:
        recorder = getattr(agent, "trace_recorder", None)
        if recorder is not None:
            recorder.emit("model_response", payload=assistant_message.model_dump(mode="json"))

    async def before_tool(self, *, agent, tool_use):
        recorder = getattr(agent, "trace_recorder", None)
        if recorder is not None:
            recorder.emit(
                "tool_call_started",
                payload=tool_use.model_dump(mode="json"),
                tags={"tool_name": tool_use.name},
            )
        return None

    async def after_tool(self, *, agent, tool_use, result) -> None:
        recorder = getattr(agent, "trace_recorder", None)
        if recorder is not None:
            recorder.emit(
                "tool_call_finished",
                payload={
                    "tool_name": tool_use.name,
                    "tool_use_id": tool_use.id,
                    "result": result.model_dump(mode="json"),
                },
                tags={"tool_name": tool_use.name, "status": "error" if result.is_error else "success"},
            )

    def _duration(self) -> float:
        if self._started is None:
            return 0.0
        return perf_counter() - self._started
