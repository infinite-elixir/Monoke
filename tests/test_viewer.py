"""T11.1 - the merged gallery lightbox and fullscreen viewer."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

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


def make_creator(conn, **overrides) -> int:
    values = {
        "service": "onlyfans",
        "service_id": "39058172",
        "name": "Belledelphine",
        "display_name": "Belledelphine",
    }
    values.update(overrides)
    creator_id = db.upsert_creator(conn, **values)
    conn.commit()
    return creator_id


def add_post(conn, creator_id: int, number: int, **overrides) -> int:
    values = {
        "creator_id": creator_id,
        "service": "onlyfans",
        "service_id": str(2183930591 + number),
        "title": f"Post {number}",
        "published": 1784196537 + number,
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
    thumb_url: str | None = None,
    duration_ms: int | None = None,
    width: int = 1080,
    height: int = 1920,
    bytes_: int | None = None,
) -> None:
    extension = {"video": "mp4", "gif": "gif", "image": "jpg", "audio": "m4a"}.get(
        kind, "bin"
    )
    existing = db.list_files(conn, post_id)
    existing.append(
        {
            "sha256": sha,
            "kind": kind,
            "raw_kind": kind,
            "mime_type": {
                "video": "video/mp4",
                "gif": "image/gif",
                "image": "image/jpeg",
                "audio": "audio/mp4",
            }.get(kind, "application/octet-stream"),
            "bytes": (
                bytes_
                if bytes_ is not None
                else (8166926 if kind == "video" else 250_000)
            ),
            "width": width,
            "height": height,
            "duration_ms": duration_ms,
            "original_filename": f"{sha[:8]}.{extension}",
            "url": url
            if url is not None
            else f"https://e3.cum.st/media/{sha}/original.{extension}",
            "thumb_url": thumb_url
            if thumb_url is not None
            else f"https://img.cum.st/thumbnail/{sha}/preview.webp",
            "position": position,
            "variants": [
                {
                    "name": f"original.{extension}",
                    "url": f"https://e3.cum.st/media/{sha}/original.{extension}",
                    "bytes": 8166926 if kind == "video" else 250_000,
                }
            ],
        }
    )
    db.replace_files(conn, post_id, existing)
    conn.commit()


def sha(number: int) -> str:
    return f"{number:064x}"


def gallery_body(client, creator_id: int, **params) -> str:
    response = client.get(f"/c/{creator_id}/gallery", params=params)
    assert response.status_code == 200
    return response.text


def overlay_markup(body: str) -> str:
    marker = 'id="gallery-lightbox"'
    assert marker in body
    return body[body.index(marker) :]


def node_script(block: str, body: str) -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    script = textwrap.dedent(block) + textwrap.dedent(body)
    result = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


# --------------------------------------------------------------------------- #
# payload contract (the JSON endpoint survives the page deletion)
# --------------------------------------------------------------------------- #


def test_data_endpoint_matches_contract_and_parses_variants(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 0, title="Payload")
    add_file(conn, post_id, sha(1), kind="video", duration_ms=10_000)

    response = client.get(
        f"/c/{creator_id}/viewer/data?post={db.get_post(conn, post_id)['service_id']}"
    )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) >= {
        "creator",
        "post",
        "media",
        "previous_post",
        "next_post",
    }
    assert payload["post"] == {
        "service_id": str(2183930591),
        "title": "Payload",
        "published": 1784196537,
        "post_url": f"/c/{creator_id}/p/2183930591",
        "media_count": 1,
        "bookmarked": False,
    }
    item = payload["media"][0]
    assert item["bookmarked"] is False
    assert item["index"] == 0
    assert item["kind"] == "video"
    assert item["mime_type"] == "video/mp4"
    assert item["width"] == 1080
    assert item["height"] == 1920
    assert item["duration_ms"] == 10_000
    assert item["bytes"] == 8166926
    assert item["post_service_id"] == "2183930591"
    assert item["post_position"] == 0
    assert item["variants"][0]["name"] == "original.mp4"
    assert payload["index"] == 0


def test_missing_payloads_stay_404_json_and_bare_payload_still_answers(client, conn):
    creator_id = make_creator(conn)
    add_post(conn, creator_id, 0, title="Only words")

    assert client.get(f"/c/{creator_id}/viewer/data?post=does-not-exist").status_code == 404
    assert client.get("/c/999/viewer/data").status_code == 404
    missing = client.get(f"/c/{creator_id}/viewer/data?post=does-not-exist")
    assert "error" in missing.json()

    # A creator with no eligible media still gets a JSON error, never HTML.
    empty = client.get(f"/c/{creator_id}/viewer/data")
    assert empty.status_code == 404
    assert empty.headers["content-type"].startswith("application/json")


def test_neighbour_lookup_skips_text_only_posts_in_both_directions(conn):
    creator_id = make_creator(conn)
    first = add_post(conn, creator_id, 0)
    add_file(conn, first, sha(1))
    add_post(conn, creator_id, 1)  # text only
    second = add_post(conn, creator_id, 2)
    add_file(conn, second, sha(2))
    add_post(conn, creator_id, 3)  # text only
    third = add_post(conn, creator_id, 4)
    add_file(conn, third, sha(3))

    first_row = db.get_post(conn, first)
    second_row = db.get_post(conn, second)
    third_row = db.get_post(conn, third)

    assert db.viewer_neighbour(conn, creator_id, third_row, "newer") is None
    assert db.viewer_neighbour(conn, creator_id, second_row, "newer") == str(2183930595)
    assert db.viewer_neighbour(conn, creator_id, first_row, "newer") == str(2183930593)
    assert db.viewer_neighbour(conn, creator_id, first_row, "older") is None
    assert db.viewer_neighbour(conn, creator_id, second_row, "older") == str(2183930591)
    assert db.viewer_neighbour(conn, creator_id, third_row, "older") == str(2183930593)


def test_neighbour_order_breaks_ties_by_post_id(conn):
    creator_id = make_creator(conn)
    same_time = 1_700_000_000
    older_row = add_post(conn, creator_id, 0, published=same_time)
    add_file(conn, older_row, sha(1))
    newer_row = add_post(conn, creator_id, 1, published=same_time)
    add_file(conn, newer_row, sha(2))

    old = db.get_post(conn, older_row)
    new = db.get_post(conn, newer_row)

    assert db.viewer_neighbour(conn, creator_id, old, "newer") == str(2183930592)
    assert db.viewer_neighbour(conn, creator_id, old, "older") is None
    assert db.viewer_neighbour(conn, creator_id, new, "older") == str(2183930591)
    assert db.viewer_neighbour(conn, creator_id, new, "newer") is None


def test_viewer_does_not_dedupe_sha_across_posts(client, conn):
    creator_id = make_creator(conn)
    first = add_post(conn, creator_id, 0, title="First copy")
    second = add_post(conn, creator_id, 1, title="Second copy")
    add_file(conn, first, sha(42))
    add_file(conn, second, sha(42))

    for post_number, title in ((0, "First copy"), (1, "Second copy")):
        payload = client.get(
            f"/c/{creator_id}/viewer/data?post={2183930591 + post_number}"
        ).json()
        assert payload["post"]["title"] == title
        assert [item["sha256"] for item in payload["media"]] == [sha(42)]


def test_viewer_media_orders_by_position_then_id(conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 0)
    add_file(conn, post_id, sha(3), position=2)
    add_file(conn, post_id, sha(1), position=0)
    add_file(conn, post_id, sha(2), position=1)

    media = db.viewer_media(conn, creator_id, "2183930591")

    assert [item["sha256"] for item in media] == [sha(1), sha(2), sha(3)]
    assert [item["index"] for item in media] == [0, 1, 2]


def test_viewer_state_exposes_neighbour_chain_and_destination_edges(client, conn):
    creator_id = make_creator(conn)
    service_ids = []
    for number in range(3):
        post_id = add_post(conn, creator_id, number, title=f"Post {number}")
        for position in range(3):
            add_file(conn, post_id, sha(number * 10 + position), position=position)
        service_ids.append(str(2183930591 + number))

    oldest, middle, newest = service_ids

    middle_state = db.viewer_state(conn, creator_id, middle)
    assert middle_state["previous_post"] == newest  # newer
    assert middle_state["next_post"] == oldest  # older

    newest_state = db.viewer_state(conn, creator_id, newest)
    assert newest_state["previous_post"] is None
    assert newest_state["next_post"] == middle

    oldest_state = db.viewer_state(conn, creator_id, oldest)
    assert oldest_state["previous_post"] == middle
    assert oldest_state["next_post"] is None

    older_payload = client.get(
        f"/c/{creator_id}/viewer/data?post={oldest}"
    ).json()
    assert [item["index"] for item in older_payload["media"]] == [0, 1, 2]
    assert older_payload["media"][0]["sha256"] == sha(0)
    assert older_payload["media"][-1]["sha256"] == sha(2)

    newer_payload = client.get(
        f"/c/{creator_id}/viewer/data?post={newest}"
    ).json()
    assert newer_payload["media"][0]["sha256"] == sha(20)
    assert newer_payload["media"][-1]["sha256"] == sha(22)


def test_viewer_state_announces_post_rank_and_media_post_total(client, conn):
    creator_id = make_creator(conn)
    oldest = add_post(conn, creator_id, 0)
    add_file(conn, oldest, sha(1))
    add_post(conn, creator_id, 1)  # text-only: not a media post
    middle = add_post(conn, creator_id, 2)
    add_file(conn, middle, sha(2))
    newest = add_post(conn, creator_id, 3)
    add_file(conn, newest, sha(3))

    newest_payload = client.get(f"/c/{creator_id}/viewer/data").json()
    assert newest_payload["post_rank"] == 1
    assert newest_payload["media_post_count"] == 3

    middle_payload = client.get(
        f"/c/{creator_id}/viewer/data?post=2183930593"
    ).json()
    assert middle_payload["post_rank"] == 2
    assert middle_payload["media_post_count"] == 3

    oldest_payload = client.get(
        f"/c/{creator_id}/viewer/data?post=2183930591"
    ).json()
    assert oldest_payload["post_rank"] == 3
    assert oldest_payload["media_post_count"] == 3


def test_neighbour_preload_images_are_edge_ordered_and_still_only(conn):
    creator_id = make_creator(conn)
    newer = add_post(conn, creator_id, 0)
    add_file(conn, newer, sha(10), position=0)
    add_file(conn, newer, sha(11), kind="gif", position=1)
    add_file(conn, newer, sha(12), kind="video", position=2)
    add_file(
        conn,
        newer,
        sha(13),
        position=3,
        url="data:image/png;base64,AAAA",
    )
    add_file(conn, newer, sha(14), position=4)
    add_file(conn, newer, sha(15), position=5)
    add_file(conn, newer, sha(16), position=6)
    add_file(conn, newer, sha(17), kind="audio", position=7)
    current = add_post(conn, creator_id, 1)
    add_file(conn, current, sha(20), position=0)
    older = add_post(conn, creator_id, 2)
    add_file(conn, older, sha(30), position=0)
    add_file(conn, older, sha(31), position=1)
    add_file(conn, older, sha(32), kind="gif", position=2)
    add_file(conn, older, sha(33), position=3)
    add_file(conn, older, sha(34), position=4)

    post = db.get_post(conn, current)
    previous = db.neighbour_preload_images(
        conn, creator_id, post, "previous", 3
    )
    following = db.neighbour_preload_images(conn, creator_id, post, "next", 3)

    assert [item["sha256"] for item in previous] == [sha(34), sha(33), sha(31)]
    assert [item["sha256"] for item in following] == [
        sha(10),
        sha(14),
        sha(15),
    ]
    assert all(item["kind"] == "image" for item in previous + following)


def test_viewer_state_and_endpoint_expose_bounded_neighbour_payload(client, conn):
    creator_id = make_creator(conn)
    newer = add_post(conn, creator_id, 0)
    for position in range(8):
        add_file(conn, newer, sha(100 + position), position=position)
    current = add_post(conn, creator_id, 1)
    add_file(conn, current, sha(200), position=0)
    older = add_post(conn, creator_id, 2)
    for position in range(8):
        add_file(conn, older, sha(300 + position), position=position)

    state = db.viewer_state(conn, creator_id, str(2183930592))
    assert state is not None
    neighbours = state["neighbour_images"]
    assert [item["sha256"] for item in neighbours["previous"]] == [
        sha(307),
        sha(306),
        sha(305),
        sha(304),
        sha(303),
        sha(302),
    ]
    assert [item["sha256"] for item in neighbours["next"]] == [
        sha(100),
        sha(101),
        sha(102),
        sha(103),
        sha(104),
        sha(105),
    ]

    payload = client.get(
        f"/c/{creator_id}/viewer/data?post=2183930592"
    ).json()
    assert payload["neighbour_images"] == neighbours


# --------------------------------------------------------------------------- #
# one overlay, one template, one controller
# --------------------------------------------------------------------------- #


def test_merged_overlay_lives_on_the_gallery_with_all_legacy_and_new_hooks(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 0), sha(1))

    body = gallery_body(client, creator_id)

    assert body.count('id="gallery-lightbox"') == 1
    assert body.count("data-lightbox-root") == 1
    for hook in (
        "data-lightbox-image",
        "data-lightbox-video",
        "data-lightbox-title",
        "data-lightbox-counter",
        "data-lightbox-post",
        "data-lightbox-original",
        "data-lightbox-prev",
        "data-lightbox-next",
        "data-lightbox-close",
        "data-lightbox-stage",
        "data-lightbox-media",
        "data-lightbox-announcer",
        "data-lightbox-toast",
        "data-lightbox-fullscreen",
        "data-lightbox-expand",
        "data-lightbox-hint",
        "data-lightbox-flash",
    ):
        assert hook in body
    overlay = overlay_markup(body)
    assert 'role="dialog"' in overlay
    assert 'aria-modal="true"' in overlay
    assert 'aria-label="Media viewer"' in overlay
    assert 'tabindex="-1"' in overlay
    assert 'data-state="closed"' in overlay
    assert 'hidden>' in overlay
    # The close control is the first focusable element inside the overlay.
    assert overlay.index("data-lightbox-close") < overlay.index("data-lightbox-stage")


def test_gallery_tiles_bootstrap_the_overlay_without_breaking_the_link(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 0, title="Tile post")
    add_file(conn, post_id, sha(1), position=0)

    body = gallery_body(client, creator_id)
    tile = re.search(r'<a class="gallery-thumb".*?</a>', body, re.S)
    assert tile is not None
    tag = tile.group(0)

    assert 'href="https://e3.cum.st/media/' + sha(1) + '/original.jpg"' in tag
    assert 'data-gallery-item' in tag
    assert 'data-post-service-id="2183930591"' in tag
    assert f'data-sha256="{sha(1)}"' in tag
    assert 'data-viewer-index="0"' in tag
    assert 'data-full="https://e3.cum.st/media/' + sha(1) + '/original.jpg"' in tag
    # No-JS contract: the tile is still a link to the media file itself.
    assert tag.index("<a ") == 0
    assert "target=" in tag


def test_tile_viewer_index_matches_the_filtered_playlist(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 0)
    add_file(conn, post_id, sha(1), kind="image", position=0)
    add_file(conn, post_id, sha(2), kind="gif", position=1)
    add_file(conn, post_id, sha(3), kind="video", position=2)

    images = gallery_body(client, creator_id, kind="image")
    image_tile = re.search(r'<a class="gallery-thumb".*?</a>', images, re.S)
    assert image_tile is not None
    assert f'data-sha256="{sha(1)}"' in image_tile.group(0)
    assert 'data-viewer-index="0"' in image_tile.group(0)

    # T11.6: the fallback index describes the filtered playlist, not every
    # viewer-kind row of the post. A video-only view starts at index 0.
    videos = gallery_body(client, creator_id, kind="video")
    video_tile = re.search(r'<a class="gallery-thumb".*?</a>', videos, re.S)
    assert video_tile is not None
    assert f'data-sha256="{sha(3)}"' in video_tile.group(0)
    assert 'data-viewer-index="0"' in video_tile.group(0)

    # Without a media filter the historical pre-filter index is unchanged.
    everything = gallery_body(client, creator_id)
    all_tiles = re.findall(r'<a class="gallery-thumb".*?</a>', everything, re.S)
    assert [re.search(r'data-viewer-index="(\d+)"', tag).group(1) for tag in all_tiles] == [
        "0",
        "1",
        "2",
    ]


def test_old_html_viewer_route_is_gone_while_data_still_answers_json(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 0), sha(1))

    assert client.get(f"/c/{creator_id}/viewer").status_code in {404, 405}
    assert client.get(f"/c/{creator_id}/viewer?post=2183930591").status_code in {
        404,
        405,
    }
    payload = client.get(f"/c/{creator_id}/viewer/data?post=2183930591")
    assert payload.status_code == 200
    assert payload.headers["content-type"].startswith("application/json")
    assert payload.json()["media"][0]["sha256"] == sha(1)


def test_creator_and_gallery_have_no_dead_viewer_links(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 0), sha(1))

    creator_body = client.get(f"/c/{creator_id}").text
    assert f'href="/c/{creator_id}/gallery"' in creator_body
    assert "View latest" not in creator_body
    assert "/viewer" not in creator_body

    gallery = gallery_body(client, creator_id)
    assert "Open viewer" in gallery
    assert "data-lightbox-open" in gallery
    assert f'/c/{creator_id}/viewer' not in gallery

    templates = Path(__file__).resolve().parents[1] / "src" / "monoke" / "web" / "templates"
    for path in templates.rglob("*.html"):
        assert "/viewer?from=gallery" not in path.read_text(encoding="utf-8")


def test_old_page_template_and_controller_are_deleted(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 0), sha(1))

    templates = Path(__file__).resolve().parents[1] / "src" / "monoke" / "web" / "templates"
    assert not (templates / "viewer.html").exists()
    assert not (STATIC_DIR / "viewer.js").exists()

    gallery = gallery_body(client, creator_id)
    assert "/static/lightbox.js" in gallery
    assert "/static/viewer.js" not in gallery
    assert "/static/app.js" in gallery

    app_source = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
    lightbox_source = (STATIC_DIR / "lightbox.js").read_text(encoding="utf-8")
    for source in (app_source, lightbox_source):
        assert "viewer.html" not in source
        assert "viewer.js" not in source
    # The old page controller is not loaded and its navigator is gone.
    assert "galleryOpen" not in app_source
    assert "galleryState" not in app_source
    assert "data-gallery-item" in lightbox_source


def test_merged_controller_owns_all_three_states_and_one_root():
    source = (STATIC_DIR / "lightbox.js").read_text(encoding="utf-8")

    for state in ("is-compact", "is-expanded", "is-browser"):
        assert state in source
    assert 'root.setAttribute("data-state", next)' in source
    assert 'document.querySelector("[data-lightbox-root]")' in source
    assert 'document.querySelector("[data-viewer-root]")' not in source
    assert 'document.getElementById("gallery-lightbox")' not in source
    # Bookmarks, toast, announcer and preloading moved into the one controller.
    assert "toggleBookmark" in source
    assert "showToast" in source
    assert "updateCachedBookmarks" in source
    assert "preloadCandidates" in source
    # Navigation is post-scoped: left/right steps media, up/down jumps posts.
    assert 'jump(payload.previous_post, -1)' in source
    assert 'jump(payload.next_post, 0)' in source


def test_entry_points_open_the_overlay_without_navigation(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 0), sha(1))

    gallery = gallery_body(client, creator_id)
    assert '<button class="button" type="button" data-lightbox-open>Open viewer</button>' in gallery

    creator_body = client.get(f"/c/{creator_id}").text
    assert "View latest" not in creator_body
    assert f'href="/c/{creator_id}/gallery"' in creator_body


# --------------------------------------------------------------------------- #
# scripts and assets
# --------------------------------------------------------------------------- #


def test_lightbox_and_app_scripts_pass_node_syntax_check():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    for name in ("app.js", "lightbox.js"):
        result = subprocess.run(
            [node, "--check", str(STATIC_DIR / name)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr


def test_gallery_links_content_hashed_assets_that_match_disk(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 0), sha(1))

    body = gallery_body(client, creator_id)

    assert f'href="{asset("style.css")}"' in body
    assert f'src="{asset("lightbox.js")}"' in body

    script = client.get(asset("lightbox.js"))
    assert script.status_code == 200
    assert script.text == (STATIC_DIR / "lightbox.js").read_text(encoding="utf-8")


def test_lightbox_source_disables_automatic_fullscreen_and_keeps_explicit_toggle():
    source = (STATIC_DIR / "lightbox.js").read_text(encoding="utf-8")

    # No implicit fullscreen request on pointer/touch: the user's chosen
    # default is tile -> compact everywhere, so only the control or F asks.
    assert "pointerdown" not in source
    assert "requestFullscreen(true)" in source
    assert 'document.addEventListener("fullscreenchange", onFullscreenChange)' in source
    assert "Fullscreen is not available in this browser." in source


def test_preload_window_covers_radius_and_filters_unsafe_items():
    source = (STATIC_DIR / "lightbox.js").read_text(encoding="utf-8")
    block = source.split("// BEGIN preload-window", 1)[1].split(
        "// END preload-window", 1
    )[0]
    payload = node_script(
        "// BEGIN preload-window" + block,
        """
        function media(count, overrides) {
          var items = [];
          for (var i = 0; i < count; i += 1) {
            items.push({
              index: i,
              kind: "image",
              url: "https://e3.cum.st/media/" + i + "/original.jpg"
            });
          }
          Object.keys(overrides || {}).forEach(function (key) {
            var patch = overrides[key];
            Object.keys(patch).forEach(function (field) {
              items[Number(key)][field] = patch[field];
            });
          });
          return items;
        }

        var failed = new Set(["https://e3.cum.st/media/3/original.jpg"]);
        var out = {
          radius: PRELOAD_RADIUS,
          max: PRELOAD_MAX,
          middle: preloadWindowIndices(media(10), 5, null),
          first: preloadWindowIndices(media(10), 0, null),
          last: preloadWindowIndices(media(10), 9, null),
          failed: preloadWindowIndices(media(10), 5, failed),
          kinds: preloadWindowIndices(
            media(10, {
              2: { kind: "video" },
              4: { kind: "gif" },
              6: { kind: "audio" },
              7: { url: "data:image/png;base64,AAAA" }
            }),
            5,
            null
          )
        };
        process.stdout.write(JSON.stringify(out));
        """,
    )

    assert payload["radius"] == 3
    assert payload["max"] == 6
    assert payload["middle"] == [2, 3, 4, 6, 7, 8]
    assert payload["first"] == [1, 2, 3]
    assert payload["last"] == [6, 7, 8]
    assert payload["failed"] == [2, 4, 6, 7, 8]
    assert payload["kinds"] == [3, 8]


def test_preload_plan_keeps_both_edges_and_prunes_stale_entries():
    source = (STATIC_DIR / "lightbox.js").read_text(encoding="utf-8")
    block = source.split("// BEGIN preload-window", 1)[1].split(
        "// END preload-plan", 1
    )[0]
    payload = node_script(
        block,
        """
        function image(name) {
          return { kind: "image", url: "https://e3.cum.st/" + name };
        }
        function media(count) {
          var items = [];
          for (var i = 0; i < count; i += 1) {
            items.push(image("m" + i));
          }
          return items;
        }

        var neighbours = {
          previous: [image("p0"), image("p1"), image("p2")],
          next: [image("n0"), image("n1"), image("n2")]
        };
        var candidates = preloadCandidates(media(10), 0, neighbours);
        var plan = buildPreloadPlan(media(10), 0, neighbours, 4, null);
        var failed = new Set(["https://e3.cum.st/p0"]);
        var failedPlan = buildPreloadPlan(media(10), 0, neighbours, 12, failed);

        var map = new Map();
        ["https://e3.cum.st/m1", "https://e3.cum.st/stale"].forEach(
          function (url) { map.set(url, {}); }
        );
        prunePreloadMap(map, new Set(plan));

        var failures = new Set([
          "https://e3.cum.st/p0",
          "https://e3.cum.st/old"
        ]);
        pruneFailedSet(failures, candidates);

        process.stdout.write(JSON.stringify({
          totalMax: PRELOAD_TOTAL_MAX,
          candidates: candidates,
          plan: plan,
          failedPlan: failedPlan,
          map: Array.from(map.keys()),
          failures: Array.from(failures)
        }));
        """,
    )

    assert payload["totalMax"] == 12
    assert payload["candidates"][:9] == [
        "https://e3.cum.st/m1",
        "https://e3.cum.st/m2",
        "https://e3.cum.st/m3",
        "https://e3.cum.st/p0",
        "https://e3.cum.st/n0",
        "https://e3.cum.st/p1",
        "https://e3.cum.st/n1",
        "https://e3.cum.st/p2",
        "https://e3.cum.st/n2",
    ]
    assert payload["plan"] == payload["candidates"][:4]
    assert "https://e3.cum.st/p0" not in payload["failedPlan"]
    assert "https://e3.cum.st/n0" in payload["failedPlan"]
    assert payload["map"] == ["https://e3.cum.st/m1"]
    assert payload["failures"] == ["https://e3.cum.st/p0"]


def test_opening_a_tile_resolves_the_requested_item_by_sha():
    source = (STATIC_DIR / "lightbox.js").read_text(encoding="utf-8")
    block = source.split("// BEGIN resolve-index", 1)[1].split(
        "// END resolve-index", 1
    )[0]
    payload = node_script(
        block,
        """
        var media = [
          { sha256: "a" }, { sha256: "b" }, { sha256: "c" }
        ];
        process.stdout.write(JSON.stringify({
          exact: resolveMediaIndex(media, "b", 0),
          fallback: resolveMediaIndex(media, "missing", 2),
          negative: resolveMediaIndex(media, "", -1),
          clamped: resolveMediaIndex(media, "", 99),
          empty: resolveMediaIndex([], "a", 0)
        }));
        """,
    )

    assert payload == {
        "exact": 1,
        "fallback": 2,
        "negative": 2,
        "clamped": 2,
        "empty": -1,
    }


def test_escape_unwinds_browser_then_expanded_then_close():
    source = (STATIC_DIR / "lightbox.js").read_text(encoding="utf-8")
    block = source.split("// BEGIN escape-state", 1)[1].split(
        "// END escape-state", 1
    )[0]
    payload = node_script(
        block,
        """
        process.stdout.write(JSON.stringify({
          browser: escapeAction("browser", true),
          expandedFullscreen: escapeAction("expanded", true),
          expanded: escapeAction("expanded", false),
          compact: escapeAction("compact", false)
        }));
        """,
    )

    assert payload == {
        "browser": "exit-browser",
        "expandedFullscreen": "exit-browser",
        "expanded": "close",
        "compact": "close",
    }


def test_jump_prefers_cached_payload_without_clearing_preloads():
    source = (STATIC_DIR / "lightbox.js").read_text(encoding="utf-8")
    jump_block = source.split("function jump(", 1)[1].split("function go(", 1)[0]

    assert "payloadCache.get(payloadKey(serviceId))" in jump_block
    assert "applyPayload(cached" in jump_block
    assert "clearPreloadCache()" not in jump_block
    assert "prunePreloadMap" in source


# --------------------------------------------------------------------------- #
# markup and styles for the merged surface
# --------------------------------------------------------------------------- #


def test_fullscreen_and_expand_controls_are_icon_only_and_accessible(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 0), sha(1))

    overlay = overlay_markup(gallery_body(client, creator_id))

    fullscreen = re.search(r"<button[^>]*data-lightbox-fullscreen[^>]*>", overlay)
    assert fullscreen is not None
    tag = fullscreen.group(0)
    assert 'type="button"' in tag
    assert 'aria-pressed="false"' in tag
    assert 'aria-label="Enter fullscreen"' in tag
    assert 'data-fullscreen-state="enter"' in tag
    assert ">Fullscreen</button>" not in overlay

    expand = re.search(r"<button[^>]*data-lightbox-expand[^>]*>", overlay)
    assert expand is not None
    assert 'aria-pressed="false"' in expand.group(0)
    assert 'aria-label="Expand viewer"' in expand.group(0)
    assert "<svg" in overlay


def test_hint_has_all_four_directions_and_is_expanded_only(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 0), sha(1))

    body = gallery_body(client, creator_id)
    overlay = overlay_markup(body)
    assert overlay.count("data-lightbox-hint") == 1
    for label in ("Newer post", "Older post", "Previous image", "Next image"):
        assert f'<span class="viewer-hint-label">{label}</span>' in overlay
    assert "Esc to close · F for fullscreen · first move hides this" in overlay

    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    compact = css.split(".lightbox.is-compact .viewer-zone,", 1)[1].split("}", 1)[0]
    assert ".viewer-hint" in compact


def test_stylesheet_defines_compact_expanded_and_browser_states():
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")

    assert ".lightbox.is-expanded,\n.lightbox.is-browser {" in css
    assert ".lightbox.is-expanded .viewer-stage," in css
    assert ".lightbox.is-expanded .lightbox-nav," in css
    assert ".lightbox.is-expanded .lightbox-caption," in css
    # Old page-only shell rules are removed.
    assert "body.viewer-page" not in css
    assert ".viewer-empty" not in css

    media_rule = css.split("\n.viewer-media {", 1)[1].split("}", 1)[0]
    assert "width: 100%" in media_rule
    assert "height: 100%" in media_rule
    assert "align-items: center" in media_rule
    assert "justify-content: center" in media_rule

    element_rule = css.split("\n.viewer-media-el {", 1)[1].split("}", 1)[0]
    assert "max-width: 100%" in element_rule
    assert "max-height: 100%" in element_rule
    assert "object-fit: contain" in element_rule


# --------------------------------------------------------------------------- #
# T11.2 - sticky bottom control bar, visible post buttons and hide control
# --------------------------------------------------------------------------- #


def test_control_bar_has_exactly_one_of_each_control(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 0), sha(1))

    overlay = overlay_markup(gallery_body(client, creator_id))

    assert len(re.findall(r"data-lightbox-controls(?=[\s>])", overlay)) == 1
    assert overlay.count("data-lightbox-hide") == 1
    assert overlay.count("data-lightbox-controls-handle") == 1
    assert overlay.count("data-lightbox-post-prev") == 1
    assert overlay.count("data-lightbox-post-next") == 1
    assert overlay.count("data-lightbox-fullscreen") == 1
    assert overlay.count("data-bookmark-button") == 1
    # The invisible left/right media zones remain; the up/down post zones are
    # replaced by visible buttons.
    assert overlay.count('data-viewer-action="prev-image"') == 1
    assert overlay.count('data-viewer-action="next-image"') == 1
    assert 'data-viewer-action="prev-post"' not in overlay
    assert 'data-viewer-action="next-post"' not in overlay


def test_control_bar_buttons_are_labelled_with_states(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 0), sha(1))

    overlay = overlay_markup(gallery_body(client, creator_id))

    newer = re.search(r"<button[^>]*data-lightbox-post-prev[^>]*>", overlay)
    older = re.search(r"<button[^>]*data-lightbox-post-next[^>]*>", overlay)
    assert newer and older
    assert 'aria-label="Newer post"' in newer.group(0)
    assert 'aria-label="Older post"' in older.group(0)
    assert "disabled" in newer.group(0)
    assert "disabled" in older.group(0)
    assert "Newer post" in overlay.split("data-lightbox-post-prev", 1)[1][:160]
    assert "Older post" in overlay.split("data-lightbox-post-next", 1)[1][:160]

    hide = re.search(r"<button[^>]*data-lightbox-hide[^>]*>", overlay)
    assert hide and 'aria-label="Hide controls"' in hide.group(0)

    handle = re.search(
        r"<button[^>]*data-lightbox-controls-handle[^>]*>", overlay
    )
    assert handle
    assert 'aria-label="Show controls"' in handle.group(0)
    assert "hidden" in handle.group(0)
    assert "Show controls" in overlay.split("data-lightbox-controls-handle", 1)[1][:160]

    # The bar bookmark keeps the shared macro's POST / next contract and the
    # target-specific pressed state.
    form = re.search(r"<form[^>]*data-bookmark-form[^>]*>", overlay)
    assert form
    assert 'method="post"' in form.group(0)
    assert f'action="/c/{creator_id}/p/2183930591/bookmark"' in overlay
    assert '<input type="hidden" name="next"' in overlay
    bookmark = re.search(r"<button[^>]*data-bookmark-button[^>]*>", overlay)
    assert bookmark
    assert 'aria-pressed="false"' in bookmark.group(0)
    assert 'aria-label="Bookmark post: this post"' in bookmark.group(0)


def test_chrome_state_is_explicit_and_never_timed():
    source = (STATIC_DIR / "lightbox.js").read_text(encoding="utf-8")
    block = source.split("// BEGIN chrome-state", 1)[1].split(
        "// END chrome-state", 1
    )[0]
    payload = node_script(
        "// BEGIN chrome-state" + block,
        """
        function state(patch) {
          var base = { open: true, pinned: false, hidden: false };
          Object.keys(patch || {}).forEach(function (key) {
            base[key] = patch[key];
          });
          return base;
        }
        process.stdout.write(JSON.stringify({
          reveal: chromeRevealAllowed(state()),
          revealPinned: chromeRevealAllowed(state({ pinned: true })),
          revealClosed: chromeRevealAllowed(state({ open: false })),
          targetHidden: chromeHandleTarget(state({ hidden: true })),
          targetVisible: chromeHandleTarget(state())
        }));
        """,
    )

    assert payload == {
        "reveal": True,
        "revealPinned": False,
        "revealClosed": False,
        "targetHidden": "handle",
        "targetVisible": "controls",
    }

    # The idle timer and its decision helper are gone: the bar only hides when
    # the user pins it away with the hide button.
    for gone in ("CHROME_TIMEOUT", "chromeCanHide", "scheduleChromeHide", "chromeTimer"):
        assert gone not in source


def test_hide_pins_the_bar_and_only_the_handle_restores_it():
    source = (STATIC_DIR / "lightbox.js").read_text(encoding="utf-8")

    hide_block = source.split("function hideControls(", 1)[1].split(
        "function firstControl(", 1
    )[0]
    assert "chromePinned = true" in hide_block
    assert "setControlsHidden(true)" in hide_block
    # Focus moves to the handle instead of being lost with the bar.
    assert "controlsHandle.focus" in hide_block

    restore_block = source.split("function restoreControls(", 1)[1].split(
        "function resetChrome(", 1
    )[0]
    assert "chromePinned = false" in restore_block
    assert "setControlsHidden(false)" in restore_block
    assert "scheduleChromeHide" not in restore_block

    activity_block = source.split("function onActivity(", 1)[1].split(
        "function showToast(", 1
    )[0]
    assert "if (chromePinned)" in activity_block
    assert "return" in activity_block

    assert 'if (target.closest("[data-lightbox-hide]"))' in source
    assert 'if (target.closest("[data-lightbox-controls-handle]"))' in source
    assert "hideControls(true)" in source
    assert "restoreControls()" in source
    assert "scheduleChromeHide" not in source.split("function openOverlay", 1)[1]
    assert "resetChrome();" in source.split("function openOverlay", 1)[1]


def test_visible_post_buttons_use_the_keyboard_post_actions():
    source = (STATIC_DIR / "lightbox.js").read_text(encoding="utf-8")

    assert 'ArrowLeft: "prev-image"' in source
    assert 'ArrowRight: "next-image"' in source
    assert 'ArrowUp: "prev-post"' in source
    assert 'ArrowDown: "next-post"' in source
    assert 'go("prev-post")' in source
    assert 'go("next-post")' in source
    assert "postPrevButton.disabled = !(payload && payload.previous_post)" in source
    assert "postNextButton.disabled = !(payload && payload.next_post)" in source


def test_control_bar_styles_and_zone_cleanup():
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")

    bar = css.split("\n.lightbox-caption {", 1)[1].split("}", 1)[0]
    assert "width: min(100% - 2rem, 980px)" in bar
    assert "background: #0b1017" in bar
    assert "border-radius" in bar
    assert "z-index: 7" in bar
    assert "flex-wrap: nowrap" in bar
    assert "padding: 0.25rem 0.5rem calc(0.25rem + env(safe-area-inset-bottom))" in bar
    assert "env(safe-area-inset-bottom)" in bar

    handle = css.split("\n.lightbox-controls-handle {", 1)[1].split("}", 1)[0]
    assert "position: absolute" in handle
    assert "bottom: max(0.5rem, env(safe-area-inset-bottom))" in handle
    assert "z-index: 8" in handle

    assert ".lightbox.is-controls-hidden .lightbox-caption" in css
    assert "visibility: hidden" in css
    # Instant hide/show for reduced motion, no layout shift.
    reduced = css.split("@media (prefers-reduced-motion: reduce) {", 2)[2]
    assert ".lightbox-caption" in reduced.split("}", 1)[0]
    assert "transition: none" in reduced.split("}", 1)[0]
    # The old up/down invisible zones and their rules are gone.
    assert ".viewer-zone-prev-post" not in css
    assert ".viewer-zone-next-post" not in css
    assert "[data-lightbox-expand]" in css.split("@media (max-width: 680px)", 1)[1]
    assert "@media (pointer: coarse)" in css
    assert "min-width: 44px" in css
    assert "min-height: 44px" in css


# --------------------------------------------------------------------------- #
# T11.5 - slim bar, top caption and overflow menu
# --------------------------------------------------------------------------- #


def test_overflow_menu_holds_the_secondary_post_actions(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 0)
    add_file(conn, post_id, sha(1))

    overlay = overlay_markup(gallery_body(client, creator_id))

    trigger = re.search(r"<button[^>]*data-lightbox-more[^>]*>", overlay)
    assert trigger
    assert 'aria-haspopup="true"' in trigger.group(0)
    assert 'aria-expanded="false"' in trigger.group(0)
    assert 'aria-controls="lightbox-menu"' in trigger.group(0)
    assert 'aria-label="More actions"' in trigger.group(0)
    assert 'title="More actions"' in trigger.group(0)
    assert overlay.count("data-lightbox-more") == 1

    assert overlay.count("data-lightbox-menu") == 1
    assert 'id="lightbox-menu"' in overlay
    menu = re.search(r'<div[^>]*data-lightbox-menu[^>]*>', overlay)
    assert menu and "hidden" in menu.group(0)

    menu_start = overlay.index("data-lightbox-menu")
    post_index = overlay.index("data-lightbox-post href")
    original_index = overlay.index("data-lightbox-original")
    bookmark_index = overlay.index("data-bookmark-form")
    assert menu_start < post_index < original_index < bookmark_index
    assert overlay.count("data-lightbox-post href") == 1
    assert overlay.count("data-lightbox-original") == 1

    # The menu is a plain disclosure, not an ARIA menu.
    assert 'role="menu"' not in overlay
    assert 'role="menuitem"' not in overlay

    # The bookmark keeps the shared macro's POST / next contract inside it.
    form = re.search(r"<form[^>]*data-bookmark-form[^>]*>", overlay)
    assert form and 'method="post"' in form.group(0)
    assert f'action="/c/{creator_id}/p/2183930591/bookmark"' in overlay
    assert '<input type="hidden" name="next"' in overlay
    bookmark = re.search(r"<button[^>]*data-bookmark-button[^>]*>", overlay)
    assert bookmark
    assert 'aria-pressed="false"' in bookmark.group(0)
    assert 'aria-label="Bookmark post: this post"' in bookmark.group(0)


def test_post_arrows_are_icon_only_and_keep_edge_states(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 0), sha(1))

    overlay = overlay_markup(gallery_body(client, creator_id))

    newer = re.search(
        r"<button[^>]*data-lightbox-post-prev[^>]*>(.*?)</button>", overlay, re.S
    )
    older = re.search(
        r"<button[^>]*data-lightbox-post-next[^>]*>(.*?)</button>", overlay, re.S
    )
    assert newer and older
    assert 'aria-label="Newer post"' in newer.group(0)
    assert 'title="Newer post"' in newer.group(0)
    assert 'aria-label="Older post"' in older.group(0)
    assert 'title="Older post"' in older.group(0)
    assert "disabled" in newer.group(0)
    assert "disabled" in older.group(0)
    # Visible text is only the glyph; the label lives in the attributes.
    assert "Newer post" not in newer.group(1)
    assert "Older post" not in older.group(1)
    assert "↑" in newer.group(1)
    assert "↓" in older.group(1)


def test_top_caption_moves_the_title_out_of_the_bar(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 0), sha(1))

    overlay = overlay_markup(gallery_body(client, creator_id))

    caption = re.search(r"<p[^>]*data-lightbox-title[^>]*>", overlay)
    assert caption
    assert 'class="lightbox-caption-title"' in caption.group(0)
    assert 'aria-hidden="true"' in caption.group(0)
    assert overlay.index("data-lightbox-title") < overlay.index("data-lightbox-stage")
    assert overlay.count("data-lightbox-title") == 1

    bar = overlay[overlay.index("data-lightbox-controls") :]
    assert "data-lightbox-title" not in bar
    assert "data-lightbox-counter" in bar
    # The secondary links are no longer loose in the bar.
    assert bar.index("data-lightbox-menu") < bar.index("data-lightbox-post href")


def test_menu_escape_order_helper():
    source = (STATIC_DIR / "lightbox.js").read_text(encoding="utf-8")
    block = source.split("// BEGIN escape-state", 1)[1].split(
        "// END escape-state", 1
    )[0]
    payload = node_script(
        "// BEGIN escape-state" + block,
        """
        process.stdout.write(JSON.stringify({
          menu: escapeAction("expanded", false, true),
          menuFullscreen: escapeAction("browser", true, true),
          browser: escapeAction("browser", true, false),
          expanded: escapeAction("expanded", false, false),
          compact: escapeAction("compact", false, false)
        }));
        """,
    )

    assert payload == {
        "menu": "close-menu",
        "menuFullscreen": "close-menu",
        "browser": "exit-browser",
        "expanded": "close",
        "compact": "close",
    }


def test_menu_open_close_focus_and_outside_click():
    source = (STATIC_DIR / "lightbox.js").read_text(encoding="utf-8")

    menu_block = source.split("function setMenuOpen(", 1)[1].split(
        "function clearChromeTimer(", 1
    )[0]
    assert 'aria-expanded' in menu_block
    assert "menu.hidden = !menuOpen" in menu_block
    assert "items[0].focus" in menu_block
    assert "moreButton.focus" in menu_block

    assert "closeMenu(true)" in source
    assert 'escapeAction(' in source
    assert "close-menu" in source

    # Navigation closes the disclosure, and so does hiding/pinning the bar.
    assert "closeMenu(false);" in source.split("function go(", 1)[1]
    assert "closeMenu(false);" in source.split("function setControlsHidden(", 1)[1]
    assert "closeMenu(false);" in source.split("function resetChrome(", 1)[1]

    outside = source.split('document.addEventListener("click", function (event) {', 2)[
        2
    ]
    assert 'target.closest("[data-lightbox-menu]")' in outside
    assert 'target.closest("[data-lightbox-more]")' in outside
    assert "closeMenu(false)" in outside

    # The bar never auto-hides, so an open menu needs no timer suppression:
    # it lives until an explicit close.
    assert "scheduleChromeHide" not in source
    assert "chromeTimer" not in source


def test_caption_counter_and_href_updates_still_wired():
    source = (STATIC_DIR / "lightbox.js").read_text(encoding="utf-8")

    chrome = source.split("function updateChrome(", 1)[1].split(
        "function render(", 1
    )[0]
    assert "setText(title, item.title || \"\")" in chrome
    assert 'title.setAttribute("title", item.title || "")' in chrome
    assert "setText(counter, counterText || \"\")" in chrome
    assert "postLink.href = item.postUrl" in chrome
    assert "originalLink.href = item.url" in chrome


def test_slim_bar_and_menu_styles():
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")

    button = css.split("\n.lightbox-control-button {", 1)[1].split("}", 1)[0]
    assert "width: 2rem" in button
    assert "height: 2rem" in button
    assert "padding: 0" in button

    caption = css.split("\n.lightbox-caption-title {", 1)[1].split("}", 1)[0]
    assert "text-overflow: ellipsis" in caption
    assert "white-space: nowrap" in caption
    assert "3.5rem" in caption  # room for the close target at 390 px
    assert "position: absolute" in caption

    menu = css.split("\n.lightbox-menu {", 1)[1].split("}", 1)[0]
    assert "position: absolute" in menu
    assert "bottom: calc(100% + 0.5rem)" in menu
    assert "right: 0" in menu
    assert "z-index: 9" in menu
    assert "calc(100vw - 1.5rem)" in menu

    assert ".lightbox-menu[hidden]" in css
    assert "display: none" in css.split(".lightbox-menu[hidden] {", 1)[1]

    # The old two-row mobile grid is gone; the bar stays one row at 390 px.
    narrow_start = css.index("@media (max-width: 680px) {\n  .lightbox-caption")
    narrow = css[narrow_start:].split("@media (pointer: coarse)", 1)[0]
    assert "grid-template-columns" not in narrow
    assert "[data-lightbox-expand]" in narrow
    assert "display: none" in narrow

    coarse = css.split("@media (pointer: coarse)", 1)[1].split("}", 1)[0]
    assert ".lightbox-menu-item" in coarse
    assert "min-width: 44px" in coarse
    assert "min-height: 44px" in coarse

    reduced = css.split("@media (prefers-reduced-motion: reduce) {", 2)[2]
    assert ".lightbox-caption-title" in reduced.split("}", 1)[0]
    assert "transition: none" in reduced.split("}", 1)[0]


# --------------------------------------------------------------------------- #
# T11.6 - the viewer follows the active gallery filters
# --------------------------------------------------------------------------- #


def test_viewer_kind_filter_returns_only_matching_media_and_filtered_indexes(
    client, conn
):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 0)
    add_file(conn, post_id, sha(1), kind="image", position=0)
    add_file(conn, post_id, sha(2), kind="gif", position=1)
    add_file(conn, post_id, sha(3), kind="video", position=2, duration_ms=5000)

    payload = client.get(
        f"/c/{creator_id}/viewer/data",
        params={"post": "2183930591", "kind": "video"},
    ).json()

    assert [item["sha256"] for item in payload["media"]] == [sha(3)]
    assert [item["index"] for item in payload["media"]] == [0]
    assert payload["post"]["media_count"] == 1
    assert payload["post_rank"] == 1
    assert payload["media_post_count"] == 1
    # A videos-only view has no still neighbour to warm.
    assert payload["neighbour_images"] == {"previous": [], "next": []}


def test_filtered_neighbours_skip_posts_without_matching_media(client, conn):
    creator_id = make_creator(conn)
    newest_video = add_post(conn, creator_id, 4)
    add_file(conn, newest_video, sha(10), kind="video", duration_ms=4000)
    image_only = add_post(conn, creator_id, 3)
    add_file(conn, image_only, sha(11))
    middle_video = add_post(conn, creator_id, 2)
    add_file(conn, middle_video, sha(12), kind="video", duration_ms=5000)
    oldest_image = add_post(conn, creator_id, 1)
    add_file(conn, oldest_image, sha(13))

    newest = client.get(
        f"/c/{creator_id}/viewer/data",
        params={"post": "2183930595", "kind": "video"},
    ).json()
    assert newest["previous_post"] is None
    assert newest["next_post"] == "2183930593"
    assert (newest["post_rank"], newest["media_post_count"]) == (1, 2)

    middle = client.get(
        f"/c/{creator_id}/viewer/data",
        params={"post": "2183930593", "kind": "video"},
    ).json()
    assert middle["previous_post"] == "2183930595"
    assert middle["next_post"] is None
    assert (middle["post_rank"], middle["media_post_count"]) == (2, 2)

    # The image-only post is not a destination in either direction.
    assert "2183930594" not in {newest["next_post"], middle["previous_post"]}


def test_filtered_neighbours_cross_gallery_pages(client, conn):
    creator_id = make_creator(conn)
    service_ids = []
    for number in range(22):
        post_id = add_post(conn, creator_id, number)
        add_file(conn, post_id, sha(1000 + number), kind="video", duration_ms=1000)
        service_ids.append(str(2183930591 + number))

    oldest = service_ids[0]
    payload = client.get(
        f"/c/{creator_id}/viewer/data",
        params={"post": oldest, "kind": "video", "page": "9"},
    ).json()

    # page is ignored: the oldest matching post is still rank 22 of 22 and has
    # an older-side neighbour from a later gallery page.
    assert payload["post_rank"] == 22
    assert payload["media_post_count"] == 22
    assert payload["previous_post"] == service_ids[1]
    assert payload["next_post"] is None


def test_search_tag_orientation_size_and_date_filters_constrain_the_viewer(
    client, conn
):
    creator_id = make_creator(conn)
    alpha = add_post(
        conn, creator_id, 2, title="Portrait Alpha", published=1_800_000_000
    )
    beta = add_post(
        conn, creator_id, 1, title="Landscape Beta", published=1_700_000_000
    )
    gamma = add_post(
        conn, creator_id, 0, title="Portrait Gamma", published=1_600_000_000
    )
    add_file(conn, alpha, sha(1), position=0, width=1080, height=1920, bytes_=100_000)
    add_file(
        conn, alpha, sha(2), kind="video", position=1, duration_ms=5000, bytes_=500_000
    )
    add_file(conn, beta, sha(3), position=0, width=1920, height=1080, bytes_=2_000_000)
    add_file(conn, gamma, sha(4), position=0, width=1080, height=1920, bytes_=2_000_000)
    db.replace_tags(conn, alpha, [{"slug": "beach", "label": "Beach"}])
    db.replace_tags(conn, beta, [{"slug": "beach", "label": "Beach"}])
    db.replace_tags(conn, gamma, [{"slug": "forest", "label": "Forest"}])

    search = client.get(
        f"/c/{creator_id}/viewer/data",
        params={"post": "2183930592", "q": "Beta"},
    ).json()
    assert [item["sha256"] for item in search["media"]] == [sha(3)]
    assert search["previous_post"] is None and search["next_post"] is None

    tagged = client.get(
        f"/c/{creator_id}/viewer/data",
        params={"post": "2183930591", "tag": "forest"},
    ).json()
    assert [item["sha256"] for item in tagged["media"]] == [sha(4)]
    assert tagged["media_post_count"] == 1

    landscape = client.get(
        f"/c/{creator_id}/viewer/data",
        params={"post": "2183930592", "orientation": "landscape"},
    ).json()
    assert [item["sha256"] for item in landscape["media"]] == [sha(3)]
    assert landscape["neighbour_images"] == {"previous": [], "next": []}

    large = client.get(
        f"/c/{creator_id}/viewer/data",
        params={"post": "2183930592", "min_bytes": "1000000"},
    ).json()
    assert [item["sha256"] for item in large["media"]] == [sha(3)]
    assert large["previous_post"] is None
    assert large["next_post"] == "2183930591"

    dated = client.get(
        f"/c/{creator_id}/viewer/data",
        params={"post": "2183930592", "from": "2023-01-01", "to": "2023-12-31"},
    ).json()
    assert [item["sha256"] for item in dated["media"]] == [sha(3)]
    assert dated["media_post_count"] == 1


def test_filtered_viewer_404s_when_the_post_has_no_matching_media(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 0)
    add_file(conn, post_id, sha(1), kind="image")

    response = client.get(
        f"/c/{creator_id}/viewer/data",
        params={"post": "2183930591", "kind": "video"},
    )

    assert response.status_code == 404
    assert response.json() == {"error": "no media match these filters"}
    # Unknown posts keep the historical error.
    unknown = client.get(
        f"/c/{creator_id}/viewer/data", params={"post": "nope", "kind": "video"}
    )
    assert unknown.status_code == 404
    assert unknown.json() == {"error": "unknown post"}


def test_sort_oldest_reverses_up_down_order(client, conn):
    creator_id = make_creator(conn)
    oldest = add_post(conn, creator_id, 0, published=1000)
    add_file(conn, oldest, sha(1))
    middle = add_post(conn, creator_id, 1, published=2000)
    add_file(conn, middle, sha(2))
    newest = add_post(conn, creator_id, 2, published=3000)
    add_file(conn, newest, sha(3))

    payload = client.get(
        f"/c/{creator_id}/viewer/data",
        params={"post": "2183930592", "sort": "oldest"},
    ).json()

    # Oldest-first: the list-order previous is the chronologically older post.
    assert payload["previous_post"] == "2183930591"
    assert payload["next_post"] == "2183930593"
    assert (payload["post_rank"], payload["media_post_count"]) == (2, 3)


def test_duration_sorts_order_neighbours_by_matching_videos(client, conn):
    creator_id = make_creator(conn)
    short = add_post(conn, creator_id, 0, published=1000)
    add_file(conn, short, sha(1), kind="video", duration_ms=10_000)
    long = add_post(conn, creator_id, 1, published=2000)
    add_file(conn, long, sha(2), kind="video", duration_ms=30_000)
    medium = add_post(conn, creator_id, 2, published=3000)
    add_file(conn, medium, sha(3), kind="video", duration_ms=20_000)

    descending = client.get(
        f"/c/{creator_id}/viewer/data",
        params={"post": "2183930593", "sort": "duration_desc", "kind": "video"},
    ).json()
    assert descending["previous_post"] == "2183930592"  # 30s is first
    assert descending["next_post"] == "2183930591"  # 10s is last
    assert (descending["post_rank"], descending["media_post_count"]) == (2, 3)

    ascending = client.get(
        f"/c/{creator_id}/viewer/data",
        params={"post": "2183930593", "sort": "duration_asc", "kind": "video"},
    ).json()
    assert ascending["previous_post"] == "2183930591"  # 10s is first
    assert ascending["next_post"] == "2183930592"  # 30s is last
    assert (ascending["post_rank"], ascending["media_post_count"]) == (2, 3)


def test_neighbour_images_follow_the_media_filter(client, conn):
    creator_id = make_creator(conn)
    newer = add_post(conn, creator_id, 1)
    add_file(conn, newer, sha(10), kind="video", position=0, duration_ms=4000)
    add_file(conn, newer, sha(11), position=1)
    current = add_post(conn, creator_id, 0)
    add_file(conn, current, sha(20), kind="video", position=0, duration_ms=4000)
    add_file(conn, current, sha(21), position=1)

    videos = client.get(
        f"/c/{creator_id}/viewer/data",
        params={"post": "2183930591", "kind": "video"},
    ).json()
    assert videos["neighbour_images"] == {"previous": [], "next": []}

    gifs = client.get(
        f"/c/{creator_id}/viewer/data",
        params={"post": "2183930591", "kind": "gif"},
    )
    assert gifs.status_code == 404  # no gif matches, never an empty playlist

    images = client.get(
        f"/c/{creator_id}/viewer/data",
        params={"post": "2183930591", "kind": "image"},
    ).json()
    assert [item["sha256"] for item in images["neighbour_images"]["previous"]] == [
        sha(11)
    ]


def test_no_filter_payload_is_unchanged(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 0)
    add_file(conn, post_id, sha(1), position=0)
    add_file(conn, post_id, sha(2), kind="gif", position=1)
    add_file(conn, post_id, sha(3), kind="video", position=2, duration_ms=5000)

    bare = client.get(
        f"/c/{creator_id}/viewer/data", params={"post": "2183930591"}
    ).json()
    with_page = client.get(
        f"/c/{creator_id}/viewer/data",
        params={"post": "2183930591", "page": "7"},
    ).json()

    assert bare == with_page
    assert [item["sha256"] for item in bare["media"]] == [sha(1), sha(2), sha(3)]
    assert [item["index"] for item in bare["media"]] == [0, 1, 2]


def test_gallery_serialises_filters_once_without_page(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 0)
    add_file(conn, post_id, sha(1), kind="video")

    body = gallery_body(client, creator_id, kind="video", page=1, sort="oldest")
    grid = re.search(r'<div class="gallery-posts" data-gallery[^>]*>', body)
    assert grid is not None
    query = re.search(r'data-viewer-query="([^"]*)"', grid.group(0)).group(1)

    assert "kind=video" in query
    assert "sort=oldest" in query
    assert "page=" not in query
    assert body.count("data-viewer-query") == 1

    # Bookmark tiles have no gallery context and therefore no filter query.
    db.toggle_media_bookmark(conn, creator_id, sha(1))
    bookmarks = client.get("/bookmarks").text
    assert "data-viewer-query" not in bookmarks


def test_every_viewer_data_request_carries_the_serialised_filters():
    source = (STATIC_DIR / "lightbox.js").read_text(encoding="utf-8")

    assert 'grid.getAttribute("data-viewer-query")' in source
    assert "var viewerQuery = \"\";" in source

    url = source.split("function viewerDataUrl(", 1)[1].split(
        "function fetchPayload(", 1
    )[0]
    assert 'query += "&" + viewerQuery' in url
    assert "/viewer/data?" in url

    fetch_block = source.split("function fetchPayload(", 1)[1].split(
        "function hydrate(", 1
    )[0]
    assert "viewerDataUrl(serviceId)" in fetch_block
    assert "/viewer/data?post=" not in fetch_block

    prefetch = source.split("function prefetchNeighbourPayloads(", 1)[1].split(
        "function schedulePayloadPrefetch(", 1
    )[0]
    assert "viewerDataUrl(pendingId)" in prefetch
    assert "/viewer/data?post=" not in prefetch

    # Cache entries are scoped to the filters as well as the post.
    assert "function payloadKey(serviceId)" in source
    assert "payloadCache.has(payloadKey(id))" in source
    assert "payloadCache.get(payloadKey(serviceId))" in source
    # Bookmark entry points reset the query string on open.
    assert "viewerQuery = \"\";" in source.split("function openOverlay(", 1)[1]


# --------------------------------------------------------------------------- #
# T11.7 - loading indicator for slow viewer media
# --------------------------------------------------------------------------- #


def test_loading_pill_markup_is_one_silent_status_hook(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 0), sha(1))

    overlay = overlay_markup(gallery_body(client, creator_id))
    assert overlay.count("data-lightbox-loading") == 1
    pill = re.search(r"<p[^>]*data-lightbox-loading[^>]*>(.*?)</p>", overlay, re.S)
    assert pill is not None
    tag = pill.group(0)
    assert 'role="status"' in tag
    assert 'aria-live="polite"' in tag
    assert "hidden" in tag
    assert "Loading…" in pill.group(1)
    dot = re.search(r'<span[^>]*class="viewer-loading-dot"[^>]*>', tag)
    assert dot is not None
    assert 'aria-hidden="true"' in dot.group(0)


def test_loading_pill_behaviour_has_delay_tokens_and_stale_guards():
    source = (STATIC_DIR / "lightbox.js").read_text(encoding="utf-8")

    assert "var LOADING_DELAY = 150;" in source
    assert "var LOADING_MIN_VISIBLE = 300;" in source
    assert "function beginLoading()" in source
    assert "function finishLoading(token)" in source
    assert "token !== loadingToken" in source
    assert "loadingShownAt" in source

    # A replacement item aborts the previous listeners and clears any wait.
    watch = source.split("function watchCurrentMedia(", 1)[1].split(
        "// ------------------------------------------------------------------- //\n  // Rendering", 1
    )[0]
    assert "new AbortController()" in watch
    assert "{ signal: signal }" in watch
    assert 'element.addEventListener("load", mediaArrived' in watch
    assert 'element.addEventListener("error", mediaArrived' in watch
    assert 'element.addEventListener("loadeddata", mediaArrived' in watch
    assert 'element.addEventListener("canplay", mediaArrived' in watch
    assert '"waiting"' in watch
    assert '"stalled"' in watch
    assert '"playing"' in watch
    assert "playbackStarted" in watch

    unload = source.split("function unloadMedia(", 1)[1].split(
        "function mediaItemFromPayload(", 1
    )[0]
    assert "mediaWatch.abort()" in unload
    assert "clearLoading()" in unload

    flash = source.split("function setFlash(", 1)[1].split(
        "function cancelLoadingTimer(", 1
    )[0]
    assert "if (message)" in flash
    assert "clearLoading()" in flash

    # Preloading and payload prefetch stay invisible.
    for name in (
        "warmPreloadWindow",
        "prefetchNeighbourPayloads",
        "schedulePreload",
        "schedulePayloadPrefetch",
    ):
        block = source.split(f"function {name}(", 1)[1].split("\n  function ", 1)[0]
        assert "beginLoading" not in block


def test_loading_pill_styles_sit_above_the_safe_area_under_the_bar():
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")

    rule = css.split("\n.viewer-loading {", 1)[1].split("}", 1)[0]
    assert "position: absolute" in rule
    assert "bottom: max(3.25rem, calc(2.75rem + env(safe-area-inset-bottom)))" in rule
    assert "left: max(0.75rem, env(safe-area-inset-left))" in rule
    assert "z-index: 5" in rule  # under the bar (7), handle (8) and menu (9)
    assert "pointer-events: none" in rule
    assert "background: #0b1017" in rule
    assert "border-radius" in rule

    reduced = css.split(".viewer-loading-dot {", 1)[1].split(
        "@media (prefers-reduced-motion: reduce) {", 1
    )[1].split("}", 1)[0]
    assert ".viewer-loading-dot" in reduced
    assert "animation: none" in reduced
