"""Resolve shortener links by following redirects, with a persistent cache.

Only rows classified as ``shortener`` are touched. A successful target is
cached forever; a failed attempt is cached for ``resolution_ttl_days`` (30 by
default) so a broken shortener is not retried on every crawl. Resolution is
best-effort: any failure leaves the link as ``shortener`` and never fails the
post or the run.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from .. import db
from ..config import Settings, get_settings
from .classify import Category, classify

log = logging.getLogger(__name__)

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
MAX_HOPS = 5
DEFAULT_TIMEOUT = 8.0
DEFAULT_BUDGET = 100
DEFAULT_CONCURRENCY = 2
DEFAULT_TTL_DAYS = 30


def _cache_key(row: dict[str, Any]) -> str:
    return str(row.get("normalized_url") or row.get("url") or "")


def _is_recent(resolved_at: str | None, ttl_days: int) -> bool:
    if not resolved_at:
        return False
    try:
        stamp = datetime.fromisoformat(resolved_at.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return datetime.now(UTC) - stamp < timedelta(days=ttl_days)


def _apply(row: dict[str, Any], final_url: str) -> None:
    row["resolved_url"] = final_url
    row["category"] = classify(final_url).value


class ShortenerResolver:
    """Resolve shortener rows with a global concurrency and per-run budget."""

    def __init__(
        self,
        settings: Settings | None = None,
        conn=None,
        *,
        budget: int | None = None,
        concurrency: int | None = None,
        timeout: float | None = None,
        ttl_days: int | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.conn = conn
        configured_budget = getattr(self.settings, "resolve_budget", DEFAULT_BUDGET)
        configured_concurrency = getattr(
            self.settings, "resolve_concurrency", DEFAULT_CONCURRENCY
        )
        configured_timeout = getattr(self.settings, "resolve_timeout", DEFAULT_TIMEOUT)
        configured_ttl = getattr(
            self.settings, "resolution_ttl_days", DEFAULT_TTL_DAYS
        )
        self.budget = max(0, budget if budget is not None else configured_budget)
        self.remaining = self.budget
        self.concurrency = max(
            1, concurrency if concurrency is not None else configured_concurrency
        )
        self.timeout = (
            configured_timeout if timeout is None else timeout
        )
        self.ttl_days = max(
            1, ttl_days if ttl_days is not None else configured_ttl
        )

        self.resolved = 0
        self.failed = 0
        self.cached = 0
        self.deferred = 0

        self._semaphore = asyncio.Semaphore(self.concurrency)
        self._budget_lock = asyncio.Lock()
        self._inflight_lock = asyncio.Lock()
        self._inflight: dict[str, asyncio.Future[str | None]] = {}
        self._owns_client = client is None
        self._client = client

    async def __aenter__(self) -> "ShortenerResolver":
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                follow_redirects=True,
                max_redirects=MAX_HOPS,
                timeout=httpx.Timeout(self.timeout),
                headers={
                    "User-Agent": BROWSER_USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                },
            )
        return self._client

    async def _take_budget(self) -> bool:
        async with self._budget_lock:
            if self.remaining <= 0:
                return False
            self.remaining -= 1
            return True

    def _cached_result(self, key: str) -> dict[str, Any] | None:
        if self.conn is None or not key:
            return None
        return db.get_resolution(self.conn, key)

    def _remember(self, key: str, final_url: str | None) -> None:
        if self.conn is None or not key:
            return
        with self.conn:
            db.put_resolution(self.conn, key, final_url)

    async def resolve_rows(self, rows: Sequence[dict[str, Any]]) -> None:
        """Resolve every shortener row in place; cache hits do not use the budget."""
        targets = [
            row
            for row in rows
            if row.get("category") == Category.SHORTENER.value
            and not row.get("resolved_url")
        ]
        if not targets:
            return
        await asyncio.gather(
            *(self.resolve_row(row) for row in targets), return_exceptions=True
        )

    async def resolve_row(self, row: dict[str, Any]) -> None:
        if (
            row.get("category") != Category.SHORTENER.value
            or row.get("resolved_url")
        ):
            return
        key = _cache_key(row)
        cached = self._cached_result(key)
        if cached is not None:
            final_url = cached.get("final_url")
            if final_url:
                _apply(row, str(final_url))
                self.cached += 1
                return
            if _is_recent(cached.get("resolved_at"), self.ttl_days):
                self.cached += 1
                return

        # Deduplicate in-flight work so two posts never resolve the same URL at
        # the same time, even across creators.
        async with self._inflight_lock:
            future = self._inflight.get(key)
            owner = future is None
            if future is None:
                if not await self._take_budget():
                    self.deferred += 1
                    return
                future = asyncio.get_running_loop().create_future()
                self._inflight[key] = future

        final_url: str | None = None
        if owner:
            url = str(row.get("url") or key)
            try:
                async with self._semaphore:
                    final_url = await self._resolve(url)
                self._remember(key, final_url)
            finally:
                self._inflight.pop(key, None)
                if not future.done():
                    future.set_result(final_url)
        else:
            final_url = await future
            self.cached += 1

        if final_url:
            _apply(row, final_url)
            if owner:
                self.resolved += 1
        elif owner:
            self.failed += 1

    async def _resolve(self, url: str) -> str | None:
        if not url:
            return None
        client = self._http()

        try:
            response = await client.head(url, follow_redirects=True)
        except httpx.TooManyRedirects as exc:
            log.info("shortener HEAD redirect loop for %s: %s", url, exc)
            return None
        except Exception as exc:  # noqa: BLE001 - best-effort resolution
            log.debug("shortener HEAD unusable for %s: %s; trying GET", url, exc)
            response = None

        if (
            response is None
            or response.status_code >= 400
            or self._still_shortener(str(response.url))
        ):
            try:
                response = await client.get(url, follow_redirects=True)
            except Exception as exc:  # noqa: BLE001 - best-effort resolution
                log.info("shortener GET failed for %s: %s", url, exc)
                return None

        if response.status_code >= 400:
            log.info("shortener %s returned %d", url, response.status_code)
            return None

        final_url = str(response.url)
        if self._still_shortener(final_url):
            log.info("shortener %s did not leave a shortener host", url)
            return None
        return final_url

    @staticmethod
    def _still_shortener(url: str) -> bool:
        return classify(url) is Category.SHORTENER


async def resolve_links(
    rows: Sequence[dict[str, Any]],
    *,
    conn=None,
    settings: Settings | None = None,
    **kwargs: Any,
) -> int:
    """Convenience one-shot resolver. Returns the number of deferred links."""
    async with ShortenerResolver(settings=settings, conn=conn, **kwargs) as resolver:
        await resolver.resolve_rows(rows)
        return resolver.deferred


async def resolve_url(
    url: str,
    *,
    settings: Settings | None = None,
    timeout: float | None = None,
    client: httpx.AsyncClient | None = None,
) -> str | None:
    """Resolve one URL without caching; useful for a one-off lookup."""
    async with ShortenerResolver(
        settings=settings, timeout=timeout, client=client
    ) as resolver:
        return await resolver._resolve(url)


resolve_shortener = resolve_url
