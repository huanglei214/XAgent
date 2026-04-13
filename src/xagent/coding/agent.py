from pathlib import Path

from xagent.agent import Agent
from xagent.coding.tools import READ_ONLY_TOOLS
from xagent.memory.project_rules import load_project_rules


def create_coding_agent(provider, model: str, cwd: str) -> Agent:
    project_rules = load_project_rules(Path(cwd))

    prompt_parts = [
        "You are XAgent, a careful coding assistant.",
        f"Your working directory is {Path(cwd).resolve().as_posix()}.",
        "Inspect files before making assumptions.",
        "Use the available tools when they help you answer repository questions.",
    ]
    if project_rules:
        prompt_parts.append("The project's AGENTS.md has been loaded below:")
        prompt_parts.append(project_rules)

    return Agent(
        provider=provider,
        model=model,
        system_prompt="\n\n".join(prompt_parts),
        tools=READ_ONLY_TOOLS,
        cwd=cwd,
    )
