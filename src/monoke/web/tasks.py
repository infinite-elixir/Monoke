"""Background crawl tasks owned by the web process.

The request that archives a creator must return immediately. The crawl runs in
an ``asyncio.Task`` on the server loop, and a small registry makes duplicate
Sync clicks idempotent for the lifetime of the process.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..config import Settings, get_settings
from ..crawler.crawl import crawl_full
from ..cumst.urls import CumstRef

log = logging.getLogger(__name__)

_active: dict[int, asyncio.Task[Any]] = {}
_active_runs: dict[int, int] = {}
_run_tasks: dict[int, asyncio.Task[Any]] = {}


async def crawl_creator(
    ref: CumstRef,
    *,
    settings: Settings,
    creator_id: int,
    run_id: int,
) -> None:
    try:
        await crawl_full(
            ref,
            settings=settings,
            creator_id=creator_id,
            run_id=run_id,
        )
    except asyncio.CancelledError:
        log.info("crawl for creator %s cancelled", creator_id)
        raise
    except Exception:  # pragma: no cover - crawl_full logs its own failures
        log.exception("background crawl for creator %s failed", creator_id)


def start_crawl(
    ref: CumstRef,
    *,
    settings: Settings | None = None,
    creator_id: int,
    run_id: int,
) -> bool:
    """Start a crawl unless one is already active for this creator."""
    current = _active.get(creator_id)
    if current is not None and not current.done():
        return False

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No event loop (for example a script calling the route directly).
        log.warning("cannot start crawl for creator %s without a running loop", creator_id)
        return False

    task = loop.create_task(
        crawl_creator(
            ref,
            settings=settings or get_settings(),
            creator_id=creator_id,
            run_id=run_id,
        )
    )
    _active[creator_id] = task
    _active_runs[creator_id] = run_id
    _run_tasks[run_id] = task

    def _finished(done: asyncio.Task[Any]) -> None:
        if _active.get(creator_id) is done:
            _active.pop(creator_id, None)
        if _active_runs.get(creator_id) == run_id:
            _active_runs.pop(creator_id, None)
        if _run_tasks.get(run_id) is done:
            _run_tasks.pop(run_id, None)

    task.add_done_callback(_finished)
    return True


def active_run_id(creator_id: int) -> int | None:
    return _active_runs.get(creator_id)


def cancel_creator(creator_id: int) -> bool:
    task = _active.get(creator_id)
    if task is not None and not task.done():
        task.cancel()
        return True
    return False


def cancel_run(run_id: int) -> bool:
    """Cooperatively cancel the task behind a run id, if this process owns it."""
    task = _run_tasks.get(run_id)
    if task is not None and not task.done():
        task.cancel()
        return True
    return False


def is_active(creator_id: int) -> bool:
    task = _active.get(creator_id)
    return task is not None and not task.done()
