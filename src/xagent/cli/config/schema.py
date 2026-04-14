from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator


ProviderName = Literal["openai", "anthropic", "ark"]


class ModelConfig(BaseModel):
    name: str = Field(min_length=1)
    provider: ProviderName = "openai"
    base_url: Optional[str] = None
    api_key_env: str = Field(min_length=1)


class AppConfig(BaseModel):
    default_model: str
    models: list[ModelConfig]

    @model_validator(mode="after")
    def validate_default_model(self) -> "AppConfig":
        if not self.models:
            raise ValueError("Config must define at least one model.")
        model_names = {model.name for model in self.models}
        if len(model_names) != len(self.models):
            raise ValueError("Config contains duplicate model names.")
        if self.default_model not in model_names:
            raise ValueError(f"default_model '{self.default_model}' does not match any configured model.")
        return self


def default_config() -> AppConfig:
    model = ModelConfig(
        name="ep-your-ark-endpoint-id",
        provider="ark",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        api_key_env="ARK_API_KEY",
    )
    return AppConfig(default_model=model.name, models=[model])
