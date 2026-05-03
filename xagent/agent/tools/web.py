from __future__ import annotations

import asyncio
import urllib.request

from xagent.agent.permissions import Approver
from xagent.agent.tools.base import Tool, ToolResult, tool


@tool(
    name="http_request",
    description="Make a basic HTTP request.",
    exclusive=True,
    parameters={
        "type": "object",
        "properties": {
            "url": {"type": "string"},
            "method": {"type": "string", "default": "GET"},
            "body": {"type": ["string", "null"], "default": None},
            "timeout_seconds": {"type": "integer", "default": 20},
        },
        "required": ["url"],
    },
)
class HttpRequestTool(Tool):
    def __init__(self, approver: Approver) -> None:
        self.approver = approver

    async def execute(
        self,
        url: str,
        method: str = "GET",
        body: str | None = None,
        timeout_seconds: int = 20,
    ) -> ToolResult:
        await _require(self.approver, "network", url, summary=f"{method.upper()} {url}")

        def run() -> str:
            data = body.encode("utf-8") if body is not None else None
            request = urllib.request.Request(url, method=method.upper(), data=data)
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return response.read().decode("utf-8", errors="replace")

        return ToolResult.ok(await asyncio.to_thread(run))


async def _require(approver: Approver, action: str, target: str, *, summary: str) -> None:
    allowed = await approver.require(action, target, summary=summary)
    if not allowed:
        raise PermissionError(f"Denied {action} for {target}")
