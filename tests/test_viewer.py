from __future__ import annotations

import json
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
            "bytes": 8166926 if kind == "video" else 250_000,
            "width": 1080,
            "height": 1920,
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


def embedded_state(body: str) -> dict:
    match = re.search(
        r'<script type="application/json" id="viewer-initial">(.*?)</script>',
        body,
        re.S,
    )
    assert match is not None
    return json.loads(match.group(1))


def test_default_opens_newest_media_and_skips_text_only_posts(client, conn):
    creator_id = make_creator(conn)
    oldest = add_post(conn, creator_id, 0, title="Oldest media")
    add_file(conn, oldest, sha(1))
    add_post(conn, creator_id, 1, title="Text only between")
    newest = add_post(conn, creator_id, 2, title="Newest media")
    add_file(conn, newest, sha(2), kind="video", duration_ms=10_000)
    add_post(conn, creator_id, 3, title="Text only newest")

    response = client.get(f"/c/{creator_id}/viewer")

    assert response.status_code == 200
    state = embedded_state(response.text)
    assert state["post"]["service_id"] == str(2183930591 + 2)
    assert state["post"]["title"] == "Newest media"
    assert state["post"]["media_count"] == 1
    assert state["media"][0]["kind"] == "video"
    assert state["previous_post"] is None
    assert state["next_post"] == str(2183930591 + 0)
    assert state["creator"] == {
        "id": creator_id,
        "name": "Belledelphine",
        "service": "onlyfans",
    }


def test_post_and_index_query_restores_exact_item_and_clamps(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 0)
    for index in range(3):
        add_file(conn, post_id, sha(index), position=index)
    service_id = str(2183930591)

    middle = client.get(f"/c/{creator_id}/viewer?post={service_id}&i=1")
    state = embedded_state(middle.text)
    assert state["index"] == 1
    assert state["media"][1]["index"] == 1
    assert f'src="https://e3.cum.st/media/{sha(1)}/original.jpg"' in middle.text
    assert "2 / 3" in middle.text

    too_large = client.get(f"/c/{creator_id}/viewer?post={service_id}&i=99")
    assert embedded_state(too_large.text)["index"] == 2
    assert "3 / 3" in too_large.text

    negative = client.get(f"/c/{creator_id}/viewer?post={service_id}&i=-4")
    assert embedded_state(negative.text)["index"] == 0


def test_post_without_media_404s_and_unknown_creator_404s(client, conn):
    creator_id = make_creator(conn)
    text_post = add_post(conn, creator_id, 0, title="Only words")
    service_id = str(2183930591)

    assert client.get(f"/c/{creator_id}/viewer?post={service_id}").status_code == 404
    assert client.get(f"/c/{creator_id}/viewer?post=does-not-exist").status_code == 404
    assert client.get("/c/999/viewer").status_code == 404
    assert client.get("/c/999/viewer/data").status_code == 404

    missing = client.get(
        f"/c/{creator_id}/viewer/data?post=does-not-exist"
    )
    assert missing.status_code == 404
    assert "error" in missing.json()

    # A creator with no eligible media still gets the empty state, not a 500.
    empty = client.get(f"/c/{creator_id}/viewer")
    assert empty.status_code == 200
    assert "No media in this archive" in empty.text


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
        body = client.get(
            f"/c/{creator_id}/viewer?post={2183930591 + post_number}"
        ).text
        state = embedded_state(body)
        assert state["post"]["title"] == title
        assert [item["sha256"] for item in state["media"]] == [sha(42)]


def test_viewer_media_orders_by_position_then_id(conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 0)
    add_file(conn, post_id, sha(3), position=2)
    add_file(conn, post_id, sha(1), position=0)
    add_file(conn, post_id, sha(2), position=1)

    media = db.viewer_media(conn, creator_id, "2183930591")

    assert [item["sha256"] for item in media] == [sha(1), sha(2), sha(3)]
    assert [item["index"] for item in media] == [0, 1, 2]


# --------------------------------------------------------------------------- #
# T8.2 - shell and media rendering
# --------------------------------------------------------------------------- #


def test_viewer_shell_has_one_stage_state_and_page_script(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 0), sha(1))

    body = client.get(f"/c/{creator_id}/viewer").text

    assert body.count("data-viewer-stage") == 1
    assert body.count('id="viewer-initial"') == 1
    assert "/static/viewer.js" in body
    assert 'class="viewer-page"' in body
    assert "/static/app.js" in body


def test_viewer_renders_image_gif_and_video_elements(client, conn):
    creator_id = make_creator(conn)
    image = add_post(conn, creator_id, 0, title="Image post")
    add_file(conn, image, sha(1), kind="image")
    gif = add_post(conn, creator_id, 1, title="GIF post")
    add_file(conn, gif, sha(2), kind="gif")
    video = add_post(conn, creator_id, 2, title="Video post")
    add_file(conn, video, sha(3), kind="video", duration_ms=9_000)

    image_body = client.get(f"/c/{creator_id}/viewer?post=2183930591").text
    assert f'<img class="viewer-media-el"' in image_body
    assert f'src="https://e3.cum.st/media/{sha(1)}/original.jpg"' in image_body
    assert 'alt="Image post"' in image_body

    gif_body = client.get(f"/c/{creator_id}/viewer?post=2183930592").text
    assert '<img class="viewer-media-el"' in gif_body
    assert f'src="https://e3.cum.st/media/{sha(2)}/original.gif"' in gif_body

    video_body = client.get(f"/c/{creator_id}/viewer?post=2183930593").text
    video_tag = video_body.split("<video", 1)[1].split("</video>", 1)[0]
    assert "controls" in video_tag
    assert "playsinline" in video_tag
    assert 'preload="metadata"' in video_tag
    assert f'poster="https://img.cum.st/thumbnail/{sha(3)}/preview.webp"' in video_tag
    assert f'src="https://e3.cum.st/media/{sha(3)}/original.mp4"' in video_tag
    assert "autoplay" not in video_tag


def test_viewer_marks_missing_media_unavailable(client, conn):
    creator_id = make_creator(conn)
    post_id = add_post(conn, creator_id, 0)
    add_file(conn, post_id, sha(1), url="", thumb_url=None)

    body = client.get(f"/c/{creator_id}/viewer").text

    assert "Media unavailable" in body
    assert "Open the post" in body


def test_viewer_script_is_not_loaded_on_other_pages(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 0), sha(1))

    for path in ("/", f"/c/{creator_id}", f"/c/{creator_id}/gallery"):
        body = client.get(path).text
        assert "/static/app.js" in body
        assert "/static/viewer.js" not in body


def test_viewer_entry_links_and_return_url(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 0), sha(1))

    creator_body = client.get(f"/c/{creator_id}").text
    assert f'href="/c/{creator_id}/viewer"' in creator_body
    assert "View latest" in creator_body

    gallery_body = client.get(f"/c/{creator_id}/gallery").text
    assert f'href="/c/{creator_id}/viewer?from=gallery"' in gallery_body
    assert "Open viewer" in gallery_body

    viewer_body = client.get(f"/c/{creator_id}/viewer?from=gallery").text
    assert f'data-return-url="/c/{creator_id}/gallery"' in viewer_body


# --------------------------------------------------------------------------- #
# T8.3 - invisible directional controls
# --------------------------------------------------------------------------- #


def test_viewer_renders_four_invisible_zone_buttons(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 0), sha(1))

    body = client.get(f"/c/{creator_id}/viewer").text

    assert body.count('class="viewer-zone') == 4
    for action in ("prev-image", "next-image", "prev-post", "next-post"):
        assert f'data-viewer-action="{action}"' in body
    assert 'aria-label="Previous media"' in body
    assert 'aria-label="Next media"' in body
    assert 'aria-label="Newer post"' in body
    assert 'aria-label="Older post"' in body
    # No toolbar or visible navigation text is rendered.
    assert "viewer-toolbar" not in body


# --------------------------------------------------------------------------- #
# T8.4 - first-run hint, focus and lifecycle
# --------------------------------------------------------------------------- #


def test_viewer_shows_first_run_hint_with_all_four_directions(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 0), sha(1))

    body = client.get(f"/c/{creator_id}/viewer").text

    assert body.count("data-viewer-hint") == 1
    assert 'class="viewer-hint"' in body
    assert 'role="status"' in body
    for label in ("Newer post", "Older post", "Previous image", "Next image"):
        assert f'<span class="viewer-hint-label">{label}</span>' in body
    assert "Esc to close · F for fullscreen · first move hides this" in body


def test_hint_zero_suppresses_hint_and_quiet_skips_fade(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 0), sha(1))

    quiet_hint = client.get(f"/c/{creator_id}/viewer?hint=0").text
    assert "data-viewer-hint" not in quiet_hint
    assert "viewer-hint-grid" not in quiet_hint

    quiet = client.get(f"/c/{creator_id}/viewer?quiet=1").text
    assert 'class="viewer viewer-quiet"' in quiet

    normal = client.get(f"/c/{creator_id}/viewer?quiet=0").text
    assert "viewer-quiet" not in normal


def test_viewer_close_button_is_accessible_and_not_persistent(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 0), sha(1))

    body = client.get(f"/c/{creator_id}/viewer").text

    close_tag = re.search(r"<button[^>]*data-viewer-close[^>]*>", body)
    assert close_tag is not None
    tag = close_tag.group(0)
    assert 'aria-label="Close viewer"' in tag
    assert 'class="viewer-close"' in tag
    # First focusable element inside the viewer.
    assert body.index("data-viewer-close") < body.index("data-viewer-stage")


def test_viewer_root_is_a_focusable_labelled_dialog(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 0), sha(1))

    body = client.get(f"/c/{creator_id}/viewer").text
    main_tag = re.search(r'<main id="viewer"[^>]*>', body)
    assert main_tag is not None
    tag = main_tag.group(0)

    assert 'role="dialog"' in tag
    assert 'aria-modal="true"' in tag
    assert 'aria-label="Media viewer"' in tag
    assert 'tabindex="-1"' in tag


def test_viewer_announces_post_rank_and_media_post_total(client, conn):
    creator_id = make_creator(conn)
    oldest = add_post(conn, creator_id, 0)
    add_file(conn, oldest, sha(1))
    add_post(conn, creator_id, 1)  # text-only: not a media post
    middle = add_post(conn, creator_id, 2)
    add_file(conn, middle, sha(2))
    newest = add_post(conn, creator_id, 3)
    add_file(conn, newest, sha(3))

    state = embedded_state(client.get(f"/c/{creator_id}/viewer").text)
    assert state["post_rank"] == 1
    assert state["media_post_count"] == 3

    middle_state = embedded_state(
        client.get(f"/c/{creator_id}/viewer?post=2183930593").text
    )
    assert middle_state["post_rank"] == 2
    assert middle_state["media_post_count"] == 3

    payload = client.get(f"/c/{creator_id}/viewer/data?post=2183930591").json()
    assert payload["post_rank"] == 3
    assert payload["media_post_count"] == 3


# --------------------------------------------------------------------------- #
# T8.6 - horizontal navigation crosses into neighbouring posts
# --------------------------------------------------------------------------- #


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

    # Right-at-the-edge lands on item 0 of the older post; left-at-the-edge
    # lands on the last item of the newer post. Both payloads must expose those
    # exact endpoints, because the JS clamps against the target's own count.
    older_payload = client.get(
        f"/c/{creator_id}/viewer/data?post={oldest}"
    ).json()
    assert [item["index"] for item in older_payload["media"]] == [0, 1, 2]
    assert older_payload["media"][0]["sha256"] == sha(0)
    assert older_payload["media"][-1]["sha256"] == sha(2)

    newer_payload = client.get(
        f"/c/{creator_id}/viewer/data?post={newest}"
    ).json()
    assert [item["index"] for item in newer_payload["media"]] == [0, 1, 2]
    assert newer_payload["media"][0]["sha256"] == sha(20)
    assert newer_payload["media"][-1]["sha256"] == sha(22)


# --------------------------------------------------------------------------- #
# T8.7 - neighbouring image preload window
# --------------------------------------------------------------------------- #


def test_preload_window_covers_radius_and_filters_unsafe_items():
    import json
    import shutil
    import subprocess
    import textwrap

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    source = (STATIC_DIR / "viewer.js").read_text(encoding="utf-8")
    block = source.split("// BEGIN preload-window", 1)[1].split(
        "// END preload-window", 1
    )[0]
    script = (
        textwrap.dedent(block)
        + textwrap.dedent(
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
            """
        )
    )
    result = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)

    assert payload["radius"] == 3
    assert payload["max"] == 6
    assert payload["middle"] == [2, 3, 4, 6, 7, 8]
    assert payload["first"] == [1, 2, 3]
    assert payload["last"] == [6, 7, 8]
    assert payload["failed"] == [2, 4, 6, 7, 8]
    assert payload["kinds"] == [3, 8]


def test_viewer_script_passes_node_syntax_check():
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    script = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "monoke"
        / "web"
        / "static"
        / "viewer.js"
    )
    result = subprocess.run(
        [node, "--check", str(script)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr



# --------------------------------------------------------------------------- #
# T8.5 - content-hashed assets and fullscreen media
# --------------------------------------------------------------------------- #


def test_viewer_links_content_hashed_assets_that_match_disk(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 0), sha(1))

    body = client.get(f"/c/{creator_id}/viewer").text

    css_url = asset("style.css")
    js_url = asset("viewer.js")
    assert f'href="{css_url}"' in body
    assert f'src="{js_url}"' in body

    css = client.get(css_url)
    assert css.status_code == 200
    assert css.headers["cache-control"] == "no-cache, must-revalidate"
    assert css.text == (STATIC_DIR / "style.css").read_text(encoding="utf-8")

    script = client.get(js_url)
    assert script.status_code == 200
    assert script.text == (STATIC_DIR / "viewer.js").read_text(encoding="utf-8")


def test_viewer_stylesheet_makes_media_fill_and_centre_the_viewport():
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")

    viewer_rule = css.split("\n.viewer {", 1)[1].split("}", 1)[0]
    assert "position: fixed" in viewer_rule
    assert "inset: 0" in viewer_rule
    assert "width: 100vw" in viewer_rule
    assert "height: 100dvh" in viewer_rule

    media_rule = css.split("\n.viewer-media {", 1)[1].split("}", 1)[0]
    assert "width: 100%" in media_rule
    assert "height: 100%" in media_rule
    assert "align-items: center" in media_rule
    assert "justify-content: center" in media_rule
    assert "min-width: 0" in media_rule
    assert "min-height: 0" in media_rule

    element_rule = css.split("\n.viewer-media-el {", 1)[1].split("}", 1)[0]
    assert "max-width: 100%" in element_rule
    assert "max-height: 100%" in element_rule
    assert "object-fit: contain" in element_rule


# --------------------------------------------------------------------------- #
# T8.8 - cross-post preload and fullscreen control
# --------------------------------------------------------------------------- #


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
    assert all(
        item["post_service_id"] == str(2183930593) for item in previous
    )
    assert all(item["post_service_id"] == str(2183930591) for item in following)


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
    assert len(neighbours["previous"]) == db.NEIGHBOUR_PRELOAD_LIMIT
    assert len(neighbours["next"]) == db.NEIGHBOUR_PRELOAD_LIMIT

    payload = client.get(
        f"/c/{creator_id}/viewer/data?post=2183930592"
    ).json()
    assert payload["neighbour_images"] == neighbours

    newest = db.viewer_state(conn, creator_id, str(2183930593))
    oldest = db.viewer_state(conn, creator_id, str(2183930591))
    assert newest["neighbour_images"]["previous"] == []
    assert newest["neighbour_images"]["next"]
    assert oldest["neighbour_images"]["previous"]
    assert oldest["neighbour_images"]["next"] == []


def test_preload_plan_keeps_both_edges_and_prunes_stale_entries():
    import json
    import shutil
    import subprocess
    import textwrap

    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    source = (STATIC_DIR / "viewer.js").read_text(encoding="utf-8")
    block = source.split("// BEGIN preload-window", 1)[1].split(
        "// END preload-plan", 1
    )[0]
    script = (
        textwrap.dedent(block)
        + textwrap.dedent(
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
            var failedPlan = buildPreloadPlan(
              media(10), 0, neighbours, 12, failed
            );

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

            var filtered = preloadCandidates(
              media(10),
              0,
              {
                previous: [
                  { kind: "gif", url: "https://e3.cum.st/gif" },
                  { kind: "image", url: "data:image/png;base64,AAAA" }
                ],
                next: [image("n0")]
              }
            );

            process.stdout.write(JSON.stringify({
              totalMax: PRELOAD_TOTAL_MAX,
              candidates: candidates,
              plan: plan,
              failedPlan: failedPlan,
              map: Array.from(map.keys()),
              failures: Array.from(failures),
              filtered: filtered
            }));
            """
        )
    )
    result = subprocess.run([node, "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)

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
    assert payload["filtered"] == [
        "https://e3.cum.st/m1",
        "https://e3.cum.st/m2",
        "https://e3.cum.st/m3",
        "https://e3.cum.st/n0",
    ]


def test_jump_prefers_cached_payload_without_clearing_preloads():
    source = (STATIC_DIR / "viewer.js").read_text(encoding="utf-8")
    jump_block = source.split("function jump(", 1)[1].split(
        "function requestFullscreen", 1
    )[0]

    assert "payloadCache.get(String(serviceId))" in jump_block
    assert "applyPayload(cached" in jump_block
    assert "clearPreloadCache()" not in jump_block
    assert "prunePreloadMap" in source


def test_viewer_has_auto_hiding_fullscreen_control(client, conn):
    creator_id = make_creator(conn)
    add_file(conn, add_post(conn, creator_id, 0), sha(1))

    body = client.get(f"/c/{creator_id}/viewer").text

    tags = re.findall(r"<button[^>]*data-viewer-fullscreen[^>]*>", body)
    assert len(tags) == 1
    tag = tags[0]
    assert 'class="viewer-fullscreen"' in tag
    assert 'type="button"' in tag
    assert 'aria-pressed="false"' in tag
    assert 'aria-label="Enter fullscreen"' in tag
    assert ">Fullscreen</button>" in body
    assert (
        body.index("data-viewer-close")
        < body.index("data-viewer-fullscreen")
        < body.index("data-viewer-stage")
    )


def test_viewer_fullscreen_script_and_styles_are_transient():
    source = (STATIC_DIR / "viewer.js").read_text(encoding="utf-8")
    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")

    assert 'document.addEventListener("fullscreenchange", onFullscreenChange)' in source
    assert "Fullscreen is not available in this browser." in source
    assert 'fullscreenButton.classList.add("is-revealed")' in source
    assert "2500" in source
    assert "updateCachedBookmarks" in source

    rule = css.split("\n.viewer-fullscreen {", 1)[1].split("}", 1)[0]
    assert "position: absolute" in rule
    assert "top: max(3rem" in rule
    assert "right: max(0.75rem" in rule
    assert "opacity: 0" in rule
    assert "pointer-events: none" in rule
    revealed = css.split(
        "\n.viewer-fullscreen.is-revealed,\n.viewer-fullscreen:focus-visible {",
        1,
    )[1].split("}", 1)[0]
    assert "opacity: 1" in revealed
    assert "pointer-events: auto" in revealed
    assert (
        "@media (prefers-reduced-motion: reduce) {\n"
        "  .viewer-fullscreen {\n"
        "    transition: none;\n"
        "  }\n"
        "}"
    ) in css
