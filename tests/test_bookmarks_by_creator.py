"""T9.4 - bookmarks grouped by creator and the bookmark-only viewer."""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, quote, unquote, urlsplit

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


def media_bookmark_id(conn, creator_id: int, sha_value: str) -> int:
    row = conn.execute(
        "SELECT id FROM media_bookmarks WHERE creator_id = ? AND sha256 = ?",
        (creator_id, sha_value),
    ).fetchone()
    assert row is not None
    return int(row["id"])


def embedded_state(body: str) -> dict:
    match = re.search(
        r'<script type="application/json" id="viewer-initial">(.*?)</script>',
        body,
        re.S,
    )
    assert match is not None
    return json.loads(match.group(1))


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
# media tiles open the bookmark viewer
# --------------------------------------------------------------------------- #


def bookmark_viewer_link(section: str) -> str:
    match = re.search(r'href="(/bookmarks/viewer\?[^"]+)"', section)
    assert match is not None
    return match.group(1).replace("&amp;", "&")


def test_media_tile_links_to_the_viewer_with_the_library_return_path(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 1), sha(1), kind="video")
    db.toggle_media_bookmark(conn, creator_id, sha(1))
    bookmark_id = media_bookmark_id(conn, creator_id, sha(1))

    body = client.get("/bookmarks?view=media").text
    section = creator_sections(body)[0]
    href = bookmark_viewer_link(section)
    query = parse_qs(urlsplit(href).query)

    assert query["b"] == [str(bookmark_id)]
    assert query["view"] == ["media"]
    assert query["creator"] == [str(creator_id)]
    assert unquote(query["next"][0]) == "/bookmarks?view=media"
    assert 'target="_blank"' not in section


def test_viewer_starts_on_the_requested_bookmark_not_the_first(client, conn):
    creator_id = make_creator(conn)
    bookmark_ids = []
    for number in range(3):
        add_file(conn, add_post(conn, creator_id, number), sha(number))
        db.toggle_media_bookmark(conn, creator_id, sha(number))
        bookmark_ids.append(media_bookmark_id(conn, creator_id, sha(number)))
    # Make the requested bookmark the oldest so it lands last in the playlist.
    set_bookmark_time(conn, "media_bookmarks", bookmark_ids[0], "2026-01-01T00:00:00+00:00")
    set_bookmark_time(conn, "media_bookmarks", bookmark_ids[1], "2026-02-01T00:00:00+00:00")
    set_bookmark_time(conn, "media_bookmarks", bookmark_ids[2], "2026-03-01T00:00:00+00:00")

    body = client.get(
        f"/bookmarks/viewer?b={bookmark_ids[0]}&creator={creator_id}"
    ).text
    state = embedded_state(body)

    assert state["mode"] == "bookmarks"
    assert state["index"] == 2
    assert state["media"][2]["bookmark_id"] == bookmark_ids[0]
    assert state["media"][0]["bookmark_id"] == bookmark_ids[2]
    assert "3 / 3" in body


def test_playlist_contains_only_media_bookmarks(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 1)
    add_file(conn, post_id, sha(1), kind="image", position=0)
    add_file(conn, post_id, sha(2), kind="video", position=1)
    add_file(conn, post_id, sha(3), kind="image", position=2)
    db.toggle_media_bookmark(conn, creator_id, sha(2))
    bookmark_id = media_bookmark_id(conn, creator_id, sha(2))

    state = embedded_state(
        client.get(f"/bookmarks/viewer?b={bookmark_id}").text
    )

    assert [item["sha256"] for item in state["media"]] == [sha(2)]
    assert state["media"][0]["bookmarked"] is True
    # No post axis is faked in bookmark mode.
    assert "previous_post" not in state
    assert "next_post" not in state
    assert "post" not in state


def test_unscoped_playlist_crosses_creators_and_scoped_rejects_other_creator(
    client, conn
):
    first = make_creator(conn, "1", "First")
    second = make_creator(conn, "2", "Second")
    add_file(conn, add_post(conn, first, 1), sha(1), kind="image")
    add_file(conn, add_post(conn, second, 2), sha(2), kind="video")
    db.toggle_media_bookmark(conn, first, sha(1))
    db.toggle_media_bookmark(conn, second, sha(2))
    first_bookmark = media_bookmark_id(conn, first, sha(1))
    second_bookmark = media_bookmark_id(conn, second, sha(2))

    state = embedded_state(
        client.get(f"/bookmarks/viewer?b={first_bookmark}").text
    )
    assert {item["creator_id"] for item in state["media"]} == {first, second}
    assert {item["creator_name"] for item in state["media"]} == {"First", "Second"}

    assert (
        client.get(
            f"/bookmarks/viewer?b={first_bookmark}&creator={second}"
        ).status_code
        == 404
    )
    assert client.get(f"/bookmarks/viewer?b={second_bookmark}&creator={second}").status_code == 200


def test_unknown_or_malformed_bookmark_is_404(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 1), sha(1))
    db.toggle_media_bookmark(conn, creator_id, sha(1))

    for url in (
        "/bookmarks/viewer",
        "/bookmarks/viewer?b=nope",
        "/bookmarks/viewer?b=99999",
        f"/bookmarks/viewer?b=1&creator=nope",
    ):
        assert client.get(url).status_code == 404, url


def test_missing_attachment_shows_unavailable_and_stays_in_playlist(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 1, title="Gone media")
    add_file(conn, post_id, sha(1))
    add_file(conn, add_post(conn, creator_id, 2), sha(2))
    db.toggle_media_bookmark(conn, creator_id, sha(1))
    db.toggle_media_bookmark(conn, creator_id, sha(2))
    missing_id = media_bookmark_id(conn, creator_id, sha(1))
    present_id = media_bookmark_id(conn, creator_id, sha(2))
    db.replace_files(conn, post_id, [])

    body = client.get(
        f"/bookmarks/viewer?b={missing_id}&creator={creator_id}"
    ).text
    state = embedded_state(body)

    assert "Media unavailable" in body
    assert f'href="/c/{creator_id}"' in body
    assert {item["bookmark_id"] for item in state["media"]} == {
        missing_id,
        present_id,
    }


def test_bookmark_viewer_hides_post_axis_and_uses_bookmark_hints(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 1), sha(1))
    db.toggle_media_bookmark(conn, creator_id, sha(1))
    bookmark_id = media_bookmark_id(conn, creator_id, sha(1))

    body = client.get(f"/bookmarks/viewer?b={bookmark_id}").text

    assert 'data-viewer-action="prev-post"' not in body
    assert 'data-viewer-action="next-post"' not in body
    assert "Previous bookmark" in body
    assert "Next bookmark" in body
    assert "Newer post" not in body
    assert "Older post" not in body


def test_viewer_script_guards_bookmark_mode_from_post_preloading_and_toggles():
    from monoke.web.templating import STATIC_DIR

    script = (STATIC_DIR / "viewer.js").read_text(encoding="utf-8")

    assert 'state.mode === "bookmarks"' in script
    assert "function itemCreatorId(item)" in script
    assert "if (bookmarksMode) {\n      return;\n    }" in script
    assert (
        'if (bookmarksMode || !mediaReady || !preloadAllowed())' in script
    )
    # Media toggles resolve the item's own creator in a mixed playlist.
    assert 'var targetCreatorId = itemCreatorId(item);' in script
    assert '"/c/" +\n        targetCreatorId +' in script
    # Up/down never requests another post payload in bookmarks mode.
    assert 'bookmarksMode && (action === "prev-post" || action === "next-post")' in script


def test_close_returns_only_to_a_local_bookmarks_url(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 1), sha(1))
    db.toggle_media_bookmark(conn, creator_id, sha(1))
    bookmark_id = media_bookmark_id(conn, creator_id, sha(1))

    next_url = f"/bookmarks?view=media&creator={creator_id}&page=2"
    body = client.get(
        f"/bookmarks/viewer?b={bookmark_id}&next={quote(next_url, safe='')}"
    ).text
    assert 'data-return-url="/bookmarks?view=media&amp;creator=' in body

    for bad in (
        "https://evil.example/bookmarks",
        "//evil.example/bookmarks",
        "/c/1",
        "/bookmarks/extra",
    ):
        body = client.get(
            f"/bookmarks/viewer?b={bookmark_id}&next={quote(bad, safe='')}"
        ).text
        assert 'data-return-url="/bookmarks"' in body, bad
