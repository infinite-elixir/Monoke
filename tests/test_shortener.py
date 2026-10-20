from __future__ import annotations

from types import SimpleNamespace

import httpx
import respx

from monoke import db
from monoke.crawler.crawl import crawl_full
from monoke.cumst.urls import parse_url
from monoke.extract.classify import Category
from monoke.extract.shortener import ShortenerResolver, resolve_links
from monoke.models import CreatorProfile, PostDetail, PostsPage, PostSummary


def link_row(url: str) -> dict[str, object]:
    from monoke.extract.classify import normalize

    return {
        "url": url,
        "normalized_url": normalize(url),
        "label": "link",
        "category": Category.SHORTENER.value,
        "origin": "caption",
        "resolved_url": None,
    }


def head(url: str, *, location: str | None = None, status: int = 200):
    headers = {"location": location} if location else {}
    return respx.head(url).mock(return_value=httpx.Response(status, headers=headers))


@respx.mock
async def test_direct_301_becomes_mega(conn, settings):
    short = "http://bit.ly/49A802v"
    target = "https://mega.nz/file/abc"
    head(short, location=target, status=301)
    head(target)
    row = link_row(short)

    async with ShortenerResolver(settings, conn) as resolver:
        await resolver.resolve_rows([row])

    assert row["url"] == short
    assert row["resolved_url"] == target
    assert row["category"] == Category.MEGA.value
    cached = db.get_resolution(conn, row["normalized_url"])
    assert cached is not None
    assert cached["final_url"] == target


@respx.mock
async def test_two_hop_chain(conn, settings):
    short = "http://bit.ly/chain"
    middle = "https://tinyurl.com/step"
    target = "https://drive.google.com/file/d/ID/view"
    head(short, location=middle, status=301)
    head(middle, location=target, status=302)
    head(target)
    row = link_row(short)

    async with ShortenerResolver(settings, conn) as resolver:
        await resolver.resolve_rows([row])

    assert row["resolved_url"] == target
    assert row["category"] == Category.GOOGLE_DRIVE.value


@respx.mock
async def test_redirect_loop_is_a_cached_failure(conn, settings):
    first = "https://bit.ly/loop-a"
    second = "https://bit.ly/loop-b"
    head(first, location=second, status=301)
    head(second, location=first, status=301)
    row = link_row(first)

    async with ShortenerResolver(settings, conn) as resolver:
        await resolver.resolve_rows([row])

    assert row["category"] == Category.SHORTENER.value
    assert row["resolved_url"] is None
    cached = db.get_resolution(conn, row["normalized_url"])
    assert cached is not None
    assert cached["final_url"] is None


@respx.mock
async def test_timeout_is_a_cached_failure(conn, settings):
    short = "https://bit.ly/slow"
    respx.head(short).mock(side_effect=httpx.ConnectTimeout("slow"))
    respx.get(short).mock(side_effect=httpx.ConnectTimeout("slow"))
    row = link_row(short)

    async with ShortenerResolver(settings, conn) as resolver:
        await resolver.resolve_rows([row])

    assert row["category"] == Category.SHORTENER.value
    assert row["resolved_url"] is None
    assert db.get_resolution(conn, row["normalized_url"]) is not None


@respx.mock
async def test_dead_shortener_does_not_raise(conn, settings):
    short = "https://bit.ly/gone"
    head(short, status=404)
    respx.get(short).mock(return_value=httpx.Response(404))
    row = link_row(short)

    async with ShortenerResolver(settings, conn) as resolver:
        await resolver.resolve_rows([row])

    assert row["category"] == Category.SHORTENER.value
    assert row["resolved_url"] is None


@respx.mock
async def test_successful_cache_hit_makes_no_request(conn, settings):
    short = "http://bit.ly/49A802v"
    target = "https://mega.nz/file/abc"
    row = link_row(short)
    db.put_resolution(conn, row["normalized_url"], target)

    async with ShortenerResolver(settings, conn) as resolver:
        await resolver.resolve_rows([row])

    assert row["resolved_url"] == target
    assert row["category"] == Category.MEGA.value
    assert resolver.cached == 1
    assert resolver.remaining == resolver.budget
    assert not respx.calls


@respx.mock
async def test_recent_failure_is_not_retried(conn, settings):
    short = "https://bit.ly/broken"
    row = link_row(short)
    db.put_resolution(conn, row["normalized_url"], None)

    async with ShortenerResolver(settings, conn) as resolver:
        await resolver.resolve_rows([row])

    assert row["category"] == Category.SHORTENER.value
    assert resolver.cached == 1
    assert not respx.calls


@respx.mock
async def test_stale_failure_is_retried(conn, settings):
    short = "https://bit.ly/stale"
    target = "https://mega.nz/file/new"
    row = link_row(short)
    db.put_resolution(conn, row["normalized_url"], None)
    with conn:
        conn.execute(
            "UPDATE link_resolutions SET resolved_at = ? WHERE url = ?",
            ("2020-01-01T00:00:00+00:00", row["normalized_url"]),
        )
    head(short, location=target, status=301)
    head(target)

    async with ShortenerResolver(settings, conn) as resolver:
        await resolver.resolve_rows([row])

    assert row["resolved_url"] == target
    assert row["category"] == Category.MEGA.value


@respx.mock
async def test_same_shortener_is_resolved_once(conn, settings):
    short = "https://bit.ly/same"
    target = "https://mega.nz/file/same"
    short_route = head(short, location=target, status=301)
    head(target)
    rows = [link_row(short), link_row(short)]

    async with ShortenerResolver(settings, conn, budget=10, concurrency=2) as resolver:
        await resolver.resolve_rows(rows)

    assert all(row["resolved_url"] == target for row in rows)
    assert short_route.call_count == 1
    assert resolver.remaining == 9


@respx.mock
async def test_budget_defers_extra_links(conn, settings):
    first = "https://bit.ly/one"
    second = "https://bit.ly/two"
    target = "https://mega.nz/file/one"
    head(first, location=target, status=301)
    head(target)
    head(second, location="https://mega.nz/file/two", status=301)
    head("https://mega.nz/file/two")
    rows = [link_row(first), link_row(second)]

    async with ShortenerResolver(settings, conn, budget=1) as resolver:
        await resolver.resolve_rows(rows)

    resolved = [row for row in rows if row["resolved_url"]]
    assert len(resolved) == 1
    assert resolved[0]["resolved_url"] in {target, "https://mega.nz/file/two"}
    assert all(row["category"] == Category.SHORTENER.value for row in rows if not row["resolved_url"])
    assert resolver.remaining == 0
    assert resolver.deferred == 1


async def test_resolve_links_convenience(conn, settings):
    short = "https://bit.ly/none"
    rows = [link_row(short)]
    deferred = await resolve_links(rows, conn=conn, settings=settings, budget=0)
    assert deferred == 1
    assert rows[0]["category"] == Category.SHORTENER.value


class _OnePostClient:
    def __init__(self, detail: PostDetail):
        self.detail = detail
        self.settings = SimpleNamespace(page_size=50)

    async def profile(self, ref):
        return CreatorProfile(id="274608", service="patreon", name="nyl2")

    async def posts_page(self, ref, offset=0, **kwargs):
        if offset:
            return PostsPage(total=1, posts=[])
        return PostsPage(
            total=1,
            posts=[PostSummary(id=self.detail.id, service=self.detail.service)],
        )

    async def post(self, ref, post_id=None):
        return self.detail


def _shortener_detail() -> PostDetail:
    caption = '<p><a href="http://bit.ly/49A802v">MEGA</a></p>'
    return PostDetail(
        id="1",
        service="patreon",
        caption=caption,
        caption_html=caption,
        added=1,
        published=1,
    )


@respx.mock
async def test_crawl_persists_resolved_link(conn, settings):
    head(
        "http://bit.ly/49A802v",
        location="https://mega.nz/file/abc",
        status=301,
    )
    head("https://mega.nz/file/abc")
    ref = parse_url("https://cum.st/creators/patreon/274608")

    result = await crawl_full(
        ref,
        settings=settings,
        conn=conn,
        client=_OnePostClient(_shortener_detail()),
    )

    assert result.status == "done"
    link = conn.execute("SELECT * FROM links").fetchone()
    assert link["url"] == "http://bit.ly/49A802v"
    assert link["resolved_url"] == "https://mega.nz/file/abc"
    assert link["category"] == Category.MEGA.value


@respx.mock
async def test_second_crawl_uses_the_cache(conn, settings):
    short_route = head(
        "http://bit.ly/49A802v",
        location="https://mega.nz/file/abc",
        status=301,
    )
    head("https://mega.nz/file/abc")
    ref = parse_url("https://cum.st/creators/patreon/274608")

    for full in (False, True):
        result = await crawl_full(
            ref,
            settings=settings,
            conn=conn,
            client=_OnePostClient(_shortener_detail()),
            full=full,
        )
        assert result.status == "done"

    assert short_route.call_count == 1


async def test_no_resolve_keeps_shortener_offline(conn, settings):
    ref = parse_url("https://cum.st/creators/patreon/274608")

    result = await crawl_full(
        ref,
        settings=settings,
        conn=conn,
        client=_OnePostClient(_shortener_detail()),
        resolve=False,
    )

    assert result.status == "done"
    link = conn.execute("SELECT * FROM links").fetchone()
    assert link["resolved_url"] is None
    assert link["category"] == Category.SHORTENER.value
    assert db.get_resolution(conn, "http://bit.ly/49A802v") is None
