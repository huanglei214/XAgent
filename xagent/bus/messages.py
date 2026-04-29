from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

OutboundKind = Literal["progress", "delta", "tool", "final", "error"]


@dataclass
class InboundMessage:
    content: str
    source: str = "terminal"
    external_id: str = "local"
    session_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid4().hex)


@dataclass
class OutboundEvent:
    kind: OutboundKind
    content: str = ""
    session_id: str | None = None
    inbound_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
