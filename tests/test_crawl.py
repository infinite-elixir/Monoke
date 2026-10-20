from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from monoke import db
from monoke.config import Settings
from monoke.crawler.crawl import crawl_full
from monoke.crawler.events import bus
from monoke.cumst.client import ApiBlocked, ApiError
from monoke.cumst.urls import parse_url
from monoke.models import CreatorProfile, PostDetail, PostsPage, PostSummary

REF = parse_url("https://cum.st/creators/patreon/274608")
PROFILE = CreatorProfile(
    id="274608", service="patreon", name="nyl2", display_name="Nyl", updated=1792482836
)


def make_pages(ids: list[str], total: int | None = None, page_size: int = 50) -> dict:
    total = len(ids) if total is None else total
    pages = {}
    offset = 0
    while offset < len(ids):
        chunk = ids[offset : offset + page_size]
        pages[offset] = PostsPage(
            total=total,
            posts=[
                PostSummary(id=post_id, service="patreon", title=f"Post {post_id}")
                for post_id in chunk
            ],
        )
        offset += page_size
    pages[offset] = PostsPage(total=total, posts=[])
    return pages


def make_detail(post_id: str, caption: str = "<p>hello</p>") -> PostDetail:
    return PostDetail(
        id=post_id,
        service="patreon",
        title=f"Post {post_id}",
        caption=caption,
        caption_html=caption,
        published=1,
        added=1,
    )


class FakeClient:
    def __init__(self, pages, details, page_size: int = 50):
        self.settings = SimpleNamespace(page_size=page_size)
        self.pages = pages
        self.details = details
        self.list_offsets: list[int] = []
        self.detail_ids: list[str] = []

    async def profile(self, ref):
        return PROFILE

    async def posts_page(self, ref, offset=0, **kwargs):
        self.list_offsets.append(offset)
        return self.pages[offset]

    async def post(self, ref, post_id=None):
        self.detail_ids.append(post_id)
        outcome = self.details[post_id]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture(autouse=True)
def clean_bus():
    bus.clear()
    yield
    bus.clear()


async def crawl(settings, conn, client, **kwargs):
    return await crawl_full(REF, settings=settings, conn=conn, client=client, **kwargs)


async def test_page_walk_collects_782_ids_in_16_calls(settings, conn):
    ids = [str(100000 + index) for index in range(782)]
    client = FakeClient(make_pages(ids), {})

    result = await crawl(settings, conn, client, limit=0)

    assert result.pages == 16
    assert result.posts_seen == 782
    assert client.list_offsets == list(range(0, 782, 50))
    assert result.status == "done"
    creator = db.find_creator(conn, "patreon", "274608")
    assert creator is not None
    assert creator["post_count"] == 782
    assert creator["last_synced_at"]


async def test_empty_page_termination_and_total_mismatch(settings, conn):
    ids = [str(500000 + index) for index in range(100)]
    client = FakeClient(make_pages(ids, total=120), {})

    result = await crawl(settings, conn, client, limit=0)

    assert result.pages == 3
    assert result.posts_seen == 100
    assert client.list_offsets == [0, 50, 100]
    assert any("total=120" in warning for warning in result.warnings)


async def test_short_page_terminates_without_extra_call(settings, conn):
    ids = [str(600000 + index) for index in range(60)]
    client = FakeClient(make_pages(ids, total=999), {})

    result = await crawl(settings, conn, client, limit=0)

    assert result.pages == 2
    assert result.posts_seen == 60
    assert client.list_offsets == [0, 50]
    assert any("total=999" in warning for warning in result.warnings)


async def test_limit_writes_exactly_n_posts(settings, conn):
    ids = [str(700000 + index) for index in range(10)]
    client = FakeClient(make_pages(ids), {post_id: make_detail(post_id) for post_id in ids})

    result = await crawl(settings, conn, client, limit=5)

    assert result.posts_done == 5
    assert result.posts_new == 5
    assert len(client.detail_ids) == 5
    assert conn.execute("SELECT count(*) AS n FROM posts").fetchone()["n"] == 5


async def test_rerun_is_idempotent(settings, conn, fixture):
    ids = ["172189145"]
    detail = PostDetail.model_validate(fixture("post_172189145.json"))
    client = FakeClient(make_pages(ids), {post_id: detail for post_id in ids})

    first = await crawl(settings, conn, client)
    assert first.posts_new == 1
    assert first.links_found >= 2
    assert first.files_count == 1

    detail_calls = len(client.detail_ids)
    second = await crawl(settings, conn, client)

    assert second.posts_new == 0
    assert second.posts_updated == 0
    assert len(client.detail_ids) == detail_calls

    assert conn.execute("SELECT count(*) AS n FROM posts").fetchone()["n"] == 1
    assert conn.execute("SELECT count(*) AS n FROM files").fetchone()["n"] == 1
    links = conn.execute("SELECT count(*) AS n FROM links").fetchone()["n"]
    assert links == first.links_found


async def test_resume_after_partial_run(settings, conn):
    ids = ["801", "802", "803"]
    client = FakeClient(make_pages(ids), {post_id: make_detail(post_id) for post_id in ids})

    first = await crawl(settings, conn, client, limit=2)
    assert first.posts_new == 2
    assert len(client.detail_ids) == 2

    second = await crawl(settings, conn, client)
    assert second.posts_new == 1
    assert client.detail_ids[2:] == ["803"]
    assert conn.execute("SELECT count(*) AS n FROM posts").fetchone()["n"] == 3


async def test_full_refetches_existing_posts(settings, conn):
    ids = ["901", "902"]
    client = FakeClient(make_pages(ids), {post_id: make_detail(post_id) for post_id in ids})

    first = await crawl(settings, conn, client)
    assert first.posts_new == 2

    result = await crawl(settings, conn, client, full=True)

    assert result.posts_new == 0
    assert result.posts_updated == 2
    assert sorted(client.detail_ids[2:]) == ids


async def test_post_failure_does_not_stop_the_rest(settings, conn):
    ids = ["911", "912", "913", "914", "915"]
    details = {post_id: make_detail(post_id) for post_id in ids}
    details["913"] = ValueError("boom")
    client = FakeClient(make_pages(ids), details)

    result = await crawl(settings, conn, client, concurrency=2)

    assert result.status == "done"
    assert result.posts_done == 4
    assert len(result.errors) == 1
    assert result.errors[0]["post_id"] == "913"
    assert "boom" in result.errors[0]["error"]
    assert conn.execute("SELECT count(*) AS n FROM posts").fetchone()["n"] == 4


async def test_majority_failure_marks_run_failed(settings, conn):
    ids = ["921", "922", "923"]
    details = {post_id: make_detail(post_id) for post_id in ids}
    details["921"] = ValueError("boom")
    details["922"] = ValueError("boom")
    client = FakeClient(make_pages(ids), details)

    result = await crawl(settings, conn, client)

    assert result.status == "failed"
    assert "2 of 3" in result.error


async def test_blocked_marks_run_failed(settings, conn):
    ids = ["931", "932"]
    details = {post_id: make_detail(post_id) for post_id in ids}
    details["931"] = ApiBlocked("challenge")
    client = FakeClient(make_pages(ids), details)

    result = await crawl(settings, conn, client, concurrency=1)

    assert result.status == "failed"
    assert result.blocked is True
    assert "challenge" in result.error
    run = db.list_runs(conn)[0]
    assert run["status"] == "failed"


async def test_profile_failure_marks_run_failed(settings, conn):
    class BrokenClient(FakeClient):
        async def profile(self, ref):
            raise ApiError("network down")

    client = BrokenClient({0: PostsPage(total=0, posts=[])}, {})
    result = await crawl(settings, conn, client)

    assert result.status == "failed"
    assert "network down" in result.error
    run = db.list_runs(conn)[0]
    assert run["status"] == "failed"


async def test_concurrency_is_capped(settings, conn):
    ids = [str(950 + index) for index in range(20)]

    class SlowClient(FakeClient):
        def __init__(self):
            super().__init__(
                make_pages(ids), {post_id: make_detail(post_id) for post_id in ids}
            )
            self.active = 0
            self.peak = 0

        async def post(self, ref, post_id=None):
            self.active += 1
            self.peak = max(self.peak, self.active)
            try:
                await asyncio.sleep(0.003)
                return await super().post(ref, post_id)
            finally:
                self.active -= 1

    client = SlowClient()
    result = await crawl(settings, conn, client, concurrency=3)

    assert result.posts_done == 20
    assert client.peak <= 3


async def test_progress_callback(settings, conn):
    ids = ["961", "962", "963"]
    client = FakeClient(make_pages(ids), {post_id: make_detail(post_id) for post_id in ids})
    seen: list[tuple[int, int, float, str | None]] = []

    def progress(done, total, rate, title):
        seen.append((done, total, rate, title))

    result = await crawl(settings, conn, client, progress=progress)

    assert result.posts_done == 3
    assert seen[-1][0] == 3 and seen[-1][1] == 3
    assert all(rate >= 0 and title for _, _, rate, title in seen)


async def test_cancellation_finishes_run_as_cancelled(settings, conn):
    ids = ["971"]
    gate = asyncio.Event()

    class BlockingClient(FakeClient):
        async def post(self, ref, post_id=None):
            gate.set()
            await asyncio.Event().wait()

    client = BlockingClient(make_pages(ids), {})
    task = asyncio.create_task(crawl(settings, conn, client, concurrency=1))
    await gate.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    run = db.list_runs(conn)[0]
    assert run["status"] == "cancelled"


async def test_no_orphan_links_or_files(settings, conn, fixture):
    ids = ["172189145"]
    detail = PostDetail.model_validate(fixture("post_172189145.json"))
    client = FakeClient(make_pages(ids), {post_id: detail for post_id in ids})

    await crawl(settings, conn, client)

    orphan_links = conn.execute(
        "SELECT count(*) AS n FROM links l LEFT JOIN posts p ON p.id = l.post_id WHERE p.id IS NULL"
    ).fetchone()["n"]
    orphan_files = conn.execute(
        "SELECT count(*) AS n FROM files f LEFT JOIN posts p ON p.id = f.post_id WHERE p.id IS NULL"
    ).fetchone()["n"]
    assert orphan_links == 0
    assert orphan_files == 0


async def test_stale_running_run_is_cancelled(settings, conn):
    with conn:
        creator_id = db.upsert_creator(conn, service="patreon", service_id="274608")
        stale_id = db.create_run(conn, creator_id, "full")

    client = FakeClient(
        make_pages(["990"]), {"990": make_detail("990")}
    )
    result = await crawl(settings, conn, client)

    stale = db.get_run(conn, stale_id)
    assert stale is not None
    assert stale["status"] == "cancelled"
    assert stale["error"] == "interrupted"
    assert result.status == "done"


async def test_run_events_are_published(settings, conn):
    ids = ["981", "982"]
    client = FakeClient(make_pages(ids), {post_id: make_detail(post_id) for post_id in ids})

    result = await crawl(settings, conn, client)

    stream = bus.subscribe(result.run_id)
    events = []
    try:
        async for event in stream:
            events.append(event)
            if event["type"] in {"done", "error"}:
                break
    finally:
        await stream.aclose()

    types = [event["type"] for event in events]
    assert "started" in types
    assert "page" in types
    assert "progress" in types
    assert types[-1] == "done"
