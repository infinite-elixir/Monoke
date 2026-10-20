"""In-process pub/sub for crawl progress, keyed by run id.

The app runs a single process, so an asyncio.Queue per subscriber is enough.
A short history buffer lets an SSE client that connects a moment late catch up.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from typing import Any

HISTORY = 200


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[int, set[asyncio.Queue]] = {}
        self._history: dict[int, deque[dict[str, Any]]] = {}

    def publish(self, run_id: int, event: dict[str, Any]) -> None:
        payload = dict(event)
        payload.setdefault("run_id", run_id)
        self._history.setdefault(run_id, deque(maxlen=HISTORY)).append(payload)
        for queue in list(self._subscribers.get(run_id, ())):
            queue.put_nowait(payload)

    async def subscribe(self, run_id: int) -> AsyncIterator[dict[str, Any]]:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(run_id, set()).add(queue)
        try:
            for event in list(self._history.get(run_id, ())):
                queue.put_nowait(event)
            while True:
                yield await queue.get()
        finally:
            subscribers = self._subscribers.get(run_id)
            if subscribers is not None:
                subscribers.discard(queue)
                if not subscribers:
                    self._subscribers.pop(run_id, None)

    def clear(self, run_id: int | None = None) -> None:
        if run_id is None:
            self._subscribers.clear()
            self._history.clear()
        else:
            self._subscribers.pop(run_id, None)
            self._history.pop(run_id, None)


bus = EventBus()
