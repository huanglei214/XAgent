from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class Approver(Protocol):
    async def require(self, action: str, target: str, *, summary: str = "") -> bool:
        ...


@dataclass
class SessionApprover:
    """Session-scoped risk approval helper."""

    default_allow: bool = False
    remembered: set[tuple[str, str]] = field(default_factory=set)

    async def require(self, action: str, target: str, *, summary: str = "") -> bool:
        key = (action, target)
        if key in self.remembered:
            return True
        if self.default_allow:
            self.remembered.add(key)
            return True
        return False


class CliApprover:
    def __init__(self) -> None:
        self.remembered: set[tuple[str, str]] = set()

    async def require(self, action: str, target: str, *, summary: str = "") -> bool:
        key = (action, target)
        if key in self.remembered:
            return True
        print()
        print("Permission required")
        print(f"  Action: {action}")
        print(f"  Target: {target}")
        if summary:
            print(f"  Summary: {summary[:500]}")
        answer = input("Allow? [o]nce / [s]ession / [d]eny: ").strip().lower()
        if answer in {"s", "session"}:
            self.remembered.add(key)
            return True
        return answer in {"o", "once", "y", "yes", "allow"}
