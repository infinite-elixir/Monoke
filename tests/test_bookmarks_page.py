"""T9.3 - the /bookmarks library page, tabs, creator filter and navigation."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from monoke import db
from monoke.config import get_settings
from monoke.web.app import create_app
from monoke.web.filters import BookmarkFilters, GALLERY_PER_PAGE, PER_PAGE


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
    rows = db.list_files(conn, post_id)
    row = {
        "sha256": sha,
        "kind": kind,
        "raw_kind": kind,
        "mime_type": "image/jpeg" if kind == "image" else "video/mp4",
        "bytes": 1024,
        "width": 1600,
        "height": 1200,
        "duration_ms": 5000 if kind == "video" else None,
        "original_filename": f"{sha[:8]}.jpg",
        "url": f"https://e1.cum.st/media/{sha}/original.jpg",
        "thumb_url": f"https://img.cum.st/thumbnail/{sha}/preview.webp",
        "position": 0,
    }
    row.update(overrides)
    rows.append(row)
    db.replace_files(conn, post_id, rows)
    conn.commit()


def sha(number: int) -> str:
    return f"{number:064x}"


def test_empty_database_shows_global_empty_state(client):
    response = client.get("/bookmarks")

    assert response.status_code == 200
    body = response.text
    assert "No bookmarks yet" in body
    assert "Browse creators" in body
    assert "bookmark-media-grid" not in body
    nav = body.split('<nav class="site-nav"', 1)[1].split("</nav>", 1)[0]
    assert nav.count('aria-current="page"') == 1


def test_three_views_and_unknown_fallback(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 1, title="Saved words")
    add_file(conn, post_id, sha(1), kind="video")
    db.toggle_post_bookmark(conn, creator_id, "900001")
    db.toggle_media_bookmark(conn, creator_id, sha(1))

    posts_view = client.get("/bookmarks?view=posts").text
    assert "Saved words" in posts_view
    assert "bookmark-media-grid" not in posts_view
    assert "bookmark-post-list" in posts_view

    media_view = client.get("/bookmarks?view=media").text
    assert "bookmark-media-grid" in media_view
    assert "bookmark-post-list" not in media_view
    assert "00000000.jpg" in media_view

    all_view = client.get("/bookmarks?view=all").text
    assert "bookmark-post-list" in all_view
    assert "bookmark-media-grid" in all_view

    fallback = client.get("/bookmarks?view=banana").text
    assert "bookmark-post-list" in fallback
    assert "bookmark-media-grid" in fallback
    # Unknown values do not leak into the tab URLs.
    assert "view=banana" not in fallback


def test_posts_and_media_are_ordered_newest_bookmark_first(client, conn):
    creator_id = make_creator(conn)
    first = add_post(conn, creator_id, 1, title="First saved")
    second = add_post(conn, creator_id, 2, title="Second saved")
    add_file(conn, first, sha(1))
    add_file(conn, second, sha(2))

    db.toggle_post_bookmark(conn, creator_id, "900001")
    db.toggle_post_bookmark(conn, creator_id, "900002")
    db.toggle_media_bookmark(conn, creator_id, sha(1))
    db.toggle_media_bookmark(conn, creator_id, sha(2))

    body = client.get("/bookmarks").text
    assert body.index("Second saved") < body.index("First saved")
    assert body.index(f"/media/{sha(2)}/bookmark") < body.index(
        f"/media/{sha(1)}/bookmark"
    )


def test_creator_filter_limits_both_sections_and_survives_tabs(client, conn):
    first = make_creator(conn, "274608", "First")
    second = make_creator(conn, "999", "Second")
    add_file(conn, add_post(conn, first, 1, title="First post"), sha(1))
    add_file(conn, add_post(conn, second, 2, title="Second post"), sha(2))
    db.toggle_post_bookmark(conn, first, "900001")
    db.toggle_post_bookmark(conn, second, "900002")
    db.toggle_media_bookmark(conn, first, sha(1))
    db.toggle_media_bookmark(conn, second, sha(2))

    body = client.get("/bookmarks").text
    # The filter is offered because more than one creator has bookmarks.
    assert 'id="bookmark-creator"' in body
    assert "First (2)" in body and "Second (2)" in body

    filtered = client.get(f"/bookmarks?creator={first}").text
    assert "First post" in filtered
    assert "Second post" not in filtered
    assert f"view=posts&amp;creator={first}" in filtered
    assert f"view=media&amp;creator={first}" in filtered
    assert f'value="{first}"' in filtered


def test_tabs_reset_page_and_creator_filter_is_preserved(client, conn):
    creator_id = make_creator(conn)
    for number in range(PER_PAGE + 3):
        add_post(conn, creator_id, number, title=f"Post {number}")
    for number in range(PER_PAGE + 3):
        db.toggle_post_bookmark(conn, creator_id, str(900000 + number))

    second_page = client.get(
        f"/bookmarks?view=posts&creator={creator_id}&page=2"
    ).text
    assert "page 2 of" in second_page
    # Tab links keep the creator but drop the page number.
    assert f"/bookmarks?view=media&amp;creator={creator_id}" in second_page
    assert (
        f"/bookmarks?view=posts&amp;creator={creator_id}&amp;page=1"
        not in second_page
    )

    # Page numbers within the tab keep the creator filter (page 1 omits the
    # parameter because it is the default).
    assert f'rel="prev"' in second_page
    assert f"/bookmarks?view=posts&amp;creator={creator_id}" in second_page


def test_page_beyond_the_end_clamps_to_the_last_page(client, conn):
    creator_id = make_creator(conn)
    for number in range(PER_PAGE + 1):
        add_post(conn, creator_id, number, title=f"Post {number}")
        db.toggle_post_bookmark(conn, creator_id, str(900000 + number))

    body = client.get("/bookmarks?view=posts&page=999").text
    assert "page 2 of 2" in body
    assert "Post 0" in body


def test_removing_from_the_library_returns_to_the_same_view(client, conn):
    creator_id = make_creator(conn)
    add_post(conn, creator_id, 1, title="Removable")
    db.toggle_post_bookmark(conn, creator_id, "900001")
    next_url = f"/bookmarks?view=posts&creator={creator_id}"

    response = client.post(
        f"/c/{creator_id}/p/900001/bookmark", data={"next": next_url}
    )
    assert response.status_code == 303
    assert response.headers["location"] == next_url

    body = client.get(next_url).text
    assert "Removable" not in body
    assert "No bookmarks yet" in body


def test_missing_media_still_renders_and_can_be_removed(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 1, title="Gone media")
    add_file(conn, post_id, sha(1))
    db.toggle_media_bookmark(conn, creator_id, sha(1))
    db.replace_files(conn, post_id, [])

    body = client.get("/bookmarks?view=media").text
    assert "No longer attached" in body

    response = client.post(
        f"/c/{creator_id}/media/{sha(1)}/bookmark",
        data={"next": "/bookmarks?view=media"},
    )
    assert response.status_code == 303
    assert "No longer attached" not in client.get("/bookmarks?view=media").text


def test_creator_filter_with_no_posts_offers_show_all(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 1), sha(1))
    db.toggle_media_bookmark(conn, creator_id, sha(1))

    body = client.get(f"/bookmarks?view=posts&creator={creator_id}").text
    assert "No posts match this view" in body
    assert "Show all bookmarks" in body


def test_nav_has_bookmarks_link_and_aria_current_only_there(client, conn):
    creator_id = make_creator(conn)
    add_post(conn, creator_id, 1)

    pages = ["/", "/links", "/bookmarks", f"/c/{creator_id}", f"/c/{creator_id}/p/900001"]
    for url in pages:
        body = client.get(url).text
        assert 'href="/bookmarks"' in body
        nav = body.split('<nav class="site-nav"', 1)[1].split("</nav>", 1)[0]
        assert ">Bookmarks</a>" in nav
        # Exactly one primary nav entry is current per page, and only the
        # Bookmarks link is current on /bookmarks.
        assert nav.count('aria-current="page"') == 1
        bookmark_link = nav.split('href="/bookmarks"', 1)[1].split("</a>", 1)[0]
        assert ('aria-current="page"' in bookmark_link) == (url == "/bookmarks")


def test_bookmark_filters_parse_and_reset_page():
    filters = BookmarkFilters.from_query({"view": "media", "creator": "7", "page": "4"})
    assert (filters.view, filters.creator, filters.page) == ("media", 7, 4)
    assert filters.url_for(view="posts") == "/bookmarks?view=posts&creator=7"

    unknown = BookmarkFilters.from_query({"view": "nope", "creator": "x", "page": "x"})
    assert (unknown.view, unknown.creator, unknown.page) == ("all", None, 1)
