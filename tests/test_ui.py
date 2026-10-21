from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from monoke import db
from monoke.config import get_settings
from monoke.extract.post import extract_post
from monoke.models import PostDetail
from monoke.web import routes
from monoke.web.app import create_app

FIXTURES = Path(__file__).parent / "fixtures" / "cumst"
CREATOR_URL = "https://cum.st/creators/patreon/274608"


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
        "post_count": 2,
        "last_synced_at": db.now_iso(),
    }
    values.update(overrides)
    return db.upsert_creator(conn, **values)


def seed_fixture_post(conn, creator_id: int, name: str) -> int:
    payload = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    detail = PostDetail.model_validate(payload)
    extracted = extract_post(detail)
    post_id, _ = db.save_post_bundle(
        conn,
        creator_id=creator_id,
        service=detail.service,
        service_id=detail.id,
        title=detail.title,
        caption_html=extracted.caption_html,
        caption_text=extracted.caption_text,
        published=detail.published,
        added=detail.added,
        prev_id=detail.prev.id if detail.prev else None,
        next_id=detail.next.id if detail.next else None,
        revisions_count=len(detail.revisions),
        linked_count=len(detail.linked),
        fetched_at=db.now_iso(),
        files=extracted.files,
        links=extracted.links,
        tags=extracted.tags,
    )
    return post_id


def seed_many(conn, creator_id: int, count: int = 782) -> None:
    categories = ["mega", "direct_media", "google_drive", "shortener", "other"]
    for index in range(count):
        title = f"Post {index:03d}"
        if index % 10 == 0:
            title = f"{title} pin"
        post_id = db.upsert_post(
            conn,
            creator_id=creator_id,
            service="patreon",
            service_id=str(900000 + index),
            title=title,
            caption_text="pin caption" if index % 10 == 0 else "",
            published=1000 + index,
            added=1000 + index,
            fetched_at=db.now_iso(),
        )
        category = categories[index % len(categories)]
        db.replace_links(
            conn,
            post_id,
            [
                {
                    "url": f"https://{category}.example/{index}",
                    "normalized_url": f"https://{category}.example/{index}",
                    "label": "Link",
                    "category": category,
                    "origin": "caption",
                }
            ],
        )
        if index % 7 == 0:
            db.replace_tags(conn, post_id, [{"slug": "pin-up", "label": "Pin-up"}])
        if index % 11 == 0:
            db.set_missing_upstream(conn, post_id, True)


# --------------------------------------------------------------------------- #
# dashboard / add creator
# --------------------------------------------------------------------------- #


def test_dashboard_empty_state_and_form(client):
    response = client.get("/")

    assert response.status_code == 200
    assert 'action="/creators"' in response.text
    assert "Paste a Kemono creator URL to start." in response.text
    assert 'name="url"' in response.text
    assert "https://" not in response.text


def test_invalid_url_shows_parser_message_and_keeps_value(client):
    for value in ("https://example.com/foo", "patreon/user/abc"):
        response = client.post("/creators", data={"url": value})

        assert response.status_code == 200
        assert value in response.text
        assert "field-error" in response.text
        assert "Traceback" not in response.text


def test_add_creator_redirects_and_is_idempotent(client, conn, monkeypatch):
    calls = []

    def fake_start(ref, *, settings=None, creator_id, run_id):
        calls.append((ref, creator_id, run_id))

    monkeypatch.setattr(routes, "start_crawl", fake_start)

    first = client.post(
        "/creators", data={"url": CREATOR_URL}, follow_redirects=False
    )
    assert first.status_code == 303
    assert first.headers["location"] == "/c/1"

    second = client.post(
        "/creators", data={"url": CREATOR_URL}, follow_redirects=False
    )
    assert second.status_code == 303
    assert second.headers["location"] == "/c/1"

    assert conn.execute("SELECT count(*) AS n FROM creators").fetchone()["n"] == 1
    assert conn.execute("SELECT count(*) AS n FROM runs").fetchone()["n"] == 1
    assert len(calls) == 1
    assert calls[0][1] == 1


# --------------------------------------------------------------------------- #
# creator page
# --------------------------------------------------------------------------- #


def test_creator_page_paginates_782_posts(client, conn):
    creator_id = make_creator(conn, post_count=782)
    seed_many(conn, creator_id, 782)

    first = client.get(f"/c/{creator_id}")
    assert first.status_code == 200
    assert first.text.count('class="post-row"') == 50
    assert "page 1 of 16" in first.text

    last = client.get(f"/c/{creator_id}?page=16")
    assert last.status_code == 200
    assert last.text.count('class="post-row"') == 32
    assert "page 16 of 16" in last.text

    # Stable order, no duplicates across the whole walk.
    seen = []
    for page in range(1, 17):
        rows, _ = db.list_posts(
            conn, creator_id, limit=50, offset=(page - 1) * 50
        )
        seen.extend(row["service_id"] for row in rows)
    assert len(seen) == 782
    assert len(set(seen)) == 782


def test_creator_filters_match_direct_sql(client, conn):
    creator_id = make_creator(conn, post_count=120)
    seed_many(conn, creator_id, 120)

    expected = conn.execute(
        """
        SELECT count(*) AS n FROM posts p
        WHERE p.creator_id = ?
          AND p.missing_upstream = 0
          AND (p.title LIKE '%pin%' OR p.caption_text LIKE '%pin%')
          AND EXISTS (SELECT 1 FROM links l
                      WHERE l.post_id = p.id AND l.category = 'mega')
        """,
        (creator_id,),
    ).fetchone()["n"]

    response = client.get(
        f"/c/{creator_id}?category=mega&dead=0&q=pin"
    )
    assert response.status_code == 200
    assert response.text.count('class="post-row"') == expected
    assert expected > 0


def test_creator_rows_partial_and_shortener_category(client, conn):
    creator_id = make_creator(conn, post_count=20)
    seed_many(conn, creator_id, 20)

    response = client.get(f"/c/{creator_id}/rows?category=shortener")
    assert response.status_code == 200
    assert "<html" not in response.text.lower()
    assert "post-row" in response.text
    assert response.text.count('class="post-row"') == 4

    facets = client.get(f"/c/{creator_id}")
    assert "Categories" in facets.text
    for label in (
        "Drive",
        "MEGA",
        "Direct media",
        "Video host",
        "Cloud",
        "Social",
        "Shortener",
        "Site asset",
        "Image",
        "Other",
    ):
        assert label in facets.text


def test_creator_cards_and_summary(client, conn):
    creator_id = make_creator(conn)
    seed_fixture_post(conn, creator_id, "post_172189145.json")
    seed_fixture_post(conn, creator_id, "post_171761266.json")

    home = client.get("/")
    assert "Nyl" in home.text
    assert "creator-card" in home.text
    assert "Drive" in home.text
    assert "img.cum.st" in home.text

    page = client.get(f"/c/{creator_id}")
    assert "Posts" in page.text
    assert "MEGA" in page.text
    assert "Last sync" in page.text
    assert "Sync" in page.text


# --------------------------------------------------------------------------- #
# post page
# --------------------------------------------------------------------------- #


def test_post_page_links_attachment_thumbnail_and_copy_all(client, conn):
    creator_id = make_creator(conn)
    post_id = seed_fixture_post(conn, creator_id, "post_172189145.json")
    [link_count] = conn.execute(
        "SELECT count(*) AS n FROM links WHERE post_id = ?", (post_id,)
    ).fetchone()

    response = client.get(f"/c/{creator_id}/p/172189145")
    body = response.text

    assert response.status_code == 200
    assert "Drive" in body and "MEGA" in body
    assert "Link" in body and "Backup Link" in body
    assert "109987" in body
    assert "image/jpeg" in body
    assert "img.cum.st/thumbnail/" in body
    assert "data-copy-all" in body
    assert body.count("data-copy-value=") == link_count
    assert 'target="_blank"' in body
    assert 'rel="noopener noreferrer nofollow"' in body


def test_post_without_attachments(client, conn):
    creator_id = make_creator(conn)
    seed_fixture_post(conn, creator_id, "post_171761266.json")

    response = client.get(f"/c/{creator_id}/p/171761266")

    assert response.status_code == 200
    assert "no attachments" in response.text
    assert "No caption." in response.text


def test_video_has_no_src_until_load(client, conn):
    creator_id = make_creator(conn, post_count=1)
    sha = "0123abcd" + "0" * 56
    post_id = db.upsert_post(
        conn,
        creator_id=creator_id,
        service="patreon",
        service_id="video-1",
        title="Video post",
        caption_html="<p>clip</p>",
        published=2000,
        fetched_at=db.now_iso(),
    )
    db.replace_files(
        conn,
        post_id,
        [
            {
                "sha256": sha,
                "kind": "video",
                "mime_type": "video/mp4",
                "bytes": 12345,
                "width": 1920,
                "height": 1080,
                "duration_ms": 65000,
                "original_filename": "clip.mp4",
                "url": f"https://e1.cum.st/media/{sha}/original.mp4",
                "thumb_url": f"https://img.cum.st/thumbnail/{sha}/preview.webp",
                "variants": [
                    {"name": "original.mp4", "url": f"https://e1.cum.st/media/{sha}/original.mp4"},
                    {"name": "720p.mp4", "url": f"https://e1.cum.st/media/{sha}/720p.mp4"},
                    {"name": "240p.mp4", "url": f"https://e1.cum.st/media/{sha}/240p.mp4"},
                ],
            }
        ],
    )

    body = client.get(f"/c/{creator_id}/p/video-1").text
    video = re.search(r"<video\b[^>]*>", body)
    assert video is not None
    assert "src=" not in video.group(0)
    assert 'preload="none"' in video.group(0)
    assert "720p.mp4" in body
    assert "1:05" in body


def test_caption_is_sanitized_on_render(client, conn):
    creator_id = make_creator(conn, post_count=1)
    db.upsert_post(
        conn,
        creator_id=creator_id,
        service="patreon",
        service_id="dirty",
        title="Dirty caption",
        caption_html=(
            '<p onclick="evil()">hi<script>alert(1)</script>'
            '<a href="https://mega.nz/file/x">MEGA</a></p>'
        ),
        published=3000,
        fetched_at=db.now_iso(),
    )
    conn.commit()

    body = client.get(f"/c/{creator_id}/p/dirty").text
    caption = body.split('<div class="caption">', 1)[1].split("</div>", 1)[0]

    assert "<script" not in caption
    assert "onclick" not in caption
    assert 'target="_blank"' in caption
    assert "noopener noreferrer nofollow" in caption


def test_prev_next_boundaries_do_not_404(client, conn):
    creator_id = make_creator(conn, post_count=2)
    seed_fixture_post(conn, creator_id, "post_172189145.json")
    seed_fixture_post(conn, creator_id, "post_171761266.json")

    newer = client.get(f"/c/{creator_id}/p/172189145")
    assert newer.status_code == 200
    assert 'aria-disabled="true">Next' in newer.text

    older = client.get(f"/c/{creator_id}/p/171761266")
    assert older.status_code == 200
    assert 'aria-disabled="true">Previous' in older.text


def test_delete_creator_cascades(client, conn):
    creator_id = make_creator(conn)
    seed_fixture_post(conn, creator_id, "post_172189145.json")
    seed_fixture_post(conn, creator_id, "post_171761266.json")

    response = client.post(f"/c/{creator_id}/delete", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    for table in ("creators", "posts", "links", "files", "tags", "runs"):
        assert conn.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"] == 0
