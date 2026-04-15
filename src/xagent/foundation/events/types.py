from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4


@dataclass
class Event:
    topic: str
    session_id: str
    payload: dict[str, Any]
    source: str
    event_id: str = field(default_factory=lambda: uuid4().hex)
    created_at: float = field(default_factory=time.time)
