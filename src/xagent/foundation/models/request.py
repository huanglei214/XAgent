from typing import Optional

from pydantic import BaseModel, Field

from xagent.foundation.messages import Message


class ModelRequest(BaseModel):
    model: str
    messages: list[Message]
    tools: list[dict] = Field(default_factory=list)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=None, ge=1)
