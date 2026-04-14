from typing import Literal, Optional

from pydantic import BaseModel, Field


ProviderName = Literal["openai", "anthropic", "ark"]


class ModelConfig(BaseModel):
    name: str = Field(min_length=1)
    provider: ProviderName = "openai"
    base_url: Optional[str] = None
    api_key_env: str = Field(min_length=1)
