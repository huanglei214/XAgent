from __future__ import annotations

import json
import re
from typing import List

from xagent.agent.core.middleware import AgentMiddleware
from xagent.foundation.messages import TextPart
from xagent.foundation.models import ModelRequest

from .loader import SkillDefinition, load_skill_bundle


class SkillsMiddleware(AgentMiddleware):
    def __init__(self, skills: List[SkillDefinition]) -> None:
        self.skills = skills

    async def before_agent_run(self, *, agent, user_text: str) -> None:
        agent.skills = list(self.skills)
        if getattr(agent, "requested_skill_name", None):
            return
        requested_skill_name = _extract_requested_skill_name(user_text, self.skills)
        if requested_skill_name:
            if hasattr(agent, "set_requested_skill_name"):
                agent.set_requested_skill_name(requested_skill_name)
            else:
                agent.requested_skill_name = requested_skill_name

    async def before_model(self, *, agent, request: ModelRequest):
        skills = getattr(agent, "skills", [])
        if not skills:
            return None

        requested_skill_name = getattr(agent, "requested_skill_name", None)
        requested_skill = None
        if requested_skill_name:
            requested_skill = next(
                (skill for skill in skills if skill.name.lower() == requested_skill_name.lower()),
                None,
            )

        payload = [
            {
                "name": skill.name,
                "description": skill.description,
                "path": skill.path,
                "source_dir": skill.source_dir,
                "type": skill.type,
            }
            for skill in skills
        ]
        extra = ""
        if requested_skill:
            loaded_skills = load_skill_bundle(requested_skill, skills)
            skill_blocks = "\n".join(
                (
                    f'<skill name="{skill.name}" path="{skill.path}">\n'
                    f"{skill.body}\n"
                    "</skill>"
                )
                for skill in loaded_skills
            )
            extra = (
                "\n<explicit_skill_invocation>\n"
                f'The user explicitly selected the skill "{requested_skill.name}".\n'
                "The requested skill and its prerequisite skill files have already been loaded below.\n"
                "Follow them directly instead of asking to read those SKILL.md files again.\n"
                "</explicit_skill_invocation>\n"
                "<loaded_skills>\n"
                f"{skill_blocks}\n"
                "</loaded_skills>\n"
            )
        request.messages = [
            *request.messages,
            type(request.messages[0])(
                role="system",
                content=[
                    TextPart(
                        text=(
                            "<skill_system>\n"
                            "You have access to project skills. Read and follow them when relevant.\n"
                            "If a skill is explicitly requested, read it first.\n"
                            f"{extra}"
                            "<skills>\n"
                            f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
                            "</skills>\n"
                            "</skill_system>"
                        )
                    )
                ],
            ),
        ]
        return request


def _extract_requested_skill_name(user_text: str, skills: List[SkillDefinition]) -> str | None:
    by_name = {skill.name.lower(): skill.name for skill in skills}
    for match in re.finditer(r"(?<!\w)[$/]([A-Za-z0-9._:-]+)", user_text):
        token = match.group(1).lower()
        if token in by_name:
            return by_name[token]
    return None
