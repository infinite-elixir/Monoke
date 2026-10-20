"""Live smoke tests against the real cum.st API.

Run with: MONOKE_LIVE=1 uv run pytest -m live
"""

from __future__ import annotations

import os

import pytest

from monoke.config import Settings
from monoke.cumst.client import CumstClient
from monoke.cumst.urls import parse_url

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        os.environ.get("MONOKE_LIVE") != "1",
        reason="set MONOKE_LIVE=1 to hit the real cum.st API",
    ),
]


async def test_live_profile_posts_and_post(tmp_path):
    ref = parse_url("https://cum.st/creators/patreon/274608")

    async with CumstClient(Settings(data_dir=tmp_path), jitter=(0, 0)) as client:
        profile = await client.profile(ref)
        assert profile.id == "274608"
        assert profile.service == "patreon"

        page = await client.posts_page(ref, 0)
        assert page.total == 782
        assert len(page.posts) == 50

        top = await client.posts_page(ref, 0, sort="top")
        assert top.total == 782

        with pytest.raises(ValueError):
            await client.posts_page(ref, 0, sort="popular")

        post = await client.post(ref, "172189145")
        assert post.id == "172189145"
        assert post.caption_html and "drive.google.com" in post.caption_html
