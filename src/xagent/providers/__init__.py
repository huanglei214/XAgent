from xagent.config.schema import ModelConfig
from xagent.providers.anthropic_provider import AnthropicProvider
from xagent.providers.base import ModelProvider
from xagent.providers.openai_provider import OpenAIChatProvider


def create_provider(config: ModelConfig) -> ModelProvider:
    if config.provider == "anthropic":
        return AnthropicProvider(config)
    return OpenAIChatProvider(config)


__all__ = ["AnthropicProvider", "ModelProvider", "OpenAIChatProvider", "create_provider"]
