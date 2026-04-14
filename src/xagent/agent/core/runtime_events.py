from __future__ import annotations

import inspect
from typing import Any


async def emit_runtime_event(agent, event_type: str, payload: dict[str, Any]) -> None:
    sink = getattr(agent, "runtime_event_sink", None)
    if sink is None:
        return
    result = sink(event_type, payload)
    if inspect.isawaitable(result):
        await result
