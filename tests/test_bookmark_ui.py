"""T9.2 - bookmark controls on posts, attachments, gallery tiles and viewer."""

from __future__ import annotations

import json
import re

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
    return TestClient(create_app(), follow_redirects=False)


@pytest.fixture()
def conn():
    connection = db.ensure_db(get_settings())
    yield connection
    connection.close()


def make_creator(conn, service_id: str = "274608", name: str = "Nyl") -> int:
    creator_id = db.upsert_creator(
        conn, service="patreon", service_id=service_id, name=name, display_name=name
    )
    conn.commit()
    return creator_id


def add_post(conn, creator_id: int, number: int, **overrides) -> int:
    values = {
        "creator_id": creator_id,
        "service": "patreon",
        "service_id": str(900000 + number),
        "title": f"Post {number}",
        "published": 1000 + number,
        "fetched_at": db.now_iso(),
    }
    values.update(overrides)
    post_id = db.upsert_post(conn, **values)
    conn.commit()
    return post_id


def add_file(conn, post_id: int, sha: str, kind: str = "image", **overrides) -> None:
    extension = {"image": "jpg", "gif": "gif", "video": "mp4", "audio": "m4a"}.get(
        kind, "bin"
    )
    rows = db.list_files(conn, post_id)
    row = {
        "sha256": sha,
        "kind": kind,
        "raw_kind": kind,
        "mime_type": {
            "image": "image/jpeg",
            "gif": "image/gif",
            "video": "video/mp4",
            "audio": "audio/mp4",
        }.get(kind),
        "bytes": 2048,
        "width": 100,
        "height": 200,
        "original_filename": f"{sha[:8]}.{extension}",
        "url": f"https://e1.cum.st/media/{sha}/original.{extension}",
        "thumb_url": f"https://img.cum.st/thumbnail/{sha}/preview.webp",
        "position": len(rows),
        "variants": [{"name": f"original.{extension}", "url": f"https://e1/{sha}.{extension}"}]
        if kind == "video"
        else [],
    }
    row.update(overrides)
    rows.append(row)
    db.replace_files(conn, post_id, rows)
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


# --------------------------------------------------------------------------- #
# server-rendered state
# --------------------------------------------------------------------------- #


def test_creator_page_renders_bookmarked_state_per_row(client, conn):
    creator_id = make_creator(conn)
    add_post(conn, creator_id, 1, title="Saved post")
    add_post(conn, creator_id, 2, title="Unsaved post")
    db.toggle_post_bookmark(conn, creator_id, "900001")

    body = client.get(f"/c/{creator_id}").text
    buttons = re.findall(r"<button[^>]*data-bookmark-button[^>]*>", body)

    assert len(buttons) == 2
    assert sum('aria-pressed="true"' in button for button in buttons) == 1
    assert sum('aria-pressed="false"' in button for button in buttons) == 1
    assert body.count('class="bookmark-icon"') == 2
    assert ">Bookmarked<" not in body
    assert ">Bookmark<" not in body
    assert "Bookmark post: Saved post" in body
    assert "Bookmark post: Unsaved post" in body


def test_post_page_has_post_toggle_and_per_attachment_toggles(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 1, title="Media post")
    for number, kind in enumerate(("image", "gif", "video", "audio")):
        add_file(conn, post_id, sha(number), kind=kind)
    db.toggle_media_bookmark(conn, creator_id, sha(1))

    body = client.get(f"/c/{creator_id}/p/900001?q=cat").text

    assert f'action="/c/{creator_id}/p/900001/bookmark"' in body
    media_forms = re.findall(
        rf'action="/c/{creator_id}/media/([0-9a-f]+)/bookmark"', body
    )
    # image, gif and video have controls; audio is deliberately skipped.
    assert media_forms == [sha(0), sha(1), sha(2)]
    assert f'name="next" value="/c/{creator_id}/p/900001?q=cat"' in body
    assert "Bookmark image: 00000000.jpg" in body
    buttons = re.findall(r"<button[^>]*data-bookmark-button[^>]*>", body)
    assert sum('aria-pressed="true"' in button for button in buttons) == 1


def test_gallery_renders_one_toggle_per_tile_outside_the_anchor(client, conn):
    creator_id = make_creator(conn)
    for number, kind in enumerate(("image", "gif", "video")):
        post_id = add_post(conn, creator_id, number)
        add_file(conn, post_id, sha(number), kind=kind)
    db.toggle_media_bookmark(conn, creator_id, sha(2))

    body = client.get(f"/c/{creator_id}/gallery").text

    anchors = re.findall(r'<a class="gallery-thumb".*?</a>', body, re.S)
    assert len(anchors) == 3
    assert all("<button" not in anchor for anchor in anchors)
    assert body.count("data-bookmark-target=") == 3
    assert body.count("gallery-bookmark") >= 3
    buttons = re.findall(r"<button[^>]*data-bookmark-button[^>]*>", body)
    assert sum('aria-pressed="true"' in button for button in buttons) == 1


# --------------------------------------------------------------------------- #
# form posts from every surface
# --------------------------------------------------------------------------- #


def test_each_surface_posts_a_form_and_returns_to_the_same_url(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 1)
    add_file(conn, post_id, sha(1))

    creator_next = f"/c/{creator_id}?q=cat&media=image"
    post_next = f"/c/{creator_id}/p/900001?page=2"
    gallery_next = f"/c/{creator_id}/gallery?kind=image&page=2"

    for url, next_url in (
        (f"/c/{creator_id}/p/900001/bookmark", post_next),
        (f"/c/{creator_id}/media/{sha(1)}/bookmark", gallery_next),
        (f"/c/{creator_id}/p/900001/bookmark", creator_next),
    ):
        response = client.post(url, data={"next": next_url})
        assert response.status_code == 303
        assert response.headers["location"] == next_url

    assert db.post_is_bookmarked(conn, creator_id, post_id) is False
    assert db.media_is_bookmarked(conn, creator_id, sha(1)) is True


# --------------------------------------------------------------------------- #
# viewer payload and shortcuts
# --------------------------------------------------------------------------- #


def test_viewer_payload_marks_post_and_each_media_item(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 1)
    add_file(conn, post_id, sha(1), kind="image")
    add_file(conn, post_id, sha(2), kind="video")
    db.toggle_post_bookmark(conn, creator_id, "900001")
    db.toggle_media_bookmark(conn, creator_id, sha(2))

    state = embedded_state(client.get(f"/c/{creator_id}/viewer").text)

    assert state["post"]["bookmarked"] is True
    assert state["media"][0]["bookmarked"] is False
    assert state["media"][1]["bookmarked"] is True


def test_viewer_json_toggle_round_trips_for_post_and_media(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 1)
    add_file(conn, post_id, sha(1))

    media_url = f"/c/{creator_id}/media/{sha(1)}/bookmark?format=json"
    first = client.post(media_url, headers={"Accept": "application/json"})
    assert first.json()["bookmarked"] is True
    assert client.post(media_url, headers={"Accept": "application/json"}).json()[
        "bookmarked"
    ] is False

    post_url = f"/c/{creator_id}/p/900001/bookmark?format=json"
    assert client.post(post_url).json()["bookmarked"] is True
    assert client.post(post_url).json()["bookmarked"] is False


def test_viewer_script_wires_b_and_shift_b_without_visible_chrome(client, conn):
    from monoke.web.templating import STATIC_DIR

    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 1), sha(1))

    body = client.get(f"/c/{creator_id}/viewer").text
    script = (STATIC_DIR / "viewer.js").read_text(encoding="utf-8")

    assert 'event.key === "b" || event.key === "B"' in script
    assert 'toggleBookmark(event.key === "B" ? "post" : "media")' in script
    assert "data-viewer-toast" in body
    assert 'data-viewer-toast aria-hidden="true" hidden' in body
    # No persistent bookmark button is added to the viewer chrome.
    assert "data-bookmark-button" not in body


# --------------------------------------------------------------------------- #
# T4.9 - icon-only controls
# --------------------------------------------------------------------------- #


def test_every_surface_renders_an_icon_without_visible_state_text(client, conn):
    from monoke.web.templating import STATIC_DIR

    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 1, title="Icon post")
    add_file(conn, post_id, sha(1), kind="image")
    add_file(conn, post_id, sha(2), kind="video")
    db.toggle_media_bookmark(conn, creator_id, sha(2))

    bodies = {
        "creator": client.get(f"/c/{creator_id}").text,
        "post": client.get(f"/c/{creator_id}/p/900001").text,
        "gallery": client.get(f"/c/{creator_id}/gallery").text,
        "bookmarks": client.get("/bookmarks").text,
    }

    for surface, body in bodies.items():
        assert "bookmark-icon" in body, surface
        assert ">Bookmark<" not in body, surface
        assert ">Bookmarked<" not in body, surface
        assert "aria-pressed=" in body, surface
        # Every control is still a plain form POST.
        assert "data-bookmark-form" in body, surface

    post_body = bodies["post"]
    assert post_body.count('class="bookmark-icon"') == 3
    post_buttons = re.findall(r"<button[^>]*data-bookmark-button[^>]*>", post_body)
    assert sum('aria-pressed="true"' in button for button in post_buttons) == 1
    assert 'title="Remove bookmark"' in post_body

    script = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    toggle = script.split("function toggleBookmarkForm", 1)[1].split(
        "function copyAllLinks", 1
    )[0]
    assert 'button.textContent = on ? "Bookmarked" : "Bookmark";' not in toggle
    assert 'button.classList.toggle("is-bookmarked", on)' in toggle
    assert 'button.setAttribute("aria-pressed"' in toggle


def test_bookmarked_tile_swaps_the_filled_glyph(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 1)
    add_file(conn, post_id, sha(1))
    db.toggle_media_bookmark(conn, creator_id, sha(1))

    body = client.get(f"/c/{creator_id}/gallery").text

    assert 'class="button button-small bookmark-button bookmark-compact is-bookmarked gallery-bookmark"' in body
    assert 'aria-pressed="true"' in body
    assert "bookmark-icon" in body
