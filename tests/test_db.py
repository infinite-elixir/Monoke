from __future__ import annotations

import json
import sqlite3

import pytest

from monoke import db


def make_creator(conn: sqlite3.Connection, service_id: str = "274608") -> int:
    return db.upsert_creator(
        conn, service="patreon", service_id=service_id, name="nyl2", display_name="Nyl"
    )


def make_post(conn: sqlite3.Connection, creator_id: int, service_id: str, **kw) -> int:
    return db.upsert_post(
        conn,
        creator_id=creator_id,
        service="patreon",
        service_id=service_id,
        fetched_at=db.now_iso(),
        **kw,
    )


def test_migrations_are_idempotent(conn):
    assert db.migrate(conn) == db.SCHEMA_VERSION
    assert db.migrate(conn) == db.SCHEMA_VERSION

    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {
        "creators",
        "posts",
        "files",
        "links",
        "tags",
        "runs",
        "api_cache",
        "link_resolutions",
    } <= tables


def test_upsert_creator_is_idempotent_and_updates(conn):
    creator_id = make_creator(conn)
    again = db.upsert_creator(
        conn, service="patreon", service_id="274608", post_count=782
    )

    assert again == creator_id
    creator = db.get_creator(conn, creator_id)
    assert creator is not None
    assert creator["name"] == "nyl2"  # COALESCE keeps the old value
    assert creator["post_count"] == 782
    assert db.find_creator(conn, "patreon", "274608")["id"] == creator_id


def test_list_creators_reports_counts(conn):
    creator_id = make_creator(conn)
    post_id = make_post(conn, creator_id, "1", title="One")
    db.replace_links(
        conn,
        post_id,
        [{"url": "https://mega.nz/x", "normalized_url": "https://mega.nz/x",
          "category": "mega", "origin": "caption"}],
    )
    db.replace_files(
        conn,
        post_id,
        [{"sha256": "a" * 64, "kind": "image", "url": "https://e1.cum.st/media/a/original.jpg"}],
    )

    [row] = db.list_creators(conn)
    assert row["id"] == creator_id
    assert row["post_rows"] == 1
    assert row["link_count"] == 1
    assert row["file_count"] == 1


def test_delete_creator_cascades(conn):
    creator_id = make_creator(conn)
    post_id = make_post(conn, creator_id, "1")
    db.replace_links(
        conn, post_id, [{"url": "https://a", "normalized_url": "https://a",
                         "category": "other", "origin": "caption"}]
    )
    db.replace_files(
        conn, post_id, [{"sha256": "b" * 64, "kind": "file", "url": "https://e1.cum.st/b"}]
    )
    db.replace_tags(conn, post_id, [{"slug": "pin", "label": "Pin"}])
    db.create_run(conn, creator_id, "full")

    assert db.delete_creator(conn, creator_id) is True
    for table in ("posts", "links", "files", "tags", "runs"):
        assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0


def test_upsert_post_is_idempotent(conn):
    creator_id = make_creator(conn)
    first = make_post(conn, creator_id, "42", title="Title")
    second = make_post(conn, creator_id, "42", title="Edited title", published=100)

    assert first == second
    post = db.get_post(conn, first)
    assert post["title"] == "Edited title"
    assert post["published"] == 100
    assert db.get_post_by_service_id(conn, creator_id, "42")["id"] == first


def test_replace_files_dedupes_by_sha(conn):
    creator_id = make_creator(conn)
    post_id = make_post(conn, creator_id, "1")

    written = db.replace_files(
        conn,
        post_id,
        [
            {"sha256": "c" * 64, "kind": "image", "url": "https://e1/original.jpg",
             "position": 0, "variants": [{"name": "original.jpg", "bytes": 10}]},
            {"sha256": "c" * 64, "kind": "image", "url": "https://e1/original.jpg",
             "position": 1},
        ],
    )

    assert written == 1
    [file_row] = db.list_files(conn, post_id)
    assert json.loads(file_row["variants_json"])[0]["name"] == "original.jpg"

    # replacement, not append
    db.replace_files(
        conn, post_id, [{"sha256": "d" * 64, "kind": "video", "url": "https://e1/original.mp4"}]
    )
    files = db.list_files(conn, post_id)
    assert len(files) == 1
    assert files[0]["sha256"] == "d" * 64


def test_replace_links_dedupes_by_normalized_url(conn):
    creator_id = make_creator(conn)
    post_id = make_post(conn, creator_id, "1")

    written = db.replace_links(
        conn,
        post_id,
        [
            {"url": "https://MEGA.nz/x#frag", "normalized_url": "https://mega.nz/x",
             "label": "Backup", "category": "mega", "origin": "caption"},
            {"url": "https://mega.nz/x?utm_source=a",
             "normalized_url": "https://mega.nz/x", "label": "dup",
             "category": "mega", "origin": "caption"},
        ],
    )

    assert written == 1
    [link] = db.list_links(conn, post_id)
    assert link["label"] == "Backup"


def test_replace_tags_is_idempotent(conn):
    creator_id = make_creator(conn)
    post_id = make_post(conn, creator_id, "1")

    assert db.replace_tags(
        conn,
        post_id,
        [{"slug": "pin", "label": "Pin"}, {"slug": "pin", "label": "Pin"}],
    ) == 1
    assert db.replace_tags(conn, post_id, [{"slug": "pin", "label": "Pin"}]) == 1
    assert len(db.list_tags(conn, post_id)) == 1


def test_list_posts_filters_and_pagination(conn):
    creator_id = make_creator(conn)
    a = make_post(conn, creator_id, "a", title="Alpha", published=300, caption_text="drive")
    b = make_post(conn, creator_id, "b", title="Beta", published=200)
    c = make_post(conn, creator_id, "c", title="Gamma", published=100)

    db.replace_links(
        conn, a, [{"url": "https://mega.nz/1", "normalized_url": "https://mega.nz/1",
                   "category": "mega", "origin": "caption"}]
    )
    db.replace_links(
        conn, b, [{"url": "https://other.example/2", "normalized_url": "https://other.example/2",
                   "category": "other", "origin": "caption"}]
    )
    db.replace_tags(conn, a, [{"slug": "pin-up", "label": "Pin-up"}])
    db.replace_files(conn, a, [{"sha256": "e" * 64, "kind": "image", "url": "https://e1/a.jpg"}])

    assert db.list_posts(conn, creator_id, category="mega")[1] == 1
    assert db.list_posts(conn, creator_id, tag="pin-up")[1] == 1
    assert db.list_posts(conn, creator_id, has_links=True)[1] == 2
    assert db.list_posts(conn, creator_id, has_attachments=True)[1] == 1
    assert db.list_posts(conn, creator_id, q="beta")[1] == 1
    assert db.list_posts(conn, creator_id, q="drive")[1] == 1
    assert db.list_posts(conn, creator_id, date_from=150)[1] == 2

    rows, total = db.list_posts(conn, creator_id, limit=2, offset=1)
    assert total == 3
    assert [row["service_id"] for row in rows] == ["b", "c"]

    titles, _ = db.list_posts(conn, creator_id, sort="title_asc")
    assert [row["title"] for row in titles] == ["Alpha", "Beta", "Gamma"]

    db.set_missing_upstream(conn, c, True)
    missing_rows, missing_total = db.list_posts(conn, creator_id, missing=True)
    assert missing_total == 1
    assert missing_rows[0]["service_id"] == "c"


def test_list_posts_rejects_unknown_sort(conn):
    creator_id = make_creator(conn)
    with pytest.raises(ValueError):
        db.list_posts(conn, creator_id, sort="popular")


def test_runs_lifecycle(conn):
    creator_id = make_creator(conn)
    run_id = db.create_run(conn, creator_id, "full")
    running = db.get_run(conn, run_id)
    assert running["status"] == "running"
    assert running["started_at"]

    db.finish_run(
        conn,
        run_id,
        status="done",
        pages=16,
        posts_seen=782,
        posts_new=782,
        links_found=812,
        files_count=912,
    )

    finished = db.get_run(conn, run_id)
    assert finished["status"] == "done"
    assert finished["posts_seen"] == 782
    assert finished["finished_at"]
    assert [row["id"] for row in db.list_runs(conn, creator_id)] == [run_id]


def test_cache_roundtrip(conn):
    url = "https://cum.st/api/v1/patreon/user/274608/posts?o=0"

    assert db.cache_get(conn, url) is None
    db.cache_put(conn, url, '{"total": 1}')
    cached = db.cache_get(conn, url)
    assert cached["body"] == '{"total": 1}'

    db.cache_put(conn, url, '{"total": 2}')
    assert db.cache_get(conn, url)["body"] == '{"total": 2}'

    db.cache_delete(conn, url)
    assert db.cache_get(conn, url) is None


def test_link_resolution_roundtrip(conn):
    url = "https://bit.ly/49A802v"
    assert db.get_resolution(conn, url) is None

    db.put_resolution(conn, url, "https://mega.nz/file/x")
    assert db.get_resolution(conn, url)["final_url"] == "https://mega.nz/file/x"

    db.put_resolution(conn, url, None)
    assert db.get_resolution(conn, url)["final_url"] is None


# --------------------------------------------------------------------------- #
# T7.1 - normalized kinds and the V5 backfill
# --------------------------------------------------------------------------- #


def _file_row(sha: str, kind: str, **overrides) -> dict:
    row = {
        "sha256": sha,
        "kind": kind,
        "raw_kind": kind,
        "mime_type": "image/gif" if kind in {"gif", "image"} else "video/mp4",
        "url": f"https://e1.cum.st/media/{sha}/original.{'mp4' if kind == 'video' else 'gif'}",
        "variants": [{"name": f"original.{'mp4' if kind == 'video' else 'gif'}"}],
    }
    row.update(overrides)
    return row


def test_v5_backfills_raw_kind_on_a_v4_database(settings):
    conn = db.connect(settings)
    try:
        for index in range(4):
            for statement in db.MIGRATIONS[index]:
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {index + 1}")
        conn.execute(
            "INSERT INTO creators (service, service_id, name) VALUES ('patreon', '1', 'n')"
        )
        conn.execute(
            "INSERT INTO posts (creator_id, service_id, service, title) VALUES (1, 'p', 'patreon', 'P')"
        )
        conn.execute(
            """
            INSERT INTO files (post_id, sha256, kind, mime_type, url)
            VALUES (1, ?, 'gif', 'image/gif', 'https://e1/original.gif')
            """,
            ("a" * 64,),
        )
        conn.commit()

        assert db.migrate(conn) == 6
        row = conn.execute("SELECT kind, raw_kind FROM files").fetchone()
        assert row["kind"] == "gif"
        assert row["raw_kind"] == "gif"
    finally:
        conn.close()


def test_reclassify_fixes_only_mp4_backed_gifs_and_is_idempotent(conn):
    creator_id = make_creator(conn)
    post_id = make_post(conn, creator_id, "1")
    mp4_gif = "1" * 64
    real_gif = "2" * 64
    image = "3" * 64
    db.replace_files(
        conn,
        post_id,
        [
            _file_row(
                mp4_gif,
                "gif",
                mime_type="image/gif",
                original_filename="clip.mp4",
                duration_ms=12_000,
                variants=[{"name": "original.mp4"}],
            ),
            _file_row(real_gif, "gif", original_filename="mood.gif"),
            _file_row(
                image,
                "image",
                mime_type="image/jpeg",
                original_filename="photo.jpg",
                variants=[{"name": "original.jpg"}],
            ),
        ],
    )

    dry = db.reclassify_files(conn, dry_run=True)
    assert dry == [{"from_kind": "gif", "to_kind": "video", "count": 1}]
    kinds = {row["sha256"]: row["kind"] for row in db.list_files(conn, post_id)}
    assert kinds == {mp4_gif: "gif", real_gif: "gif", image: "image"}

    assert db.reclassify_files(conn) == [
        {"from_kind": "gif", "to_kind": "video", "count": 1}
    ]
    kinds = {row["sha256"]: row["kind"] for row in db.list_files(conn, post_id)}
    assert kinds == {mp4_gif: "video", real_gif: "gif", image: "image"}
    assert all(row["raw_kind"] for row in db.list_files(conn, post_id))

    assert db.reclassify_files(conn) == []
