"""T11.3 - media bookmarks open the merged lightbox-viewer at their sha."""

from __future__ import annotations

import re

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


def make_creator(conn, service_id: str = "274608", name: str = "Nyl") -> int:
    creator_id = db.upsert_creator(
        conn,
        service="patreon",
        service_id=service_id,
        name=name,
        display_name=name,
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


def add_file(
    conn,
    post_id: int,
    sha: str,
    kind: str = "image",
    *,
    position: int = 0,
    url: str | None = None,
) -> None:
    extension = {"video": "mp4", "gif": "gif", "image": "jpg"}.get(kind, "bin")
    rows = db.list_files(conn, post_id)
    rows.append(
        {
            "sha256": sha,
            "kind": kind,
            "raw_kind": kind,
            "mime_type": {"video": "video/mp4", "gif": "image/gif"}.get(
                kind, "image/jpeg"
            ),
            "bytes": 1024,
            "width": 1600,
            "height": 1200,
            "duration_ms": 5000 if kind == "video" else None,
            "original_filename": f"{sha[:8]}.{extension}",
            "url": url
            if url is not None
            else f"https://e1.cum.st/media/{sha}/original.{extension}",
            "thumb_url": f"https://img.cum.st/thumbnail/{sha}/preview.webp",
            "position": position,
        }
    )
    db.replace_files(conn, post_id, rows)
    conn.commit()


def sha(number: int) -> str:
    return f"{number:064x}"


def tile_tag(body: str, bookmark_id: int) -> str:
    match = re.search(
        rf'<a\b[^>]*id="bookmark-media-{bookmark_id}"[^>]*>', body
    )
    if match:
        return match.group(0)
    match = re.search(
        rf'<span\b[^>]*id="bookmark-media-{bookmark_id}"[^>]*>', body
    )
    assert match is not None, f"no media tile for bookmark {bookmark_id}"
    return match.group(0)


def tile_figure(body: str, bookmark_id: int) -> str:
    start = body.index(f'id="bookmark-media-{bookmark_id}"')
    figure_start = body.rfind("<figure", 0, start)
    figure_end = body.index("</figure>", start)
    return body[figure_start:figure_end]


def bookmark_id_for(conn, creator_id: int, media_sha: str) -> int:
    row = conn.execute(
        "SELECT id FROM media_bookmarks WHERE creator_id = ? AND sha256 = ?",
        (creator_id, media_sha),
    ).fetchone()
    assert row is not None
    return int(row["id"])


def test_media_tile_renders_overlay_hooks_and_keeps_the_post_href(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 1, title="Saved post")
    add_file(conn, post_id, sha(1), "image", position=0)
    add_file(conn, post_id, sha(2), kind="gif", position=1)
    add_file(conn, post_id, sha(3), kind="video", position=2)
    db.toggle_media_bookmark(conn, creator_id, sha(3))
    bookmark_id = bookmark_id_for(conn, creator_id, sha(3))

    body = client.get("/bookmarks?view=media&creator=%s" % creator_id).text
    tag = tile_tag(body, bookmark_id)

    assert 'href="/c/%s/p/900001"' % creator_id in tag
    assert "data-lightbox-open" in tag
    assert 'data-creator-id="%s"' % creator_id in tag
    assert 'data-post-service-id="900001"' in tag
    assert 'data-sha256="%s"' % sha(3) in tag
    assert 'data-kind="video"' in tag
    assert 'data-full="https://e1.cum.st/media/%s/original.mp4"' % sha(3) in tag
    assert 'data-title="Saved post"' in tag
    assert 'data-post-url="/c/%s/p/900001"' % creator_id in tag
    assert "data-viewer-open" not in tag
    assert "data-viewer-focus" not in tag
    assert "data-viewer-index" not in tag


def test_overlay_and_lightbox_script_are_shared_with_every_bookmarks_view(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 1, title="Saved post")
    add_file(conn, post_id, sha(1))
    db.toggle_media_bookmark(conn, creator_id, sha(1))

    urls = [
        "/bookmarks",
        f"/bookmarks?creator={creator_id}",
        f"/bookmarks?view=media&creator={creator_id}",
        f"/bookmarks?view=media&creator={creator_id}&page=1",
    ]
    for url in urls:
        response = client.get(url)
        assert response.status_code == 200, url
        body = response.text
        assert body.count('id="gallery-lightbox"') == 1, url
        assert body.count("data-lightbox-root") == 1, url
        assert f'src="{asset("lightbox.js")}"' in body, url
        assert "/static/viewer.js" not in body, url
        assert "/bookmarks/viewer" not in body, url
        assert f"/c/{creator_id}/viewer" not in body, url


def test_overlay_is_present_on_a_paginated_media_view(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 1, title="Many")
    for number in range(61):
        add_file(conn, post_id, sha(number), position=number)
        db.toggle_media_bookmark(conn, creator_id, sha(number))

    body = client.get(f"/bookmarks?view=media&creator={creator_id}&page=2").text

    assert "page 2 of" in body
    assert body.count('id="gallery-lightbox"') == 1


def test_opening_a_bookmark_targets_its_sha_in_the_bookmarks_playlist(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 1, title="Saved post")
    add_file(conn, post_id, sha(11), position=0)
    add_file(conn, post_id, sha(12), position=1)
    add_file(conn, post_id, sha(13), position=2)
    db.toggle_media_bookmark(conn, creator_id, sha(13))
    bookmark_id = bookmark_id_for(conn, creator_id, sha(13))

    payload = client.get(f"/c/{creator_id}/viewer/data?post=900001").json()
    assert [item["sha256"] for item in payload["media"]] == [sha(11), sha(12), sha(13)]

    tag = tile_tag(
        client.get(f"/bookmarks?view=media&creator={creator_id}").text,
        bookmark_id,
    )
    assert f'data-sha256="{sha(13)}"' in tag
    assert 'data-viewer-context="bookmarks"' in tag

    # T11.10: bookmark entry points load the filtered library, not the post's
    # first item, and preserve the clicked sha as the starting index.
    playlist = client.get(
        "/bookmarks/viewer/data",
        params={"view": "media", "creator": creator_id, "sha": sha(13)},
    ).json()
    assert playlist["context"] == "bookmarks"
    assert playlist["view"] == "media"
    assert playlist["creator_id"] == creator_id
    assert [item["sha256"] for item in playlist["media"]] == [sha(13)]
    assert playlist["index"] == 0
    assert playlist["total"] == 1

    source = (STATIC_DIR / "lightbox.js").read_text(encoding="utf-8")
    hydrate = source.split("function hydrate(", 1)[1].split("function jump(", 1)[0]
    assert 'tile.getAttribute("data-viewer-context") === "bookmarks"' in hydrate
    assert "fetchJson(bookmarksDataUrl(tile), signal)" in hydrate
    assert "fetchPayload(serviceId, signal)" in hydrate
    assert "? -1" in hydrate
    assert "resolveMediaIndex(next.media, sha, fallback)" in source


def test_bookmarks_viewer_data_is_the_whole_library_across_pages(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 1, title="Many")
    shas = [sha(number) for number in range(61)]
    for number, media_sha in enumerate(shas):
        add_file(conn, post_id, media_sha, position=number)
        db.toggle_media_bookmark(conn, creator_id, media_sha)

    payload = client.get(
        "/bookmarks/viewer/data",
        params={"view": "media", "creator": creator_id},
    ).json()

    assert payload["context"] == "bookmarks"
    assert payload["view"] == "media"
    assert payload["creator_id"] == creator_id
    assert payload["total"] == 61
    assert [item["index"] for item in payload["media"]] == list(range(61))
    assert {item["sha256"] for item in payload["media"]} == set(shas)
    assert all(item["bookmarked"] for item in payload["media"])
    assert all(item["creator_id"] == creator_id for item in payload["media"])

    # The page-2 tiles point at the same playlist: page boundaries no longer
    # cut navigation short.
    body = client.get(
        f"/bookmarks?view=media&creator={creator_id}&page=2"
    ).text
    assert "page 2 of 2" in body
    assert body.count('data-viewer-context="bookmarks"') >= 1


def test_bookmarks_viewer_data_scopes_creator_and_reports_errors(client, conn):
    first = make_creator(conn, service_id="274608", name="First")
    second = make_creator(conn, service_id="274609", name="Second")
    first_post = add_post(conn, first, 1)
    second_post = add_post(conn, second, 1, service_id="900001")
    add_file(conn, first_post, sha(1))
    add_file(conn, second_post, sha(2))
    db.toggle_media_bookmark(conn, first, sha(1))
    db.toggle_media_bookmark(conn, second, sha(2))

    scoped = client.get(
        "/bookmarks/viewer/data",
        params={"view": "media", "creator": first},
    ).json()
    assert [item["sha256"] for item in scoped["media"]] == [sha(1)]
    assert scoped["index"] == -1

    both = client.get("/bookmarks/viewer/data", params={"view": "media"}).json()
    assert {item["sha256"] for item in both["media"]} == {sha(1), sha(2)}

    unknown = client.get(
        "/bookmarks/viewer/data", params={"view": "media", "creator": 999}
    )
    assert unknown.status_code == 404
    assert unknown.json() == {"error": "unknown creator"}

    conn.execute("DELETE FROM media_bookmarks WHERE creator_id = ?", (second,))
    conn.commit()
    empty = client.get(
        "/bookmarks/viewer/data", params={"view": "media", "creator": second}
    )
    assert empty.status_code == 404
    assert empty.json() == {"error": "no media bookmarks"}


def test_bookmarks_playlist_disables_vertical_navigation_and_caches_nothing():
    source = (STATIC_DIR / "lightbox.js").read_text(encoding="utf-8")

    apply_payload = source.split("function applyPayload(", 1)[1].split(
        "function viewerDataUrl(", 1
    )[0]
    assert "if (!bookmarkContext()) {" in apply_payload
    assert "rememberPayload(next);" in apply_payload

    go = source.split("function go(", 1)[1].split("function openOverlay(", 1)[0]
    assert "if (bookmarkContext()) {" in go
    assert 'if (action === "prev-post")' in go
    assert 'if (action === "next-post")' in go

    counter = source.split("function payloadCounter(", 1)[1].split(
        "function renderPayload(", 1
    )[0]
    assert 'currentIndex + 1 + " of " + payload.media.length + " bookmarks"' in counter

    touch = source.split("function onTouchEnd(", 1)[1].split(
        "function afterTouch(", 1
    )[0]
    assert "go(dy < 0 ? \"prev-post\" : \"next-post\")" in touch
    assert "if (bookmarkContext()) {" in go


def test_prev_next_walks_the_whole_post_and_up_down_crosses_posts(client, conn):
    creator_id = make_creator(conn)
    newer = add_post(conn, creator_id, 0, title="Newer")
    add_file(conn, newer, sha(1), position=0)
    older = add_post(conn, creator_id, 1, title="Older")
    add_file(conn, older, sha(2), position=0)
    add_file(conn, older, sha(3), position=1)
    db.toggle_media_bookmark(conn, creator_id, sha(3))

    payload = client.get(f"/c/{creator_id}/viewer/data?post=900001").json()

    # The bookmarked item shares its post playlist with unbookmarked media.
    assert [item["sha256"] for item in payload["media"]] == [sha(2), sha(3)]
    assert [item["bookmarked"] for item in payload["media"]] == [False, True]
    # Up/down still crosses posts through the retained JSON endpoint.
    assert payload["previous_post"] is None
    assert payload["next_post"] == "900000"


def test_missing_attachment_opens_unavailable_and_recovers_on_next(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 1, title="Broken media")
    add_file(conn, post_id, sha(1), position=0)
    add_file(conn, post_id, sha(2), position=1)
    db.toggle_media_bookmark(conn, creator_id, sha(1))
    bookmark_id = bookmark_id_for(conn, creator_id, sha(1))
    conn.execute(
        "UPDATE files SET url = '' WHERE post_id = ? AND sha256 = ?",
        (post_id, sha(1)),
    )
    conn.commit()

    body = client.get(f"/bookmarks?view=media&creator={creator_id}").text
    figure = tile_figure(body, bookmark_id)
    tag = tile_tag(body, bookmark_id)

    assert "is-missing" in figure
    assert "No longer attached" in figure
    assert "data-lightbox-open" in tag
    assert "data-missing" in tag
    assert 'data-sha256="%s"' % sha(1) in tag
    assert 'data-post-service-id="900001"' in tag
    assert 'href="/c/%s/p/900001"' % creator_id in tag

    payload = client.get(f"/c/{creator_id}/viewer/data?post=900001").json()
    assert payload["media"][0]["sha256"] == sha(1)
    assert not payload["media"][0]["url"]
    # The next item is still reachable from the unavailable state.
    assert payload["media"][1]["sha256"] == sha(2)
    assert payload["media"][1]["url"]

    source = (STATIC_DIR / "lightbox.js").read_text(encoding="utf-8")
    tile_source = source.split("function mediaItemFromTile(", 1)[1].split(
        "function canStep(", 1
    )[0]
    assert 'tile.hasAttribute("data-missing")' in tile_source
    assert '? ""' in tile_source


def test_detached_media_tile_stays_non_interactive(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 1)
    add_file(conn, post_id, sha(1))
    db.toggle_media_bookmark(conn, creator_id, sha(1))
    bookmark_id = bookmark_id_for(conn, creator_id, sha(1))
    db.replace_files(conn, post_id, [])

    body = client.get(f"/bookmarks?view=media&creator={creator_id}").text
    figure = tile_figure(body, bookmark_id)

    assert "No longer attached" in figure
    assert "data-lightbox-open" not in figure
    assert "<span" in figure


def test_post_bookmark_cards_keep_opening_the_post_page(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 1, title="Saved post")
    add_file(conn, post_id, sha(1))
    db.toggle_post_bookmark(conn, creator_id, "900001")
    db.toggle_media_bookmark(conn, creator_id, sha(1))

    body = client.get("/bookmarks").text
    match = re.search(r'<article class="bookmark-card".*?</article>', body, re.S)
    assert match is not None
    card = match.group(0)

    assert f'href="/c/{creator_id}/p/900001"' in card
    assert "data-lightbox-open" not in card


def test_media_tile_href_still_resolves_without_javascript(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 1)
    add_file(conn, post_id, sha(1))
    db.toggle_media_bookmark(conn, creator_id, sha(1))
    bookmark_id = bookmark_id_for(conn, creator_id, sha(1))

    response = client.get(f"/bookmarks?view=media&creator={creator_id}")
    tag = tile_tag(response.text, bookmark_id)

    assert 'href="/c/%s/p/900001"' % creator_id in tag
    assert 'href="https://e1.cum.st/media/%s/original.jpg"' % sha(1) not in tag


def test_unbookmarking_from_the_overlay_updates_the_library_on_close(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 1, title="Saved post")
    add_file(conn, post_id, sha(1))
    db.toggle_post_bookmark(conn, creator_id, "900001")
    db.toggle_media_bookmark(conn, creator_id, sha(1))

    body = client.get("/bookmarks").text
    assert 'data-bookmark-post-card' in body
    assert 'data-post-service-id="900001"' in body
    assert "data-bookmark-media" in body
    assert f'data-sha256="{sha(1)}"' in body

    source = (STATIC_DIR / "lightbox.js").read_text(encoding="utf-8")
    toggle = source.split("function toggleBookmark(", 1)[1].split(
        "function onKeydown(", 1
    )[0]
    assert "libraryMediaRemoved.add" in toggle
    assert "removeBookmarkFromPlaylist(item.sha256)" in toggle
    # T11.10: removing the current bookmark never closes the overlay; the
    # medium stays visible until the next navigation.
    assert "closeOverlay()" not in toggle

    splice = source.split("function removeBookmarkFromPlaylist(", 1)[1].split(
        "function toggleBookmark(", 1
    )[0]
    assert "payload.media.splice(index, 1)" in splice
    assert "payload.total = payload.media.length" in splice

    sync = source.split("function syncBookmarkLibrary(", 1)[1].split(
        "function openOverlay(", 1
    )[0]
    assert 'document.querySelectorAll("[data-bookmark-post-card]")' in sync
    assert 'document.querySelectorAll("[data-bookmark-media]")' in sync
    assert ".remove()" in sync


def test_no_bookmark_only_viewer_route_or_controller(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 1), sha(1))
    db.toggle_media_bookmark(conn, creator_id, sha(1))

    assert client.get("/bookmarks/viewer").status_code == 404
    body = client.get("/bookmarks").text
    assert "/bookmarks/viewer" not in body

    source = (STATIC_DIR / "lightbox.js").read_text(encoding="utf-8")
    assert "bookmark_viewer" not in source
    assert "bookmark-only" not in source.lower()
