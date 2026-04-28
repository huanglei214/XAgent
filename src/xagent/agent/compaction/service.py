from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable, Optional
from uuid import uuid4

from xagent.agent.memory import EpisodicMemory, WorkingMemory
from xagent.bus.messages import make_progress
from xagent.bus.queue import MessageBus
from xagent.provider.types import Message, message_text


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
        message_bus: MessageBus,
        working_memory: WorkingMemory,
        episodic_memory: EpisodicMemory,
        session_id_getter: Callable[[], str],
        is_turn_active: Callable[[], bool],
        source: str = "auto_compact",
        message_threshold: Optional[int] = None,
        token_threshold: Optional[int] = 4000,
    ) -> None:
        self.message_bus = message_bus
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
        # 兼容旧行为：如果没有 message_bus，直接不做（理论上 5.8 后不会发生）。
        if self.message_bus is None:
            return None
        decision = self.evaluate(self.working_memory.messages)
        if not decision.should_compact:
            return None
        session_id = self.session_id_getter()
        correlation_id = uuid4().hex
        await self.message_bus.publish_outbound(
            make_progress(
                correlation_id=correlation_id,
                session_id=session_id,
                session_key=session_id,
                channel="system",
                chat_id=session_id,
                source=self.source,
                event="compact_started",
                extra_metadata={
                    "trigger": decision.trigger,
                    "message_count": decision.message_count,
                    "token_count": decision.token_count,
                },
            )
        )
        task = asyncio.create_task(self._run_compaction(decision))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def on_post_turn(self, ctx) -> None:
        """PostTurnHook 入口：在 SessionRuntime 每轮 turn 成功完成后由
        ``SessionRuntime._run_post_turn_hooks`` 调用。

        参数 ``ctx`` 的类型是 ``xagent.agent.runtime.session_runtime.PostTurnContext``；
        这里使用鸭子类型以避免循环 import。本方法内部复用
        ``request_if_needed`` 的逻辑，对行为零变更。
        """
        await self.request_if_needed()

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
        await self.message_bus.publish_outbound(
            make_progress(
                correlation_id=uuid4().hex,
                session_id=session_id,
                session_key=session_id,
                channel="system",
                chat_id=session_id,
                source=self.source,
                event="compact_finished",
                extra_metadata={
                    "trigger": decision.trigger,
                    "message_count": decision.message_count,
                    "token_count": decision.token_count,
                    "restored_message_count": metadata.restored_message_count,
                    "recent_message_count": metadata.recent_message_count,
                    "checkpointed_message_count": metadata.checkpointed_message_count,
                    "has_checkpoint": metadata.has_checkpoint,
                },
            )
        )
        return True
