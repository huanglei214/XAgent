from xagent.provider.anthropic.provider import AnthropicProvider
from xagent.provider.ark.provider import ArkProvider
from xagent.provider.openai.provider import OpenAIChatProvider
from xagent.foundation.models import ModelConfig, ModelProvider


def create_provider(config: ModelConfig) -> ModelProvider:
    if config.provider == "anthropic":
        return AnthropicProvider(config)
    if config.provider == "ark":
        return ArkProvider(config)
    return OpenAIChatProvider(config)


__all__ = ["AnthropicProvider", "ArkProvider", "ModelProvider", "OpenAIChatProvider", "create_provider"]
