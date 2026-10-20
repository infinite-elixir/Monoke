from __future__ import annotations

import httpx
import pytest
import respx

from monoke.cumst.client import CumstClient
from monoke.cumst.urls import parse_url

BASE = "https://cum.st/api/v1/patreon/user/274608"
PROFILE_URL = f"{BASE}/profile"
POSTS_URL = f"{BASE}/posts"
POST_URL = f"{BASE}/post/172189145"

REF = parse_url("https://cum.st/creators/patreon/274608")


def _client(settings):
    return CumstClient(settings, jitter=(0, 0), sleep=_no_sleep)


async def _no_sleep(_delay: float) -> None:
    return None


async def test_profile(settings, fixture):
    payload = fixture("profile_274608.json")

    with respx.mock(assert_all_called=False) as router:
        router.get(PROFILE_URL).mock(return_value=httpx.Response(200, json=payload))

        async with _client(settings) as client:
            profile = await client.profile(REF)

    assert profile.id == "274608"
    assert profile.service == "patreon"
    assert profile.name == "nyl2"
    assert profile.display_name == "Nyl"
    assert profile.updated == 1792482836
    assert profile.has_tags is True
    assert profile.from_cache is False
    assert profile.fetched_at
    assert '"displayName": "Nyl"' in profile.raw


async def test_posts_page_uses_the_documented_query(settings, fixture):
    payload = fixture("posts_274608_o0.json")

    with respx.mock(assert_all_called=False) as router:
        route = router.get(POSTS_URL).mock(
            return_value=httpx.Response(200, json=payload)
        )

        async with _client(settings) as client:
            page = await client.posts_page(REF, 0)

        params = route.calls[0].request.url.params

    assert page.total == 782
    assert len(page.posts) == 50
    assert page.posts[0].id == "172189145"
    assert params["n"] == "50"
    assert params["compact"] == "1"
    assert params["sort"] == "recent"
    assert params["o"] == "0"


async def test_posts_page_last_page_and_empty_page(settings, fixture):
    last = fixture("posts_274608_o780.json")
    empty = fixture("posts_274608_empty.json")

    with respx.mock(assert_all_called=False) as router:
        router.get(POSTS_URL).mock(
            side_effect=[
                httpx.Response(200, json=last),
                httpx.Response(200, json=empty),
            ]
        )

        async with _client(settings) as client:
            tail = await client.posts_page(REF, 780)
            beyond = await client.posts_page(REF, 782)

    assert tail.total == 782
    assert len(tail.posts) == 2
    assert beyond.total == 782
    assert beyond.posts == []


async def test_q_and_tag_are_forwarded(settings, fixture):
    with respx.mock(assert_all_called=False) as router:
        route = router.get(POSTS_URL).mock(
            return_value=httpx.Response(200, json=fixture("posts_274608_tag.json"))
        )

        async with _client(settings) as client:
            await client.posts_page(REF, 0, q="overwatch", tag="public-model-release")

        params = route.calls[0].request.url.params

    assert params["q"] == "overwatch"
    assert params["tag"] == "public-model-release"


async def test_sort_is_validated(settings):
    async with _client(settings) as client:
        with pytest.raises(ValueError):
            await client.posts_page(REF, 0, sort="popular")


async def test_post_detail(settings, fixture):
    payload = fixture("post_172189145.json")

    with respx.mock(assert_all_called=False) as router:
        route = router.get(POST_URL).mock(
            return_value=httpx.Response(200, json=payload)
        )

        async with _client(settings) as client:
            detail = await client.post(REF, "172189145")

        params = route.calls[0].request.url.params

    assert detail.id == "172189145"
    assert len(detail.attachments) == 1

    attachment = detail.attachments[0]
    assert attachment.kind == "image"
    assert attachment.mime_type == "image/jpeg"
    assert attachment.bytes == 109987
    assert attachment.variants[0].name == "original.jpg"
    assert attachment.sha256 == "827766a19dfe8e34bd223e0ccf23a440444e092afb8799fb6d9ab349460e0692"

    assert [tag.slug for tag in detail.tags] == ["public-model-release"]
    assert 'target="_blank"' in detail.caption_html
    assert detail.prev is not None and detail.prev.id == "171761266"
    assert detail.next is None
    assert detail.creator is not None and detail.creator.display_name == "Nyl"
    assert params["include"] == "linked,revisions"
    assert '"captionHtml"' in detail.raw


async def test_post_id_can_come_from_the_url(settings, fixture):
    ref = parse_url("https://cum.st/creators/patreon/274608/post/172189145")

    with respx.mock(assert_all_called=False) as router:
        router.get(POST_URL).mock(
            return_value=httpx.Response(200, json=fixture("post_172189145.json"))
        )

        async with _client(settings) as client:
            detail = await client.post(ref)

    assert detail.id == "172189145"


async def test_empty_post_fields_are_tolerated(settings, fixture):
    with respx.mock(assert_all_called=False) as router:
        router.get(BASE + "/post/171761266").mock(
            return_value=httpx.Response(200, json=fixture("post_171761266.json"))
        )

        async with _client(settings) as client:
            detail = await client.post(REF, "171761266")

    assert detail.attachments == []
    assert [tag.slug for tag in detail.tags] == ["animation"]


async def test_post_requires_an_id(settings):
    async with _client(settings) as client:
        with pytest.raises(ValueError):
            await client.post(REF)


async def test_mock_posts_helper(settings, mock_posts):
    with respx.mock(assert_all_called=False) as router:
        route = mock_posts(router, offset=100, count=2, total=782)

        async with _client(settings) as client:
            page = await client.posts_page(REF, 100)

        params = route.calls[0].request.url.params

    assert page.total == 782
    assert [post.id for post in page.posts] == ["900100", "900101"]
    assert params["o"] == "100"
