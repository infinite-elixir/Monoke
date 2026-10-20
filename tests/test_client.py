from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import respx

from monoke.config import Settings
from monoke.cumst.client import (
    ApiBlocked,
    ApiDecode,
    ApiNotFound,
    ApiRateLimited,
    ApiServerError,
    ApiTimeout,
    CumstClient,
)

BASE = "https://cum.st/api/v1"
POSTS_URL = f"{BASE}/patreon/user/274608/posts"
PROFILE_URL = f"{BASE}/patreon/user/274608/profile"


async def _no_sleep(_delay: float) -> None:
    return None


def _client(settings: Settings, conn=None, **kwargs) -> CumstClient:
    kwargs.setdefault("jitter", (0, 0))
    kwargs.setdefault("sleep", _no_sleep)
    kwargs.setdefault("min_interval", 0)
    return CumstClient(settings, conn, **kwargs)


async def test_second_call_is_served_from_cache(settings, conn, fixture):
    payload = fixture("posts_274608_o0.json")

    with respx.mock(assert_all_called=False) as router:
        route = router.get(POSTS_URL).mock(
            return_value=httpx.Response(200, json=payload)
        )

        async with _client(settings, conn) as client:
            data, fetched_at, from_cache = await client.get_json(
                "/patreon/user/274608/posts", {"o": 0, "n": 50}
            )
            second = await client.get_json(
                "/patreon/user/274608/posts", {"o": 0, "n": 50}
            )

        assert route.call_count == 1
        assert data["total"] == 782
        assert from_cache is False
        assert fetched_at
        assert second[2] is True
        assert second[0] == data


async def test_fresh_bypasses_the_cache(settings, conn, fixture):
    payload = fixture("posts_274608_o0.json")

    with respx.mock(assert_all_called=False) as router:
        route = router.get(POSTS_URL).mock(
            return_value=httpx.Response(200, json=payload)
        )

        async with _client(settings, conn) as client:
            await client.get_json("/patreon/user/274608/posts", {"o": 0, "n": 50})
            await client.get_json(
                "/patreon/user/274608/posts", {"o": 0, "n": 50}, fresh=True
            )

        assert route.call_count == 2


async def test_cache_key_ignores_param_order(settings, conn, fixture):
    payload = fixture("posts_274608_o780.json")

    with respx.mock(assert_all_called=False) as router:
        route = router.get(POSTS_URL).mock(
            return_value=httpx.Response(200, json=payload)
        )

        async with _client(settings, conn) as client:
            await client.get_json("/patreon/user/274608/posts", {"o": 780, "n": 50})
            await client.get_json("/patreon/user/274608/posts", {"n": 50, "o": 780})

        assert route.call_count == 1


async def test_500_then_200_retries_and_succeeds(settings, conn, fixture):
    payload = fixture("posts_274608_empty.json")

    with respx.mock(assert_all_called=False) as router:
        route = router.get(POSTS_URL).mock(
            side_effect=[
                httpx.Response(500, text="boom"),
                httpx.Response(502, text="bad gateway"),
                httpx.Response(200, json=payload),
            ]
        )

        async with _client(settings) as client:
            data, _, from_cache = await client.get_json(
                "/patreon/user/274608/posts", {"o": 782}
            )

        assert route.call_count == 3
        assert data["posts"] == []
        assert from_cache is False


async def test_500_exhausts_attempts(settings):
    with respx.mock(assert_all_called=False) as router:
        route = router.get(POSTS_URL).mock(return_value=httpx.Response(500, text="boom"))

        async with _client(settings) as client:
            with pytest.raises(ApiServerError) as excinfo:
                await client.get_json("/patreon/user/274608/posts", {"o": 0})

        assert route.call_count == 3
        assert excinfo.value.status == 500


async def test_429_honors_retry_after(settings, fixture):
    payload = fixture("posts_274608_empty.json")
    sleeps: list[float] = []

    async def record(delay: float) -> None:
        sleeps.append(delay)

    with respx.mock(assert_all_called=False) as router:
        route = router.get(POSTS_URL).mock(
            side_effect=[
                httpx.Response(
                    429,
                    headers={"retry-after": "7"},
                    json={"error": "slow down"},
                ),
                httpx.Response(200, json=payload),
            ]
        )

        async with CumstClient(
            settings, jitter=(0, 0), sleep=record, min_interval=0
        ) as client:
            data, _, _ = await client.get_json(
                "/patreon/user/274608/posts", {"o": 782}
            )

    assert route.call_count == 2
    assert any(6.5 <= delay <= 7.5 for delay in sleeps)
    assert data["total"] == 782


async def test_repeated_429_stops_the_client(settings):
    with respx.mock(assert_all_called=False) as router:
        route = router.get(POSTS_URL).mock(
            return_value=httpx.Response(
                429, headers={"retry-after": "0"}, json={"error": "slow down"}
            )
        )

        async with _client(settings) as client:
            with pytest.raises(ApiServerError):
                await client.get_json("/patreon/user/274608/posts", {"o": 0})
            with pytest.raises(ApiRateLimited):
                await client.get_json("/patreon/user/274608/posts", {"o": 0})

        assert route.call_count == 6


async def test_404_raises_immediately(settings):
    with respx.mock(assert_all_called=False) as router:
        route = router.get(POSTS_URL).mock(return_value=httpx.Response(404, text="nope"))

        async with _client(settings) as client:
            with pytest.raises(ApiNotFound):
                await client.get_json("/patreon/user/274608/posts", {"o": 0})

        assert route.call_count == 1


async def test_html_200_is_blocked_not_decode_error(settings):
    with respx.mock(assert_all_called=False) as router:
        router.get(POSTS_URL).mock(
            return_value=httpx.Response(
                200,
                headers={"content-type": "text/html; charset=utf-8"},
                text="<html><body>Checking your browser...</body></html>",
            )
        )

        async with _client(settings) as client:
            with pytest.raises(ApiBlocked) as excinfo:
                await client.get_json("/patreon/user/274608/posts", {"o": 0})

        assert "challenge" in str(excinfo.value)
        assert "Checking your browser" in excinfo.value.snippet
        assert len(excinfo.value.snippet) <= 200


async def test_json_content_type_with_broken_body_is_decode_error(settings):
    with respx.mock(assert_all_called=False) as router:
        router.get(POSTS_URL).mock(
            return_value=httpx.Response(
                200,
                headers={"content-type": "application/json"},
                text="{not json",
            )
        )

        async with _client(settings) as client:
            with pytest.raises(ApiDecode):
                await client.get_json("/patreon/user/274608/posts", {"o": 0})


async def test_timeout_retries_then_raises(settings):
    with respx.mock(assert_all_called=False) as router:
        route = router.get(POSTS_URL).mock(side_effect=httpx.ConnectTimeout("slow"))

        async with _client(settings) as client:
            with pytest.raises(ApiTimeout):
                await client.get_json("/patreon/user/274608/posts", {"o": 0})

        assert route.call_count == 3


async def test_concurrency_never_exceeds_the_semaphore(settings):
    active = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)

    async with _client(settings, transport=transport, concurrency=2) as client:
        results = await asyncio.gather(
            *[client.get_json(f"/probe/{index}") for index in range(12)]
        )

    assert len(results) == 12
    assert peak == 2


async def test_cache_rows_expose_fetched_at(settings, conn, fixture):
    payload = fixture("profile_274608.json")

    with respx.mock(assert_all_called=False) as router:
        router.get(PROFILE_URL).mock(return_value=httpx.Response(200, json=payload))

        async with _client(settings, conn) as client:
            _, fetched_at, _ = await client.get_json("/patreon/user/274608/profile")

    row = conn.execute("SELECT fetched_at, body FROM api_cache").fetchone()
    assert row is not None
    assert row["fetched_at"] == fetched_at
    assert json.loads(row["body"])["displayName"] == "Nyl"
