"""T9.1 - local bookmark storage, repository helpers and toggle endpoints."""

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
        "width": 100,
        "height": 200,
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


# --------------------------------------------------------------------------- #
# schema
# --------------------------------------------------------------------------- #


def test_fresh_db_is_v6_and_v5_upgrades_without_losing_rows(settings):
    conn = db.connect(settings)
    try:
        for index in range(5):
            for statement in db.MIGRATIONS[index]:
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {index + 1}")
        conn.execute(
            "INSERT INTO creators (service, service_id, name) VALUES ('patreon', '1', 'n')"
        )
        conn.execute(
            "INSERT INTO posts (creator_id, service_id, service, title) VALUES (1, 'p', 'patreon', 'P')"
        )
        conn.commit()

        assert db.migrate(conn) == db.SCHEMA_VERSION == 6
        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert {"post_bookmarks", "media_bookmarks"} <= tables
        assert conn.execute("SELECT count(*) FROM posts").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM creators").fetchone()[0] == 1
    finally:
        conn.close()


def test_deleting_a_creator_removes_both_bookmark_kinds(conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 1)
    add_file(conn, post_id, sha(1))
    db.toggle_post_bookmark(conn, creator_id, "900001")
    db.toggle_media_bookmark(conn, creator_id, sha(1))

    assert db.delete_creator(conn, creator_id) is True
    assert conn.execute("SELECT count(*) FROM post_bookmarks").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM media_bookmarks").fetchone()[0] == 0


# --------------------------------------------------------------------------- #
# post bookmarks
# --------------------------------------------------------------------------- #


def test_post_toggle_is_idempotent_and_creator_scoped(conn):
    first = make_creator(conn, "274608", "First")
    second = make_creator(conn, "999", "Second")
    add_post(conn, first, 1)
    add_post(conn, second, 2)

    assert db.toggle_post_bookmark(conn, first, "900001") is True
    assert db.toggle_post_bookmark(conn, first, "900001") is False
    assert db.toggle_post_bookmark(conn, first, "900001") is True

    [post] = db.list_post_bookmarks(conn, limit=10)[0]
    assert post["creator_id"] == first
    assert db.post_is_bookmarked(conn, first, post["post_id"]) is True
    assert db.post_is_bookmarked(conn, second, post["post_id"]) is False

    with pytest.raises(KeyError):
        db.toggle_post_bookmark(conn, second, "900001")


def test_post_toggle_never_touches_upstream_bookmarked(conn):
    creator_id = make_creator(conn)
    add_post(conn, creator_id, 1, bookmarked=1)

    db.toggle_post_bookmark(conn, creator_id, "900001")
    db.toggle_post_bookmark(conn, creator_id, "900001")

    row = conn.execute("SELECT bookmarked FROM posts").fetchone()
    assert row["bookmarked"] == 1


def test_batch_post_helpers_return_exactly_the_page(conn):
    creator_id = make_creator(conn)
    ids = [add_post(conn, creator_id, number) for number in range(4)]
    db.toggle_post_bookmark(conn, creator_id, "900001")
    db.toggle_post_bookmark(conn, creator_id, "900003")

    assert db.bookmarked_post_ids(conn, creator_id, ids) == {ids[1], ids[3]}
    assert db.bookmarked_post_ids(conn, creator_id, [ids[0]]) == set()
    assert db.bookmarked_post_ids(conn, creator_id, []) == set()


# --------------------------------------------------------------------------- #
# media bookmarks
# --------------------------------------------------------------------------- #


def test_media_toggle_is_sha_keyed_and_survives_replace_files(conn):
    creator_id = make_creator(conn)
    first = add_post(conn, creator_id, 1)
    second = add_post(conn, creator_id, 2)
    add_file(conn, first, sha(1))
    add_file(conn, second, sha(1))

    assert db.toggle_media_bookmark(conn, creator_id, sha(1)) is True
    # Re-bookmarking from another post is the same bookmark, not a second one.
    assert conn.execute("SELECT count(*) FROM media_bookmarks").fetchone()[0] == 1
    assert db.media_is_bookmarked(conn, creator_id, sha(1)) is True

    # A sync replaces files rows; the sha-keyed bookmark is untouched.
    add_file(conn, first, sha(1), position=5)
    assert db.media_is_bookmarked(conn, creator_id, sha(1)) is True

    assert db.toggle_media_bookmark(conn, creator_id, sha(1)) is False


def test_media_toggle_rejects_junk_and_cross_creator_shas(conn):
    first = make_creator(conn, "274608", "First")
    second = make_creator(conn, "999", "Second")
    add_file(conn, add_post(conn, first, 1), sha(7))

    with pytest.raises(KeyError):
        db.toggle_media_bookmark(conn, second, sha(7))
    with pytest.raises(KeyError):
        db.toggle_media_bookmark(conn, first, "f" * 64)
    with pytest.raises(KeyError):
        db.toggle_media_bookmark(conn, first, "")

    assert conn.execute("SELECT count(*) FROM media_bookmarks").fetchone()[0] == 0


def test_batch_media_helper_returns_exactly_the_page(conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 1)
    for number in range(3):
        add_file(conn, post_id, sha(number), position=number)
    db.toggle_media_bookmark(conn, creator_id, sha(1))

    page = [sha(0), sha(1), sha(2)]
    assert db.bookmarked_media_shas(conn, creator_id, page) == {sha(1)}
    assert db.bookmarked_media_shas(conn, creator_id, [sha(0)]) == set()
    assert db.bookmarked_media_shas(conn, creator_id, []) == set()


# --------------------------------------------------------------------------- #
# list functions
# --------------------------------------------------------------------------- #


def test_list_post_bookmarks_orders_filters_and_joins_creators(conn):
    first = make_creator(conn, "274608", "First")
    second = make_creator(conn, "999", "Second")
    first_post = add_post(conn, first, 1, title="First title")
    second_post = add_post(conn, second, 2, title="Second title")
    add_file(conn, first_post, sha(1), thumb_url="https://img/thumb.webp")

    db.toggle_post_bookmark(conn, first, "900001")
    db.toggle_post_bookmark(conn, second, "900002")

    rows, total = db.list_post_bookmarks(conn, limit=10)
    assert total == 2
    # Newest bookmark first; same-second inserts tie-break on bookmark id.
    assert [row["creator_id"] for row in rows] == [second, first]
    assert rows[0]["creator_name"] == "Second"
    assert rows[0]["service_id"] == "900002"
    assert rows[0]["post_id"] == second_post
    assert rows[1]["thumb_url"] == "https://img/thumb.webp"
    assert rows[0]["title"] == "Second title"
    assert rows[0]["missing_upstream"] == 0

    only_first, first_total = db.list_post_bookmarks(conn, creator_id=first, limit=10)
    assert first_total == 1
    assert only_first[0]["post_id"] == first_post


def test_list_media_bookmarks_marks_missing_but_keeps_the_row(conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 1, title="With media")
    add_file(conn, post_id, sha(1), kind="video", duration_ms=9000)
    db.toggle_media_bookmark(conn, creator_id, sha(1))

    rows, total = db.list_media_bookmarks(conn, limit=10)
    assert total == 1
    row = rows[0]
    assert row["sha256"] == sha(1)
    assert row["missing"] == 0
    assert row["kind"] == "video"
    assert row["duration_ms"] == 9000
    assert row["post_service_id"] == "900001"
    assert row["creator_name"] == "Nyl"
    assert row["url"].endswith("original.jpg")

    # The attachment disappears upstream; the bookmark stays removable.
    db.replace_files(conn, post_id, [])
    rows, total = db.list_media_bookmarks(conn, limit=10)
    assert total == 1
    assert rows[0]["missing"] == 1
    assert rows[0]["url"] is None
    assert rows[0]["thumb_url"] is None


def test_list_clamps_limit_and_offset(conn):
    creator_id = make_creator(conn)
    add_post(conn, creator_id, 1)

    rows, total = db.list_post_bookmarks(conn, limit=-5, offset=-2)
    assert total == 0 and rows == []

    db.toggle_post_bookmark(conn, creator_id, "900001")
    assert db.list_post_bookmarks(conn, limit=10_000)[0] != []


def test_bookmark_creator_facets_only_lists_creators_with_bookmarks(conn):
    first = make_creator(conn, "274608", "First")
    second = make_creator(conn, "999", "Second")
    make_creator(conn, "777", "Empty")
    add_file(conn, add_post(conn, first, 1), sha(1))
    add_post(conn, second, 2)

    db.toggle_post_bookmark(conn, first, "900001")
    db.toggle_media_bookmark(conn, first, sha(1))
    db.toggle_post_bookmark(conn, second, "900002")

    facets = {row["creator_name"]: row for row in db.bookmark_creator_facets(conn)}
    assert set(facets) == {"First", "Second"}
    assert facets["First"]["post_count"] == 1
    assert facets["First"]["media_count"] == 1
    assert facets["Second"]["media_count"] == 0


# --------------------------------------------------------------------------- #
# HTTP endpoints
# --------------------------------------------------------------------------- #


def test_post_toggle_route_redirects_to_safe_next(client, conn):
    creator_id = make_creator(conn)
    add_post(conn, creator_id, 1, title="Bookmark me")

    url = f"/c/{creator_id}/p/900001/bookmark"
    response = client.post(url, data={"next": f"/c/{creator_id}?q=x&page=2"})

    assert response.status_code == 303
    assert response.headers["location"] == f"/c/{creator_id}?q=x&page=2"
    row = conn.execute("SELECT count(*) FROM post_bookmarks").fetchone()[0]
    assert row == 1

    again = client.post(url, data={"next": f"/c/{creator_id}"})
    assert again.status_code == 303
    assert conn.execute("SELECT count(*) FROM post_bookmarks").fetchone()[0] == 0


@pytest.mark.parametrize(
    "next_value",
    [
        "https://evil.example/steal",
        "//evil.example/steal",
        "/\\evil.example",
        "javascript:alert(1)",
    ],
)
def test_post_toggle_route_rejects_external_next(client, conn, next_value):
    creator_id = make_creator(conn)
    add_post(conn, creator_id, 1)

    response = client.post(
        f"/c/{creator_id}/p/900001/bookmark", data={"next": next_value}
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/c/{creator_id}/p/900001"


def test_post_toggle_route_returns_json_and_404s_for_unknowns(client, conn):
    creator_id = make_creator(conn)
    add_post(conn, creator_id, 1)

    response = client.post(
        f"/c/{creator_id}/p/900001/bookmark?format=json",
        headers={"Accept": "application/json"},
    )
    assert response.status_code == 200
    assert response.json() == {"bookmarked": True, "kind": "post", "id": "900001"}

    second = client.post(f"/c/{creator_id}/p/900001/bookmark")
    assert second.status_code == 303
    json_off = client.post(
        f"/c/{creator_id}/p/900001/bookmark", headers={"Accept": "application/json"}
    )
    # The form POST reverted the JSON toggle above, so this one turns it on.
    assert json_off.json()["bookmarked"] is True

    assert client.post("/c/999/p/900001/bookmark").status_code == 404
    assert client.post(f"/c/{creator_id}/p/nope/bookmark").status_code == 404


def test_media_toggle_route_json_redirect_and_cross_creator_404(client, conn):
    first = make_creator(conn, "274608", "First")
    second = make_creator(conn, "999", "Second")
    add_file(conn, add_post(conn, first, 1), sha(1))

    url = f"/c/{first}/media/{sha(1)}/bookmark"
    toggled = client.post(url, headers={"Accept": "application/json"})
    assert toggled.status_code == 200
    assert toggled.json() == {"bookmarked": True, "kind": "media", "id": sha(1)}

    form = client.post(url, data={"next": f"/c/{first}/gallery?kind=video"})
    assert form.status_code == 303
    assert form.headers["location"] == f"/c/{first}/gallery?kind=video"
    assert db.media_is_bookmarked(conn, first, sha(1)) is False

    assert client.post(f"/c/{second}/media/{sha(1)}/bookmark").status_code == 404
    assert client.post(f"/c/{first}/media/{'a' * 64}/bookmark").status_code == 404
    assert client.post(f"/c/999/media/{sha(1)}/bookmark").status_code == 404
