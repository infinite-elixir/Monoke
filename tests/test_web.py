from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from monoke.config import get_settings
from monoke.web.app import create_app
from monoke.web.templating import TEMPLATES_DIR


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
    assert "/static/style.css?v=0.1.0" in body
    assert "/static/app.js?v=0.1.0" in body
    assert "/static/htmx.min.js?v=0.1.0" in body
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
    assert "img.thumb.nsfw" in css.text
    assert ".no-attachments" in css.text

    js = client.get("/static/app.js")
    assert js.status_code == 200
    assert "localStorage" in js.text
    assert "monoke-theme" in js.text
    assert "monoke-nsfw" in js.text
    assert "htmx:afterSwap" in js.text
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
    assert "/static/style.css?v=0.1.0" in response.text


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
