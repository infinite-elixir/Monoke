"""Async HTTP client for the cum.st JSON API.

Cache-first: responses are stored in ``api_cache`` and replayed unless the
caller asks for ``fresh=True``. Retries are limited to transient failures
(429, 5xx, timeouts) and every outbound request passes through one global
semaphore.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import urlencode

import httpx

from .. import db
from ..config import Settings, get_settings
from ..models import CreatorProfile, PostDetail, PostsPage
from .urls import CumstRef, post_path, posts_path, profile_path

log = logging.getLogger(__name__)

BACKOFF_SECONDS = (0.5, 1.5, 4.5)
MAX_ATTEMPTS = 3
SNIPPET_LIMIT = 200
ALLOWED_SORTS = frozenset({"recent", "oldest", "top"})


class ApiError(Exception):
    """Base class for every failure while talking to cum.st."""

    def __init__(self, message: str, *, status: int | None = None, snippet: str = ""):
        super().__init__(message)
        self.status = status
        self.snippet = snippet[:SNIPPET_LIMIT]


class ApiNotFound(ApiError):
    """The creator, post, or endpoint does not exist (404)."""


class ApiServerError(ApiError):
    """429 or 5xx after all retries."""


class ApiTimeout(ApiError):
    """The request timed out after all retries."""


class ApiDecode(ApiError):
    """A 2xx response that was not the JSON we expected."""


class ApiBlocked(ApiError):
    """An HTML/JS challenge (DDOS-Guard) instead of JSON."""


class CumstClient:
    def __init__(
        self,
        settings: Settings | None = None,
        conn=None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        concurrency: int | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        jitter: tuple[float, float] = (0.1, 0.3),
        backoff: tuple[float, ...] = BACKOFF_SECONDS,
    ) -> None:
        self.settings = settings or get_settings()
        self.conn = conn
        self._sleep = sleep or asyncio.sleep
        self._jitter = jitter
        self._backoff = backoff
        self._semaphore = asyncio.Semaphore(concurrency or self.settings.concurrency)
        self._client = httpx.AsyncClient(
            base_url=self.settings.api_base.rstrip("/"),
            headers={
                "User-Agent": self.settings.user_agent,
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(self.settings.request_timeout),
            follow_redirects=False,
            transport=transport,
        )

    async def __aenter__(self) -> "CumstClient":
        return self

    async def __aexit__(self, *exc_info) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ #
    # transport
    # ------------------------------------------------------------------ #

    def _build_url(self, path: str, params: Mapping[str, Any] | None) -> str:
        base = self.settings.api_base.rstrip("/")
        url = f"{base}/{path.lstrip('/')}"
        if params:
            pairs = sorted(
                (key, value)
                for key, value in params.items()
                if value is not None and value != ""
            )
            if pairs:
                url = f"{url}?{urlencode(pairs)}"
        return url

    async def get_json(
        self,
        path: str,
        params: Mapping[str, Any] | None = None,
        *,
        fresh: bool = False,
    ) -> tuple[Any, str, bool]:
        """Fetch JSON, using the SQLite cache unless ``fresh`` is true.

        Returns ``(data, fetched_at, from_cache)``.
        """
        url = self._build_url(path, params)

        if not fresh and self.conn is not None:
            cached = db.cache_get(self.conn, url)
            if cached is not None:
                try:
                    data = json.loads(cached["body"])
                except (TypeError, ValueError):
                    log.warning("discarding corrupt cache entry for %s", url)
                else:
                    log.info("GET %s -> cache hit (fetched %s)", url, cached["fetched_at"])
                    return data, cached["fetched_at"], True

        text = await self._request(url)
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise ApiDecode(
                f"cum.st returned invalid JSON for {path}",
                snippet=text,
            ) from exc

        if self.conn is not None:
            db.cache_put(self.conn, url, text)
            row = db.cache_get(self.conn, url)
            fetched_at = row["fetched_at"] if row else db.now_iso()
        else:
            fetched_at = db.now_iso()

        return data, fetched_at, False

    async def _request(self, url: str) -> str:
        last_error: ApiError | None = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            await self._sleep(random.uniform(*self._jitter))

            started = time.perf_counter()
            try:
                async with self._semaphore:
                    response = await self._client.get(url)
            except httpx.TimeoutException:
                last_error = ApiTimeout(f"timed out requesting {url}")
                log.warning("GET %s -> timeout (attempt %d)", url, attempt)
            except httpx.TransportError as exc:
                last_error = ApiError(f"network error requesting {url}: {exc}")
                log.warning("GET %s -> transport error (attempt %d): %s", url, attempt, exc)
            else:
                elapsed_ms = (time.perf_counter() - started) * 1000
                status = response.status_code
                content_type = response.headers.get("content-type", "")

                if status == 404:
                    log.info("GET %s -> 404 in %.0fms", url, elapsed_ms)
                    raise ApiNotFound(f"cum.st has no such resource: {url}", status=status)

                if status == 403 or "text/html" in content_type.lower():
                    log.warning("GET %s -> %d %s (blocked)", url, status, content_type)
                    raise ApiBlocked(
                        "cum.st returned an HTML challenge instead of JSON "
                        "(DDOS-Guard); try again later.",
                        status=status,
                        snippet=response.text,
                    )

                if status == 429 or 500 <= status < 600:
                    last_error = ApiServerError(
                        f"cum.st returned HTTP {status} for {url}",
                        status=status,
                        snippet=response.text,
                    )
                    log.warning("GET %s -> %d in %.0fms (attempt %d)", url, status, elapsed_ms, attempt)
                elif status >= 400:
                    raise ApiError(
                        f"cum.st returned HTTP {status} for {url}",
                        status=status,
                        snippet=response.text,
                    )
                elif "json" not in content_type.lower():
                    raise ApiDecode(
                        f"cum.st returned {content_type or 'no content type'} instead of JSON",
                        status=status,
                        snippet=response.text,
                    )
                else:
                    log.info("GET %s -> %d in %.0fms", url, status, elapsed_ms)
                    return response.text

            if attempt < MAX_ATTEMPTS:
                delay = self._backoff[min(attempt - 1, len(self._backoff) - 1)]
                await self._sleep(delay * random.uniform(0.8, 1.2))

        assert last_error is not None
        raise last_error

    # ------------------------------------------------------------------ #
    # endpoints
    # ------------------------------------------------------------------ #

    @staticmethod
    def _attach(model, text: str, fetched_at: str, from_cache: bool):
        model.raw = text
        model.fetched_at = fetched_at
        model.from_cache = from_cache
        return model

    async def profile(self, ref: CumstRef) -> CreatorProfile:
        data, fetched_at, from_cache = await self.get_json(profile_path(ref))
        profile = CreatorProfile.model_validate(data)
        return self._attach(profile, json.dumps(data, ensure_ascii=False), fetched_at, from_cache)

    async def posts_page(
        self,
        ref: CumstRef,
        offset: int = 0,
        *,
        sort: str = "recent",
        q: str | None = None,
        tag: str | None = None,
    ) -> PostsPage:
        if sort not in ALLOWED_SORTS:
            raise ValueError(f"sort must be one of {sorted(ALLOWED_SORTS)}, got {sort!r}")

        params: dict[str, Any] = {
            "o": offset,
            "n": self.settings.page_size,
            "compact": 1,
            "sort": sort,
        }
        if q:
            params["q"] = q
        if tag:
            params["tag"] = tag

        data, fetched_at, from_cache = await self.get_json(posts_path(ref), params)
        page = PostsPage.model_validate(data)

        if len(page.posts) > self.settings.page_size:
            raise ApiError(
                f"server returned {len(page.posts)} posts, expected <= {self.settings.page_size}"
            )

        return self._attach(page, json.dumps(data, ensure_ascii=False), fetched_at, from_cache)

    async def post(self, ref: CumstRef, post_id: str | None = None) -> PostDetail:
        pid = post_id or ref.post_id
        if not pid:
            raise ValueError("post() needs a post id or a URL that points at a post")

        data, fetched_at, from_cache = await self.get_json(
            post_path(ref, pid),
            {"include": "linked,revisions"},
        )
        detail = PostDetail.model_validate(data)
        return self._attach(detail, json.dumps(data, ensure_ascii=False), fetched_at, from_cache)
