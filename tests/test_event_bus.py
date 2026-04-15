import unittest

from xagent.foundation.events import Event, InMemoryMessageBus


class EventBusTests(unittest.IsolatedAsyncioTestCase):
    async def test_bus_invokes_exact_and_wildcard_handlers_in_order(self) -> None:
        bus = InMemoryMessageBus()
        seen = []

        async def _exact(event: Event) -> None:
            seen.append(("exact", event.topic, event.payload["value"]))

        async def _wildcard(event: Event) -> None:
            seen.append(("wildcard", event.topic, event.payload["value"]))

        bus.subscribe("session.turn.requested", _exact)
        bus.subscribe("*", _wildcard)

        await bus.publish(
            Event(
                topic="session.turn.requested",
                session_id="session-1",
                payload={"value": 1},
                source="test",
            )
        )

        self.assertEqual(
            seen,
            [
                ("exact", "session.turn.requested", 1),
                ("wildcard", "session.turn.requested", 1),
            ],
        )

    async def test_bus_unsubscribe_stops_future_deliveries(self) -> None:
        bus = InMemoryMessageBus()
        seen = []

        async def _handler(event: Event) -> None:
            seen.append(event.topic)

        unsubscribe = bus.subscribe("topic", _handler)
        unsubscribe()

        await bus.publish(Event(topic="topic", session_id="session-1", payload={}, source="test"))

        self.assertEqual(seen, [])
