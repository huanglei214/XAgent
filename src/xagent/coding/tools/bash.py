import asyncio

from pydantic import BaseModel, Field

from xagent.foundation.tools import Tool, ToolContext, ToolResult


class BashInput(BaseModel):
    command: str = Field(description="Shell command to run inside the workspace.")
    timeout_seconds: int = Field(default=30, ge=1, le=600, description="Command timeout in seconds.")


async def _bash(args: BashInput, ctx: ToolContext) -> ToolResult:
    process = await asyncio.create_subprocess_shell(
        args.command,
        cwd=ctx.cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=args.timeout_seconds)
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        return ToolResult(content=f"Command timed out after {args.timeout_seconds} seconds.", is_error=True)

    output = stdout.decode("utf-8", errors="replace")
    error_output = stderr.decode("utf-8", errors="replace")
    combined = output.strip()
    if error_output.strip():
        combined = f"{combined}\n{error_output.strip()}".strip()

    if process.returncode != 0:
        return ToolResult(
            content=f"Command exited with status {process.returncode}.\n{combined}".strip(),
            is_error=True,
        )
    return ToolResult(content=combined or "(command completed with no output)")


bash_tool = Tool(
    name="bash",
    description="Run a shell command inside the workspace.",
    input_model=BashInput,
    handler=_bash,
)
