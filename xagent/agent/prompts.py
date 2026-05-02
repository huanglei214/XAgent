from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from jinja2 import Environment, PackageLoader, StrictUndefined, select_autoescape


@dataclass
class PromptRenderer:
    """Render bundled Markdown prompt templates."""

    environment: Environment = field(default_factory=lambda: _default_environment())

    def render(self, template_name: str, **values: Any) -> str:
        template = self.environment.get_template(template_name)
        return template.render(**values).strip()


def _default_environment() -> Environment:
    return Environment(
        loader=PackageLoader("xagent", "prompts"),
        autoescape=select_autoescape(disabled_extensions=("md",)),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
