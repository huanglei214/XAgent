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
        return ToolResult.fail(
            f"Command timed out after {args.timeout_seconds} seconds.",
            summary=f"Command timed out after {args.timeout_seconds} seconds.",
            code="BASH_TIMEOUT",
            details={"command": args.command, "timeout_seconds": args.timeout_seconds},
        )
    except asyncio.CancelledError:
        process.kill()
        await process.communicate()
        raise

    output = stdout.decode("utf-8", errors="replace")
    error_output = stderr.decode("utf-8", errors="replace")
    combined = output.strip()
    if error_output.strip():
        combined = f"{combined}\n{error_output.strip()}".strip()

    if process.returncode != 0:
        return ToolResult.fail(
            f"Command exited with status {process.returncode}.",
            summary=f"Command exited with status {process.returncode}.",
            code="BASH_EXIT_NONZERO",
            content=f"Command exited with status {process.returncode}.\n{combined}".strip(),
            details={"command": args.command, "exit_code": process.returncode, "stdout_stderr": combined},
        )
    return ToolResult.ok(
        "Command completed successfully.",
        content=combined or "(command completed with no output)",
        data={"command": args.command, "output": combined or "", "exit_code": 0},
    )


bash_tool = Tool(
    name="bash",
    description="Execute a bash command in a unix-like environment",
    input_model=BashInput,
    handler=_bash,
)
