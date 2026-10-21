from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

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
    existing.append(
        {
            "sha256": sha,
            "kind": kind,
            "mime_type": "image/gif" if kind == "gif" else "image/jpeg",
            "bytes": size,
            "width": width,
            "height": height,
            "original_filename": f"{sha[:8]}.jpg",
            "url": f"https://e1.cum.st/media/{sha}/original.jpg",
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
    assert body.count("data-gallery-item") == 3
    assert "img.cum.st/thumbnail" in body
    # Full-size edge media is only fetched by the lightbox, never on load:
    # the URL sits in data attributes, but nothing points an <img> at it.
    assert 'src="https://e1.cum.st' not in body
    assert body.count('loading="lazy"') == 3
    assert 'id="gallery-lightbox"' in body
    assert "data-lightbox-image" in body
    assert "3</strong> images" in body


def test_gallery_deduplicates_the_same_sha_across_posts(client, conn):
    creator_id = make_creator(conn)
    first = add_post(conn, creator_id, 0, title="First")
    second = add_post(conn, creator_id, 1, title="Second")
    add_image(conn, first, sha(42))
    add_image(conn, second, sha(42))

    response = client.get(f"/c/{creator_id}/gallery")

    assert response.status_code == 200
    assert response.text.count("data-gallery-item") == 1
    assert "in 2 posts" in response.text

    rows, total = db.list_gallery_images(conn, creator_id)
    assert total == 1
    assert rows[0]["copies"] == 2
    assert rows[0]["sha256"] == sha(42)


def test_gallery_paginates_at_sixty_and_keeps_order(client, conn):
    creator_id = make_creator(conn)
    for index in range(61):
        post_id = add_post(conn, creator_id, index)
        add_image(conn, post_id, sha(index))

    first = client.get(f"/c/{creator_id}/gallery")
    second = client.get(f"/c/{creator_id}/gallery?page=2")

    assert first.status_code == 200
    assert first.text.count("data-gallery-item") == 60
    assert second.text.count("data-gallery-item") == 1

    rows_first, total = db.list_gallery_images(conn, creator_id, limit=60, offset=0)
    rows_second, _ = db.list_gallery_images(conn, creator_id, limit=60, offset=60)
    assert total == 61
    assert len(rows_first) == 60
    assert len(rows_second) == 1
    assert {row["sha256"] for row in rows_first}.isdisjoint(
        {row["sha256"] for row in rows_second}
    )
    # Newest post first.
    assert rows_first[0]["sha256"] == sha(60)


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
    assert portrait_body.count("data-gallery-item") == 1
    assert "Portrait pin</a>" in portrait_body
    assert "Landscape</a>" not in portrait_body

    tag_body = client.get(f"/c/{creator_id}/gallery?tag=pin-up").text
    assert tag_body.count("data-gallery-item") == 2

    search_body = client.get(f"/c/{creator_id}/gallery?q=landscape").text
    assert search_body.count("data-gallery-item") == 1
    assert "Landscape</a>" in search_body

    size_body = client.get(f"/c/{creator_id}/gallery?min_bytes=102400").text
    assert size_body.count("data-gallery-item") == 2
    assert "Tiny icon" not in size_body


def test_gallery_empty_states(client, conn):
    creator_id = make_creator(conn)
    add_post(conn, creator_id, 0)

    empty = client.get(f"/c/{creator_id}/gallery")
    assert empty.status_code == 200
    assert "No media in this archive" in empty.text
    assert "data-gallery-item" not in empty.text

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

    assert body.count("data-gallery-item") == 3
    assert 'data-kind="video"' in body
    assert 'data-kind="gif"' in body
    assert "gallery-play" in body
    assert "0:09" in body
    assert "1</strong> image" in body
    assert "1</strong> video" in body
    assert "1</strong> gif" in body
    # Edge media only ever lives in data attributes on load.
    assert 'src="https://e1.cum.st' not in body
    assert "data-full=\"https://e1.cum.st" in body

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
    assert videos.count("data-gallery-item") == 1
    assert 'data-kind="video"' in videos
    assert 'data-kind="image"' not in videos
    assert 'data-kind="gif"' not in videos

    images = client.get(f"/c/{creator_id}/gallery?kind=image").text
    assert images.count("data-gallery-item") == 1
    assert 'data-kind="image"' in images

    everything = client.get(f"/c/{creator_id}/gallery?kind=bogus").text
    assert everything.count("data-gallery-item") == 3


def test_gallery_kind_composes_with_orientation(client, conn):
    creator_id = make_creator(conn)
    add_video(conn, add_post(conn, creator_id, 0), sha(1), width=1080, height=1920)
    add_video(conn, add_post(conn, creator_id, 1), sha(2), width=1920, height=1080)
    add_image(conn, add_post(conn, creator_id, 2), sha(3), width=1080, height=1920)

    portrait = client.get(
        f"/c/{creator_id}/gallery?kind=video&orientation=portrait"
    ).text

    assert portrait.count("data-gallery-item") == 1
    assert 'data-kind="video"' in portrait


def test_gallery_deduplicates_videos_across_posts(client, conn):
    creator_id = make_creator(conn)
    first = add_post(conn, creator_id, 0, title="First")
    second = add_post(conn, creator_id, 1, title="Second")
    add_video(conn, first, sha(9))
    add_video(conn, second, sha(9))

    body = client.get(f"/c/{creator_id}/gallery?kind=video").text

    assert body.count("data-gallery-item") == 1
    assert "in 2 posts" in body
    rows, total = db.list_gallery_images(conn, creator_id, kind="video")
    assert total == 1
    assert rows[0]["copies"] == 2
    assert rows[0]["duration_ms"] == 9000


def test_gallery_quiet_mode_is_still_a_static_grid(client, conn):
    creator_id = make_creator(conn)
    add_video(conn, add_post(conn, creator_id, 0), sha(1))

    body = client.get(f"/c/{creator_id}/gallery?quiet=1").text

    assert body.count("data-gallery-item") == 1
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


def test_gallery_duration_sort_orders_videos_and_sinks_unknowns(client, conn):
    creator_id = make_creator(conn)
    short_post = add_post(conn, creator_id, 0)
    long_post = add_post(conn, creator_id, 1)
    unknown_post = add_post(conn, creator_id, 2)
    image_post = add_post(conn, creator_id, 3)
    add_video(conn, short_post, sha(1), duration_ms=5_000)
    add_video(conn, long_post, sha(2), duration_ms=90_000)
    add_video(conn, unknown_post, sha(3), duration_ms=None)
    add_image(conn, image_post, sha(4))

    longest, total = db.list_gallery_images(conn, creator_id, sort="duration_desc")
    assert total == 4
    assert [row["sha256"] for row in longest] == [sha(2), sha(1), sha(3), sha(4)]

    shortest, _ = db.list_gallery_images(conn, creator_id, sort="duration_asc")
    assert [row["sha256"] for row in shortest] == [sha(1), sha(2), sha(3), sha(4)]

    # A zero duration is unknown, not "0 seconds at the top".
    zero_post = add_post(conn, creator_id, 4)
    add_video(conn, zero_post, sha(5), duration_ms=0)
    shortest, _ = db.list_gallery_images(conn, creator_id, sort="duration_asc")
    shortest_shas = [row["sha256"] for row in shortest]
    assert shortest_shas[:2] == [sha(1), sha(2)]
    assert shortest_shas[-1] == sha(4)
    assert shortest_shas.index(sha(5)) > shortest_shas.index(sha(2))

    # Same rule inside a video-only grid: unknowns always last.
    videos_desc, video_total = db.list_gallery_images(
        conn, creator_id, kind="video", sort="duration_desc"
    )
    assert video_total == 4
    assert [row["sha256"] for row in videos_desc] == [sha(2), sha(1), sha(5), sha(3)]
    videos_asc, _ = db.list_gallery_images(conn, creator_id, kind="video", sort="duration_asc")
    assert [row["sha256"] for row in videos_asc] == [sha(1), sha(2), sha(5), sha(3)]

    # Unknown sort keys are rejected before they can reach the SQL string.
    with pytest.raises(ValueError):
        db.list_gallery_images(conn, creator_id, sort="duration; DROP TABLE files")


def test_gallery_duration_sort_paging_has_no_duplicates(client, conn):
    creator_id = make_creator(conn)
    # Durations are deliberately not in published order so the sort matters.
    durations = [30_000, 5_000, 120_000, 60_000, 15_000]
    for index, duration in enumerate(durations):
        add_video(conn, add_post(conn, creator_id, index), sha(index), duration_ms=duration)

    seen: list[str] = []
    for offset in (0, 2, 4):
        rows, _ = db.list_gallery_images(
            conn, creator_id, sort="duration_desc", limit=2, offset=offset
        )
        seen.extend(row["sha256"] for row in rows)

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
    for index in range(61):
        add_video(
            conn, add_post(conn, creator_id, index), sha(index), duration_ms=index * 1_000
        )

    body = client.get(f"/c/{creator_id}/gallery?kind=video&sort=duration_desc").text

    assert 'value="duration_desc" selected' in body
    assert "kind=video&amp;sort=duration_desc&amp;page=2" in body
    page_two = client.get(
        f"/c/{creator_id}/gallery?kind=video&sort=duration_desc&page=2"
    ).text
    assert page_two.count("data-gallery-item") == 1
    # Longest video (the newest post) is on page 1, the shortest on page 2.
    assert sha(60) in body
    assert sha(60) not in page_two
    assert sha(0) in page_two
