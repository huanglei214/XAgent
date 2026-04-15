from typing import Literal

from pydantic import BaseModel, Field, model_validator

from xagent.foundation.models import ModelConfig


class AppConfig(BaseModel):
    default_model: str
    max_model_calls: int = Field(default=100, ge=1, le=1000)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "WARNING"
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
    return AppConfig(default_model=model.name, max_model_calls=100, log_level="WARNING", models=[model])
