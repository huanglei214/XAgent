from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional

from xagent.agent.core.middleware import AgentMiddleware
from xagent.agent.core.runtime_events import emit_runtime_event
from xagent.provider.types import ModelRequest, TextPart


@dataclass
class SkillDefinition:
    name: str
    description: str
    path: str
    source_dir: str
    body: str = ""
    dependencies: list[str] | None = None
    type: str = "skill"


def discover_skills(skills_dirs: List[str]) -> List[SkillDefinition]:
    """扫描给定目录列表，发现并返回所有有效的 SkillDefinition。"""
    discovered: List[SkillDefinition] = []
    seen_paths = set()

    for raw_dir in skills_dirs:
        skills_dir = _expand_skill_dir(raw_dir)
        if not skills_dir.exists() or not skills_dir.is_dir():
            continue

        for child in sorted(skills_dir.iterdir()):
            if not child.is_dir():
                continue
            skill_file = child / "SKILL.md"
            if not skill_file.exists():
                continue
            resolved = str(skill_file.resolve())
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            discovered.append(_read_skill_frontmatter(skill_file))

    return discovered


def _expand_skill_dir(raw_dir: str) -> Path:
    """将含 ~ 的路径展开为绝对路径。"""
    if raw_dir.startswith("~"):
        return Path(raw_dir).expanduser()
    return Path(raw_dir)


def _read_skill_frontmatter(path: Path) -> SkillDefinition:
    """从 SKILL.md 文件中解析 frontmatter 元数据与正文，构造 SkillDefinition。"""
    content = path.read_text(encoding="utf-8")
    metadata, body = _split_frontmatter(content)
    name = metadata.get("name") or path.parent.name
    description = metadata.get("description") or _first_paragraph(body) or f"Skill from {path.parent.name}"
    return SkillDefinition(
        name=name,
        description=description,
        path=str(path.resolve()),
        source_dir=str(path.parent.resolve()),
        body=body.strip(),
        dependencies=_extract_skill_dependencies(body, path.parent),
    )


def _split_frontmatter(content: str) -> tuple[dict, str]:
    """将 SKILL.md 内容按 --- 分隔符拆分为 frontmatter 字典和正文。"""
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, content

    metadata = {}
    body_start = 1
    for index in range(1, len(lines)):
        line = lines[index].rstrip()
        if line.strip() == "---":
            body_start = index + 1
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        metadata[key.strip()] = _parse_scalar(value.strip())
    return metadata, "\n".join(lines[body_start:])


def _parse_scalar(value: str) -> str:
    """去除 frontmatter 值两端的可选引号。"""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _first_paragraph(body: str) -> Optional[str]:
    """返回正文中第一个非空段落。"""
    for chunk in body.split("\n\n"):
        normalized = " ".join(line.strip() for line in chunk.splitlines()).strip()
        if normalized:
            return normalized
    return None


def load_skill_bundle(skill: SkillDefinition, known_skills: Iterable[SkillDefinition]) -> list[SkillDefinition]:
    """按依赖拓扑序加载指定技能及其所有前置依赖。"""
    by_path = {item.path: item for item in known_skills}
    ordered: list[SkillDefinition] = []
    seen: set[str] = set()

    def _visit(current: SkillDefinition) -> None:
        if current.path in seen:
            return
        seen.add(current.path)

        for dependency_path in current.dependencies or []:
            dependency = by_path.get(dependency_path)
            if dependency is None:
                dep_path = Path(dependency_path)
                if not dep_path.exists():
                    continue
                dependency = _read_skill_frontmatter(dep_path)
                by_path[dependency.path] = dependency
            _visit(dependency)

        ordered.append(current)

    _visit(skill)
    return ordered


def _extract_skill_dependencies(body: str, base_dir: Path) -> list[str]:
    """从正文中提取指向其他 SKILL.md 的 Markdown 链接依赖。"""
    dependencies: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"\[[^\]]+\]\(([^)]+)\)", body):
        target = match.group(1).strip()
        if not target or "://" in target or target.startswith("#"):
            continue
        clean_target = target.split("#", 1)[0].split("?", 1)[0]
        if Path(clean_target).name.lower() != "skill.md":
            continue
        resolved = str((base_dir / clean_target).resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        dependencies.append(resolved)
    return dependencies


class SkillsMiddleware(AgentMiddleware):
    """技能中间件：在 agent 运行前注入技能列表，在模型调用前将技能提示合并到请求中。"""

    def __init__(self, skills: List[SkillDefinition]) -> None:
        self.skills = skills

    async def before_agent_run(self, *, agent, user_text: str) -> None:
        """在 agent 运行前设置技能列表并检测用户是否显式请求了某个技能。"""
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
        """在模型调用前将技能信息注入系统提示；若用户显式请求了技能则加载完整技能包。"""
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
    """从用户文本中提取 $/ 前缀的技能名称。"""
    by_name = {skill.name.lower(): skill.name for skill in skills}
    for match in re.finditer(r"(?<!\w)[$/]([A-Za-z0-9._:-]+)", user_text):
        token = match.group(1).lower()
        if token in by_name:
            source = "dollar" if user_text[match.start()] == "$" else "slash"
            return by_name[token], source
    return None, None


def _merge_skill_prompt_into_request(request: ModelRequest, skill_text: str) -> None:
    """将技能提示文本合并到请求的首条系统消息中；若无系统消息则插入一条。"""
    for message in request.messages:
        if message.role != "system":
            continue
        existing_text = "".join(part.text for part in message.content if isinstance(part, TextPart)).strip()
        merged = f"{existing_text}\n\n{skill_text}".strip() if existing_text else skill_text
        message.content = [TextPart(text=merged)]
        return

    request.messages = [type(request.messages[0])(role="system", content=[TextPart(text=skill_text)]), *request.messages]


def _first_system_text(request: ModelRequest) -> str:
    """返回请求中首条系统消息的纯文本内容。"""
    for message in request.messages:
        if message.role == "system":
            return "".join(part.text for part in message.content if isinstance(part, TextPart))
    return ""
