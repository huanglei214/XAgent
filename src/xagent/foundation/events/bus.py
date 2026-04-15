from __future__ import annotations

import inspect
from collections import defaultdict
from typing import Any, Callable

from xagent.foundation.events.types import Event

EventHandler = Callable[[Event], Any]


class InMemoryMessageBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[tuple[int, EventHandler]]] = defaultdict(list)
        self._next_handler_id = 0

    def subscribe(self, topic: str, handler: EventHandler) -> Callable[[], None]:
        handler_id = self._next_handler_id
        self._next_handler_id += 1
        self._handlers[topic].append((handler_id, handler))

        def _unsubscribe() -> None:
            handlers = self._handlers.get(topic, [])
            self._handlers[topic] = [
                (existing_id, existing_handler)
                for existing_id, existing_handler in handlers
                if existing_id != handler_id
            ]
            if not self._handlers[topic]:
                self._handlers.pop(topic, None)

        return _unsubscribe

    async def publish(self, event: Event) -> None:
        handlers = [
            *list(self._handlers.get(event.topic, [])),
            *list(self._handlers.get("*", [])),
        ]
        for _, handler in handlers:
            result = handler(event)
            if inspect.isawaitable(result):
                await result
