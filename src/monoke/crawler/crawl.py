"""Crawl orchestration: page walk, detail fan-out, persistence, CLI."""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import logging
import math
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .. import db
from ..config import Settings, get_settings
from ..cumst.client import ApiBlocked, ApiRateLimited, CumstClient
from ..cumst.media import creator_images
from ..cumst.urls import CumstRef
from ..extract.post import extract_post
from .events import bus

log = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int, float, str | None], Any]


@dataclass(slots=True)
class RunResult:
    status: str = "done"
    pages: int = 0
    posts_seen: int = 0
    posts_done: int = 0
    posts_new: int = 0
    posts_updated: int = 0
    links_found: int = 0
    files_count: int = 0
    duration_s: float = 0.0
    warnings: list[str] = field(default_factory=list)
    errors: list[dict[str, str]] = field(default_factory=list)
    error: str | None = None
    blocked: bool = False
    run_id: int | None = None
    creator_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "pages": self.pages,
            "posts_seen": self.posts_seen,
            "posts_done": self.posts_done,
            "posts_new": self.posts_new,
            "posts_updated": self.posts_updated,
            "links_found": self.links_found,
            "files_count": self.files_count,
            "duration_s": self.duration_s,
            "warnings": self.warnings,
            "errors": self.errors,
            "error": self.error,
            "blocked": self.blocked,
            "run_id": self.run_id,
            "creator_id": self.creator_id,
        }

    def report(self) -> str:
        return (
            f"{self.posts_done}/{self.posts_seen} posts · {self.links_found} links · "
            f"{self.files_count} files · {self.duration_s:.1f}s "
            f"({self.pages} pages, {len(self.errors)} errors)"
        )


async def _notify(callback: ProgressCallback | None, *args: Any) -> None:
    if callback is None:
        return
    outcome = callback(*args)
    if inspect.isawaitable(outcome):
        await outcome


async def crawl_full(
    ref: CumstRef,
    *,
    settings: Settings | None = None,
    conn=None,
    client: CumstClient | None = None,
    concurrency: int = 4,
    progress: ProgressCallback | None = None,
    full: bool = False,
    limit: int | None = None,
    fresh: bool = False,
) -> RunResult:
    """Crawl one creator from URL ref to a complete archive."""
    settings = settings or get_settings()
    started = time.perf_counter()
    owns_conn = conn is None
    owns_client = client is None

    conn = conn or db.ensure_db(settings)
    client = client or CumstClient(
        settings, conn, concurrency=concurrency, fresh=fresh
    )
    result = RunResult()
    run_id: int | None = None
    creator_id: int | None = None
    pending: list[str] = []

    try:
        with conn:
            creator_id = db.upsert_creator(
                conn, service=ref.service, service_id=ref.creator_id
            )
            db.cancel_stale_runs(conn, creator_id)
            run_id = db.create_run(conn, creator_id, "full")
        result.creator_id = creator_id
        result.run_id = run_id
        bus.publish(
            run_id,
            {
                "type": "started",
                "phase": "profile",
                "service": ref.service,
                "creator": ref.creator_id,
            },
        )

        profile = await client.profile(ref)
        images = creator_images(profile, img_base=settings.img_base)
        with conn:
            db.upsert_creator(
                conn,
                service=profile.service,
                service_id=profile.id,
                name=profile.name,
                display_name=profile.display_name,
                avatar_url=images["avatar_url"],
                header_url=images["header_url"],
                indexed_at=profile.indexed,
                updated_at=profile.updated,
                content_updated_at=profile.content_updated,
                bookmarked=profile.bookmarked,
            )

        page_size = client.settings.page_size
        ids: list[str] = []
        seen: set[str] = set()
        totals: list[int] = []
        offset = 0
        pages = 0

        while True:
            page = await client.posts_page(ref, offset)
            pages += 1
            totals.append(page.total)
            bus.publish(
                run_id,
                {
                    "type": "page",
                    "phase": "posts",
                    "page": pages,
                    "pages": math.ceil(page.total / page_size) if page.total else 0,
                    "done": len(ids),
                    "total": page.total,
                },
            )

            if not page.posts:
                break

            for summary in page.posts:
                if summary.id not in seen:
                    seen.add(summary.id)
                    ids.append(summary.id)

            if len(page.posts) < page_size:
                break

            offset += page_size
            if pages > 10_000:  # defensive: a server bug must not loop forever
                result.warnings.append("list walk stopped after 10000 pages")
                break

        result.pages = pages
        result.posts_seen = len(ids)
        total = totals[-1] if totals else 0
        if len(set(totals)) > 1:
            result.warnings.append(
                f"total changed between pages: {sorted(set(totals))}"
            )
        if total and len(ids) != total:
            result.warnings.append(
                f"list walk returned {len(ids)} posts but total={total}"
            )
            bus.publish(
                run_id,
                {
                    "type": "warning",
                    "phase": "posts",
                    "message": result.warnings[-1],
                },
            )

        status_map = db.post_status_map(conn, creator_id)
        if full:
            pending = ids
        else:
            pending = [post_id for post_id in ids if not status_map.get(post_id)]
        if limit is not None:
            pending = pending[: max(0, limit)]

        await _fan_out(
            ref,
            pending,
            client=client,
            conn=conn,
            creator_id=creator_id,
            concurrency=concurrency,
            progress=progress,
            result=result,
            run_id=run_id,
        )

        with conn:
            db.upsert_creator(
                conn,
                service=ref.service,
                service_id=ref.creator_id,
                post_count=total or len(ids),
                last_synced_at=db.now_iso(),
            )

        if pending and len(result.errors) * 2 > len(pending):
            result.status = "failed"
            result.error = (
                f"{len(result.errors)} of {len(pending)} post details failed"
            )
        else:
            result.status = "done"

    except asyncio.CancelledError:
        result.status = "cancelled"
        result.error = "cancelled"
        raise
    except (ApiBlocked, ApiRateLimited) as exc:
        result.status = "failed"
        result.blocked = True
        result.error = str(exc)
        log.error("crawl blocked: %s", exc)
    except Exception as exc:  # never leak a traceback through the CLI
        result.status = "failed"
        result.error = f"{type(exc).__name__}: {exc}"
        log.exception("crawl failed")
    finally:
        result.duration_s = round(time.perf_counter() - started, 3)
        if run_id is not None:
            try:
                with conn:
                    db.finish_run(
                        conn,
                        run_id,
                        status=result.status,
                        pages=result.pages,
                        posts_seen=result.posts_seen,
                        posts_done=result.posts_done,
                        posts_new=result.posts_new,
                        posts_updated=result.posts_updated,
                        links_found=result.links_found,
                        files_count=result.files_count,
                        error=result.error,
                    )
            except Exception:
                log.exception("could not finish run %s", run_id)
            bus.publish(
                run_id,
                {
                    "type": "done" if result.status in {"done", "cancelled"} else "error",
                    "status": result.status,
                    "result": result.to_dict(),
                },
            )
        if owns_client:
            await client.aclose()
        if owns_conn:
            conn.close()

    return result


async def _fan_out(
    ref: CumstRef,
    pending: list[str],
    *,
    client: CumstClient,
    conn,
    creator_id: int,
    concurrency: int,
    progress: ProgressCallback | None,
    result: RunResult,
    run_id: int,
) -> None:
    if not pending:
        return

    queue: asyncio.Queue[str] = asyncio.Queue()
    for post_id in pending:
        queue.put_nowait(post_id)

    total = len(pending)
    done = 0
    started = time.perf_counter()
    lock = asyncio.Lock()

    async def worker() -> None:
        nonlocal done
        while True:
            try:
                post_id = queue.get_nowait()
            except asyncio.QueueEmpty:
                return

            try:
                detail = await client.post(ref, post_id)
                extracted = extract_post(detail)
                _, created = db.save_post_bundle(
                    conn,
                    creator_id=creator_id,
                    service=detail.service,
                    service_id=detail.id,
                    title=detail.title,
                    caption_html=extracted.caption_html,
                    caption_text=extracted.caption_text,
                    published=detail.published,
                    added=detail.added,
                    prev_id=detail.prev.id if detail.prev else None,
                    next_id=detail.next.id if detail.next else None,
                    revisions_count=len(detail.revisions),
                    linked_count=len(detail.linked),
                    raw_json=detail.raw,
                    fetched_at=detail.fetched_at or db.now_iso(),
                    files=extracted.files,
                    links=extracted.links,
                    tags=extracted.tags,
                )
            except (asyncio.CancelledError, ApiBlocked, ApiRateLimited):
                raise
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                async with lock:
                    result.errors.append({"post_id": post_id, "error": message})
                bus.publish(
                    run_id,
                    {
                        "type": "post_failed",
                        "phase": "posts",
                        "post_id": post_id,
                        "error": message,
                    },
                )
                continue

            async with lock:
                done += 1
                result.posts_done = done
                result.posts_new += int(created)
                result.posts_updated += int(not created)
                result.links_found += len(extracted.links)
                result.files_count += len(extracted.files)
                for warning in extracted.warnings:
                    result.warnings.append(f"{post_id}: {warning}")
                links_so_far = result.links_found

            for warning in extracted.warnings:
                bus.publish(
                    run_id,
                    {
                        "type": "warning",
                        "phase": "posts",
                        "post_id": post_id,
                        "message": warning,
                    },
                )

            rate = done / max(0.001, time.perf_counter() - started)
            await _notify(
                progress, done, total, rate, detail.title or f"Post {post_id}"
            )
            bus.publish(
                run_id,
                {
                    "type": "progress",
                    "phase": "posts",
                    "done": done,
                    "total": total,
                    "links": links_so_far,
                },
            )

    worker_count = max(1, min(concurrency, total))
    tasks = [asyncio.create_task(worker()) for _ in range(worker_count)]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _stderr_progress(done: int, total: int, rate: float, title: str | None) -> None:
    step = max(1, total // 10)
    if done == 1 or done == total or done % step == 0:
        print(
            f"  {done}/{total} posts · {rate:.1f}/s · {title or ''}",
            file=sys.stderr,
        )


def run_sync(args: argparse.Namespace) -> int:
    """Implementation of ``monoke sync``."""
    from ..cumst.urls import UrlError, parse_url

    logging.basicConfig(
        level=logging.WARNING,
        stream=sys.stderr,
        format="%(message)s",
    )

    try:
        ref = parse_url(args.url)
    except UrlError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.concurrency is not None and args.concurrency < 1:
        print("error: --concurrency must be at least 1", file=sys.stderr)
        return 2
    if args.limit is not None and args.limit < 0:
        print("error: --limit cannot be negative", file=sys.stderr)
        return 2

    settings = get_settings()
    result = None
    try:
        result = asyncio.run(
            crawl_full(
                ref,
                settings=settings,
                concurrency=args.concurrency or settings.concurrency,
                progress=_stderr_progress,
                full=bool(args.full or args.deep),
                limit=args.limit,
                fresh=bool(args.no_cache),
            )
        )
    except KeyboardInterrupt:
        print("cancelled", file=sys.stderr)
        return 130

    assert result is not None

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    elif result.status == "done":
        print(result.report())

    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if result.error:
        print(f"error: {result.error}", file=sys.stderr)
    if result.status == "done" and result.errors:
        print(f"{len(result.errors)} posts failed; re-run to retry", file=sys.stderr)

    if result.status == "done":
        return 0
    if result.blocked:
        return 3
    if result.status == "cancelled":
        return 130
    return 1
