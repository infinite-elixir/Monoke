"""Tests for the debugging and UX tickets T6.1 - T6.5."""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from monoke.config import get_settings
from monoke.crawler.events import MAX_RUNS, EventBus
from monoke.web.app import configure_logging, create_app
from monoke.web.templating import STATIC_DIR, TEMPLATES_DIR

BASE_HTML = (TEMPLATES_DIR / "base.html").read_text(encoding="utf-8")
APP_JS = (STATIC_DIR / "app.js").read_text(encoding="utf-8")
STYLE_CSS = (STATIC_DIR / "style.css").read_text(encoding="utf-8")


@pytest.fixture()
def restore_logging():
    """Put the root logger back the way the test found it."""
    root = logging.getLogger()
    handlers = root.handlers[:]
    level = root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MONOKE_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


# --------------------------------------------------------------------------- #
# T6.1 - favicon
# --------------------------------------------------------------------------- #


def test_favicon_is_served_instead_of_404():
    client = TestClient(create_app())
    response = client.get("/favicon.ico")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/x-icon"
    assert response.content[:4] == b"\x00\x00\x01\x00"  # ICO header


def test_favicon_is_linked_from_the_shell():
    client = TestClient(create_app())
    body = client.get("/").text

    assert '<link rel="icon" href="/favicon.ico"' in body
    assert "favicon.svg" in body


def test_favicon_file_exists_on_disk():
    assert (STATIC_DIR / "favicon.ico").exists()
    assert (STATIC_DIR / "favicon.svg").exists()


# --------------------------------------------------------------------------- #
# T6.2 - the add-creator input must be usable
# --------------------------------------------------------------------------- #


def test_both_scripts_are_deferred():
    assert "src=\"{{ asset('app.js') }}\" defer" in BASE_HTML
    assert "src=\"{{ asset('htmx.min.js') }}\" defer" in BASE_HTML


def test_no_script_runs_before_the_dom_is_ready():
    assert "src=\"{{ asset('app.js') }}\"" in BASE_HTML
    # The only inline script is the theme/blur bootstrap in <head>.
    assert BASE_HTML.index("localStorage.getItem") < BASE_HTML.index("</head>")


def test_nothing_overlays_the_add_creator_form():
    # The merged overlay's onboarding hint is the only pointer-events:none
    # element, and it lives in the gallery overlay (T11.1). Nothing on the app
    # shell may overlay the add-creator form.
    shell_css = STYLE_CSS.split("Merged gallery lightbox", 1)[0]
    assert "pointer-events" not in shell_css
    # No fixed overlay sits above the form; the debug panel is hidden by
    # default and only exists in debug mode.
    assert 'class="debug-panel"' in BASE_HTML
    assert "hidden>" in BASE_HTML
    assert "position: fixed" not in STYLE_CSS.split(".debug-panel")[0]


def test_the_add_creator_form_is_present_and_usable():
    client = TestClient(create_app())
    body = client.get("/").text

    assert '<form class="add-creator" method="post" action="/creators"' in body
    assert 'id="creator-url"' in body
    assert 'type="submit">Archive</button>' in body


# --------------------------------------------------------------------------- #
# T6.3 - page performance
# --------------------------------------------------------------------------- #


def test_no_backdrop_filter_on_sticky_elements():
    assert "backdrop-filter" not in STYLE_CSS


def test_no_mutation_observer_scans_the_document():
    assert "MutationObserver" not in APP_JS


def test_thumbnail_blur_is_css_driven():
    assert "--thumb-filter" in STYLE_CSS
    assert 'html[data-nsfw="on"]' in STYLE_CSS
    # The blur is applied by the stylesheet, so swapped-in images need no
    # JavaScript pass over the DOM.
    assert "querySelectorAll(\"img.thumb\")" not in APP_JS


# --------------------------------------------------------------------------- #
# T6.4 - debug mode
# --------------------------------------------------------------------------- #


def test_debug_log_file_is_written(tmp_path: Path, restore_logging):
    log_path = tmp_path / "monoke.debug.log"
    configure_logging(debug=True, log_path=log_path)

    client = TestClient(create_app())
    client.get("/healthz")

    assert log_path.exists()
    text = log_path.read_text(encoding="utf-8")
    assert "GET /healthz" in text
    assert " in " in text and " ms" in text


def test_requests_are_logged_with_status_and_timing(caplog):
    app = create_app()
    client = TestClient(app)

    with caplog.at_level(logging.DEBUG, logger="monoke.web.app"):
        response = client.get("/healthz")

    assert response.status_code == 200
    messages = [record.getMessage() for record in caplog.records]
    assert any("GET /healthz -> 200" in message for message in messages)


def test_normal_mode_writes_no_debug_log(tmp_path: Path, restore_logging):
    log_path = tmp_path / "monoke.debug.log"
    configure_logging(debug=False, log_path=log_path)

    client = TestClient(create_app())
    client.get("/healthz")

    assert not log_path.exists()


def test_debug_mode_marks_the_page_for_the_client_overlay(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("MONOKE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("MONOKE_DEBUG", "1")
    get_settings.cache_clear()
    try:
        client = TestClient(create_app())
        body = client.get("/").text
    finally:
        get_settings.cache_clear()

    assert 'data-debug="1"' in body
    assert 'id="monoke-debug"' in body


def test_normal_mode_has_no_debug_overlay(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MONOKE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("MONOKE_DEBUG", raising=False)
    get_settings.cache_clear()
    try:
        client = TestClient(create_app())
        body = client.get("/").text
    finally:
        get_settings.cache_clear()

    assert 'data-debug="1"' not in body
    assert 'id="monoke-debug"' in body  # present, but hidden


def test_client_debug_capture_is_bounded():
    assert "DEBUG_MAX_LINES" in APP_JS
    assert "console.error" in APP_JS
    assert "unhandledrejection" in APP_JS
    assert "htmx:responseError" in APP_JS
    # The buffer is flushed in batches instead of rebuilding a string per
    # line, which used to grow quadratically.
    assert "textContent +=" not in APP_JS


def test_page_does_not_watch_the_dom():
    """A page that reacts to DOM changes feeds extension observers.

    Ad blockers and password managers install their own MutationObserver and
    rewrite the DOM; a page that does the same ends up in a feedback loop
    with them, which is what makes Zen blame the extension (T6.3, T6.5).
    """
    assert "MutationObserver" not in APP_JS
    assert "ResizeObserver" not in APP_JS
    assert "IntersectionObserver" not in APP_JS


def test_dom_writes_only_happen_when_the_value_changes():
    assert "function setText(" in APP_JS
    assert "setText(elapsed" in APP_JS
    # The debug panel appends a node per flush instead of replacing the log.
    assert "log.appendChild(" in APP_JS


def test_quiet_mode_is_available():
    assert "quietMode" in APP_JS
    assert 'get("quiet") === "1"' in APP_JS


def test_normal_mode_logs_to_the_console(tmp_path: Path, restore_logging):
    configure_logging(debug=False, log_path=tmp_path / "monoke.debug.log")
    handlers = logging.getLogger().handlers

    assert any(isinstance(handler, logging.StreamHandler) for handler in handlers)
    assert not any(
        isinstance(handler, logging.handlers.RotatingFileHandler)
        for handler in handlers
    )


def test_cli_serve_debug_flag(monkeypatch, data_dir):
    from monoke import cli

    captured: dict[str, object] = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr("monoke.web.app.run", fake_run)

    assert cli.main(["serve", "--debug"]) == 0
    assert captured["debug"] is True


def test_cli_serve_debug_env_var(monkeypatch, data_dir):
    from monoke import cli

    monkeypatch.setenv("MONOKE_DEBUG", "1")
    get_settings.cache_clear()
    captured: dict[str, object] = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr("monoke.web.app.run", fake_run)

    assert cli.main(["serve"]) == 0
    assert captured["debug"] is True


def test_cli_serve_without_debug(monkeypatch, data_dir):
    from monoke import cli

    monkeypatch.delenv("MONOKE_DEBUG", raising=False)
    get_settings.cache_clear()
    captured: dict[str, object] = {}

    def fake_run(**kwargs):
        captured.update(kwargs)
        return 0

    monkeypatch.setattr("monoke.web.app.run", fake_run)

    assert cli.main(["serve"]) == 0
    assert captured["debug"] is False


# --------------------------------------------------------------------------- #
# T6.5 - memory guardrails
# --------------------------------------------------------------------------- #


def test_debug_log_is_rotated(tmp_path: Path, restore_logging):
    settings = get_settings()
    log_path = tmp_path / "monoke.debug.log"
    configure_logging(debug=True, log_path=log_path)

    handler = logging.getLogger().handlers[-1]
    assert isinstance(handler, logging.handlers.RotatingFileHandler)
    assert handler.maxBytes == settings.debug_log_max_bytes
    assert handler.backupCount == settings.debug_log_backups


def test_event_bus_keeps_only_a_bounded_number_of_runs():
    instance = EventBus()

    for index in range(MAX_RUNS + 10):
        instance.publish(index, {"type": "page"})

    assert len(instance._history) <= MAX_RUNS
    assert len(instance._runs) <= MAX_RUNS


def test_event_bus_keeps_history_for_live_subscribers():
    instance = EventBus()
    instance.publish(1, {"type": "started"})

    async def check():
        stream = instance.subscribe(1)
        await anext(stream)  # replay
        for index in range(MAX_RUNS + 5):
            instance.publish(index + 2, {"type": "page"})
        assert 1 in instance._history
        await stream.aclose()

    asyncio.run(check())


def test_api_cache_can_be_pruned(data_dir):
    from monoke import db

    settings = get_settings()
    conn = db.ensure_db(settings)
    try:
        conn.execute(
            "INSERT INTO api_cache (url, fetched_at, body) VALUES (?, ?, ?)",
            ("https://cum.st/old", "2000-01-01T00:00:00+00:00", "{}"),
        )
        conn.execute(
            "INSERT INTO api_cache (url, fetched_at, body) VALUES (?, ?, ?)",
            ("https://cum.st/new", db.now_iso(), "{}"),
        )
        conn.commit()

        removed = db.cache_prune(conn, max_age_days=7)

        assert removed == 1
        assert db.cache_get(conn, "https://cum.st/old") is None
        assert db.cache_get(conn, "https://cum.st/new") is not None
    finally:
        conn.close()
