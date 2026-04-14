from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional


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
    if raw_dir.startswith("~"):
        return Path(raw_dir).expanduser()
    return Path(raw_dir)


def _read_skill_frontmatter(path: Path) -> SkillDefinition:
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
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _first_paragraph(body: str) -> Optional[str]:
    for chunk in body.split("\n\n"):
        normalized = " ".join(line.strip() for line in chunk.splitlines()).strip()
        if normalized:
            return normalized
    return None


def load_skill_bundle(skill: SkillDefinition, known_skills: Iterable[SkillDefinition]) -> list[SkillDefinition]:
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
                path = Path(dependency_path)
                if not path.exists():
                    continue
                dependency = _read_skill_frontmatter(path)
                by_path[dependency.path] = dependency
            _visit(dependency)

        ordered.append(current)

    _visit(skill)
    return ordered


def _extract_skill_dependencies(body: str, base_dir: Path) -> list[str]:
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
