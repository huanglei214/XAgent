from xagent.provider.types import ModelConfig, ModelProvider
from xagent.provider.anthropic import AnthropicProvider
from xagent.provider.ark import ArkProvider
from xagent.provider.openai import OpenAIChatProvider


def create_provider(config: ModelConfig) -> ModelProvider:
    if config.provider == "anthropic":
        return AnthropicProvider(config)
    if config.provider == "ark":
        return ArkProvider(config)
    return OpenAIChatProvider(config)


__all__ = ["AnthropicProvider", "ArkProvider", "ModelProvider", "OpenAIChatProvider", "create_provider"]
