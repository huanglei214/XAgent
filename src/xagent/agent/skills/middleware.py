from __future__ import annotations

import hashlib
import json
import re
from typing import List

from xagent.agent.core.middleware import AgentMiddleware
from xagent.agent.core.runtime_events import emit_runtime_event
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
        requested_skill_name, request_source = _extract_requested_skill_name(user_text, self.skills)
        if requested_skill_name:
            if hasattr(agent, "set_requested_skill_name"):
                agent.set_requested_skill_name(requested_skill_name)
            else:
                agent.requested_skill_name = requested_skill_name
            recorder = getattr(agent, "trace_recorder", None)
            if recorder is not None:
                recorder.emit(
                    "skill_requested_detected",
                    payload={
                        "requested_skill_name": requested_skill_name,
                        "source": request_source,
                        "user_text_preview": user_text[:200],
                    },
                    tags={"skill_name": requested_skill_name, "source": request_source},
                )
            await emit_runtime_event(
                agent,
                "skill_requested_detected",
                {"requested_skill_name": requested_skill_name, "source": request_source},
            )

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
            recorder = getattr(agent, "trace_recorder", None)
            if recorder is not None:
                recorder.emit(
                    "skill_bundle_resolved",
                    payload={
                        "requested_skill_name": requested_skill.name,
                        "loaded_skills": [
                            {
                                "name": skill.name,
                                "path": skill.path,
                                "dependency_count": len(skill.dependencies or []),
                            }
                            for skill in loaded_skills
                        ],
                        "missing_dependencies": [],
                    },
                    tags={"skill_name": requested_skill.name, "loaded_skill_count": len(loaded_skills)},
                )
            await emit_runtime_event(
                agent,
                "skill_bundle_resolved",
                {
                    "requested_skill_name": requested_skill.name,
                    "loaded_skill_names": [skill.name for skill in loaded_skills],
                },
            )
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
        skill_text = (
            "<skill_system>\n"
            "You have access to project skills. Read and follow them when relevant.\n"
            "If a skill is explicitly requested, read it first.\n"
            f"{extra}"
            "<skills>\n"
            f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n"
            "</skills>\n"
            "</skill_system>"
        )
        _merge_skill_prompt_into_request(request, skill_text)
        if requested_skill:
            recorder = getattr(agent, "trace_recorder", None)
            if recorder is not None:
                injected_text = _first_system_text(request)
                recorder.emit(
                    "skill_prompt_injected",
                    payload={
                        "requested_skill_name": requested_skill.name,
                        "loaded_skill_names": [skill.name for skill in loaded_skills],
                        "loaded_skill_paths": [skill.path for skill in loaded_skills],
                        "loaded_skill_char_count": sum(len(skill.body) for skill in loaded_skills),
                        "final_injected_block_hash": hashlib.sha256(injected_text.encode("utf-8")).hexdigest(),
                        "final_injected_block_preview": injected_text[:400],
                    },
                    tags={"skill_name": requested_skill.name, "loaded_skill_count": len(loaded_skills)},
                )
            await emit_runtime_event(
                agent,
                "skill_prompt_injected",
                {
                    "requested_skill_name": requested_skill.name,
                    "loaded_skill_names": [skill.name for skill in loaded_skills],
                },
            )
        return request


def _extract_requested_skill_name(user_text: str, skills: List[SkillDefinition]) -> tuple[str | None, str | None]:
    by_name = {skill.name.lower(): skill.name for skill in skills}
    for match in re.finditer(r"(?<!\w)[$/]([A-Za-z0-9._:-]+)", user_text):
        token = match.group(1).lower()
        if token in by_name:
            source = "dollar" if user_text[match.start()] == "$" else "slash"
            return by_name[token], source
    return None, None


def _merge_skill_prompt_into_request(request: ModelRequest, skill_text: str) -> None:
    for message in request.messages:
        if message.role != "system":
            continue
        existing_text = "".join(part.text for part in message.content if isinstance(part, TextPart)).strip()
        merged = f"{existing_text}\n\n{skill_text}".strip() if existing_text else skill_text
        message.content = [TextPart(text=merged)]
        return

    request.messages = [type(request.messages[0])(role="system", content=[TextPart(text=skill_text)]), *request.messages]


def _first_system_text(request: ModelRequest) -> str:
    for message in request.messages:
        if message.role == "system":
            return "".join(part.text for part in message.content if isinstance(part, TextPart))
    return ""
