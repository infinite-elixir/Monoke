from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from monoke import db
from monoke.config import get_settings
from monoke.web.app import create_app
from monoke.web.templating import STATIC_DIR, asset


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
        "service": "onlyfans",
        "service_id": "39058172",
        "name": "Belledelphine",
        "display_name": "Belledelphine",
    }
    values.update(overrides)
    creator_id = db.upsert_creator(conn, **values)
    conn.commit()
    return creator_id


def add_post(conn, creator_id: int, number: int, **overrides) -> int:
    values = {
        "creator_id": creator_id,
        "service": "onlyfans",
        "service_id": str(2183930591 + number),
        "title": f"Post {number}",
        "published": 1784196537 + number,
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
    kind: str = "image",
    *,
    position: int = 0,
    url: str | None = None,
    thumb_url: str | None = None,
    duration_ms: int | None = None,
) -> None:
    extension = {"video": "mp4", "gif": "gif", "image": "jpg", "audio": "m4a"}.get(
        kind, "bin"
    )
    existing = db.list_files(conn, post_id)
    existing.append(
        {
            "sha256": sha,
            "kind": kind,
            "raw_kind": kind,
            "mime_type": {
                "video": "video/mp4",
                "gif": "image/gif",
                "image": "image/jpeg",
                "audio": "audio/mp4",
            }.get(kind, "application/octet-stream"),
            "bytes": 8166926 if kind == "video" else 250_000,
            "width": 1080,
            "height": 1920,
            "duration_ms": duration_ms,
            "original_filename": f"{sha[:8]}.{extension}",
            "url": url
            if url is not None
            else f"https://e3.cum.st/media/{sha}/original.{extension}",
            "thumb_url": thumb_url
            if thumb_url is not None
            else f"https://img.cum.st/thumbnail/{sha}/preview.webp",
            "position": position,
            "variants": [
                {
                    "name": f"original.{extension}",
                    "url": f"https://e3.cum.st/media/{sha}/original.{extension}",
                    "bytes": 8166926 if kind == "video" else 250_000,
                }
            ],
        }
    )
    db.replace_files(conn, post_id, existing)
    conn.commit()


def sha(number: int) -> str:
    return f"{number:064x}"


def embedded_state(body: str) -> dict:
    match = re.search(
        r'<script type="application/json" id="viewer-initial">(.*?)</script>',
        body,
        re.S,
    )
    assert match is not None
    return json.loads(match.group(1))


def test_default_opens_newest_media_and_skips_text_only_posts(client, conn):
    creator_id = make_creator(conn)
    oldest = add_post(conn, creator_id, 0, title="Oldest media")
    add_file(conn, oldest, sha(1))
    add_post(conn, creator_id, 1, title="Text only between")
    newest = add_post(conn, creator_id, 2, title="Newest media")
    add_file(conn, newest, sha(2), kind="video", duration_ms=10_000)
    add_post(conn, creator_id, 3, title="Text only newest")

    response = client.get(f"/c/{creator_id}/viewer")

    assert response.status_code == 200
    state = embedded_state(response.text)
    assert state["post"]["service_id"] == str(2183930591 + 2)
    assert state["post"]["title"] == "Newest media"
    assert state["post"]["media_count"] == 1
    assert state["media"][0]["kind"] == "video"
    assert state["previous_post"] is None
    assert state["next_post"] == str(2183930591 + 0)
    assert state["creator"] == {
        "id": creator_id,
        "name": "Belledelphine",
        "service": "onlyfans",
    }
    assert state["manifest"]["version"] == 1
    assert [post["service_id"] for post in state["manifest"]["posts"]] == [
        str(2183930591 + 2),
        str(2183930591 + 0),
    ]
    assert state["manifest"]["posts"][0]["data_url"] == (
        f"/c/{creator_id}/viewer/data?post={2183930591 + 2}"
    )
    assert state["manifest"]["posts"][1]["post_url"] == (
        f"/c/{creator_id}/p/{2183930591 + 0}"
    )


def test_post_and_index_query_restores_exact_item_and_clamps(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 0)
    for index in range(3):
        add_file(conn, post_id, sha(index), position=index)
    service_id = str(2183930591)

    middle = client.get(f"/c/{creator_id}/viewer?post={service_id}&i=1")
    state = embedded_state(middle.text)
    assert state["index"] == 1
    assert state["media"][1]["index"] == 1
    assert f'src="https://e3.cum.st/media/{sha(1)}/original.jpg"' in middle.text
    assert "2 / 3" in middle.text

    too_large = client.get(f"/c/{creator_id}/viewer?post={service_id}&i=99")
    assert embedded_state(too_large.text)["index"] == 2
    assert "3 / 3" in too_large.text

    negative = client.get(f"/c/{creator_id}/viewer?post={service_id}&i=-4")
    assert embedded_state(negative.text)["index"] == 0


def test_post_without_media_404s_and_unknown_creator_404s(client, conn):
    creator_id = make_creator(conn)
    text_post = add_post(conn, creator_id, 0, title="Only words")
    service_id = str(2183930591)

    assert client.get(f"/c/{creator_id}/viewer?post={service_id}").status_code == 404
    assert client.get(f"/c/{creator_id}/viewer?post=does-not-exist").status_code == 404
    assert client.get("/c/999/viewer").status_code == 404
    assert client.get("/c/999/viewer/data").status_code == 404

    missing = client.get(
        f"/c/{creator_id}/viewer/data?post=does-not-exist"
    )
    assert missing.status_code == 404
    assert "error" in missing.json()

    # A creator with no eligible media still gets the empty state, not a 500.
    empty = client.get(f"/c/{creator_id}/viewer")
    assert empty.status_code == 200
    assert "No media in this archive" in empty.text


def test_data_endpoint_matches_contract_and_parses_variants(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 0, title="Payload")
    add_file(conn, post_id, sha(1), kind="video", duration_ms=10_000)

    response = client.get(
        f"/c/{creator_id}/viewer/data?post={db.get_post(conn, post_id)['service_id']}"
    )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) >= {
        "creator",
        "post",
        "media",
        "previous_post",
        "next_post",
    }
    assert "manifest" not in payload
    assert payload["post"] == {
        "service_id": str(2183930591),
        "title": "Payload",
        "published": 1784196537,
        "post_url": f"/c/{creator_id}/p/2183930591",
        "media_count": 1,
        "bookmarked": False,
    }
    item = payload["media"][0]
    assert item["bookmarked"] is False
    assert item["index"] == 0
    assert item["kind"] == "video"
    assert item["mime_type"] == "video/mp4"
    assert item["width"] == 1080
    assert item["height"] == 1920
    assert item["duration_ms"] == 10_000
    assert item["bytes"] == 8166926
    assert item["post_service_id"] == "2183930591"
    assert item["post_position"] == 0
    assert item["variants"][0]["name"] == "original.mp4"
    assert payload["index"] == 0


def test_neighbour_lookup_skips_text_only_posts_in_both_directions(conn):
    creator_id = make_creator(conn)
    first = add_post(conn, creator_id, 0)
    add_file(conn, first, sha(1))
    add_post(conn, creator_id, 1)  # text only
    second = add_post(conn, creator_id, 2)
    add_file(conn, second, sha(2))
    add_post(conn, creator_id, 3)  # text only
    third = add_post(conn, creator_id, 4)
    add_file(conn, third, sha(3))

    first_row = db.get_post(conn, first)
    second_row = db.get_post(conn, second)
    third_row = db.get_post(conn, third)

    assert db.viewer_neighbour(conn, creator_id, third_row, "newer") is None
    assert db.viewer_neighbour(conn, creator_id, second_row, "newer") == str(2183930595)
    assert db.viewer_neighbour(conn, creator_id, first_row, "newer") == str(2183930593)
    assert db.viewer_neighbour(conn, creator_id, first_row, "older") is None
    assert db.viewer_neighbour(conn, creator_id, second_row, "older") == str(2183930591)
    assert db.viewer_neighbour(conn, creator_id, third_row, "older") == str(2183930593)


def test_neighbour_order_breaks_ties_by_post_id(conn):
    creator_id = make_creator(conn)
    same_time = 1_700_000_000
    older_row = add_post(conn, creator_id, 0, published=same_time)
    add_file(conn, older_row, sha(1))
    newer_row = add_post(conn, creator_id, 1, published=same_time)
    add_file(conn, newer_row, sha(2))

    old = db.get_post(conn, older_row)
    new = db.get_post(conn, newer_row)

    assert db.viewer_neighbour(conn, creator_id, old, "newer") == str(2183930592)
    assert db.viewer_neighbour(conn, creator_id, old, "older") is None
    assert db.viewer_neighbour(conn, creator_id, new, "older") == str(2183930591)
    assert db.viewer_neighbour(conn, creator_id, new, "newer") is None


def test_viewer_does_not_dedupe_sha_across_posts(client, conn):
    creator_id = make_creator(conn)
    first = add_post(conn, creator_id, 0, title="First copy")
    second = add_post(conn, creator_id, 1, title="Second copy")
    add_file(conn, first, sha(42))
    add_file(conn, second, sha(42))

    for post_number, title in ((0, "First copy"), (1, "Second copy")):
        body = client.get(
            f"/c/{creator_id}/viewer?post={2183930591 + post_number}"
        ).text
        state = embedded_state(body)
        assert state["post"]["title"] == title
        assert [item["sha256"] for item in state["media"]] == [sha(42)]


def test_viewer_media_orders_by_position_then_id(conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 0)
    add_file(conn, post_id, sha(3), position=2)
    add_file(conn, post_id, sha(1), position=0)
    add_file(conn, post_id, sha(2), position=1)

    media = db.viewer_media(conn, creator_id, "2183930591")

    assert [item["sha256"] for item in media] == [sha(1), sha(2), sha(3)]
    assert [item["index"] for item in media] == [0, 1, 2]


# --------------------------------------------------------------------------- #
# T8.2 - shell and media rendering
# --------------------------------------------------------------------------- #


def test_viewer_shell_has_one_stage_state_and_page_script(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 0), sha(1))

    body = client.get(f"/c/{creator_id}/viewer").text

    assert body.count("data-viewer-stage") == 1
    assert body.count('id="viewer-initial"') == 1
    assert "/static/viewer.js" in body
    assert 'class="viewer-page"' in body
    assert "/static/app.js" in body


def test_viewer_renders_image_gif_and_video_elements(client, conn):
    creator_id = make_creator(conn)
    image = add_post(conn, creator_id, 0, title="Image post")
    add_file(conn, image, sha(1), kind="image")
    gif = add_post(conn, creator_id, 1, title="GIF post")
    add_file(conn, gif, sha(2), kind="gif")
    video = add_post(conn, creator_id, 2, title="Video post")
    add_file(conn, video, sha(3), kind="video", duration_ms=9_000)

    image_body = client.get(f"/c/{creator_id}/viewer?post=2183930591").text
    assert '<img class="viewer-media-el viewer-image"' in image_body
    assert f'src="https://e3.cum.st/media/{sha(1)}/original.jpg"' in image_body
    assert 'alt="Image post"' in image_body
    assert 'width="1080"' in image_body
    assert 'height="1920"' in image_body

    gif_body = client.get(f"/c/{creator_id}/viewer?post=2183930592").text
    assert '<img class="viewer-media-el viewer-image"' in gif_body
    assert f'src="https://e3.cum.st/media/{sha(2)}/original.gif"' in gif_body

    video_body = client.get(f"/c/{creator_id}/viewer?post=2183930593").text
    video_tag = video_body.split("<video", 1)[1].split("</video>", 1)[0]
    assert "controls" in video_tag
    assert "playsinline" in video_tag
    assert 'preload="metadata"' in video_tag
    assert f'poster="https://img.cum.st/thumbnail/{sha(3)}/preview.webp"' in video_tag
    assert f'src="https://e3.cum.st/media/{sha(3)}/original.mp4"' in video_tag
    assert "autoplay" not in video_tag


def test_viewer_renders_video_at_every_position_in_a_mixed_post(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 0, title="Mixed")
    add_file(conn, post_id, sha(1), kind="video", position=0, duration_ms=4_000)
    add_file(conn, post_id, sha(2), kind="image", position=1)

    first = client.get(f"/c/{creator_id}/viewer?post=2183930591").text
    video_tag = first.split("<video", 1)[1].split("</video>", 1)[0]
    assert f'src="https://e3.cum.st/media/{sha(1)}/original.mp4"' in video_tag
    assert f'poster="https://img.cum.st/thumbnail/{sha(1)}/preview.webp"' in video_tag
    assert "controls" in video_tag and "autoplay" not in video_tag

    later = client.get(
        f"/c/{creator_id}/viewer?post=2183930591"
        f"&i=0&sha256={sha(2)}&gf=kind%3Dimage"
    ).text
    state = embedded_state(later)
    assert state["index"] == 0
    assert [item["sha256"] for item in state["media"]] == [sha(2)]
    assert f'src="https://e3.cum.st/media/{sha(2)}/original.jpg"' in later


def test_viewer_normalizes_contradictory_stored_kind_at_read_time(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 0)
    # Upstream called an MP4 an image. It must render as a video element, not a
    # black `<img>`, and the gallery kind filter must agree with the viewer.
    db.replace_files(
        conn,
        post_id,
        [
            {
                "sha256": sha(1),
                "kind": "image",
                "raw_kind": "image",
                "mime_type": "image/png",
                "bytes": 1000,
                "width": 1080,
                "height": 1920,
                "duration_ms": 7_000,
                "original_filename": "clip.mp4",
                "url": f"https://e3.cum.st/media/{sha(1)}/original.mp4",
                "thumb_url": f"https://img.cum.st/thumbnail/{sha(1)}/preview.webp",
                "position": 0,
                "variants": [{"name": "original.mp4"}],
            }
        ],
    )
    conn.commit()

    media = db.viewer_media(conn, creator_id, "2183930591")
    assert [item["kind"] for item in media] == ["video"]
    body = client.get(f"/c/{creator_id}/viewer?post=2183930591").text
    assert "<video" in body
    assert "<img class=\"viewer-media-el viewer-image\"" not in body

    groups, _ = db.list_gallery_posts(conn, creator_id, kind="video")
    assert [group["service_id"] for group in groups] == ["2183930591"]
    groups, _ = db.list_gallery_posts(conn, creator_id, kind="image")
    assert groups == []


@pytest.mark.parametrize(
    "signals,expected",
    [
        (
            dict(
                raw_kind="gif",
                mime_type="image/gif",
                original_filename="loop.gif",
                url="https://x/loop.gif",
            ),
            "gif",
        ),
        (
            # No extension anywhere: the mime type is a still image, not a gif
            # payload, so SQL and Python must both say image.
            dict(raw_kind="gif", mime_type="image/gif", url="https://x/blob"),
            "image",
        ),
        (
            # Duration alone is a video signal unless a still/gif extension
            # contradicts it (an animated still with a bogus duration).
            dict(
                raw_kind="image",
                mime_type="image/png",
                original_filename="still.png",
                duration_ms=4_000,
            ),
            "image",
        ),
        (
            dict(
                raw_kind="file",
                mime_type="application/octet-stream",
                url="https://x/clip.webm?token=1",
                duration_ms=4_000,
            ),
            "video",
        ),
    ],
)
def test_effective_kind_sql_matches_python(conn, signals, expected):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 0)
    row = {
        "sha256": sha(9),
        "kind": signals.get("raw_kind") or "file",
        "raw_kind": signals.get("raw_kind") or "file",
        "mime_type": signals.get("mime_type"),
        "bytes": 1000,
        "width": 100,
        "height": 100,
        "duration_ms": signals.get("duration_ms"),
        "original_filename": signals.get("original_filename"),
        "url": signals.get("url") or f"https://e3.cum.st/media/{sha(9)}/blob",
        "thumb_url": f"https://img.cum.st/thumbnail/{sha(9)}/preview.webp",
        "position": 0,
        "variants": [],
    }
    db.replace_files(conn, post_id, [row])
    conn.commit()

    media = db.viewer_media(conn, creator_id, "2183930591")
    assert [item["kind"] for item in media] == [expected]
    groups, _ = db.list_gallery_posts(conn, creator_id, kind=expected)
    assert [group["service_id"] for group in groups] == ["2183930591"]


def test_viewer_marks_missing_media_unavailable(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 0)
    add_file(conn, post_id, sha(1), url="", thumb_url=None)

    body = client.get(f"/c/{creator_id}/viewer").text

    assert "Media unavailable" in body
    assert "Open the post" in body


def test_viewer_script_is_not_loaded_on_other_pages(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 0), sha(1))

    for path in ("/", f"/c/{creator_id}", f"/c/{creator_id}/gallery"):
        body = client.get(path).text
        assert "/static/app.js" in body
        assert "/static/viewer.js" not in body


def test_viewer_entry_links_and_return_url(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 0), sha(1))

    creator_body = client.get(f"/c/{creator_id}").text
    assert f'href="/c/{creator_id}/viewer"' in creator_body
    assert "View latest" in creator_body

    gallery_body = client.get(f"/c/{creator_id}/gallery").text
    assert "Open viewer" in gallery_body
    viewer_href = re.search(
        r'href="([^"]*/viewer\?[^"]*from=gallery[^"]*)"', gallery_body
    )
    assert viewer_href is not None
    viewer_query = parse_qs(urlsplit(viewer_href.group(1).replace("&amp;", "&")).query)
    assert viewer_query["post"] == ["2183930591"]
    assert viewer_query["i"] == ["0"]
    assert viewer_query["next"]

    viewer_body = client.get(f"/c/{creator_id}/viewer?from=gallery").text
    assert f'data-return-url="/c/{creator_id}/gallery"' in viewer_body


# --------------------------------------------------------------------------- #
# T8.11 - the invisible click regions are gone
# --------------------------------------------------------------------------- #


def test_viewer_renders_no_invisible_navigation_zones(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 0), sha(1))

    body = client.get(f"/c/{creator_id}/viewer").text

    assert "viewer-zone" not in body
    assert "data-viewer-action" not in body
    # No toolbar or visible navigation text is rendered either.
    assert "viewer-toolbar" not in body
    # The stage keeps its media, counter, hint and flash only.
    assert "data-viewer-stage" in body
    assert "data-viewer-media" in body


def test_viewer_script_and_stylesheet_have_no_zone_code():
    from monoke.web.templating import STATIC_DIR

    source = (STATIC_DIR / "viewer.js").read_text(encoding="utf-8")
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")

    assert "viewer-zone" not in source
    assert "onZoneClick" not in source
    assert "data-viewer-action" not in source
    assert "afterTouch" not in source
    assert "lastTouchNav" not in source
    assert "updateBottomZone" not in source
    assert "nextPostZone" not in source
    assert "viewer-zone" not in css

    # Tapping the stage only dismisses the hint; it never navigates.
    click_block = source.split("function onStageClick(", 1)[1].split(
        "function onCloseClick(", 1
    )[0]
    assert "dismissHint()" in click_block
    assert "go(" not in click_block


# --------------------------------------------------------------------------- #
# T8.4 - first-run hint, focus and lifecycle
# --------------------------------------------------------------------------- #


def test_viewer_shows_first_run_hint_with_all_four_directions(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 0), sha(1))

    body = client.get(f"/c/{creator_id}/viewer").text

    assert body.count("data-viewer-hint") == 1
    assert 'class="viewer-hint"' in body
    assert 'role="status"' in body
    for label in ("Older post", "Newer post", "Previous image", "Next image"):
        assert f'<span class="viewer-hint-label">{label}</span>' in body
    assert "Esc to close · F for fullscreen · first move hides this" in body


def test_viewer_vertical_direction_contract_is_consistent():
    source = (STATIC_DIR / "viewer.js").read_text(encoding="utf-8")

    assert 'ArrowUp: "next-post"' in source
    assert 'ArrowDown: "prev-post"' in source
    assert 'go(dy > 0 ? "prev-post" : "next-post")' in source
    assert 'return delta < 0 ? "next-post" : "prev-post";' in source


def test_hint_zero_suppresses_hint_and_quiet_skips_fade(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 0), sha(1))

    quiet_hint = client.get(f"/c/{creator_id}/viewer?hint=0").text
    assert "data-viewer-hint" not in quiet_hint
    assert "viewer-hint-grid" not in quiet_hint

    quiet = client.get(f"/c/{creator_id}/viewer?quiet=1").text
    assert 'class="viewer viewer-quiet"' in quiet

    normal = client.get(f"/c/{creator_id}/viewer?quiet=0").text
    assert "viewer-quiet" not in normal


def test_viewer_close_button_is_accessible_and_not_persistent(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 0), sha(1))

    body = client.get(f"/c/{creator_id}/viewer").text

    close_tag = re.search(r"<button[^>]*data-viewer-close[^>]*>", body)
    assert close_tag is not None
    tag = close_tag.group(0)
    assert 'aria-label="Close viewer"' in tag
    assert 'class="viewer-close"' in tag
    # First focusable element inside the viewer.
    assert body.index("data-viewer-close") < body.index("data-viewer-stage")


def test_viewer_root_is_a_focusable_labelled_dialog(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 0), sha(1))

    body = client.get(f"/c/{creator_id}/viewer").text
    main_tag = re.search(r'<main id="viewer"[^>]*>', body)
    assert main_tag is not None
    tag = main_tag.group(0)

    assert 'role="dialog"' in tag
    assert 'aria-modal="true"' in tag
    assert 'aria-label="Media viewer"' in tag
    assert 'tabindex="-1"' in tag


def test_viewer_announces_post_rank_and_media_post_total(client, conn):
    creator_id = make_creator(conn)
    oldest = add_post(conn, creator_id, 0)
    add_file(conn, oldest, sha(1))
    add_post(conn, creator_id, 1)  # text-only: not a media post
    middle = add_post(conn, creator_id, 2)
    add_file(conn, middle, sha(2))
    newest = add_post(conn, creator_id, 3)
    add_file(conn, newest, sha(3))

    state = embedded_state(client.get(f"/c/{creator_id}/viewer").text)
    assert state["post_rank"] == 1
    assert state["media_post_count"] == 3

    middle_state = embedded_state(
        client.get(f"/c/{creator_id}/viewer?post=2183930593").text
    )
    assert middle_state["post_rank"] == 2
    assert middle_state["media_post_count"] == 3

    payload = client.get(f"/c/{creator_id}/viewer/data?post=2183930591").json()
    assert payload["post_rank"] == 3
    assert payload["media_post_count"] == 3


# --------------------------------------------------------------------------- #
# T8.6 - horizontal navigation crosses into neighbouring posts
# --------------------------------------------------------------------------- #


def test_viewer_state_exposes_neighbour_chain_and_destination_edges(client, conn):
    creator_id = make_creator(conn)
    service_ids = []
    for number in range(3):
        post_id = add_post(conn, creator_id, number, title=f"Post {number}")
        for position in range(3):
            add_file(conn, post_id, sha(number * 10 + position), position=position)
        service_ids.append(str(2183930591 + number))

    oldest, middle, newest = service_ids

    middle_state = db.viewer_state(conn, creator_id, middle)
    assert middle_state["previous_post"] == newest  # newer
    assert middle_state["next_post"] == oldest  # older

    newest_state = db.viewer_state(conn, creator_id, newest)
    assert newest_state["previous_post"] is None
    assert newest_state["next_post"] == middle

    oldest_state = db.viewer_state(conn, creator_id, oldest)
    assert oldest_state["previous_post"] == middle
    assert oldest_state["next_post"] is None

    # Right-at-the-edge lands on item 0 of the older post; left-at-the-edge
    # lands on the last item of the newer post. Both payloads must expose those
    # exact endpoints, because the JS clamps against the target's own count.
    older_payload = client.get(
        f"/c/{creator_id}/viewer/data?post={oldest}"
    ).json()
    assert [item["index"] for item in older_payload["media"]] == [0, 1, 2]
    assert older_payload["media"][0]["sha256"] == sha(0)
    assert older_payload["media"][-1]["sha256"] == sha(2)

    newer_payload = client.get(
        f"/c/{creator_id}/viewer/data?post={newest}"
    ).json()
    assert [item["index"] for item in newer_payload["media"]] == [0, 1, 2]
    assert newer_payload["media"][0]["sha256"] == sha(20)
    assert newer_payload["media"][-1]["sha256"] == sha(22)


# --------------------------------------------------------------------------- #
# T8.7 - neighbouring image preload window
# --------------------------------------------------------------------------- #


def test_preload_window_covers_radius_and_filters_unsafe_items():
    import json
    import shutil
    import subprocess
    import textwrap

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    source = (STATIC_DIR / "viewer.js").read_text(encoding="utf-8")
    block = source.split("// BEGIN preload-window", 1)[1].split(
        "// END preload-window", 1
    )[0]
    script = (
        textwrap.dedent(block)
        + textwrap.dedent(
            """
            function media(count, overrides) {
              var items = [];
              for (var i = 0; i < count; i += 1) {
                items.push({
                  index: i,
                  kind: "image",
                  url: "https://e3.cum.st/media/" + i + "/original.jpg"
                });
              }
              Object.keys(overrides || {}).forEach(function (key) {
                var patch = overrides[key];
                Object.keys(patch).forEach(function (field) {
                  items[Number(key)][field] = patch[field];
                });
              });
              return items;
            }

            var failed = new Set(["https://e3.cum.st/media/3/original.jpg"]);
            var out = {
              radius: PRELOAD_RADIUS,
              max: PRELOAD_MAX,
              middle: preloadWindowIndices(media(10), 5, null),
              first: preloadWindowIndices(media(10), 0, null),
              last: preloadWindowIndices(media(10), 9, null),
              failed: preloadWindowIndices(media(10), 5, failed),
              kinds: preloadWindowIndices(
                media(10, {
                  2: { kind: "video" },
                  4: { kind: "gif" },
                  6: { kind: "audio" },
                  7: { url: "data:image/png;base64,AAAA" }
                }),
                5,
                null
              )
            };
            process.stdout.write(JSON.stringify(out));
            """
        )
    )
    result = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)

    assert payload["radius"] == 3
    assert payload["max"] == 6
    assert payload["middle"] == [2, 3, 4, 6, 7, 8]
    assert payload["first"] == [1, 2, 3]
    assert payload["last"] == [6, 7, 8]
    assert payload["failed"] == [2, 4, 6, 7, 8]
    assert payload["kinds"] == [3, 8]


def test_viewer_script_passes_node_syntax_check():
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    script = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "monoke"
        / "web"
        / "static"
        / "viewer.js"
    )
    result = subprocess.run(
        [node, "--check", str(script)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr



# --------------------------------------------------------------------------- #
# T8.5 - content-hashed assets and fullscreen media
# --------------------------------------------------------------------------- #


def test_viewer_links_content_hashed_assets_that_match_disk(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 0), sha(1))

    body = client.get(f"/c/{creator_id}/viewer").text

    css_url = asset("style.css")
    js_url = asset("viewer.js")
    assert f'href="{css_url}"' in body
    assert f'src="{js_url}"' in body

    css = client.get(css_url)
    assert css.status_code == 200
    assert css.headers["cache-control"] == "no-cache, must-revalidate"
    assert css.text == (STATIC_DIR / "style.css").read_text(encoding="utf-8")

    script = client.get(js_url)
    assert script.status_code == 200
    assert script.text == (STATIC_DIR / "viewer.js").read_text(encoding="utf-8")


def test_viewer_stylesheet_makes_media_fill_and_centre_the_viewport():
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")

    viewer_rule = css.split("\n.viewer {", 1)[1].split("}", 1)[0]
    assert "position: fixed" in viewer_rule
    assert "inset: 0" in viewer_rule
    assert "width: 100vw" in viewer_rule
    assert "height: 100dvh" in viewer_rule

    media_rule = css.split("\n.viewer-media {", 1)[1].split("}", 1)[0]
    assert "width: 100%" in media_rule
    assert "height: 100%" in media_rule
    assert "display: grid" in media_rule
    assert "place-items: center" in media_rule
    assert "overflow: hidden" in media_rule
    assert "min-width: 0" in media_rule
    assert "min-height: 0" in media_rule

    element_rule = css.split("\n.viewer-media-el {", 1)[1].split("}", 1)[0]
    assert "grid-area: 1 / 1" in css
    assert "max-width: 100%" in element_rule
    assert "max-height: 100%" in element_rule
    assert "object-fit: contain" in element_rule


# --------------------------------------------------------------------------- #
# T8.8 - cross-post preload and fullscreen control
# --------------------------------------------------------------------------- #


def test_neighbour_preload_images_are_edge_ordered_and_use_posters(conn):
    creator_id = make_creator(conn)
    newer = add_post(conn, creator_id, 0)
    add_file(conn, newer, sha(10), position=0)
    add_file(conn, newer, sha(11), kind="gif", position=1)
    add_file(conn, newer, sha(12), kind="video", position=2)
    add_file(
        conn,
        newer,
        sha(13),
        position=3,
        url="data:image/png;base64,AAAA",
    )
    add_file(conn, newer, sha(14), position=4)
    add_file(conn, newer, sha(15), position=5)
    add_file(conn, newer, sha(16), position=6)
    add_file(conn, newer, sha(17), kind="audio", position=7)
    current = add_post(conn, creator_id, 1)
    add_file(conn, current, sha(20), position=0)
    older = add_post(conn, creator_id, 2)
    add_file(conn, older, sha(30), position=0)
    add_file(conn, older, sha(31), position=1)
    add_file(conn, older, sha(32), kind="gif", position=2)
    add_file(conn, older, sha(33), position=3)
    add_file(conn, older, sha(34), position=4)

    post = db.get_post(conn, current)
    previous = db.neighbour_preload_images(
        conn, creator_id, post, "previous", 3
    )
    following = db.neighbour_preload_images(conn, creator_id, post, "next", 3)

    # T8.18: the edge window carries the nearest images *and* video/GIF
    # posters. Video bytes and animated GIFs are never preloaded.
    assert [item["sha256"] for item in previous] == [
        sha(34),
        sha(33),
        sha(32),
    ]
    assert [item["sha256"] for item in following] == [
        sha(10),
        sha(11),
        sha(12),
    ]
    assert [item["kind"] for item in previous] == ["image", "image", "gif"]
    assert [item["kind"] for item in following] == ["image", "gif", "video"]
    assert previous[2]["warm_url"] == previous[2]["thumb_url"]
    assert next(item for item in following if item["kind"] == "video")[
        "warm_url"
    ].endswith("/preview.webp")
    assert all(
        item["post_service_id"] == str(2183930593) for item in previous
    )
    assert all(
        item["post_service_id"] == str(2183930591) for item in following
    )


def test_viewer_state_and_endpoint_expose_bounded_neighbour_payload(client, conn):
    creator_id = make_creator(conn)
    newer = add_post(conn, creator_id, 0)
    for position in range(8):
        add_file(conn, newer, sha(100 + position), position=position)
    current = add_post(conn, creator_id, 1)
    add_file(conn, current, sha(200), position=0)
    older = add_post(conn, creator_id, 2)
    for position in range(8):
        add_file(conn, older, sha(300 + position), position=position)

    state = db.viewer_state(conn, creator_id, str(2183930592))
    assert state is not None
    neighbours = state["neighbour_images"]
    assert [item["sha256"] for item in neighbours["previous"]] == [
        sha(307),
        sha(306),
        sha(305),
        sha(304),
        sha(303),
        sha(302),
    ]
    assert [item["sha256"] for item in neighbours["next"]] == [
        sha(100),
        sha(101),
        sha(102),
        sha(103),
        sha(104),
        sha(105),
    ]
    assert len(neighbours["previous"]) == db.NEIGHBOUR_PRELOAD_LIMIT
    assert len(neighbours["next"]) == db.NEIGHBOUR_PRELOAD_LIMIT

    payload = client.get(
        f"/c/{creator_id}/viewer/data?post=2183930592"
    ).json()
    assert payload["neighbour_images"] == neighbours

    newest = db.viewer_state(conn, creator_id, str(2183930593))
    oldest = db.viewer_state(conn, creator_id, str(2183930591))
    assert newest["neighbour_images"]["previous"] == []
    assert newest["neighbour_images"]["next"]
    assert oldest["neighbour_images"]["previous"]
    assert oldest["neighbour_images"]["next"] == []


def test_preload_plan_keeps_both_edges_and_prunes_stale_entries():
    import json
    import shutil
    import subprocess
    import textwrap

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    source = (STATIC_DIR / "viewer.js").read_text(encoding="utf-8")
    block = source.split("// BEGIN preload-window", 1)[1].split(
        "// END preload-plan", 1
    )[0]
    script = (
        textwrap.dedent(block)
        + textwrap.dedent(
            """
            function image(name) {
              return { kind: "image", url: "https://e3.cum.st/" + name };
            }
            function media(count) {
              var items = [];
              for (var i = 0; i < count; i += 1) {
                items.push(image("m" + i));
              }
              return items;
            }

            var neighbours = {
              previous: [image("p0"), image("p1"), image("p2")],
              next: [image("n0"), image("n1"), image("n2")]
            };
            var candidates = preloadCandidates(media(10), 0, neighbours);
            var plan = buildPreloadPlan(media(10), 0, neighbours, 4, null);
            var failed = new Set(["https://e3.cum.st/p0"]);
            var failedPlan = buildPreloadPlan(
              media(10), 0, neighbours, 12, failed
            );

            var map = new Map();
            ["https://e3.cum.st/m1", "https://e3.cum.st/stale"].forEach(
              function (url) { map.set(url, {}); }
            );
            prunePreloadMap(map, new Set(plan));

            // A painted node dropped from the window must be reclaimed through
            // the caller's release hook, so its bench element is not orphaned.
            var reclaimMap = new Map();
            ["keep", "drop"].forEach(function (name) {
              var url = "https://e3.cum.st/" + name;
              reclaimMap.set(url, { state: "painted", url: url });
            });
            var reclaimed = [];
            prunePreloadMap(
              reclaimMap,
              new Set(["https://e3.cum.st/keep"]),
              function (entry) {
                reclaimed.push(entry.url);
                reclaimMap.delete(entry.url);
              }
            );

            var failures = new Set([
              "https://e3.cum.st/p0",
              "https://e3.cum.st/old"
            ]);
            pruneFailedSet(failures, candidates);

            var filtered = preloadCandidates(
              media(10),
              0,
              {
                previous: [
                  { kind: "gif", url: "https://e3.cum.st/gif" },
                  { kind: "image", url: "data:image/png;base64,AAAA" }
                ],
                next: [image("n0")]
              }
            );

            // T8.18 C: pace-based widening with a hard cap and a floor.
            var radii = [
              nextPreloadRadius(PRELOAD_RADIUS, 100),
              nextPreloadRadius(PRELOAD_RADIUS_MAX, 100),
              nextPreloadRadius(PRELOAD_RADIUS_MAX, 5000),
              nextPreloadRadius(PRELOAD_RADIUS, null)
            ];

            process.stdout.write(JSON.stringify({
              totalMax: PRELOAD_TOTAL_MAX,
              floor: PRELOAD_RADIUS,
              cap: PRELOAD_RADIUS_MAX,
              radii: radii,
              candidates: candidates,
              plan: plan,
              failedPlan: failedPlan,
              map: Array.from(map.keys()),
              reclaimed: reclaimed,
              reclaimMap: Array.from(reclaimMap.keys()),
              failures: Array.from(failures),
              filtered: filtered
            }));
            """
        )
    )
    result = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)

    assert payload["totalMax"] == 12
    assert payload["floor"] == 3
    assert payload["cap"] == 5
    assert payload["radii"] == [4, 5, 4, 3]
    # Direction-of-travel first: the immediate next item, deeper in-post work,
    # then the adjacent post edge in that direction, then the opposite edge
    # (T8.18 C).
    assert payload["candidates"][:9] == [
        "https://e3.cum.st/m1",
        "https://e3.cum.st/m2",
        "https://e3.cum.st/m3",
        "https://e3.cum.st/n0",
        "https://e3.cum.st/n1",
        "https://e3.cum.st/n2",
        "https://e3.cum.st/p0",
        "https://e3.cum.st/p1",
        "https://e3.cum.st/p2",
    ]
    assert payload["plan"] == payload["candidates"][:4]
    assert "https://e3.cum.st/p0" not in payload["failedPlan"]
    assert "https://e3.cum.st/n0" in payload["failedPlan"]
    assert payload["map"] == ["https://e3.cum.st/m1"]
    assert payload["reclaimed"] == ["https://e3.cum.st/drop"]
    assert payload["reclaimMap"] == ["https://e3.cum.st/keep"]
    assert payload["failures"] == ["https://e3.cum.st/p0"]
    assert payload["filtered"] == [
        "https://e3.cum.st/m1",
        "https://e3.cum.st/m2",
        "https://e3.cum.st/m3",
        "https://e3.cum.st/n0",
    ]


def test_jump_prefers_cached_payload_without_clearing_preloads():
    source = (STATIC_DIR / "viewer.js").read_text(encoding="utf-8")
    jump_block = source.split("function jump(", 1)[1].split(
        "function requestFullscreen", 1
    )[0]

    assert "payloadCache.get(String(serviceId))" in jump_block
    assert "applyPayload(cached" in jump_block
    assert "clearPreloadCache()" not in jump_block
    assert "prunePreloadMap" in source
    assert "manifestDataUrl(serviceId)" in jump_block
    assert "installManifest(state.manifest)" in source


def test_viewer_manifest_exposes_full_post_navigation_links(conn):
    creator_id = make_creator(conn)
    newest = add_post(conn, creator_id, 0, title="Newest")
    add_file(conn, newest, sha(1), position=0)
    add_post(conn, creator_id, 1, title="Text only skipped")
    middle = add_post(conn, creator_id, 2, title="Middle")
    add_file(conn, middle, sha(2), position=0)
    oldest = add_post(conn, creator_id, 3, title="Oldest")
    add_file(conn, oldest, sha(3), position=0)

    state = db.viewer_state(conn, creator_id, str(2183930591 + 0))
    assert state is not None
    manifest = state["manifest"]["posts"]

    manifest_ids = [entry["service_id"] for entry in manifest]
    assert manifest_ids == [
        str(db.get_post(conn, oldest)["service_id"]),
        str(db.get_post(conn, middle)["service_id"]),
        str(db.get_post(conn, newest)["service_id"]),
    ]
    for index, entry in enumerate(manifest):
        previous_expected = manifest[index - 1]["service_id"] if index > 0 else None
        next_expected = (
            manifest[index + 1]["service_id"]
            if index < len(manifest) - 1
            else None
        )
        assert entry["previous_post"] == previous_expected
        assert entry["next_post"] == next_expected
        assert entry["data_url"] == (
            f"/c/{creator_id}/viewer/data?post={entry['service_id']}"
        )
        assert entry["post_url"] == f"/c/{creator_id}/p/{entry['service_id']}"


def test_viewer_eagerly_prefetches_payloads_and_prioritizes_immediate_images():
    source = (STATIC_DIR / "viewer.js").read_text(encoding="utf-8")

    assert 'priority: "low"' in source
    # Deep/window work is low priority; the paint ring uses browser-default
    # priority because it must be ready before the gesture (T8.18 C).
    assert 'element.fetchPriority = "low";' in source
    assert source.count("prefetchNeighbourPayloads();") >= 3
    assert "warmPreloadWindow(currentIndex)" in source
    assert "previous.unshift(last)" in source
    assert "next.unshift(edge)" in source
    assert "refreshPreloadWindow();" in source


def test_viewer_has_inset_left_right_tap_zones_without_overlay_controls():
    source = (STATIC_DIR / "viewer.js").read_text(encoding="utf-8")

    assert "TAP_ZONE_INSET = 0.12" in source
    assert "TAP_ZONE_WIDTH = 0.28" in source
    assert "function tapActionAt(s)" in source
    assert 'return "prev-image";' in source
    assert 'return "next-image";' in source
    assert "TAP_MAX_DISTANCE = 8" in source
    assert "TAP_MAX_DURATION = 500" in source
    assert "suppressNextClick = true" in source
    assert "viewer-zone" not in source


def test_viewer_has_auto_hiding_fullscreen_control(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 0), sha(1))

    body = client.get(f"/c/{creator_id}/viewer").text

    tags = re.findall(r"<button[^>]*data-viewer-fullscreen[^>]*>", body)
    assert len(tags) == 1
    tag = tags[0]
    assert 'class="viewer-fullscreen"' in tag
    assert 'type="button"' in tag
    assert 'aria-pressed="false"' in tag
    assert 'aria-label="Enter fullscreen"' in tag
    assert body.count("viewer-icon-expand") == 1
    assert body.count("viewer-icon-contract") == 1
    assert "data-fullscreen-state=\"enter\"" in tag
    assert ">Fullscreen</button>" not in body
    assert ">Exit fullscreen</button>" not in body
    assert (
        body.index("data-viewer-close")
        < body.index("data-viewer-fullscreen")
        < body.index("data-viewer-stage")
    )


# --------------------------------------------------------------------------- #
# T8.9 - fluid transitions between viewer media
# --------------------------------------------------------------------------- #


def test_transition_plan_maps_actions_to_directions():
    import json
    import shutil
    import subprocess
    import textwrap

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    source = (STATIC_DIR / "viewer.js").read_text(encoding="utf-8")
    block = source.split("// BEGIN transition-plan", 1)[1].split(
        "// END transition-plan", 1
    )[0]
    script = (
        textwrap.dedent(block)
        + textwrap.dedent(
            """
            process.stdout.write(JSON.stringify({
              nextImage: transitionPlan("next-image"),
              prevImage: transitionPlan("prev-image"),
              nextPost: transitionPlan("next-post"),
              prevPost: transitionPlan("prev-post"),
              unknown: transitionPlan("nope"),
              empty: transitionPlan(null),
              incoming: INCOMING_OFFSET,
              outgoing: OUTGOING_OFFSET,
              duration: TRANSITION_MS
            }));
            """
        )
    )
    result = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)

    assert payload["nextImage"] == {"x": 1, "y": 0}
    assert payload["prevImage"] == {"x": -1, "y": 0}
    assert payload["nextPost"] == {"x": 0, "y": 1}
    assert payload["prevPost"] == {"x": 0, "y": -1}
    assert payload["unknown"] is None
    assert payload["empty"] is None
    assert payload["incoming"] == 0.08
    assert payload["outgoing"] == 0.04
    assert 180 <= payload["duration"] <= 240


def test_render_accepts_intent_and_two_layer_transition_is_bounded():
    source = (STATIC_DIR / "viewer.js").read_text(encoding="utf-8")

    assert "function render(index, updateUrl, intent, handoff)" in source
    assert "transitionPlan(action)" in source
    assert "function settleTransitions(keep, preserveActive)" in source
    assert "function commitTransition(" in source
    assert "function prepareLayer" not in source
    assert "at most one committed layer" in source

    # Reduced motion and quiet mode must take the instant path.
    render_block = source.split("function render(index, updateUrl", 1)[1].split(
        "function go(", 1
    )[0]
    assert "prefersReducedMotion()" in render_block
    assert 'root.classList.contains("viewer-quiet")' in render_block
    assert "renderInstant(item, updateUrl)" in render_block

    # Commit is what publishes the counter/URL once.
    commit_block = source.split("function commitTransition(", 1)[1].split(
        "function render(", 1
    )[0]
    assert "finishRender(item, updateUrl)" in commit_block
    assert "updateCounter" not in commit_block
    assert "replaceUrl" not in commit_block

    # The outgoing layer always gets a pointer-events guard and cleanup.
    assert "outgoing.style.pointerEvents = \"none\"" in commit_block
    assert "removeLayerNow(outgoing)" in source
    assert "transitionend" in source


def test_transition_styles_and_reduced_motion_block():
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")

    transition_rule = css.split("\n.viewer-media-el.is-transitioning {", 1)[
        1
    ].split("}", 1)[0]
    assert "transform 200ms cubic-bezier(0.22, 1, 0.36, 1)" in transition_rule
    assert "opacity 200ms cubic-bezier(0.22, 1, 0.36, 1)" in transition_rule
    assert "width" not in transition_rule
    assert "height" not in transition_rule

    entering = css.split("\n.viewer-media-el.is-entering {", 1)[1].split("}", 1)[
        0
    ]
    leaving = css.split("\n.viewer-media-el.is-leaving {", 1)[1].split("}", 1)[0]
    preview = css.split("\n.viewer-media-el.is-preview {", 1)[1].split("}", 1)[0]
    assert "z-index: 2" in entering
    assert "z-index: 1" in leaving
    assert "pointer-events: none" in leaving
    assert "pointer-events: none" in preview

    reduced = css.split(
        "@media (prefers-reduced-motion: reduce) {\n"
        "  .viewer-media-el.is-transitioning {",
        1,
    )[1].split("}", 1)[0]
    assert "transition: none" in reduced

    stage_rule = css.split("\n.viewer-stage {", 1)[1].split("}", 1)[0]
    assert "touch-action: none" in stage_rule

    media_rule = css.split("\n.viewer-media {", 1)[1].split("}", 1)[0]
    assert "transition" not in media_rule
    assert "touch-action: none" in media_rule


# --------------------------------------------------------------------------- #
# T8.10 - finger-following swipe with rubber-band edges
# --------------------------------------------------------------------------- #


def test_resolve_swipe_axis_direction_threshold_velocity_and_rubber_band():
    import json
    import shutil
    import subprocess
    import textwrap

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    source = (STATIC_DIR / "viewer.js").read_text(encoding="utf-8")
    block = source.split("// BEGIN swipe-plan", 1)[1].split(
        "// END swipe-plan", 1
    )[0]
    script = (
        textwrap.dedent(block)
        + textwrap.dedent(
            """
            function swipe(overrides) {
              var base = {
                dx: 0,
                dy: 0,
                width: 1000,
                height: 800,
                vx: 0,
                vy: 0
              };
              Object.keys(overrides || {}).forEach(function (key) {
                base[key] = overrides[key];
              });
              return resolveSwipe(base);
            }
            process.stdout.write(JSON.stringify({
              slop: SWIPE_SLOP,
              belowSlop: swipe({ dx: 5, dy: 3 }),
              lockX: swipe({ dx: 20, dy: 12 }),
              lockY: swipe({ dx: 8, dy: 30 }),
              nextImage: swipe({ dx: -200 }),
              prevImage: swipe({ dx: 200 }),
              nextPost: swipe({ dy: -200 }),
              prevPost: swipe({ dy: 200 }),
              underThreshold: swipe({ dx: -95 }),
              atThreshold: swipe({ dx: -100 }),
              smallStage: swipe({ dx: -80, width: 400 }),
              flick: swipe({ dx: -30, vx: -0.8 }),
              slowFlick: swipe({ dx: -30, vx: -0.2 }),
              reverseFlick: swipe({ dx: -30, vx: 0.8 }),
              rubber: swipe({ dx: -400, hasDestination: false }),
              rubberUndersized: swipe({ dx: -20, hasDestination: false })
            }));
            """
        )
    )
    result = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)

    assert payload["slop"] == 9
    assert payload["belowSlop"]["commit"] is False
    assert payload["belowSlop"]["axis"] is None

    assert payload["lockX"]["axis"] == "x"
    assert payload["lockX"]["action"] == "prev-image"
    assert payload["lockY"]["axis"] == "y"
    assert payload["lockY"]["action"] == "prev-post"

    assert payload["nextImage"]["action"] == "next-image"
    assert payload["prevImage"]["action"] == "prev-image"
    # The vertical axis follows the finger: up is older, down is newer.
    assert payload["nextPost"]["action"] == "next-post"
    assert payload["prevPost"]["action"] == "prev-post"

    assert payload["underThreshold"]["commit"] is False
    assert payload["atThreshold"]["commit"] is True
    assert payload["atThreshold"]["threshold"] == 96
    assert payload["smallStage"]["threshold"] == 72

    assert payload["flick"]["commit"] is True
    assert payload["slowFlick"]["commit"] is False
    assert payload["reverseFlick"]["commit"] is False

    assert payload["rubber"]["rubber"] is True
    assert payload["rubber"]["commit"] is False
    assert payload["rubber"]["resistance"] == 0.25
    assert payload["rubberUndersized"]["commit"] is False


def test_swipe_uses_pointer_events_with_capture_and_cancel_paths():
    source = (STATIC_DIR / "viewer.js").read_text(encoding="utf-8")

    assert 'stage.addEventListener("pointerdown", onStagePointerDown' in source
    assert 'stage.addEventListener("pointermove", onStagePointerMove' in source
    assert 'stage.addEventListener("pointerup", onStagePointerUp' in source
    assert 'stage.addEventListener("pointercancel", onStagePointerCancel' in source
    assert 'addEventListener("lostpointercapture", onLostPointerCapture' in source
    assert "stage.setPointerCapture(event.pointerId)" in source
    assert "stage.releasePointerCapture(s.id)" in source
    assert "onWindowBlur" in source
    assert 'window.addEventListener("blur", onWindowBlur)' in source

    # The old touchend-only 40 px threshold must be gone.
    assert "onTouchStart" not in source
    assert "onTouchEnd" not in source
    assert "changedTouches" not in source
    assert "< 40" not in source

    # One gesture is throttled to one move, and the locked axis is kept.
    pointer_block = source.split("function finishPointer(", 1)[1].split(
        "function onStagePointerDown(", 1
    )[0]
    assert "go(result.action, handoff)" in pointer_block
    assert "if (!go(result.action, handoff))" in pointer_block
    assert "axis: s.axis" in pointer_block
    # The invisible zones and their click suppression are gone (T8.11).
    assert "lastTouchNav" not in source
    assert "onZoneClick" not in source
    assert "afterTouch" not in source

    # A drag must not fetch just to preview an uncached neighbour.
    preview_block = source.split("function beginSwipePreview(", 1)[1].split(
        "function swipeVelocity(", 1
    )[0]
    assert "cachedPostPayload" in source
    assert "fetch(" not in preview_block

    # Reduced motion still tracks, but the settle is instant.
    spring_block = source.split("function springActiveLayer(", 1)[1].split(
        "function applyPayload(", 1
    )[0]
    assert "prefersReducedMotion()" in spring_block
    assert "resetLayerStyle(layer)" in spring_block
    assert "requestAnimationFrame" in source
    assert "requestAnimationFrame" not in spring_block


def test_viewer_fullscreen_script_and_styles_are_transient():
    source = (STATIC_DIR / "viewer.js").read_text(encoding="utf-8")
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")

    assert 'document.addEventListener("fullscreenchange", onFullscreenChange)' in source
    assert "Fullscreen is not available in this browser." in source
    assert 'fullscreenButton.classList.add("is-revealed")' in source
    assert "2500" in source
    assert "updateCachedBookmarks" in source

    update = source.split("function updateFullscreenControl", 1)[1].split(
        "function revealViewerChrome", 1
    )[0]
    assert "textContent" not in update
    assert '"data-fullscreen-state"' in update
    assert 'active ? "exit" : "enter"' in update

    rule = css.split("\n.viewer-fullscreen {", 1)[1].split("}", 1)[0]
    assert "position: absolute" in rule
    assert "top: max(3rem" in rule
    assert "right: max(0.75rem" in rule
    assert "opacity: 0" in rule
    assert "pointer-events: none" in rule
    assert ".viewer-fullscreen[data-fullscreen-state=" in css
    revealed = css.split(
        "\n.viewer-fullscreen.is-revealed,\n.viewer-fullscreen:focus-visible {",
        1,
    )[1].split("}", 1)[0]
    assert "opacity: 1" in revealed
    assert "pointer-events: auto" in revealed
    assert (
        "@media (prefers-reduced-motion: reduce) {\n"
        "  .viewer-fullscreen {\n"
        "    transition: none;\n"
        "  }\n"
        "}"
    ) in css


# --------------------------------------------------------------------------- #
# T8.11 - smooth swipe geometry, axis lock and no click zones
# --------------------------------------------------------------------------- #


def test_swipe_axis_lock_uses_dominance_and_never_flips():
    import json
    import shutil
    import subprocess
    import textwrap

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    source = (STATIC_DIR / "viewer.js").read_text(encoding="utf-8")
    block = source.split("// BEGIN swipe-plan", 1)[1].split(
        "// END swipe-plan", 1
    )[0]
    script = (
        textwrap.dedent(block)
        + textwrap.dedent(
            """
            function axis(dx, dy) {
              return swipeAxis(dx, dy, SWIPE_SLOP, SWIPE_AXIS_DOMINANCE,
                               SWIPE_AXIS_FALLBACK);
            }
            process.stdout.write(JSON.stringify({
              slop: SWIPE_SLOP,
              dominance: SWIPE_AXIS_DOMINANCE,
              fallback: SWIPE_AXIS_FALLBACK,
              belowSlop: axis(5, 3),
              jitter: axis(10, 9),
              mostlyVertical: axis(10, 26),
              mostlyHorizontal: axis(26, 10),
              clearVertical: axis(8, 30),
              clearHorizontal: axis(30, 8),
              exactTie: axis(15, 15),
              tiePastFallback: axis(30, 30),
              ambiguousPastFallback: axis(20, 18)
            }));
            """
        )
    )
    result = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)

    assert payload["slop"] == 9
    assert payload["dominance"] == 1.2
    assert payload["fallback"] == 24

    # Below the slop nothing is decided yet.
    assert payload["belowSlop"] is None
    # A few pixels of jitter must not lock the wrong axis.
    assert payload["jitter"] is None
    # Mostly vertical and mostly horizontal gestures keep their axis.
    assert payload["mostlyVertical"] == "y"
    assert payload["mostlyHorizontal"] == "x"
    assert payload["clearVertical"] == "y"
    assert payload["clearHorizontal"] == "x"
    # A true diagonal stays undecided until the fallback distance, then it
    # commits to exactly one axis.
    assert payload["exactTie"] is None
    assert payload["tiePastFallback"] == "x"
    assert payload["ambiguousPastFallback"] is None


def test_swipe_geometry_uses_media_boxes_and_keeps_edges_flush():
    import json
    import shutil
    import subprocess
    import textwrap

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    source = (STATIC_DIR / "viewer.js").read_text(encoding="utf-8")
    block = source.split("// BEGIN swipe-plan", 1)[1].split(
        "// END swipe-plan", 1
    )[0]
    script = (
        textwrap.dedent(block)
        + textwrap.dedent(
            """
            var landscape = { width: 800, height: 200 };
            var portrait = { width: 400, height: 600 };
            var stepX = swipeTrackStep("x", landscape, portrait);
            var stepY = swipeTrackStep("y", landscape, portrait);
            var left = swipeOffsets("x", -100, 1, stepX);
            var right = swipeOffsets("x", 100, 1, stepX);
            var up = swipeOffsets("y", -100, 1, stepY);
            var down = swipeOffsets("y", 100, 1, stepY);
            var rubber = swipeOffsets("x", -100, SWIPE_RUBBER_BAND, stepX);

            // The near edge of the incoming media must touch the far edge of
            // the current media exactly (no stage-sized black band).
            function gap(axis, current, incoming, offsets) {
              var currentHalf =
                (axis === "x" ? current.width : current.height) / 2;
              var incomingHalf =
                (axis === "x" ? incoming.width : incoming.height) / 2;
              var currentCentre = axis === "x" ? offsets.drag.x : offsets.drag.y;
              var incomingCentre =
                axis === "x" ? offsets.preview.x : offsets.preview.y;
              var currentLeft = currentCentre - currentHalf;
              var currentRight = currentCentre + currentHalf;
              var incomingLeft = incomingCentre - incomingHalf;
              var incomingRight = incomingCentre + incomingHalf;
              return Math.max(
                currentLeft - incomingRight,
                incomingLeft - currentRight
              );
            }

            process.stdout.write(JSON.stringify({
              fittedLandscape: fittedMediaBox(1600, 900, 1000, 800),
              fittedPortrait: fittedMediaBox(900, 1600, 1000, 800),
              fittedSmall: fittedMediaBox(400, 300, 1000, 800),
              stepX: stepX,
              stepY: stepY,
              left: left,
              right: right,
              up: up,
              down: down,
              rubber: rubber,
              gapLeft: gap("x", landscape, portrait, left),
              gapRight: gap("x", landscape, portrait, right),
              gapUp: gap("y", landscape, portrait, up),
              gapDown: gap("y", landscape, portrait, down)
            }));
            """
        )
    )
    result = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)

    # Letterboxed media is measured, not the stage.
    assert payload["fittedLandscape"] == {"width": 1000, "height": 562.5}
    assert payload["fittedPortrait"] == {"width": 450, "height": 800}
    assert payload["fittedSmall"] == {"width": 400, "height": 300}

    # Centre-to-centre step: half of each visible box.
    assert payload["stepX"] == 600
    assert payload["stepY"] == 400

    # 1:1 under the finger, one axis only.
    assert payload["left"]["drag"] == {"x": -100, "y": 0}
    assert payload["right"]["drag"] == {"x": 100, "y": 0}
    assert payload["up"]["drag"] == {"x": 0, "y": -100}
    assert payload["down"]["drag"] == {"x": 0, "y": 100}

    # Dragging left reveals the neighbour on the right and vice versa.
    assert payload["left"]["preview"]["x"] == 500
    assert payload["right"]["preview"]["x"] == -500
    # Dragging up reveals the neighbour below, dragging down reveals it above.
    assert payload["up"]["preview"]["y"] == 300
    assert payload["down"]["preview"]["y"] == -300

    # The incoming media touches the outgoing media: no black band.
    assert payload["gapLeft"] == 0
    assert payload["gapRight"] == 0
    assert payload["gapUp"] == 0
    assert payload["gapDown"] == 0

    # An archive edge rubber-bands the current media.
    assert payload["rubber"]["drag"] == {"x": -25, "y": 0}


def test_swipe_direction_invariant_and_media_sized_thresholds():
    import json
    import shutil
    import subprocess
    import textwrap

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    source = (STATIC_DIR / "viewer.js").read_text(encoding="utf-8")
    block = source.split("// BEGIN swipe-plan", 1)[1].split(
        "// END swipe-plan", 1
    )[0]
    script = (
        textwrap.dedent(block)
        + textwrap.dedent(
            """
            function swipe(overrides) {
              var base = { dx: 0, dy: 0, width: 1000, height: 800, vx: 0, vy: 0 };
              Object.keys(overrides || {}).forEach(function (key) {
                base[key] = overrides[key];
              });
              return resolveSwipe(base);
            }
            process.stdout.write(JSON.stringify({
              down: swipe({ dy: 200 }),
              up: swipe({ dy: -200 }),
              downAxis: swipe({ dy: 200 }).axis,
              upAxis: swipe({ dy: -200 }).axis,
              downAction: swipeActionFor("y", 200),
              upAction: swipeActionFor("y", -200),
              mediaThreshold: swipe({ dx: -60, width: 300, height: 200 }),
              stageThreshold: swipe({ dx: -60, width: 1000, height: 800 }),
              lockedVertical: swipe({ dx: -200, dy: -100, axis: "y" })
            }));
            """
        )
    )
    result = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)

    # Direction invariant: up = older post, down = newer post.
    assert payload["downAction"] == "prev-post"
    assert payload["upAction"] == "next-post"
    assert payload["down"]["action"] == "prev-post"
    assert payload["up"]["action"] == "next-post"
    assert payload["downAxis"] == "y"
    assert payload["upAxis"] == "y"

    # The commit threshold follows the media box, not the stage.
    assert payload["mediaThreshold"]["threshold"] == 54
    assert payload["mediaThreshold"]["commit"] is True
    assert payload["stageThreshold"]["threshold"] == 96
    assert payload["stageThreshold"]["commit"] is False

    # A locked axis is never switched by a later horizontal jump.
    assert payload["lockedVertical"]["axis"] == "y"
    assert payload["lockedVertical"]["action"] == "next-post"


def test_swipe_preview_uses_measured_media_box_and_is_placed_before_paint():
    source = (STATIC_DIR / "viewer.js").read_text(encoding="utf-8")

    # The gesture measures the visible media box once, at pointerdown.
    down_block = source.split("function onStagePointerDown(", 1)[1].split(
        "function onStagePointerMove(", 1
    )[0]
    assert "layerBox(activeLayer)" in down_block
    # The stage size is only a fallback for a stage without a media layer.
    assert down_block.count("stageSize()") == 1
    assert "layerBox(activeLayer) || stageSize()" in down_block

    preview_block = source.split("function beginSwipePreview(", 1)[1].split(
        "function swipeVelocity(", 1
    )[0]
    # The preview is sized and placed from the media boxes, never the stage.
    assert "stageSize(" not in preview_block
    assert "fittedMediaBox(" in preview_block
    assert "swipeTrackStep(" in preview_block
    assert "swipeAxis(" in preview_block
    # The preview transform is set before the layer joins the host, so the
    # first painted frame is already flush against the current media.
    assert preview_block.index("applySwipeOffsets(s);") < preview_block.index(
        "host.appendChild(layer);"
    )

    # The axis lock and the drag helpers stay pure and testable.
    assert "// BEGIN swipe-plan" in source
    assert "// END swipe-plan" in source
    assert "function swipeAxis(" in source
    assert "function swipeOffsets(" in source


def test_viewer_drag_harness_tracks_the_finger_without_a_black_band():
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    harness = (
        Path(__file__).resolve().parent / "fixtures" / "viewer_drag_harness.js"
    )
    result = subprocess.run(
        [node, str(harness)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "FAIL" not in result.stdout
    assert "ALL CHECKS PASSED" in result.stdout
    # The harness asserts the concrete geometry of the drag.
    assert "ok   active follows the finger" in result.stdout
    assert "ok   preview is flush with the current media" in result.stdout
    assert "ok   vertical drag moves on the y axis" in result.stdout
    assert "ok   edge drag is rubber-banded" in result.stdout


def test_server_rendered_layer_adopts_its_item_for_the_gesture():
    source = (STATIC_DIR / "viewer.js").read_text(encoding="utf-8")

    init_block = source.split(
        "activeLayer = host ? host.querySelector", 1
    )[1].split("if (closeButton)", 1)[0]
    # The server-rendered layer goes through the same setup as a JS-created
    # one, so the gesture gets __viewerItem and videos get their error handlers
    # (T8.17 A).
    assert "adoptServerMedia" in init_block
    assert "state.media[currentIndex]" in init_block


def test_viewer_has_a_paint_warm_ring_outside_the_media_host():
    source = (STATIC_DIR / "viewer.js").read_text(encoding="utf-8")
    template = (
        STATIC_DIR.parent / "templates" / "viewer.html"
    ).read_text(encoding="utf-8")
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")

    # The ring is a separate bench, outside [data-viewer-media], so the host
    # still contains only active/incoming/preview layers (T8.18 F).
    assert template.index("data-viewer-paint-bench") < template.index(
        "data-viewer-media"
    )
    assert "inert" in template.split("data-viewer-paint-bench", 1)[1].split(
        ">", 1
    )[0]
    assert 'root.querySelector("[data-viewer-paint-bench]")' in source
    assert "function promoteRingEntry" in source
    assert "function returnRingEntry" in source
    assert "function parkOutgoing" in source
    assert 'element.__viewerPromoted = true' in source
    assert 'entry.state = "painted"' in source
    assert 'entry.state = "promoted"' in source
    assert "paintedAfterFrame" in source
    assert "PRELOAD_TOTAL_MAX" in source
    assert "DEEP_CONCURRENCY" in source
    # Ring ownership is explicit: promoted nodes stop being ring loaders, a
    # decoded deep-window request is adopted instead of duplicated, pruning
    # reclaims dropped bench nodes, and the debug snapshot carries the timing
    # evidence for "requested before the gesture, not repeated on commit"
    # (T8.18 B/E/F).
    assert "function bindRingHandlers" in source
    assert "unbindRingHandlers(entry)" in source
    assert "function adoptDeepEntry" in source
    assert "prunePreloadMap(preloadMap, new Set(Object.keys(keep)), releaseEntry)" in source
    assert "timing: Array.from(debugTiming.values())" in source
    assert "preloadStats.duplicates += 1" in source
    assert 'noteTiming(entry, "requestedAt")' in source
    assert 'noteTiming(entry, "promotedAt")' in source

    bench_rule = css.split("\n.viewer-paint-bench {", 1)[1].split("}", 1)[0]
    assert "pointer-events: none" in bench_rule
    assert "contain: layout paint" in bench_rule
    assert ".viewer-paint-bench .viewer-media-el" in css


# --------------------------------------------------------------------------- #
# T8.19 - viewer context: media, boundaries, manifest and starting item
# --------------------------------------------------------------------------- #


def test_viewer_context_limits_media_and_skips_nonmatching_post_boundaries(
    client, conn
):
    creator_id = make_creator(conn)
    post0 = add_post(conn, creator_id, 0, title="older image")
    add_file(conn, post0, sha(1))
    post1 = add_post(conn, creator_id, 1, title="mixed")
    add_file(conn, post1, sha(2))
    add_file(conn, post1, sha(3), kind="video", position=1, duration_ms=5_000)
    post2 = add_post(conn, creator_id, 2, title="video only")
    add_file(conn, post2, sha(4), kind="video", duration_ms=8_000)
    post3 = add_post(conn, creator_id, 3, title="newest image")
    add_file(conn, post3, sha(5))

    body = client.get(
        f"/c/{creator_id}/viewer?post=2183930592"
        "&gf=kind%3Dimage&from=gallery"
        f"&next=%2Fc%2F{creator_id}%2Fgallery%3Fkind%3Dimage"
    ).text
    state = embedded_state(body)

    # Only the matching image from the requested post, never its video.
    assert [item["sha256"] for item in state["media"]] == [sha(2)]
    # The video-only post is skipped at both boundaries.
    assert state["previous_post"] == "2183930594"
    assert state["next_post"] == "2183930591"
    assert state["post_rank"] == 2
    assert state["media_post_count"] == 3
    assert state["gf"] == "kind=image"
    assert state["context"]["plural"] == "images"

    # Every manifest data URL keeps the context token for post-boundary jumps.
    manifest = state["manifest"]["posts"]
    assert [entry["service_id"] for entry in manifest] == [
        "2183930594",
        "2183930592",
        "2183930591",
    ]
    assert all("gf=kind%3Dimage" in entry["data_url"] for entry in manifest)


def test_viewer_data_route_preserves_the_filter_context(client, conn):
    creator_id = make_creator(conn)
    older = add_post(conn, creator_id, 0)
    add_file(conn, older, sha(1))
    mixed = add_post(conn, creator_id, 1)
    add_file(conn, mixed, sha(2), kind="video", position=0, duration_ms=3_000)
    add_file(conn, mixed, sha(3), position=1)
    newer_video = add_post(conn, creator_id, 2)
    add_file(conn, newer_video, sha(4), kind="video", duration_ms=9_000)

    payload = client.get(
        f"/c/{creator_id}/viewer/data?post=2183930592&gf=kind%3Dimage"
    ).json()

    assert payload["gf"] == "kind=image"
    assert payload["context"]["plural"] == "images"
    assert [item["sha256"] for item in payload["media"]] == [sha(3)]
    assert payload["previous_post"] is None
    assert payload["next_post"] == "2183930591"

    # The unfiltered route keeps every viewer kind, as before T8.19.
    payload = client.get(
        f"/c/{creator_id}/viewer/data?post=2183930592"
    ).json()
    assert [item["sha256"] for item in payload["media"]] == [sha(2), sha(3)]
    assert "gf" not in payload


def test_viewer_stable_sha256_wins_over_the_unfiltered_index(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 0)
    add_file(conn, post_id, sha(1), kind="video", position=0, duration_ms=4_000)
    add_file(conn, post_id, sha(2), position=1)

    body = client.get(
        f"/c/{creator_id}/viewer?post=2183930591&i=0"
        f"&sha256={sha(2)}&gf=kind%3Dimage"
    ).text
    state = embedded_state(body)

    assert state["index"] == 0
    assert [item["sha256"] for item in state["media"]] == [sha(2)]


def test_viewer_context_keeps_date_range_despite_from_gallery(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 0), sha(1))

    body = client.get(
        f"/c/{creator_id}/viewer?post=2183930591"
        "&gf=kind%3Dimage%26from%3D1970-01-01&from=gallery"
    ).text
    state = embedded_state(body)

    # `from=gallery` is an origin flag, not a date; it must not erase gf's
    # date range.
    assert state["gf"] == "kind=image&from=1970-01-01"
    assert state["context"]["kind"] == "image"


def test_viewer_invalid_context_is_ignored_not_reflected(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 0)
    add_file(conn, post_id, sha(1))
    add_file(conn, post_id, sha(2), kind="video", position=1, duration_ms=2_000)

    body = client.get(
        f"/c/{creator_id}/viewer?post=2183930591&gf=kind%3Devil%26sort%3Dnope"
    ).text
    state = embedded_state(body)

    assert state["gf"] is None
    assert state.get("context") is None
    assert {item["kind"] for item in state["media"]} == {"image", "video"}


def test_viewer_oldest_sort_swaps_post_boundary_directions(client, conn):
    creator_id = make_creator(conn)
    for number in range(3):
        add_file(conn, add_post(conn, creator_id, number), sha(number + 1))

    body = client.get(
        f"/c/{creator_id}/viewer?post=2183930592&gf=sort%3Doldest"
    ).text
    state = embedded_state(body)

    assert state["previous_post"] == "2183930591"
    assert state["next_post"] == "2183930593"
    assert state["context"]["sort"] == "oldest"


def test_viewer_video_context_warms_posters_not_images(client, conn):
    creator_id = make_creator(conn)
    for number in range(3):
        add_file(
            conn,
            add_post(conn, creator_id, number),
            sha(number + 1),
            kind="video",
            duration_ms=4_000 + number,
        )

    body = client.get(
        f"/c/{creator_id}/viewer?post=2183930592&gf=kind%3Dvideo"
    ).text
    state = embedded_state(body)

    assert [item["sha256"] for item in state["media"]] == [sha(2)]
    neighbours = state["neighbour_images"]
    assert [item["sha256"] for item in neighbours["previous"]] == [sha(3)]
    assert [item["sha256"] for item in neighbours["next"]] == [sha(1)]
    for item in neighbours["previous"] + neighbours["next"]:
        assert item["kind"] == "video"
        assert item["warm_url"].endswith("/preview.webp")
        assert "/original.mp4" not in item["warm_url"]
