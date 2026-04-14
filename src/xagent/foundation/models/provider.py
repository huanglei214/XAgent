from typing import AsyncIterator, Protocol

from xagent.foundation.messages import Message
from xagent.foundation.models.request import ModelRequest


class ModelProvider(Protocol):
    async def stream_complete(self, request: ModelRequest) -> AsyncIterator[Message]:
        ...

    async def complete(self, request: ModelRequest) -> Message:
        ...

    async def stream_text(self, request: ModelRequest) -> AsyncIterator[str]:
        ...
