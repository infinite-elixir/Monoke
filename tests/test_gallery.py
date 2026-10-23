from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient
from urllib.parse import parse_qs, quote, unquote, urlsplit

from monoke import db
from monoke.config import get_settings
from monoke.web.app import create_app


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MONOKE_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture()
def client():
    return TestClient(create_app())


@pytest.fixture()
def conn():
    connection = db.ensure_db(get_settings())
    yield connection
    connection.close()


def make_creator(conn, **overrides) -> int:
    values = {
        "service": "patreon",
        "service_id": "274608",
        "name": "nyl2",
        "display_name": "Nyl",
    }
    values.update(overrides)
    creator_id = db.upsert_creator(conn, **values)
    conn.commit()
    return creator_id


def add_post(conn, creator_id: int, number: int, **overrides) -> int:
    values = {
        "creator_id": creator_id,
        "service": "patreon",
        "service_id": str(900000 + number),
        "title": f"Post {number}",
        "caption_text": f"caption {number}",
        "published": 1000 + number,
        "added": 1000 + number,
        "fetched_at": db.now_iso(),
    }
    values.update(overrides)
    post_id = db.upsert_post(conn, **values)
    conn.commit()
    return post_id


def add_image(
    conn,
    post_id: int,
    sha: str,
    *,
    kind: str = "image",
    width: int | None = 1600,
    height: int | None = 1200,
    size: int | None = 250_000,
    position: int = 0,
) -> None:
    existing = db.list_files(conn, post_id)
    extension = {"gif": "gif", "audio": "m4a"}.get(kind, "jpg")
    existing.append(
        {
            "sha256": sha,
            "kind": kind,
            "mime_type": {
                "gif": "image/gif",
                "audio": "audio/mpeg",
            }.get(kind, "image/jpeg"),
            "bytes": size,
            "width": width,
            "height": height,
            "original_filename": f"{sha[:8]}.{extension}",
            "url": f"https://e1.cum.st/media/{sha}/original.{extension}",
            "thumb_url": f"https://img.cum.st/thumbnail/{sha}/preview.webp",
            "position": position,
        }
    )
    db.replace_files(conn, post_id, existing)
    conn.commit()


def sha(number: int) -> str:
    return f"{number:064x}"


def seed(conn, count: int = 3) -> int:
    creator_id = make_creator(conn)
    for index in range(count):
        post_id = add_post(conn, creator_id, index)
        add_image(conn, post_id, sha(index))
    return creator_id


def test_gallery_renders_every_image_with_thumbnails_only(client, conn):
    creator_id = seed(conn, 3)

    response = client.get(f"/c/{creator_id}/gallery")

    assert response.status_code == 200
    body = response.text
    assert body.count('class="gallery-thumb"') == 3
    assert body.count('class="gallery-post"') == 3
    assert "img.cum.st/thumbnail" in body
    # Full-size edge media is never fetched by the gallery on load: the tile
    # links to the viewer page, not the raw media URL.
    assert 'src="https://e1.cum.st' not in body
    assert body.count('loading="lazy"') == 3
    assert "gallery-lightbox" in body
    assert "data-lightbox-image" in body
    assert body.count("data-gallery-item") == 3
    assert "data-viewer-open" not in body
    assert "data-viewer-focus" not in body
    assert "3</strong> images" in body


def test_gallery_tile_has_a_direct_bookmark_control_without_a_menu(client, conn):
    creator_id = seed(conn, 2)

    body = client.get(f"/c/{creator_id}/gallery").text

    assert body.count("data-gallery-menu") == 0
    assert "gallery-menu-trigger" not in body
    assert "gallery-menu-panel" not in body
    assert "Actions for" not in body
    # One form per tile; the viewer's top media control is not a form.
    assert body.count("data-bookmark-form") == 2
    assert body.count("gallery-bookmark") >= 2
    assert body.count('data-bookmark-target="image"') == 2
    # The control is a sibling of the thumbnail anchor, so activating it never
    # reaches the lightbox item.
    assert body.index("gallery-bookmark") > body.index('class="gallery-thumb"')
    assert "Start viewer here" not in body
    assert "Open in viewer" not in body
    assert "data-viewer-open" not in body


def test_gallery_keeps_cross_post_duplicates_in_each_section(client, conn):
    creator_id = make_creator(conn)
    first = add_post(conn, creator_id, 0, title="First")
    second = add_post(conn, creator_id, 1, title="Second")
    add_image(conn, first, sha(42))
    add_image(conn, second, sha(42))

    response = client.get(f"/c/{creator_id}/gallery")

    assert response.status_code == 200
    assert response.text.count('class="gallery-thumb"') == 2
    assert response.text.count('class="gallery-post"') == 2
    assert "in 2 posts" not in response.text

    groups, total = db.list_gallery_posts(conn, creator_id)
    assert total == 2
    assert [group["service_id"] for group in groups] == ["900001", "900000"]
    assert [[item["sha256"] for item in group["media"]] for group in groups] == [
        [sha(42)],
        [sha(42)],
    ]


def test_gallery_paginates_by_twenty_posts_and_keeps_order(client, conn):
    creator_id = make_creator(conn)
    for index in range(61):
        post_id = add_post(conn, creator_id, index)
        add_image(conn, post_id, sha(index))

    first = client.get(f"/c/{creator_id}/gallery")
    second = client.get(f"/c/{creator_id}/gallery?page=2")
    last = client.get(f"/c/{creator_id}/gallery?page=4")

    assert first.status_code == 200
    assert first.text.count('class="gallery-post"') == 20
    assert second.text.count('class="gallery-post"') == 20
    assert last.text.count('class="gallery-post"') == 1
    assert "61 posts" in first.text

    rows_first, total = db.list_gallery_posts(conn, creator_id, limit=20, offset=0)
    rows_second, _ = db.list_gallery_posts(conn, creator_id, limit=20, offset=20)
    assert total == 61
    assert len(rows_first) == 20
    assert len(rows_second) == 20
    assert {row["id"] for row in rows_first}.isdisjoint(
        {row["id"] for row in rows_second}
    )
    # Newest post first.
    assert rows_first[0]["service_id"] == "900060"


def test_gallery_filters_compose(client, conn):
    creator_id = make_creator(conn)
    portrait = add_post(conn, creator_id, 0, title="Portrait pin")
    landscape = add_post(conn, creator_id, 1, title="Landscape")
    tiny = add_post(conn, creator_id, 2, title="Tiny icon")
    add_image(conn, portrait, sha(1), width=800, height=1600)
    add_image(conn, landscape, sha(2), width=1600, height=800)
    add_image(conn, tiny, sha(3), width=32, height=32, size=512)
    db.replace_tags(conn, portrait, [{"slug": "pin-up", "label": "Pin-up"}])
    db.replace_tags(conn, tiny, [{"slug": "pin-up", "label": "Pin-up"}])

    portrait_body = client.get(f"/c/{creator_id}/gallery?orientation=portrait").text
    assert portrait_body.count('class="gallery-thumb"') == 1
    assert "Portrait pin</a>" in portrait_body
    assert "Landscape</a>" not in portrait_body

    tag_body = client.get(f"/c/{creator_id}/gallery?tag=pin-up").text
    assert tag_body.count('class="gallery-thumb"') == 2

    search_body = client.get(f"/c/{creator_id}/gallery?q=landscape").text
    assert search_body.count('class="gallery-thumb"') == 1
    assert "Landscape</a>" in search_body

    size_body = client.get(f"/c/{creator_id}/gallery?min_bytes=102400").text
    assert size_body.count('class="gallery-thumb"') == 2
    assert "Tiny icon" not in size_body


def test_gallery_empty_states(client, conn):
    creator_id = make_creator(conn)
    add_post(conn, creator_id, 0)

    empty = client.get(f"/c/{creator_id}/gallery")
    assert empty.status_code == 200
    assert "No media in this archive" in empty.text
    assert 'class="gallery-thumb"' not in empty.text

    add_image(conn, add_post(conn, creator_id, 1), sha(7))
    filtered = client.get(f"/c/{creator_id}/gallery?q=does-not-exist")
    assert "No media match these filters" in filtered.text


def test_gallery_unknown_creator_is_404(client):
    assert client.get("/c/999/gallery").status_code == 404


def test_gallery_statistics(client, conn):
    creator_id = make_creator(conn)
    first = add_post(conn, creator_id, 0)
    second = add_post(conn, creator_id, 1)
    add_image(conn, first, sha(1))
    add_image(conn, first, sha(2), kind="gif", position=1)
    add_image(conn, second, sha(1))

    stats = db.gallery_stats(conn, creator_id)

    assert stats["images"] == 1
    assert stats["gifs"] == 1
    assert stats["posts"] == 2


# --------------------------------------------------------------------------- #
# T7.2 - media-aware gallery
# --------------------------------------------------------------------------- #


def add_video(
    conn,
    post_id: int,
    sha_value: str,
    *,
    width: int = 1080,
    height: int = 1920,
    duration_ms: int | None = 9000,
    position: int = 0,
) -> None:
    existing = db.list_files(conn, post_id)
    existing.append(
        {
            "sha256": sha_value,
            "kind": "video",
            "mime_type": "video/mp4",
            "bytes": 5_000_000,
            "width": width,
            "height": height,
            "duration_ms": duration_ms,
            "original_filename": f"{sha_value[:8]}.mp4",
            "url": f"https://e1.cum.st/media/{sha_value}/original.mp4",
            "thumb_url": f"https://img.cum.st/thumbnail/{sha_value}/preview.webp",
            "position": position,
            "variants": [
                {
                    "name": "original.mp4",
                    "url": f"https://e1.cum.st/media/{sha_value}/original.mp4",
                },
                {
                    "name": "720p.mp4",
                    "url": f"https://e1.cum.st/media/{sha_value}/720p.mp4",
                },
            ],
        }
    )
    db.replace_files(conn, post_id, existing)
    conn.commit()


def test_gallery_shows_images_videos_and_gifs(client, conn):
    creator_id = make_creator(conn)
    add_image(conn, add_post(conn, creator_id, 0), sha(1))
    add_video(conn, add_post(conn, creator_id, 1), sha(2))
    add_image(conn, add_post(conn, creator_id, 2), sha(3), kind="gif")

    body = client.get(f"/c/{creator_id}/gallery").text

    assert body.count('class="gallery-thumb"') == 3
    assert 'data-kind="video"' in body
    assert 'data-kind="gif"' in body
    assert "gallery-play" in body
    assert "0:09" in body
    assert "1</strong> image" in body
    assert "1</strong> video" in body
    assert "1</strong> gif" in body
    # Edge media is never fetched on gallery load.
    assert 'src="https://e1.cum.st' not in body

    stats = db.gallery_stats(conn, creator_id)
    assert stats == {
        "images": 1,
        "videos": 1,
        "gifs": 1,
        "audio": 0,
        "posts": 3,
        "files": 3,
    }


def test_gallery_kind_filter_and_unknown_fallback(client, conn):
    creator_id = make_creator(conn)
    add_image(conn, add_post(conn, creator_id, 0), sha(1))
    add_video(conn, add_post(conn, creator_id, 1), sha(2))
    add_image(conn, add_post(conn, creator_id, 2), sha(3), kind="gif")

    videos = client.get(f"/c/{creator_id}/gallery?kind=video").text
    assert videos.count('class="gallery-thumb"') == 1
    assert 'data-kind="video"' in videos
    assert 'data-kind="image"' not in videos
    assert 'data-kind="gif"' not in videos

    images = client.get(f"/c/{creator_id}/gallery?kind=image").text
    assert images.count('class="gallery-thumb"') == 1
    assert 'data-kind="image"' in images

    everything = client.get(f"/c/{creator_id}/gallery?kind=bogus").text
    assert everything.count('class="gallery-thumb"') == 3


def test_gallery_kind_composes_with_orientation(client, conn):
    creator_id = make_creator(conn)
    add_video(conn, add_post(conn, creator_id, 0), sha(1), width=1080, height=1920)
    add_video(conn, add_post(conn, creator_id, 1), sha(2), width=1920, height=1080)
    add_image(conn, add_post(conn, creator_id, 2), sha(3), width=1080, height=1920)

    portrait = client.get(
        f"/c/{creator_id}/gallery?kind=video&orientation=portrait"
    ).text

    assert portrait.count('class="gallery-thumb"') == 1
    assert 'data-kind="video"' in portrait


def test_gallery_shows_the_same_video_on_both_posts(client, conn):
    creator_id = make_creator(conn)
    first = add_post(conn, creator_id, 0, title="First")
    second = add_post(conn, creator_id, 1, title="Second")
    add_video(conn, first, sha(9))
    add_video(conn, second, sha(9))

    body = client.get(f"/c/{creator_id}/gallery?kind=video").text

    assert body.count('class="gallery-thumb"') == 2
    assert "in 2 posts" not in body
    groups, total = db.list_gallery_posts(conn, creator_id, kind="video")
    assert total == 2
    assert all(group["media"][0]["duration_ms"] == 9000 for group in groups)


def test_gallery_keeps_the_lightbox_for_video_tiles(client, conn):
    creator_id = make_creator(conn)
    add_video(conn, add_post(conn, creator_id, 0), sha(1))

    body = client.get(f"/c/{creator_id}/gallery").text

    assert body.count('class="gallery-thumb"') == 1
    assert "gallery-lightbox" in body
    assert "data-lightbox-video" in body


# --------------------------------------------------------------------------- #
# T7.6 - duration sort
# --------------------------------------------------------------------------- #


def test_gallery_sort_filter_parsing_and_urls():
    from monoke.web.filters import GalleryFilters

    assert GalleryFilters.from_query({"sort": "duration_desc"}).sort == "duration_desc"
    assert GalleryFilters.from_query({"sort": "duration_asc"}).sort == "duration_asc"
    assert GalleryFilters.from_query({"sort": "oldest"}).sort == "oldest"
    # Plain/legacy spellings and anything unknown fall back to the default.
    assert GalleryFilters.from_query({"sort": "duration"}).sort == "duration_desc"
    assert GalleryFilters.from_query({"sort": "length"}).sort == "duration_desc"
    assert GalleryFilters.from_query({"sort": "bogus"}).sort == "newest"
    assert GalleryFilters.from_query({}).sort == "newest"

    default = GalleryFilters.from_query({})
    assert ("sort", "newest") not in default.pairs()
    assert default.active is False

    sorted_filters = GalleryFilters.from_query({"sort": "duration_asc", "page": "3"})
    # A non-default sort is part of the bookmarkable URL and survives paging.
    assert ("sort", "duration_asc") in sorted_filters.pairs()
    assert "sort=duration_asc" in sorted_filters.url_for(7)
    assert "sort=duration_asc" in sorted_filters.url_for(7, page=2)

    # Choosing a sort is not a filter: no "Clear filters" just for that.
    assert sorted_filters.active is False
    # Changing the sort starts again at page 1.
    assert sorted_filters.with_changes(sort="newest").page == 1


def test_gallery_duration_sort_orders_posts_and_sinks_unknowns(client, conn):
    creator_id = make_creator(conn)
    short_post = add_post(conn, creator_id, 0)
    long_post = add_post(conn, creator_id, 1)
    unknown_post = add_post(conn, creator_id, 2)
    image_post = add_post(conn, creator_id, 3)
    add_video(conn, short_post, sha(1), duration_ms=5_000)
    add_video(conn, long_post, sha(2), duration_ms=90_000)
    add_video(conn, unknown_post, sha(3), duration_ms=None)
    add_image(conn, image_post, sha(4))

    longest, total = db.list_gallery_posts(conn, creator_id, sort="duration_desc")
    assert total == 4
    assert [group["media"][0]["sha256"] for group in longest] == [
        sha(2),
        sha(1),
        sha(4),
        sha(3),
    ]

    shortest, _ = db.list_gallery_posts(conn, creator_id, sort="duration_asc")
    assert [group["media"][0]["sha256"] for group in shortest] == [
        sha(1),
        sha(2),
        sha(4),
        sha(3),
    ]

    # A zero duration is unknown, not "0 seconds at the top".
    zero_post = add_post(conn, creator_id, 4)
    add_video(conn, zero_post, sha(5), duration_ms=0)
    shortest, _ = db.list_gallery_posts(conn, creator_id, sort="duration_asc")
    shortest_shas = [group["media"][0]["sha256"] for group in shortest]
    assert shortest_shas[:2] == [sha(1), sha(2)]
    assert shortest_shas[-1] == sha(3)
    assert shortest_shas.index(sha(5)) > shortest_shas.index(sha(2))

    # Same rule inside a video-only grid: unknowns always last.
    videos_desc, video_total = db.list_gallery_posts(
        conn, creator_id, kind="video", sort="duration_desc"
    )
    assert video_total == 4
    assert [group["media"][0]["sha256"] for group in videos_desc] == [
        sha(2),
        sha(1),
        sha(5),
        sha(3),
    ]
    videos_asc, _ = db.list_gallery_posts(
        conn, creator_id, kind="video", sort="duration_asc"
    )
    assert [group["media"][0]["sha256"] for group in videos_asc] == [
        sha(1),
        sha(2),
        sha(5),
        sha(3),
    ]

    # Unknown sort keys are rejected before they can reach the SQL string.
    with pytest.raises(ValueError):
        db.list_gallery_posts(conn, creator_id, sort="duration; DROP TABLE files")


def test_gallery_duration_sort_paging_has_no_duplicate_posts(client, conn):
    creator_id = make_creator(conn)
    # Durations are deliberately not in published order so the sort matters.
    durations = [30_000, 5_000, 120_000, 60_000, 15_000]
    for index, duration in enumerate(durations):
        add_video(conn, add_post(conn, creator_id, index), sha(index), duration_ms=duration)

    seen: list[str] = []
    for offset in (0, 2, 4):
        groups, _ = db.list_gallery_posts(
            conn, creator_id, sort="duration_desc", limit=2, offset=offset
        )
        seen.extend(group["media"][0]["sha256"] for group in groups)

    assert len(seen) == len(set(seen)) == 5
    assert seen == [sha(2), sha(3), sha(0), sha(4), sha(1)]


def test_gallery_sort_control_is_labelled_and_sticky(client, conn):
    creator_id = make_creator(conn)
    for index in range(3):
        add_video(conn, add_post(conn, creator_id, index), sha(index), duration_ms=index * 1000)

    body = client.get(f"/c/{creator_id}/gallery?kind=video&sort=duration_desc").text

    assert 'for="gallery-sort"' in body
    assert 'id="gallery-sort"' in body
    for key, label in (
        ("newest", "Newest first"),
        ("oldest", "Oldest first"),
        ("duration_desc", "Longest first"),
        ("duration_asc", "Shortest first"),
    ):
        assert f'value="{key}"' in body
        assert f">{label}<" in body
    assert 'value="duration_desc" selected>Longest first<' in body
    # The chosen order drives the grid: longest video first.
    assert body.index(sha(2)) < body.index(sha(1)) < body.index(sha(0))

    # Stills can not answer "longest first", so those options disappear.
    images_only = client.get(f"/c/{creator_id}/gallery?kind=image").text
    assert 'value="duration_desc"' not in images_only
    assert 'value="duration_asc"' not in images_only
    assert 'value="newest" selected' in images_only

    # ...but a bookmarked duration URL keeps the selected option visible.
    bookmarked = client.get(
        f"/c/{creator_id}/gallery?kind=image&sort=duration_desc"
    ).text
    assert 'value="duration_desc" selected>Longest first<' in bookmarked


def test_gallery_sort_survives_pagination_links(client, conn):
    creator_id = make_creator(conn)
    for index in range(21):
        add_video(
            conn, add_post(conn, creator_id, index), sha(index), duration_ms=index * 1_000
        )

    body = client.get(f"/c/{creator_id}/gallery?kind=video&sort=duration_desc").text

    assert 'value="duration_desc" selected' in body
    assert "kind=video&amp;sort=duration_desc&amp;page=2" in body
    page_two = client.get(
        f"/c/{creator_id}/gallery?kind=video&sort=duration_desc&page=2"
    ).text
    assert body.count('class="gallery-thumb"') == 20
    assert page_two.count('class="gallery-thumb"') == 1
    # Longest video (the newest post) is on page 1, the shortest on page 2.
    assert sha(20) in body
    assert sha(20) not in page_two
    assert sha(0) in page_two


# --------------------------------------------------------------------------- #
# T4.11 - grouping, filters and query budget
# --------------------------------------------------------------------------- #


def test_post_sections_use_viewer_order_and_section_counts(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 0, title="Ordered post")
    add_image(conn, post_id, sha(1), position=2)
    add_image(conn, post_id, sha(2), kind="gif", position=0)
    add_video(conn, post_id, sha(3), position=1)

    body = client.get(f"/c/{creator_id}/gallery").text

    assert body.count('class="gallery-post"') == 1
    assert "Ordered post</a>" in body
    assert "3 media" in body
    order = [body.index(f"gallery-{sha(n)}") for n in (2, 3, 1)]
    assert order == sorted(order)


def test_top_level_open_viewer_button_opens_the_overlay_in_place(client, conn):
    creator_id = make_creator(conn)
    image_post = add_post(conn, creator_id, 0, title="Older image")
    add_image(conn, image_post, sha(1))

    body = client.get(f"/c/{creator_id}/gallery?kind=image").text

    # T11.1: the page-level entry point is a button over the first tile, not a
    # link to the deleted viewer page.
    assert (
        '<button class="button" type="button" data-lightbox-open>Open viewer</button>'
        in body
    )
    assert f"/c/{creator_id}/viewer" not in body
    assert body.count("data-gallery-item") >= 1


def test_posts_without_matching_media_are_omitted(client, conn):
    creator_id = make_creator(conn)
    video_post = add_post(conn, creator_id, 0, title="Video post")
    image_post = add_post(conn, creator_id, 1, title="Image only")
    add_video(conn, video_post, sha(1))
    add_image(conn, image_post, sha(2))

    body = client.get(f"/c/{creator_id}/gallery?kind=video").text

    assert "Video post</a>" in body
    assert "Image only" not in body
    groups, total = db.list_gallery_posts(conn, creator_id, kind="video")
    assert total == 1
    assert [group["service_id"] for group in groups] == ["900000"]


def test_gallery_page_issues_no_per_section_queries(client, conn):
    creator_id = make_creator(conn)
    for index in range(5):
        post_id = add_post(conn, creator_id, index)
        add_image(conn, post_id, sha(index))

    class CountingConnection:
        def __init__(self, inner):
            self.inner = inner
            self.count = 0

        def execute(self, *args, **kwargs):
            self.count += 1
            return self.inner.execute(*args, **kwargs)

    counter = CountingConnection(conn)
    groups, total = db.list_gallery_posts(counter, creator_id)

    assert total == 5
    assert sum(len(group["media"]) for group in groups) == 5
    # total, post page and one batched media query.
    assert counter.count == 3


# --------------------------------------------------------------------------- #
# T7.7 - media-kind tab strip
# --------------------------------------------------------------------------- #


def kind_tab_links(body: str) -> list[dict]:
    nav = body.split('aria-label="Media type"', 1)[1].split("</nav>", 1)[0]
    tabs: list[dict] = []
    for chunk in nav.split("<a ")[1:]:
        content = chunk.split("</a>", 1)[0]
        href = re.search(r'href="([^"]+)"', content).group(1).replace("&amp;", "&")
        label = re.search(r">\s*([^<>]+?)\s*<span", content).group(1)
        count = int(re.search(r'gallery-kind-count">(\d+)</span>', content).group(1))
        tabs.append(
            {
                "href": href,
                "label": label,
                "count": count,
                "current": 'aria-current="page"' in content,
            }
        )
    return tabs


def seed_every_kind(conn) -> int:
    creator_id = make_creator(conn)
    add_image(conn, add_post(conn, creator_id, 0), sha(1))
    add_image(conn, add_post(conn, creator_id, 1), sha(2))
    add_video(conn, add_post(conn, creator_id, 2), sha(3))
    add_video(conn, add_post(conn, creator_id, 3), sha(4))
    add_image(conn, add_post(conn, creator_id, 4), sha(5), kind="gif")
    return creator_id


def test_gallery_renders_kind_tabs_in_order_with_counts(client, conn):
    creator_id = seed_every_kind(conn)

    body = client.get(f"/c/{creator_id}/gallery").text
    tabs = kind_tab_links(body)

    assert [tab["label"] for tab in tabs] == ["All media", "Images", "Videos", "GIFs"]
    assert [tab["count"] for tab in tabs] == [5, 2, 2, 1]
    # The old dropdown is gone; the strip is the only kind control.
    assert 'id="gallery-kind"' not in body
    assert 'name="kind"' not in body


def test_gallery_all_media_is_the_default_active_tab(client, conn):
    creator_id = seed_every_kind(conn)

    tabs = kind_tab_links(client.get(f"/c/{creator_id}/gallery").text)

    assert [tab["current"] for tab in tabs] == [True, False, False, False]
    assert tabs[0]["href"] == f"/c/{creator_id}/gallery"
    assert tabs[1]["href"] == f"/c/{creator_id}/gallery?kind=image"
    assert tabs[2]["href"] == f"/c/{creator_id}/gallery?kind=video"
    assert tabs[3]["href"] == f"/c/{creator_id}/gallery?kind=gif"


@pytest.mark.parametrize(
    ("kind", "index"), [("image", 1), ("video", 2), ("gif", 3)]
)
def test_gallery_matching_kind_tab_is_current(client, conn, kind, index):
    creator_id = seed_every_kind(conn)

    tabs = kind_tab_links(client.get(f"/c/{creator_id}/gallery?kind={kind}").text)

    assert tabs[index]["current"] is True
    assert [tab["current"] for tab in tabs].count(True) == 1


def test_gallery_kind_tabs_preserve_filters_and_reset_page(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(
        conn,
        creator_id,
        0,
        title="post",
        published=1_600_000_000,
    )
    add_image(conn, post_id, sha(1), width=800, height=1600, size=250_000)
    db.replace_tags(conn, post_id, [{"slug": "pin-up", "label": "Pin-up"}])

    body = client.get(
        f"/c/{creator_id}/gallery?q=post&tag=pin-up&orientation=portrait"
        f"&min_bytes=1024&from=2020-01-01&to=2021-01-01&sort=oldest&page=2"
    ).text
    tabs = kind_tab_links(body)

    for tab in tabs:
        assert "q=post" in tab["href"]
        assert "tag=pin-up" in tab["href"]
        assert "orientation=portrait" in tab["href"]
        assert "min_bytes=1024" in tab["href"]
        assert "from=2020-01-01" in tab["href"]
        assert "to=2021-01-01" in tab["href"]
        assert "sort=oldest" in tab["href"]
        assert "page=" not in tab["href"]


def test_gallery_filter_form_keeps_the_active_kind_hidden(client, conn):
    creator_id = seed_every_kind(conn)

    body = client.get(f"/c/{creator_id}/gallery?kind=video").text

    assert '<input type="hidden" name="kind" value="video">' in body
    # Submitting the form with the hidden kind keeps the same filtered grid.
    assert kind_tab_links(body)[2]["current"] is True

    all_media = client.get(f"/c/{creator_id}/gallery").text
    assert 'name="kind"' not in all_media


def test_gallery_unknown_kind_falls_back_to_all_media(client, conn):
    creator_id = seed_every_kind(conn)

    body = client.get(f"/c/{creator_id}/gallery?kind=bogus").text
    tabs = kind_tab_links(body)

    assert tabs[0]["current"] is True
    assert tabs[0]["href"] == f"/c/{creator_id}/gallery"
    assert body.count('class="gallery-thumb"') == 5


def test_gallery_empty_kind_shows_tab_specific_state(client, conn):
    creator_id = make_creator(conn)
    add_image(conn, add_post(conn, creator_id, 0), sha(1))

    body = client.get(f"/c/{creator_id}/gallery?kind=video").text

    assert "No videos match these filters" in body
    assert f'href="/c/{creator_id}/gallery"' in body
    assert "Clear filters" in body
    assert kind_tab_links(body)[2]["current"] is True

    # An archive with no media at all keeps the creator-level empty state.
    empty_creator = make_creator(conn, service_id="empty")
    empty = client.get(f"/c/{empty_creator}/gallery?kind=video").text
    assert "No media in this archive" in empty
    assert "No videos match these filters" not in empty


def test_gallery_image_tab_removes_duration_sort_options(client, conn):
    creator_id = seed_every_kind(conn)

    images = client.get(f"/c/{creator_id}/gallery?kind=image").text
    assert 'value="duration_desc"' not in images
    assert 'value="duration_asc"' not in images

    for url in (
        f"/c/{creator_id}/gallery",
        f"/c/{creator_id}/gallery?kind=video",
    ):
        body = client.get(url).text
        assert 'value="duration_desc"' in body
        assert 'value="duration_asc"' in body

