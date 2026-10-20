from __future__ import annotations

import json

from monoke.extract.classify import Category
from monoke.extract.links import MAX_LINKS, extract_links
from monoke.models import PostDetail


def detail(caption: str | None = None, links=None) -> PostDetail:
    return PostDetail(id="1", service="patreon", caption=caption, links=links or [])


def test_fixture_drive_and_mega_labels(fixture):
    payload = fixture("post_172189145.json")
    rows = {row.normalized_url: row for row in extract_links(PostDetail.model_validate(payload))}

    drive = next(row for row in rows.values() if row.category is Category.GOOGLE_DRIVE)
    mega = next(row for row in rows.values() if row.category is Category.MEGA)

    assert drive.label == "Link"
    assert mega.label == "Backup Link"
    assert "drive.google.com" in drive.url
    assert "mega.nz" in mega.url


def test_fixture_without_links(fixture):
    payload = fixture("post_151983862.json")
    assert extract_links(PostDetail.model_validate(payload)) == []


def test_javascript_and_data_urls_are_ignored():
    rows = extract_links(
        detail(
            '<p><a href="javascript:alert(1)">x</a>'
            '<a href="data:text/plain,hi">y</a>'
            '<a href="mailto:a@b.c">mail</a></p>'
        )
    )
    assert rows == []


def test_bare_url_has_no_label():
    rows = extract_links(detail("<p>mirror: https://example.com/file.mp4, thanks</p>"))
    assert len(rows) == 1
    assert rows[0].url == "https://example.com/file.mp4"
    assert rows[0].label is None
    assert rows[0].category is Category.DIRECT_MEDIA


def test_anchor_label_falls_back_to_hostname():
    rows = extract_links(detail('<a href="https://example.com/x"></a>'))
    assert rows[0].label == "example.com"


def test_relative_urls_resolve_against_cum_st():
    rows = extract_links(detail('<a href="/creators/patreon/1">creator</a>'))
    assert rows[0].url == "https://cum.st/creators/patreon/1"


def test_duplicate_keeps_the_labelled_row():
    rows = extract_links(
        detail(
            '<a href="https://mega.nz/file/x#frag">MEGA</a>'
            '<a href="https://mega.nz/file/x"></a>'
        )
    )
    assert len(rows) == 1
    assert rows[0].normalized_url == "https://mega.nz/file/x"
    assert rows[0].label == "MEGA"


def test_caption_images_are_links_with_image_category():
    rows = extract_links(
        detail('<p><img src="https://img.cum.st/thumbnail/abc/preview.webp" alt="preview"></p>')
    )
    assert len(rows) == 1
    assert rows[0].category is Category.IMAGE
    assert rows[0].label == "preview"


def test_api_links_are_merged_defensively():
    rows = extract_links(
        detail('<a href="https://example.com/a">a</a>',
               links=[{"url": "https://mega.nz/file/y", "label": "backup"}])
    )
    assert {row.origin for row in rows} == {"caption", "api_links"}
    mega = next(row for row in rows if row.category is Category.MEGA)
    assert mega.label == "backup"


def test_magnet_links_are_kept():
    rows = extract_links(detail('<a href="magnet:?xt=urn:btih:abc">torrent</a>'))
    assert len(rows) == 1
    assert rows[0].url.startswith("magnet:")
    assert rows[0].category is Category.OTHER


def test_link_cap():
    caption = "".join(f'<a href="https://example.com/{i}">x</a>' for i in range(MAX_LINKS + 20))
    rows = extract_links(detail(caption))
    assert len(rows) == MAX_LINKS


def test_caption_is_not_required():
    post = PostDetail.model_validate(
        json.loads('{"id": "1", "service": "patreon", "caption": null}')
    )
    assert extract_links(post) == []
