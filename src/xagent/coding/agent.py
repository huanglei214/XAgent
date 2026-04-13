from pathlib import Path
from typing import Awaitable, Callable, Optional

from xagent.agent import Agent
from xagent.coding.tools import ALL_CODING_TOOLS
from xagent.memory.project_rules import load_project_rules


def create_coding_agent(
    provider,
    model: str,
    cwd: str,
    approval_handler: Optional[Callable] = None,
) -> Agent:
    project_rules = load_project_rules(Path(cwd))

    prompt_parts = [
        "You are XAgent, a careful coding assistant.",
        f"Your working directory is {Path(cwd).resolve().as_posix()}.",
        "Inspect files before making assumptions.",
        "Use the available tools when they help you answer repository questions.",
        "Read the relevant files before editing them.",
        "Prefer apply_patch for targeted file updates and write_file for full rewrites.",
    ]
    if project_rules:
        prompt_parts.append("The project's AGENTS.md has been loaded below:")
        prompt_parts.append(project_rules)

    return Agent(
        provider=provider,
        model=model,
        system_prompt="\n\n".join(prompt_parts),
        tools=ALL_CODING_TOOLS,
        cwd=cwd,
        approval_handler=approval_handler,
    )
