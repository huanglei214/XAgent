from __future__ import annotations

import asyncio
import inspect
import threading
from typing import Any, Callable, Generic, Optional, TypeVar

MessageT = TypeVar("MessageT")
MessageHandler = Callable[[MessageT], Any]


class TypedMessageBus(Generic[MessageT]):
    """Type-safe pub/sub bus with optional predicate-based filtering."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._handlers: dict[int, tuple[MessageHandler[MessageT], Optional[Callable[[MessageT], bool]]]] = {}
        self._next_handler_id = 0

    def subscribe(
        self,
        handler: MessageHandler[MessageT],
        *,
        predicate: Optional[Callable[[MessageT], bool]] = None,
    ) -> Callable[[], None]:
        with self._lock:
            handler_id = self._next_handler_id
            self._next_handler_id += 1
            self._handlers[handler_id] = (handler, predicate)

        def _unsubscribe() -> None:
            with self._lock:
                self._handlers.pop(handler_id, None)

        return _unsubscribe

    async def publish(self, message: MessageT) -> None:
        for handler, predicate in self._snapshot_handlers():
            if predicate is not None and not predicate(message):
                continue
            result = handler(message)
            if inspect.isawaitable(result):
                await result

    def publish_nowait(self, message: MessageT) -> None:
        for handler, predicate in self._snapshot_handlers():
            if predicate is not None and not predicate(message):
                continue
            result = handler(message)
            if inspect.isawaitable(result):
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    asyncio.run(result)
                else:
                    loop.create_task(result)

    def _snapshot_handlers(self) -> list[tuple[MessageHandler[MessageT], Optional[Callable[[MessageT], bool]]]]:
        with self._lock:
            return list(self._handlers.values())
