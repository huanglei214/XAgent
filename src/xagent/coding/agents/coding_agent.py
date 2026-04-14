from pathlib import Path
from typing import Callable, Optional

from xagent.agent import Agent, SkillsMiddleware, create_todo_system, discover_skills
from xagent.agent.core.middleware import AgentMiddleware
from xagent.foundation.messages import Message, TextPart
from xagent.coding.tools import ALL_CODING_TOOLS, create_ask_user_question_tool
from xagent.coding.context.project_rules import load_project_rules
from xagent.coding.middleware import EditGuardrailsMiddleware, ProjectRulesMiddleware


def create_coding_agent(
    provider,
    model: str,
    cwd: str,
    middlewares: Optional[list[AgentMiddleware]] = None,
    approval_handler: Optional[Callable] = None,
    ask_user_question: Optional[Callable] = None,
) -> Agent:
    project_rules = load_project_rules(Path(cwd))
    todo_tool, todo_middleware, todo_store = create_todo_system()
    cwd_path = Path(cwd).resolve().as_posix()
    skills = discover_skills(
        [
            str(Path(cwd) / "skills"),
            str(Path(cwd) / ".agents" / "skills"),
            str(Path(cwd) / ".xagent" / "skills"),
            "~/.xagent/skills",
            "~/.agents/skills",
        ]
    )

    system_prompt = f"""<agent name="XAgent" role="coding_agent" description="A repository-focused coding assistant">
Use the given tools and loaded skills to solve the user's request in the working directory.
</agent>

<working_directory dir="{cwd_path}/" />

<tool_usage>
- Inspect directories before assuming file paths.
- Prefer list_files or glob_search to discover files.
- Prefer grep_search to locate relevant content.
- Read a file before editing it.
- Prefer apply_patch or str_replace for targeted edits.
- Use write_file only for full rewrites or brand-new files.
- If an edit strategy fails, re-read the file and choose a safer next step.
- Do not repeat the same failing tool call with unchanged invalid input.
- Use tool results and error messages to decide the next step.
- Use todo_write for complex, multi-step tasks when tracking progress would help.
</tool_usage>

<editing_rules>
- Prefer minimal, reviewable diffs.
- Keep changes reversible when possible.
- Do not introduce new dependencies unless the user explicitly asks.
</editing_rules>

<notes>
- If the request is simple and does not need tools, answer directly and stop.
- Do not start long-running local servers unless the user explicitly asks.
</notes>"""
    context_messages = []
    project_rules_middleware = []
    if project_rules:
        context_messages.append(
            Message(
                role="user",
                content=[
                    TextPart(
                        text="> The project's AGENTS.md scope chain has been automatically loaded. "
                        f"Here is the content:\n\n{project_rules}"
                    )
                ],
            )
        )
        project_rules_middleware.append(ProjectRulesMiddleware(project_rules))

    extra_tools = [create_ask_user_question_tool(ask_user_question)] if ask_user_question else []
    agent = Agent(
        provider=provider,
        model=model,
        system_prompt=system_prompt,
        context_messages=context_messages,
        tools=[*ALL_CODING_TOOLS, todo_tool, *extra_tools],
        middlewares=[
            *project_rules_middleware,
            todo_middleware,
            SkillsMiddleware(skills),
            EditGuardrailsMiddleware(),
            *(middlewares or []),
        ],
        cwd=cwd,
        approval_handler=approval_handler,
    )
    agent.todo_store = todo_store
    agent.skills = skills
    agent.project_rules = project_rules
    return agent
