from pathlib import Path
from typing import Callable, Optional

from xagent.agent import Agent, SkillsMiddleware, create_todo_system, discover_skills
from xagent.agent.core.middleware import AgentMiddleware
from xagent.coding.tools import ALL_CODING_TOOLS
from xagent.coding.context.project_rules import load_project_rules
from xagent.coding.middleware import EditGuardrailsMiddleware


def create_coding_agent(
    provider,
    model: str,
    cwd: str,
    middlewares: Optional[list[AgentMiddleware]] = None,
    approval_handler: Optional[Callable] = None,
) -> Agent:
    project_rules = load_project_rules(Path(cwd))
    todo_tool, todo_middleware, todo_store = create_todo_system()
    skills = discover_skills(
        [
            str(Path(cwd) / "skills"),
            str(Path(cwd) / ".agents" / "skills"),
            str(Path(cwd) / ".xagent" / "skills"),
            "~/.xagent/skills",
            "~/.agents/skills",
        ]
    )

    prompt_parts = [
        "You are XAgent, a careful coding assistant.",
        f"Your working directory is {Path(cwd).resolve().as_posix()}.",
        "Inspect files before making assumptions.",
        "Use the available tools when they help you answer repository questions.",
        "Read the relevant files before editing them.",
        "Prefer str_replace or apply_patch for targeted file updates and write_file for full rewrites.",
        "Use todo_write for complex, multi-step tasks when tracking progress would help.",
    ]
    if project_rules:
        prompt_parts.append("The project's AGENTS.md scope chain has been loaded below:")
        prompt_parts.append(project_rules)

    agent = Agent(
        provider=provider,
        model=model,
        system_prompt="\n\n".join(prompt_parts),
        tools=[*ALL_CODING_TOOLS, todo_tool],
        middlewares=[todo_middleware, SkillsMiddleware(skills), EditGuardrailsMiddleware(), *(middlewares or [])],
        cwd=cwd,
        approval_handler=approval_handler,
    )
    agent.todo_store = todo_store
    agent.skills = skills
    return agent
