from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable, Optional

from xagent.agent.memory import EpisodicMemory, WorkingMemory
from xagent.foundation.events import Event, InMemoryMessageBus
from xagent.foundation.messages import Message, message_text


@dataclass
class AutoCompactDecision:
    should_compact: bool
    trigger: Optional[str]
    message_count: int
    token_count: int


class AutoCompactService:
    def __init__(
        self,
        *,
        bus: InMemoryMessageBus,
        working_memory: WorkingMemory,
        episodic_memory: EpisodicMemory,
        session_id_getter: Callable[[], str],
        is_turn_active: Callable[[], bool],
        source: str = "auto_compact",
        message_threshold: Optional[int] = None,
        token_threshold: Optional[int] = 4000,
    ) -> None:
        self.bus = bus
        self.working_memory = working_memory
        self.episodic_memory = episodic_memory
        self.session_id_getter = session_id_getter
        self.is_turn_active = is_turn_active
        self.source = source
        self.message_threshold = (
            message_threshold
            if message_threshold is not None
            else getattr(getattr(episodic_memory, "store", None), "checkpoint_threshold", 20)
        )
        self.token_threshold = token_threshold
        self._tasks: set[asyncio.Task[bool]] = set()

    def evaluate(self, messages: list[Message]) -> AutoCompactDecision:
        message_count = len(messages)
        token_count = sum(len(message_text(message).split()) for message in messages)
        if self.message_threshold is not None and message_count > self.message_threshold:
            return AutoCompactDecision(True, "message_count", message_count, token_count)
        if self.token_threshold is not None and token_count > self.token_threshold:
            return AutoCompactDecision(True, "token_budget", message_count, token_count)
        return AutoCompactDecision(False, None, message_count, token_count)

    async def request_if_needed(self) -> Optional[asyncio.Task[bool]]:
        decision = self.evaluate(self.working_memory.messages)
        if not decision.should_compact:
            return None
        await self.bus.publish(
            Event(
                topic="memory.compaction.requested",
                session_id=self.session_id_getter(),
                payload={
                    "trigger": decision.trigger,
                    "message_count": decision.message_count,
                    "token_count": decision.token_count,
                },
                source=self.source,
            )
        )
        task = asyncio.create_task(self._run_compaction(decision))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def wait_for_all(self) -> None:
        if not self._tasks:
            return
        await asyncio.gather(*list(self._tasks))

    async def _run_compaction(self, decision: AutoCompactDecision) -> bool:
        await asyncio.sleep(0)
        if self.is_turn_active() or self.working_memory.active_tools:
            return False

        session_id = self.session_id_getter()
        self.episodic_memory.save(session_id, self.working_memory.messages, compact=True)
        restored = self.episodic_memory.restore(session_id)
        if restored is None:
            return False
        _, compacted_messages, metadata = restored
        self.working_memory.replace_messages(compacted_messages)
        await self.bus.publish(
            Event(
                topic="memory.compaction.completed",
                session_id=session_id,
                payload={
                    "trigger": decision.trigger,
                    "message_count": decision.message_count,
                    "token_count": decision.token_count,
                    "restored_message_count": metadata.restored_message_count,
                    "recent_message_count": metadata.recent_message_count,
                    "checkpointed_message_count": metadata.checkpointed_message_count,
                    "has_checkpoint": metadata.has_checkpoint,
                },
                source=self.source,
            )
        )
        return True
