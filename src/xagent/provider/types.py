"""LLM 消息与 Provider 协议的领域模型定义。

本模块定义 Provider 层通用的数据结构：`Message` / `ContentPart`
(`TextPart`/`ToolUsePart`/`ToolResultPart`) 以及 `ModelConfig` / `ModelRequest`
/ `ModelProvider` 协议。它本质属于 provider / domain 层，与"消息总线"概念无关
（openspec 0001-simplify-bus）。
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Literal, Optional, Protocol, Union

from pydantic import BaseModel, Field


class TextPart(BaseModel):
    """会话消息中的纯文本片段。"""

    type: Literal["text"] = "text"
    text: str


class ToolUsePart(BaseModel):
    """assistant 请求调用某工具时的 content 片段。"""

    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    input: dict[str, Any]


class ToolResultPart(BaseModel):
    """工具执行返回结果后注入回话的 content 片段。"""

    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: str
    is_error: bool = False


ContentPart = Union[TextPart, ToolUsePart, ToolResultPart]


class Message(BaseModel):
    """单条对话消息：一个 role + 一组 content 片段。"""

    role: Literal["system", "user", "assistant", "tool"]
    content: list[ContentPart]


def message_text(message: Message) -> str:
    """从 Message 中抽取所有 TextPart 的纯文本并拼接返回。"""

    parts = []
    for part in message.content:
        if isinstance(part, TextPart):
            parts.append(part.text)
    return "".join(parts)


ProviderName = Literal["openai", "anthropic", "ark"]


class ModelConfig(BaseModel):
    """用户侧的模型配置（名字/厂商/base_url/密钥）。"""

    name: str = Field(min_length=1)
    provider: ProviderName = "openai"
    base_url: Optional[str] = None
    api_key: str = ""


class ModelRequest(BaseModel):
    """向 Provider 发起一次补全请求时的请求体。"""

    model: str
    messages: list[Message]
    tools: list[dict] = Field(default_factory=list)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: Optional[int] = Field(default=None, ge=1)


class ModelProvider(Protocol):
    """Provider 统一协议：同步/流式/纯文本流三种调用形态。"""

    async def stream_complete(self, request: ModelRequest) -> AsyncIterator[Message]:
        ...

    async def complete(self, request: ModelRequest) -> Message:
        ...

    async def stream_text(self, request: ModelRequest) -> AsyncIterator[str]:
        ...
