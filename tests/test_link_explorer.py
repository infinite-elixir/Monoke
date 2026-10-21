from __future__ import annotations

import csv
import io
import json
import re
import time
from datetime import UTC, datetime

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
    with conn:
        return db.upsert_creator(conn, **values)


def add_link(
    conn,
    creator_id: int,
    *,
    service_id: str,
    title: str,
    published: int,
    url: str,
    category: str,
    label: str | None = None,
    resolved_url: str | None = None,
) -> int:
    post_id = db.upsert_post(
        conn,
        creator_id=creator_id,
        service="patreon",
        service_id=service_id,
        title=title,
        published=published,
        added=published,
        fetched_at=db.now_iso(),
    )
    db.replace_links(
        conn,
        post_id,
        [
            {
                "url": url,
                "normalized_url": url,
                "label": label,
                "category": category,
                "origin": "caption",
                "resolved_url": resolved_url,
            }
        ],
    )
    return post_id


def test_link_explorer_shows_every_link_including_other(client, conn):
    creator_id = make_creator(conn)
    add_link(
        conn,
        creator_id,
        service_id="p1",
        title="Drive post",
        published=100,
        url="https://drive.google.com/file/d/one",
        category="google_drive",
        label="Drive",
    )
    add_link(
        conn,
        creator_id,
        service_id="p2",
        title="Mega post",
        published=200,
        url="https://mega.nz/file/two",
        category="mega",
        label="MEGA",
    )
    add_link(
        conn,
        creator_id,
        service_id="p3",
        title="Odd post",
        published=300,
        url="https://odd.example/thing",
        category="other",
        label="Odd",
    )

    response = client.get("/links")

    assert response.status_code == 200
    assert response.text.count('class="link-row"') == 3
    for url in (
        "drive.google.com/file/d/one",
        "mega.nz/file/two",
        "odd.example/thing",
    ):
        assert url in response.text
    # Grouped by category is the default and includes a count per group.
    assert response.text.count("link-group-header") == 3
    assert "Other" in response.text
    assert f'data-row-href="/c/{creator_id}/p/p1"' in response.text

    flat = client.get("/links?group=0")
    assert flat.status_code == 200
    assert "link-group-header" not in flat.text
    assert flat.text.count('class="link-row"') == 3


def test_unknown_category_is_a_row_not_a_drop(client, conn):
    creator_id = make_creator(conn)
    add_link(
        conn,
        creator_id,
        service_id="weird",
        title="Weird post",
        published=100,
        url="https://weird.example/x",
        category="weird_custom",
        label="Weird",
    )

    response = client.get("/links")

    assert response.status_code == 200
    assert "weird_custom" in response.text
    assert "weird.example/x" in response.text
    assert response.text.count('class="link-row"') == 1


def test_category_and_search_filters_match_csv_export(client, conn):
    creator_id = make_creator(conn)
    add_link(
        conn,
        creator_id,
        service_id="p1",
        title="Mega archive",
        published=100,
        url="https://mega.nz/file/aaa",
        category="mega",
        label="archive",
    )
    add_link(
        conn,
        creator_id,
        service_id="p2",
        title="Mega mp4 backup",
        published=200,
        url="https://mega.nz/file/bbb",
        category="mega",
        label="mp4 backup",
    )
    add_link(
        conn,
        creator_id,
        service_id="p3",
        title="Direct clip",
        published=300,
        url="https://cdn.example/clip.mp4",
        category="direct_media",
        label="clip",
    )
    add_link(
        conn,
        creator_id,
        service_id="p4",
        title="Other mp4",
        published=400,
        url="https://other.example/mp4",
        category="other",
        label="mp4 elsewhere",
    )

    query = "category=mega,direct_media&q=mp4"
    page = client.get(f"/c/{creator_id}/links?{query}")
    assert page.status_code == 200
    assert page.text.count('class="link-row"') == 2
    assert "mega.nz/file/bbb" in page.text
    assert "cdn.example/clip.mp4" in page.text
    assert "other.example/mp4" not in page.text
    assert "mega.nz/file/aaa" not in page.text

    response = client.get(f"/c/{creator_id}/links.csv?{query}")
    assert response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(response.text)))
    assert len(rows) == 2
    assert {row["url"] for row in rows} == {
        "https://mega.nz/file/bbb",
        "https://cdn.example/clip.mp4",
    }
    assert list(rows[0].keys()) == list(
        (
            "creator",
            "service",
            "post_id",
            "post_title",
            "published",
            "category",
            "label",
            "url",
            "resolved_url",
            "origin",
        )
    )


def test_checkbox_category_params_are_accepted(client, conn):
    creator_id = make_creator(conn)
    add_link(
        conn,
        creator_id,
        service_id="p1",
        title="One",
        published=100,
        url="https://mega.nz/file/one",
        category="mega",
    )
    add_link(
        conn,
        creator_id,
        service_id="p2",
        title="Two",
        published=200,
        url="https://cdn.example/two",
        category="direct_media",
    )

    response = client.get(
        "/links", params=[("category", "mega"), ("category", "direct_media")]
    )

    assert response.status_code == 200
    assert response.text.count('class="link-row"') == 2


def test_archive_wide_creator_filter(client, conn):
    first = make_creator(conn, service_id="111", display_name="First")
    second = make_creator(conn, service_id="222", display_name="Second")
    add_link(
        conn,
        first,
        service_id="a",
        title="First post",
        published=100,
        url="https://first.example/one",
        category="mega",
    )
    add_link(
        conn,
        second,
        service_id="b",
        title="Second post",
        published=200,
        url="https://second.example/two",
        category="mega",
    )

    archive = client.get("/links")
    assert "All creators" in archive.text
    assert archive.text.count('class="link-row"') == 2

    scoped = client.get(f"/links?creator={first}")
    assert scoped.text.count('class="link-row"') == 1
    assert "first.example/one" in scoped.text
    assert "second.example/two" not in scoped.text


def test_copy_button_prefers_resolved_url_and_says_which(client, conn):
    creator_id = make_creator(conn)
    add_link(
        conn,
        creator_id,
        service_id="p1",
        title="Resolved",
        published=100,
        url="https://shorte.st/abc",
        category="shortener",
        resolved_url="https://mega.nz/file/final",
    )
    add_link(
        conn,
        creator_id,
        service_id="p2",
        title="Raw",
        published=200,
        url="https://shorte.st/xyz",
        category="shortener",
    )

    response = client.get("/links")

    assert 'data-copy="https://mega.nz/file/final"' in response.text
    assert 'title="Copy resolved URL"' in response.text
    assert 'data-copy="https://shorte.st/xyz"' in response.text
    assert 'title="Copy original URL"' in response.text
    assert "Unresolved shorteners" in response.text


def test_has_resolved_filter_and_header_stats(client, conn):
    creator_id = make_creator(conn)
    add_link(
        conn,
        creator_id,
        service_id="p1",
        title="Resolved",
        published=100,
        url="https://shorte.st/abc",
        category="shortener",
        resolved_url="https://mega.nz/file/final",
    )
    add_link(
        conn,
        creator_id,
        service_id="p2",
        title="Unresolved",
        published=200,
        url="https://shorte.st/xyz",
        category="shortener",
    )

    page = client.get("/links")
    assert "2" in page.text
    assert "Unresolved shorteners" in page.text

    resolved = client.get("/links?has_resolved=1")
    assert resolved.text.count('class="link-row"') == 1
    assert "mega.nz/file/final" in resolved.text
    assert "shorte.st/xyz" not in resolved.text

    unresolved = client.get("/links?has_resolved=0")
    assert unresolved.text.count('class="link-row"') == 1
    assert "shorte.st/xyz" in unresolved.text


def test_exports_use_ticket_filenames_and_apply_filters(client, conn):
    creator_id = make_creator(conn)
    add_link(
        conn,
        creator_id,
        service_id="p1",
        title="Mega post",
        published=100,
        url="https://mega.nz/file/one",
        category="mega",
        label="one",
    )
    add_link(
        conn,
        creator_id,
        service_id="p2",
        title="Drive post",
        published=200,
        url="https://drive.google.com/file/two",
        category="google_drive",
        label="two",
    )

    csv_response = client.get(f"/c/{creator_id}/links.csv?category=mega")
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    assert csv_response.status_code == 200
    assert csv_response.headers["content-type"].startswith("text/csv")
    assert csv_response.headers["content-disposition"] == (
        f'attachment; filename="monoke-patreon-274608-links-{stamp}.csv"'
    )
    csv_rows = list(csv.DictReader(io.StringIO(csv_response.text)))
    assert [row["url"] for row in csv_rows] == ["https://mega.nz/file/one"]

    json_response = client.get(f"/c/{creator_id}/links.json?category=mega")
    assert json_response.status_code == 200
    assert json_response.headers["content-type"].startswith("application/json")
    assert json_response.headers["content-disposition"].endswith(
        f'monoke-patreon-274608-links-{stamp}.json"'
    )
    payload = json.loads(json_response.text)
    assert len(payload) == 1
    assert payload[0]["url"] == "https://mega.nz/file/one"
    assert payload[0]["creator"] == "Nyl"

    archive = client.get("/links.csv")
    assert archive.headers["content-disposition"].startswith(
        'attachment; filename="monoke-archive-links-'
    )
    assert len(list(csv.DictReader(io.StringIO(archive.text)))) == 2


def test_empty_states_link_to_sync(client, conn):
    creator_id = make_creator(conn)
    make_creator(conn, service_id="other", name="Other")

    archive = client.get("/links")
    assert "No links yet" in archive.text
    assert "Back to creators" in archive.text

    scoped = client.get(f"/c/{creator_id}/links")
    assert "No links yet" in scoped.text
    assert f'action="/c/{creator_id}/sync"' in scoped.text

    filtered = client.get("/links?q=nothing-matches")
    assert "No links match these filters" in filtered.text


def test_ten_thousand_links_paginate_quickly(client, conn):
    creator_id = make_creator(conn)
    post_ids = [
        db.upsert_post(
            conn,
            creator_id=creator_id,
            service="patreon",
            service_id=f"perf-{index}",
            title=f"Perf {index}",
            published=1000 + index,
            fetched_at=db.now_iso(),
        )
        for index in range(100)
    ]
    for index, post_id in enumerate(post_ids):
        db.replace_links(
            conn,
            post_id,
            [
                {
                    "url": f"https://host{link}.example/file/{index}-{link}",
                    "normalized_url": f"https://host{link}.example/file/{index}-{link}",
                    "label": f"link {index}-{link}",
                    "category": ["mega", "direct_media", "google_drive", "other"][
                        link % 4
                    ],
                    "origin": "caption",
                }
                for link in range(100)
            ],
        )

    client.get("/links")  # warm the template cache and SQLite page cache
    started = time.perf_counter()
    page = client.get("/links?page=50")
    elapsed = time.perf_counter() - started

    assert page.status_code == 200
    assert page.text.count('class="link-row"') == 100
    assert "10,000" not in page.text  # no thousands separators in the pager
    assert "10000 links · page 50 of 100" in page.text
    assert elapsed < 0.3, f"10k link page took {elapsed:.3f}s"
