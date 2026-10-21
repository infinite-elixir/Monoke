from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from monoke import db
from monoke.config import get_settings
from monoke.crawler.events import bus
from monoke.web import routes, tasks
from monoke.web.app import create_app


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MONOKE_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    bus.clear()
    yield tmp_path
    bus.clear()
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


def make_run(conn, creator_id: int) -> int:
    with conn:
        return db.create_run(conn, creator_id, "full")


def sse_events(body: str) -> list[dict]:
    events = []
    for line in body.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: ") :]))
    return events


def test_run_status_merges_live_bus_progress(client, conn):
    creator_id = make_creator(conn)
    run_id = make_run(conn, creator_id)
    bus.publish(
        run_id,
        {"type": "progress", "phase": "posts", "done": 120, "total": 782, "links": 41},
    )

    response = client.get(f"/runs/{run_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["run_id"] == run_id
    assert payload["status"] == "running"
    assert payload["posts_done"] == 120
    assert payload["posts_seen"] == 782
    assert payload["links_found"] == 41
    assert payload["duration_s"] >= 0


def test_run_status_404(client):
    assert client.get("/runs/9999").status_code == 404


def test_events_stream_replays_history_and_closes_on_done(client, conn):
    creator_id = make_creator(conn)
    run_id = make_run(conn, creator_id)
    bus.publish(run_id, {"type": "started", "phase": "profile"})
    bus.publish(run_id, {"type": "page", "page": 1, "pages": 16, "done": 50, "total": 782})
    bus.publish(run_id, {"type": "progress", "done": 120, "total": 782, "links": 41})
    bus.publish(
        run_id,
        {
            "type": "done",
            "status": "done",
            "result": {
                "posts_done": 782,
                "posts_seen": 782,
                "links_found": 812,
                "files_count": 912,
                "duration_s": 14.2,
            },
        },
    )

    with client.stream("GET", f"/events/{run_id}") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join(response.iter_text())

    events = sse_events(body)
    assert [event["type"] for event in events] == [
        "started",
        "page",
        "progress",
        "done",
    ]
    assert events[-1]["result"]["posts_done"] == 782


def test_events_emit_terminal_event_when_history_is_gone(client, conn):
    creator_id = make_creator(conn)
    run_id = make_run(conn, creator_id)
    with conn:
        db.finish_run(
            conn,
            run_id,
            status="cancelled",
            posts_seen=20,
            posts_done=12,
            links_found=3,
            files_count=4,
            error="cancelled",
        )

    response = client.get(f"/events/{run_id}")

    assert response.status_code == 200
    events = sse_events(response.text)
    assert len(events) == 1
    assert events[0]["type"] == "done"
    assert events[0]["status"] == "cancelled"
    assert events[0]["result"]["posts_done"] == 12


def test_events_unknown_run_is_404(client):
    assert client.get("/events/9999").status_code == 404


def test_heartbeat_matches_the_ticket():
    assert routes.HEARTBEAT_SECONDS == 15


def test_sync_button_returns_json_and_creates_one_run(client, conn, monkeypatch):
    creator_id = make_creator(conn)
    state = {"active": False, "run_id": None, "starts": 0}

    def fake_is_active(cid):
        return state["active"]

    def fake_active_run_id(cid):
        return state["run_id"]

    def fake_start(ref, *, settings=None, creator_id, run_id):
        state["active"] = True
        state["run_id"] = run_id
        state["starts"] += 1
        return True

    monkeypatch.setattr(routes, "is_active", fake_is_active)
    monkeypatch.setattr(routes, "active_run_id", fake_active_run_id)
    monkeypatch.setattr(routes, "start_crawl", fake_start)

    first = client.post(
        f"/c/{creator_id}/sync", headers={"Accept": "application/json"}
    )
    assert first.status_code == 200
    first_payload = first.json()
    assert first_payload["run_id"] == 1
    assert first_payload["active"] is True

    second = client.post(
        f"/c/{creator_id}/sync", headers={"Accept": "application/json"}
    )
    assert second.status_code == 200
    assert second.json()["run_id"] == first_payload["run_id"]
    assert second.json()["active"] is True

    assert state["starts"] == 1
    assert conn.execute("SELECT count(*) AS n FROM runs").fetchone()["n"] == 1


def test_plain_sync_form_still_redirects(client, conn, monkeypatch):
    creator_id = make_creator(conn)
    monkeypatch.setattr(routes, "is_active", lambda cid: False)
    monkeypatch.setattr(
        routes,
        "start_crawl",
        lambda ref, *, settings=None, creator_id, run_id: True,
    )

    response = client.post(f"/c/{creator_id}/sync", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == f"/c/{creator_id}"


def test_cancel_orphaned_run_updates_database(client, conn):
    creator_id = make_creator(conn)
    run_id = make_run(conn, creator_id)

    response = client.post(f"/runs/{run_id}/cancel")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "cancelled"
    assert payload["finished_at"]
    run = db.get_run(conn, run_id)
    assert run["status"] == "cancelled"
    assert run["error"] == "cancelled by user"


async def test_cancel_run_cancels_the_owned_task():
    started = asyncio.Event()

    async def work():
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(work())
    await started.wait()
    tasks._run_tasks[4242] = task
    try:
        assert tasks.cancel_run(4242) is True
        with pytest.raises(asyncio.CancelledError):
            await task
    finally:
        tasks._run_tasks.pop(4242, None)


def test_creator_page_shows_active_run_progress(client, conn, monkeypatch):
    creator_id = make_creator(conn)
    run_id = make_run(conn, creator_id)
    monkeypatch.setattr(routes, "is_active", lambda cid: True)

    response = client.get(f"/c/{creator_id}")

    assert response.status_code == 200
    body = response.text
    assert "data-sync-progress" in body
    assert f'data-run-id="{run_id}"' in body
    assert "Syncing…" in body
    assert "data-sync-cancel" in body
    assert f'data-rows-url="/c/{creator_id}/rows"' in body
    assert "disabled" in body


def test_creator_page_has_hidden_progress_when_idle(client, conn):
    creator_id = make_creator(conn)

    response = client.get(f"/c/{creator_id}")

    assert response.status_code == 200
    assert "data-sync-progress" in response.text
    assert 'data-run-id=""' in response.text
    # The empty progress panel is present for JS but hidden.
    assert "data-sync-progress" in response.text
    assert "hidden" in response.text


def test_assets_include_sync_and_link_explorer_hooks(client):
    js = client.get("/static/app.js").text
    assert "EventSource" in js
    assert '"/events/"' in js
    assert "setInterval(fetchOnce, 2000)" in js
    assert "data-sync-progress" in js
    assert "data-sync-cancel" in js
    assert "data-row-href" in js

    css = client.get("/static/style.css").text
    assert ".sync-progress" in css
    assert ".sync-bar-fill" in css
    assert ".link-group-header" in css
    assert ".category-checks" in css
