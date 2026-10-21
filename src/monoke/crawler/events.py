"""In-process pub/sub for crawl progress, keyed by run id.

The app runs a single process, so an asyncio.Queue per subscriber is enough.
A short history buffer lets an SSE client that connects a moment late catch up.
Both the per-run history and the number of remembered runs are bounded so a
long-lived process cannot grow without limit (T6.5).
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from typing import Any

HISTORY = 200
MAX_RUNS = 16


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[int, set[asyncio.Queue]] = {}
        self._history: dict[int, deque[dict[str, Any]]] = {}
        self._runs: deque[int] = deque()

    def publish(self, run_id: int, event: dict[str, Any]) -> None:
        payload = dict(event)
        payload.setdefault("run_id", run_id)
        if run_id not in self._history:
            self._runs.append(run_id)
        self._history.setdefault(run_id, deque(maxlen=HISTORY)).append(payload)
        for queue in list(self._subscribers.get(run_id, ())):
            queue.put_nowait(payload)
        self._prune()

    def _prune(self) -> None:
        """Drop the history of finished runs once too many runs are known."""
        if len(self._runs) <= MAX_RUNS:
            return
        for run_id in list(self._runs)[:-MAX_RUNS]:
            if self._subscribers.get(run_id):
                # Still streaming: keep the buffer until the client leaves.
                continue
            self._history.pop(run_id, None)
        self._runs = deque(
            run_id
            for run_id in self._runs
            if run_id in self._history or self._subscribers.get(run_id)
        )

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

    def latest(self, run_id: int) -> dict[str, Any] | None:
        """Most recent event for a run, for polling clients that missed SSE."""
        history = self._history.get(run_id)
        return dict(history[-1]) if history else None

    def forget(self, run_id: int) -> None:
        """Trim a finished run's buffer to its terminal event.

        A client that subscribes a moment late still needs the last event, so
        only the rest of the history is released.
        """
        history = self._history.get(run_id)
        if history is None:
            return
        last = history[-1] if history else None
        self._history[run_id] = deque([last] if last is not None else [], maxlen=HISTORY)

    def clear(self, run_id: int | None = None) -> None:
        if run_id is None:
            self._subscribers.clear()
            self._history.clear()
            self._runs.clear()
        else:
            self._subscribers.pop(run_id, None)
            self._history.pop(run_id, None)
            try:
                self._runs.remove(run_id)
            except ValueError:
                pass


bus = EventBus()
