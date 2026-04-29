from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Skill:
    name: str
    prompt: str = ""
    tool_names: tuple[str, ...] = field(default_factory=tuple)
