from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderSpec:
    name: str
    backend: str


OPENAI_COMPAT_SPEC = ProviderSpec(
    name="openai_compat",
    backend="openai_compat",
)

PROVIDERS: tuple[ProviderSpec, ...] = (OPENAI_COMPAT_SPEC,)


def find_by_name(name: str) -> ProviderSpec | None:
    normalized = name.replace("-", "_").lower()
    for spec in PROVIDERS:
        if spec.name == normalized:
            return spec
    return None
