from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from monoke.config import get_settings
from monoke.web.app import create_app
from monoke.web.templating import TEMPLATES_DIR, asset


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MONOKE_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def test_shell_layout_and_cache_busting():
    client = TestClient(create_app())
    response = client.get("/")

    assert response.status_code == 200
    body = response.text
    assert '<a class="brand"' in body
    assert ">Creators</a>" in body
    assert ">Links</a>" in body
    assert "Data <code>" in body
    assert "Last sync" in body
    assert asset("style.css") in body
    assert asset("app.js") in body
    assert asset("htmx.min.js") in body
    assert "http://" not in body
    assert "https://" not in body


def test_static_assets_are_local():
    client = TestClient(create_app())

    css = client.get("/static/style.css")
    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")
    for category in (
        "google_drive",
        "mega",
        "direct_media",
        "video_host",
        "shortener",
        "other",
    ):
        assert f".badge-{category}" in css.text
    assert ':root[data-theme="dark"]' in css.text
    assert "img.thumb" in css.text
    assert "--thumb-filter" in css.text
    assert ".no-attachments" in css.text

    js = client.get("/static/app.js")
    assert js.status_code == 200
    assert "localStorage" in js.text
    assert "monoke-theme" in js.text
    assert "monoke-nsfw" in js.text
    assert "data-copy" in js.text

    htmx = client.get("/static/htmx.min.js")
    assert htmx.status_code == 200
    assert len(htmx.content) > 10_000
    assert "htmx" in htmx.text


def test_links_placeholder_renders():
    client = TestClient(create_app())
    response = client.get("/links")

    assert response.status_code == 200
    assert "Links" in response.text
    assert 'aria-current="page"' in response.text


def test_404_uses_template():
    client = TestClient(create_app())
    response = client.get("/does-not-exist")

    assert response.status_code == 404
    assert "Page not found" in response.text
    assert "Traceback" not in response.text
    assert asset("style.css") in response.text


def test_500_uses_template():
    app = create_app()

    @app.get("/__boom")
    def boom():
        raise RuntimeError("boom")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/__boom")

    assert response.status_code == 500
    assert "Something went wrong" in response.text
    assert "Traceback" not in response.text


def test_templates_have_no_remote_assets():
    for path in TEMPLATES_DIR.rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        assert "http://" not in text, path
        assert "https://" not in text, path
        assert "//cdn" not in text, path
        assert "unpkg" not in text, path
        assert "fonts.googleapis" not in text, path


def test_static_directory_has_vendored_htmx():
    static = Path(TEMPLATES_DIR).parent / "static" / "htmx.min.js"
    assert static.is_file()
    assert static.stat().st_size > 10_000


# --------------------------------------------------------------------------- #
# T8.5 - content-hashed assets and cache revalidation
# --------------------------------------------------------------------------- #


def test_asset_version_tracks_file_content(tmp_path, monkeypatch):
    from monoke.web import templating

    monkeypatch.setattr(templating, "STATIC_DIR", tmp_path)
    target = tmp_path / "style.css"
    target.write_text("body { color: black; }", encoding="utf-8")

    first = templating.asset("style.css")
    assert first == templating.asset("style.css")
    assert first.startswith("/static/style.css?v=")
    assert templating.ASSET_VERSION not in first

    target.write_text("body { color: white; }", encoding="utf-8")
    second = templating.asset("style.css")
    assert second != first
    assert templating.asset("style.css") == second

    # A missing file degrades to the app version rather than raising.
    missing = templating.asset("nope.css")
    assert missing == f"/static/nope.css?v={templating.ASSET_VERSION}"


def test_static_responses_revalidate():
    client = TestClient(create_app())

    response = client.get("/static/style.css")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache, must-revalidate"


def test_stylesheet_has_a_zone_reset_rule():
    from monoke.web.templating import STATIC_DIR

    css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    assert "\n.viewer-zone {" in css
    zone_rule = css.split("\n.viewer-zone {", 1)[1].split("}", 1)[0]
    for declaration in (
        "appearance: none",
        "-webkit-appearance: none",
        "background: none",
        "border: 0",
        "box-shadow: none",
        "border-radius: 0",
        "color: transparent",
        "font-size: 0",
        "line-height: 0",
        "min-width: 0",
        "min-height: 0",
    ):
        assert declaration in zone_rule
