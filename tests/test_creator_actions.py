"""T11.8 - every creator surface offers exactly one Gallery action."""

from __future__ import annotations

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
    return TestClient(create_app())


@pytest.fixture()
def conn():
    connection = db.ensure_db(get_settings())
    yield connection
    connection.close()


def make_creator(conn) -> int:
    creator_id = db.upsert_creator(
        conn,
        service="patreon",
        service_id="274608",
        name="Nyl",
        display_name="Nyl",
    )
    conn.commit()
    return creator_id


def add_post(conn, creator_id: int) -> int:
    post_id = db.upsert_post(
        conn,
        creator_id=creator_id,
        service="patreon",
        service_id="900001",
        title="One post",
        published=1000,
        fetched_at=db.now_iso(),
    )
    conn.commit()
    return post_id


def gallery_actions(body: str, creator_id: int) -> list[re.Match[str]]:
    return list(
        re.finditer(
            rf'<a\b[^>]*href="/c/{creator_id}/gallery"[^>]*>\s*Gallery\s*</a>',
            body,
        )
    )


def test_creator_header_has_exactly_one_gallery_button(client, conn):
    creator_id = make_creator(conn)
    body = client.get(f"/c/{creator_id}").text

    actions = gallery_actions(body, creator_id)
    assert len(actions) == 1
    assert 'class="button"' in actions[0].group(0)
    assert actions[0].group(0).startswith("<a ")


def test_creator_actions_keep_gallery_sync_delete_order(client, conn):
    creator_id = make_creator(conn)
    body = client.get(f"/c/{creator_id}").text

    header = body[body.index('<div class="creator-actions">') :]
    header = header[: header.index("</div>")]
    gallery = header.index("Gallery")
    sync = header.index("Sync")
    delete = header.index("Delete")

    assert gallery < sync < delete


def test_no_other_rendered_page_duplicates_the_gallery_action(client, conn):
    creator_id = make_creator(conn)
    add_post(conn, creator_id)

    pages = {
        "dashboard": "/",
        "creator": f"/c/{creator_id}",
        "gallery": f"/c/{creator_id}/gallery",
        "post": f"/c/{creator_id}/p/900001",
        "bookmarks": "/bookmarks",
        "links": "/links",
    }
    for name, url in pages.items():
        response = client.get(url)
        assert response.status_code == 200, name
        assert len(gallery_actions(response.text, creator_id)) <= 1, name

    # The remaining button still points at the gallery route.
    assert len(gallery_actions(client.get(f"/c/{creator_id}").text, creator_id)) == 1
