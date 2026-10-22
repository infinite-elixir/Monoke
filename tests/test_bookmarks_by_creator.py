"""T9.4 - bookmarks grouped by creator and the bookmark-only viewer."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from monoke import db
from monoke.config import get_settings
from monoke.web.app import create_app
from monoke.web.filters import BOOKMARKS_PER_CREATOR_PREVIEW, PER_PAGE


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


def add_file(conn, post_id: int, sha_value: str, kind: str = "image", **overrides) -> None:
    extension = {"image": "jpg", "gif": "gif", "video": "mp4"}.get(kind, "bin")
    rows = db.list_files(conn, post_id)
    row = {
        "sha256": sha_value,
        "kind": kind,
        "raw_kind": kind,
        "mime_type": f"{kind}/{'mp4' if kind == 'video' else 'jpeg'}",
        "bytes": 2048,
        "width": 100,
        "height": 200,
        "duration_ms": 5000 if kind == "video" else None,
        "original_filename": f"{sha_value[:8]}.{extension}",
        "url": f"https://e1.cum.st/media/{sha_value}/original.{extension}",
        "thumb_url": f"https://img.cum.st/thumbnail/{sha_value}/preview.webp",
        "position": len(rows),
    }
    row.update(overrides)
    rows.append(row)
    db.replace_files(conn, post_id, rows)
    conn.commit()


def sha(number: int) -> str:
    return f"{number:064x}"


def set_bookmark_time(conn, table: str, bookmark_id: int, value: str) -> None:
    with conn:
        conn.execute(
            f"UPDATE {table} SET created_at = ? WHERE id = ?",
            (value, int(bookmark_id)),
        )


def creator_sections(body: str) -> list[str]:
    chunks = body.split('class="bookmark-section bookmark-creator-section"')[1:]
    return [chunk.split("</section>", 1)[0] for chunk in chunks]


# --------------------------------------------------------------------------- #
# overview grouping
# --------------------------------------------------------------------------- #


def test_overview_groups_by_creator_newest_bookmark_first(client, conn):
    first = make_creator(conn, "1", "Alpha")
    second = make_creator(conn, "2", "Bravo")
    third = make_creator(conn, "3", "Charlie")
    for creator_id, number in ((first, 1), (second, 2), (third, 3)):
        add_post(conn, creator_id, number, title=f"{creator_id} post")
        db.toggle_post_bookmark(conn, creator_id, str(900000 + number))

    rows = conn.execute("SELECT id, creator_id FROM post_bookmarks").fetchall()
    for row in rows:
        if int(row["creator_id"]) == first:
            set_bookmark_time(conn, "post_bookmarks", row["id"], "2026-01-01T10:00:00+00:00")
        elif int(row["creator_id"]) == second:
            set_bookmark_time(conn, "post_bookmarks", row["id"], "2026-03-01T10:00:00+00:00")
        elif int(row["creator_id"]) == third:
            set_bookmark_time(conn, "post_bookmarks", row["id"], "2026-02-01T10:00:00+00:00")

    sections = creator_sections(client.get("/bookmarks").text)

    assert len(sections) == 3
    assert "Bravo" in sections[0]
    assert "Charlie" in sections[1]
    assert "Alpha" in sections[2]


def test_preview_caps_at_six_and_links_to_the_full_list(client, conn):
    creator_id = make_creator(conn)
    for number in range(7):
        add_file(conn, add_post(conn, creator_id, number), sha(number))
        db.toggle_media_bookmark(conn, creator_id, sha(number))

    body = client.get("/bookmarks?view=media").text

    assert body.count("bookmark-media-tile") == BOOKMARKS_PER_CREATOR_PREVIEW
    assert "View all 7 bookmarks" in body
    assert (
        f'href="/bookmarks?view=media&amp;creator={creator_id}"' in body
    )


def test_views_preview_only_their_own_kind(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 1, title="Saved words")
    add_file(conn, post_id, sha(1))
    db.toggle_post_bookmark(conn, creator_id, "900001")
    db.toggle_media_bookmark(conn, creator_id, sha(1))

    all_view = creator_sections(client.get("/bookmarks").text)[0]
    assert "bookmark-card" in all_view
    assert "bookmark-media-tile" in all_view

    posts_view = creator_sections(client.get("/bookmarks?view=posts").text)[0]
    assert "bookmark-card" in posts_view
    assert "bookmark-media-tile" not in posts_view

    media_view = creator_sections(client.get("/bookmarks?view=media").text)[0]
    assert "bookmark-media-tile" in media_view
    assert "bookmark-card" not in media_view


def test_view_all_keeps_the_tab_uncaps_and_pages(client, conn):
    creator_id = make_creator(conn)
    for number in range(PER_PAGE + 1):
        add_post(conn, creator_id, number, title=f"Post {number}")
        db.toggle_post_bookmark(conn, creator_id, str(900000 + number))

    first = client.get("/bookmarks?view=posts").text
    sections = creator_sections(first)
    assert len(sections) == 1
    assert sections[0].count('class="bookmark-card"') == BOOKMARKS_PER_CREATOR_PREVIEW
    assert "View all 51 bookmarks" in sections[0]

    full = client.get(f"/bookmarks?view=posts&creator={creator_id}").text
    assert full.count('class="bookmark-card"') == PER_PAGE
    assert "page 1 of 2" in full

    second = client.get(
        f"/bookmarks?view=posts&creator={creator_id}&page=2"
    ).text
    assert second.count('class="bookmark-card"') == 1
    assert "page 2 of 2" in second


def test_creator_without_items_in_the_active_view_gets_no_section(client, conn):
    posts_creator = make_creator(conn, "1", "Posts Only")
    media_creator = make_creator(conn, "2", "Media Only")
    add_post(conn, posts_creator, 1)
    db.toggle_post_bookmark(conn, posts_creator, "900001")
    add_file(conn, add_post(conn, media_creator, 2), sha(2))
    db.toggle_media_bookmark(conn, media_creator, sha(2))

    sections = creator_sections(client.get("/bookmarks?view=posts").text)

    assert len(sections) == 1
    assert "Posts Only" in sections[0]
    assert "Media Only" not in sections[0]


def test_missing_media_preview_keeps_placeholder_and_remove_form(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 1, title="Gone media")
    add_file(conn, post_id, sha(1))
    db.toggle_media_bookmark(conn, creator_id, sha(1))
    db.replace_files(conn, post_id, [])

    body = client.get("/bookmarks?view=media").text
    assert "No longer attached" in body
    assert f'action="/c/{creator_id}/media/{sha(1)}/bookmark"' in body

    response = client.post(
        f"/c/{creator_id}/media/{sha(1)}/bookmark",
        data={"next": "/bookmarks?view=media"},
    )
    assert response.status_code == 303
    assert "No longer attached" not in client.get("/bookmarks?view=media").text


def test_overview_uses_a_bounded_number_of_queries(conn):
    class CountingConnection:
        def __init__(self, inner):
            self.inner = inner
            self.count = 0

        def execute(self, *args, **kwargs):
            self.count += 1
            return self.inner.execute(*args, **kwargs)

    for number in range(5):
        creator_id = make_creator(conn, str(number), f"Creator {number}")
        add_post(conn, creator_id, number)
        db.toggle_post_bookmark(conn, creator_id, str(900000 + number))

    counter = CountingConnection(conn)
    sections, totals = db.bookmark_overview(
        counter, view="all", preview_limit=BOOKMARKS_PER_CREATOR_PREVIEW
    )

    assert len(sections) == 5
    assert totals["all"] == 5
    # One windowed query for posts, one for media, regardless of creator count.
    assert counter.count == 2


# --------------------------------------------------------------------------- #
# media tiles link to their post, not a viewer
# --------------------------------------------------------------------------- #


def test_media_tile_links_to_the_underlying_post(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 1, title="Saved post")
    add_file(conn, post_id, sha(1), kind="video")
    db.toggle_media_bookmark(conn, creator_id, sha(1))

    body = client.get("/bookmarks?view=media").text
    section = creator_sections(body)[0]

    assert f'href="/c/{creator_id}/p/900001"' in section
    assert "/bookmarks/viewer" not in section
    assert "data-viewer-open" not in section
    assert "data-viewer-focus" not in section
