from __future__ import annotations

import pytest

from monoke.cumst.urls import (
    CumstRef,
    UrlError,
    api_path,
    creator_path,
    creator_url,
    parse_url,
    post_path,
    post_url,
    posts_path,
    profile_path,
)

REF = CumstRef(service="patreon", creator_id="274608")


@pytest.mark.parametrize(
    ("raw", "post_id"),
    [
        ("https://cum.st/creators/patreon/274608", None),
        ("https://cum.st/creators/patreon/274608/", None),
        ("https://cum.st/creators/patreon/274608?page=2", None),
        ("https://cum.st/creators/patreon/274608#posts", None),
        ("http://cum.st/creators/patreon/274608", None),
        ("https://www.cum.st/creators/patreon/274608", None),
        ("https://CUM.ST/creators/patreon/274608", None),
        ("creators/patreon/274608", None),
        ("/creators/patreon/274608", None),
        ("cum.st/creators/patreon/274608", None),
        ("  https://cum.st/creators/patreon/274608  ", None),
        ("https://cum.st/creators/patreon/274608/post/172189145", "172189145"),
        ("https://cum.st/creators/fansly/123/post/9/", "9"),
    ],
)
def test_parse_accepts(raw, post_id):
    ref = parse_url(raw)

    assert ref.service in {"patreon", "fansly"}
    assert ref.post_id == post_id
    assert isinstance(ref.creator_id, str)
    assert ref.is_post is (post_id is not None)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("", "Paste a cum.st creator URL"),
        ("https://kemono.cr/patreon/user/2793377", "old site"),
        ("https://coomer.party/patreon/user/1", "old site"),
        ("https://example.com/creators/patreon/1", "Expected a cum.st URL"),
        ("https://cum.st/patreon/user/274608", "Expected /creators"),
        ("https://cum.st/creators/patreon", "Expected /creators"),
        ("https://cum.st/creators/patreon/274608/post", "Expected /creators"),
        ("https://cum.st/creators/patreon/abc", "creator id"),
        ("https://cum.st/creators/patreon/274608/post/abc", "post id"),
        ("https://cum.st/creators/Pat/274608", "Invalid service"),
        ("ftp://cum.st/creators/patreon/1", "http"),
        ("just some text", "Expected a URL"),
    ],
)
def test_parse_rejects(raw, message):
    with pytest.raises(UrlError) as excinfo:
        parse_url(raw)

    assert message.lower() in str(excinfo.value).lower()


def test_builders():
    assert creator_path(REF) == "/creators/patreon/274608"
    assert creator_url(REF) == "https://cum.st/creators/patreon/274608"
    assert post_url(REF, "42") == "https://cum.st/creators/patreon/274608/post/42"
    assert post_url(CumstRef("fansly", "9", "3")) == "https://cum.st/creators/fansly/9/post/3"
    assert api_path(REF) == "/patreon/user/274608"
    assert profile_path(REF) == "/patreon/user/274608/profile"
    assert posts_path(REF) == "/patreon/user/274608/posts"
    assert post_path(REF, "42") == "/patreon/user/274608/post/42"
    assert post_path(CumstRef("patreon", "1", "2")) == "/patreon/user/1/post/2"


def test_post_builders_require_an_id():
    with pytest.raises(UrlError):
        post_url(REF)
    with pytest.raises(UrlError):
        post_path(REF)
