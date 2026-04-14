from xagent.community.anthropic.provider import AnthropicProvider
from xagent.community.ark.provider import ArkProvider
from xagent.community.openai.provider import OpenAIChatProvider
from xagent.foundation.models import ModelConfig, ModelProvider


def create_provider(config: ModelConfig) -> ModelProvider:
    if config.provider == "anthropic":
        return AnthropicProvider(config)
    if config.provider == "ark":
        return ArkProvider(config)
    return OpenAIChatProvider(config)


__all__ = ["AnthropicProvider", "ArkProvider", "ModelProvider", "OpenAIChatProvider", "create_provider"]
