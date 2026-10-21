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
        "last_synced_at": db.now_iso(),
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


def add_file(
    conn,
    post_id: int,
    sha: str,
    kind: str,
    *,
    position: int = 0,
    mime_type: str | None = None,
    filename: str | None = None,
    duration_ms: int | None = None,
) -> None:
    extension = {"video": "mp4", "gif": "gif", "image": "jpg", "audio": "m4a"}.get(
        kind, "bin"
    )
    if filename and "." in filename:
        extension = filename.rsplit(".", 1)[1].lower()
    mime = mime_type or {
        "video": "video/mp4",
        "gif": "image/gif",
        "image": "image/jpeg",
        "audio": "audio/mp4",
    }.get(kind, "application/octet-stream")
    existing = db.list_files(conn, post_id)
    existing.append(
        {
            "sha256": sha,
            "kind": kind,
            "raw_kind": kind,
            "mime_type": mime,
            "bytes": 1000,
            "width": 1080,
            "height": 1920,
            "duration_ms": duration_ms,
            "original_filename": filename or f"{sha[:8]}.{extension}",
            "url": f"https://e1.cum.st/media/{sha}/original.{extension}",
            "thumb_url": f"https://img.cum.st/thumbnail/{sha}/preview.webp",
            "position": position,
            "variants": [
                {
                    "name": f"original.{extension}",
                    "url": f"https://e1.cum.st/media/{sha}/original.{extension}",
                }
            ],
        }
    )
    db.replace_files(conn, post_id, existing)
    conn.commit()


def sha(number: int) -> str:
    return f"{number:064x}"


# --------------------------------------------------------------------------- #
# T7.3 - creator summary, media filter and post-card badges
# --------------------------------------------------------------------------- #


def test_creator_summary_counts_media_kinds(conn):
    creator_id = make_creator(conn)
    image_only = add_post(conn, creator_id, 0)
    video_only = add_post(conn, creator_id, 1)
    mixed = add_post(conn, creator_id, 2)
    add_post(conn, creator_id, 3)

    add_file(conn, image_only, sha(1), "image")
    add_file(conn, video_only, sha(2), "video")
    add_file(conn, mixed, sha(3), "image")
    add_file(conn, mixed, sha(4), "video")
    add_file(conn, mixed, sha(5), "gif")
    # Same video attached twice must count once.
    add_file(conn, video_only, sha(4), "video", position=1)

    summary = db.creator_summary(conn, creator_id)

    assert summary["images"] == 2
    assert summary["videos"] == 2
    assert summary["gifs"] == 1
    assert summary["audio"] == 0
    assert summary["other_files"] == 0
    assert summary["files"] == 6


def test_list_posts_media_filter_composes(conn):
    creator_id = make_creator(conn)
    video = add_post(conn, creator_id, 0, title="Video pin")
    image = add_post(conn, creator_id, 1, title="Image pin")
    add_file(conn, video, sha(1), "video")
    add_file(conn, image, sha(2), "image")
    db.replace_tags(conn, video, [{"slug": "pin-up", "label": "Pin-up"}])
    db.replace_tags(conn, image, [{"slug": "pin-up", "label": "Pin-up"}])

    rows, total = db.list_posts(conn, creator_id, media_kind="video")
    assert total == 1
    assert [row["service_id"] for row in rows] == ["900000"]

    rows, total = db.list_posts(
        conn, creator_id, media_kind="video", tag="pin-up", has_links=False
    )
    assert total == 1
    assert rows[0]["service_id"] == "900000"

    rows, total = db.list_posts(conn, creator_id, media_kind="gif")
    assert total == 0


def test_creator_page_reports_and_links_media(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 0), sha(1), "image")
    add_file(conn, add_post(conn, creator_id, 1), sha(2), "video")
    add_file(conn, add_post(conn, creator_id, 2), sha(3), "video")
    add_file(conn, add_post(conn, creator_id, 2), sha(4), "gif")

    body = client.get(f"/c/{creator_id}").text

    assert "<dt>Videos</dt><dd><a href=\"/c/1?media=video\">2</a></dd>" in body
    assert "<dt>Images</dt><dd><a href=\"/c/1?media=image\">1</a></dd>" in body
    assert "<dt>GIFs</dt><dd><a href=\"/c/1?media=gif\">1</a></dd>" in body
    assert "Direct links" in body
    assert 'href="/c/1/gallery?kind=video"' in body
    assert '<option value="video"' in body


def test_post_cards_show_media_badges_only_where_present(client, conn):
    creator_id = make_creator(conn, post_count=3)
    video = add_post(conn, creator_id, 0, title="Five videos")
    for index in range(5):
        add_file(conn, video, sha(index), "video", position=index)
    add_file(conn, add_post(conn, creator_id, 1, title="Still"), sha(20), "image")
    add_post(conn, creator_id, 2, title="Nothing")

    body = client.get(f"/c/{creator_id}").text

    assert "Video × 5" in body
    assert "Image × 1" in body
    # Only the video post carries the badge; the image and empty posts do not.
    assert body.count("Video × 5") == 1
    assert body.count("Image × 1") == 1


def test_media_filter_pagination_keeps_the_filter(client, conn):
    creator_id = make_creator(conn, post_count=60)
    for index in range(55):
        add_file(conn, add_post(conn, creator_id, index), sha(index), "video")
    for index in range(55, 60):
        add_file(conn, add_post(conn, creator_id, index), sha(index), "image")

    first = client.get(f"/c/{creator_id}?media=video")
    second = client.get(f"/c/{creator_id}?media=video&page=2")

    assert first.text.count('class="post-row"') == 50
    assert second.text.count('class="post-row"') == 5
    assert "media=video" in second.text


# --------------------------------------------------------------------------- #
# T7.4 - post page playback derives from the normalized kind
# --------------------------------------------------------------------------- #


def test_mp4_backed_gif_renders_and_plays_as_video(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 0, title="Silent clip")
    add_file(
        conn,
        post_id,
        sha(1),
        "gif",
        mime_type="image/gif",
        filename="clip.mp4",
        duration_ms=12_000,
    )

    body = client.get(f"/c/{creator_id}/p/900000").text
    row = body.split("data-attachment", 1)[1].split("</article>", 1)[0]

    assert 'data-kind="video"' in row
    assert "<video" in row
    assert "<img" not in row.split("<video", 1)[1]
    assert "data-variants" in row
    assert "original.mp4" in row
    assert "12 s" in row or "0:12" in row


def test_real_gif_still_renders_as_an_image(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 0)
    add_file(conn, post_id, sha(2), "gif", filename="mood.gif")

    body = client.get(f"/c/{creator_id}/p/900000").text
    row = body.split("data-attachment", 1)[1].split("</article>", 1)[0]

    assert 'data-kind="gif"' in row
    assert "data-media-image" in row
    assert "<video" not in row


def test_image_with_a_video_mime_is_treated_as_video(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 0)
    add_file(
        conn,
        post_id,
        sha(3),
        "image",
        mime_type="video/mp4",
        filename="clip.mp4",
        duration_ms=5000,
    )

    body = client.get(f"/c/{creator_id}/p/900000").text
    row = body.split("data-attachment", 1)[1].split("</article>", 1)[0]

    assert 'data-kind="video"' in row
    assert "<video" in row


# --------------------------------------------------------------------------- #
# T7.5 - empty upstream tags are explained, not faked
# --------------------------------------------------------------------------- #


def test_creator_without_tags_hides_the_select_and_explains(client, conn):
    creator_id = make_creator(conn)
    add_post(conn, creator_id, 0)

    body = client.get(f"/c/{creator_id}").text

    assert 'name="tag"' not in body
    assert "No tags provided by cum.st for this creator" in body
    assert "not derived from captions" in body
    # Every other filter is still there.
    assert 'name="media"' in body
    assert 'name="category"' in body


def test_creator_with_tags_keeps_the_select_and_hides_the_note(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 0)
    db.replace_tags(conn, post_id, [{"slug": "pin-up", "label": "Pin-up"}])

    body = client.get(f"/c/{creator_id}").text

    assert 'name="tag"' in body
    assert "Pin-up" in body
    assert "No tags provided by cum.st" not in body


def test_active_tag_with_no_matching_facets_can_be_cleared(client, conn):
    creator_id = make_creator(conn)
    add_post(conn, creator_id, 0)

    body = client.get(f"/c/{creator_id}?tag=ghost").text

    assert 'name="tag"' in body
    assert "Any" in body
    assert 'value="ghost" selected' in body


def test_gallery_without_tags_hides_the_select_and_explains(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 0), sha(1), "image")

    body = client.get(f"/c/{creator_id}/gallery").text

    assert 'name="tag"' not in body
    assert "No tags provided by cum.st for this creator" in body
    assert 'name="kind"' in body
