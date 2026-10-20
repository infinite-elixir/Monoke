from __future__ import annotations

import pytest

from monoke.crawler.events import EventBus


@pytest.fixture()
def event_bus():
    instance = EventBus()
    yield instance
    instance.clear()


async def test_replays_history_and_streams_live(event_bus):
    event_bus.publish(7, {"type": "started"})

    stream = event_bus.subscribe(7)
    first = await anext(stream)
    event_bus.publish(7, {"type": "done"})
    second = await anext(stream)
    await stream.aclose()

    assert first == {"type": "started", "run_id": 7}
    assert second == {"type": "done", "run_id": 7}


async def test_subscriptions_are_isolated_by_run(event_bus):
    stream = event_bus.subscribe(1)

    event_bus.publish(2, {"type": "page"})
    event_bus.publish(1, {"type": "progress", "done": 1})

    event = await anext(stream)
    await stream.aclose()

    assert event["run_id"] == 1
    assert event["done"] == 1


async def test_history_is_bounded(event_bus):
    for index in range(250):
        event_bus.publish(3, {"type": "progress", "done": index})

    stream = event_bus.subscribe(3)
    events = []
    for _ in range(200):
        events.append(await anext(stream))
    await stream.aclose()

    assert len(events) == 200
    assert events[0]["done"] == 50
    assert events[-1]["done"] == 249
