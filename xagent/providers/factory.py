from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from xagent.config import AppConfig
from xagent.providers.openai_compatible import OpenAICompatProvider
from xagent.providers.registry import ProviderSpec, find_by_name
from xagent.providers.types import Provider


@dataclass(frozen=True)
class ProviderSnapshot:
    provider: Provider
    model: str
    provider_name: str
    api_base: str | None
    signature: tuple[object, ...]


def make_provider(config: AppConfig) -> ProviderSnapshot:
    defaults = config.agents.defaults
    spec = find_by_name(defaults.provider)
    if spec is None:
        raise ValueError(f"Unsupported provider {defaults.provider!r}.")
    if spec.backend != "openai_compat":
        raise ValueError(f"Unsupported provider backend {spec.backend!r}.")

    provider_config = config.providers.openai_compat
    provider = OpenAICompatProvider(
        api_key=provider_config.api_key,
        api_base=provider_config.api_base,
        extra_headers=provider_config.extra_headers,
        extra_body=provider_config.extra_body,
        timeout_seconds=provider_config.timeout_seconds,
        spec=spec,
    )
    return ProviderSnapshot(
        provider=provider,
        model=defaults.model,
        provider_name=spec.name,
        api_base=provider_config.api_base,
        signature=_provider_signature(config, spec),
    )


def _provider_signature(config: AppConfig, spec: ProviderSpec) -> tuple[object, ...]:
    defaults = config.agents.defaults
    provider_config = config.providers.openai_compat
    return (
        defaults.model,
        defaults.provider,
        spec.backend,
        provider_config.api_base,
        _fingerprint(provider_config.api_key),
        _stable_items(provider_config.extra_headers),
        _stable_items(provider_config.extra_body),
        provider_config.timeout_seconds,
        defaults.temperature,
        defaults.max_tokens,
    )


def _fingerprint(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _stable_items(value: dict[str, Any]) -> tuple[tuple[str, Any], ...]:
    return tuple(sorted(value.items()))
