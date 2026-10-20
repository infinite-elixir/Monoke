from __future__ import annotations

import pytest

from monoke.extract.classify import Category, classify, normalize


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://drive.google.com/file/d/ID/view?usp=sharing", Category.GOOGLE_DRIVE),
        ("https://docs.google.com/document/d/x", Category.GOOGLE_DRIVE),
        ("https://mega.nz/file/abc#frag", Category.MEGA),
        ("https://mega.co.nz/#!abc", Category.MEGA),
        ("https://files.catbox.moe/x.mp4?utm_source=a", Category.DIRECT_MEDIA),
        ("https://example.com/clip.MP4", Category.DIRECT_MEDIA),
        ("https://example.com/clip.webm", Category.DIRECT_MEDIA),
        ("https://example.com/clip.gifv", Category.DIRECT_MEDIA),
        ("https://redgifs.com/watch/x", Category.VIDEO_HOST),
        ("https://gfycat.com/thing", Category.VIDEO_HOST),
        ("https://twitter.com/user/status/1/video/1", Category.VIDEO_HOST),
        ("http://bit.ly/49A802v", Category.SHORTENER),
        ("https://t.co/abc", Category.SHORTENER),
        ("https://tinyurl.com/abc", Category.SHORTENER),
        ("https://twitter.com/user/status/1", Category.SOCIAL),
        ("https://x.com/user", Category.SOCIAL),
        ("https://somebody.tumblr.com/post/1", Category.SOCIAL),
        ("https://www.pixiv.net/artworks/1", Category.SOCIAL),
        ("https://www.dropbox.com/s/abc/file", Category.CLOUD_OTHER),
        ("https://1drv.ms/u/s!abc", Category.CLOUD_OTHER),
        ("https://e2.cum.st/media/abc/original.mp4", Category.SITE_ASSET),
        ("https://img.cum.st/thumbnail/abc/preview.webp", Category.SITE_ASSET),
        ("https://example.com/pic.jpg", Category.IMAGE),
        ("https://example.com/page", Category.OTHER),
        ("magnet:?xt=urn:btih:abc", Category.OTHER),
        ("not a url at all", Category.OTHER),
        ("", Category.OTHER),
    ],
)
def test_classify(url, expected):
    assert classify(url) is expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://MEGA.nz/file/abc#frag", "https://mega.nz/file/abc"),
        ("https://example.com/a/", "https://example.com/a"),
        ("https://example.com/", "https://example.com/"),
        ("https://example.com:443/a", "https://example.com/a"),
        ("http://example.com:80/a", "http://example.com/a"),
        ("https://example.com:8443/a", "https://example.com:8443/a"),
        (
            "https://example.com/a/?b=2&a=1&utm_source=x&fbclid=y#frag",
            "https://example.com/a?a=1&b=2",
        ),
        ("https://example.com/a?gclid=1&z=2", "https://example.com/a?z=2"),
    ],
)
def test_normalize(url, expected):
    assert normalize(url) == expected


def test_normalize_never_raises_on_malformed_input():
    for url in ["http://[::1", "://bad", "https://exa mple.com/x", "%%%"]:
        assert isinstance(normalize(url), str)
        assert isinstance(classify(url), Category)
