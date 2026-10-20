from __future__ import annotations

import json

import httpx
import pytest
import respx

from monoke import cli, db
from monoke.config import get_settings

BASE = "https://cum.st/api/v1/patreon/user/274608"
PROFILE_URL = f"{BASE}/profile"
POSTS_URL = f"{BASE}/posts"
CREATOR_URL = "https://cum.st/creators/patreon/274608"

PROFILE = {
    "id": "274608",
    "name": "nyl2",
    "service": "patreon",
    "displayName": "Nyl",
    "updated": 1792482836,
}


def post_summary(post_id: str) -> dict:
    return {
        "id": post_id,
        "service": "patreon",
        "title": f"Post {post_id}",
        "captionText": "",
        "added": 1,
        "published": 1,
        "bookmarked": 0,
        "attachments": [],
    }


def post_detail(post_id: str) -> dict:
    return {
        "id": post_id,
        "service": "patreon",
        "caption": '<p><a href="https://mega.nz/file/x">MEGA</a></p>',
        "captionHtml": '<p><a href="https://mega.nz/file/x">MEGA</a></p>',
        "title": f"Post {post_id}",
        "added": 1,
        "published": 1,
        "attachments": [],
        "links": [],
        "tags": [],
        "prev": None,
        "next": None,
        "creator": None,
        "linked": [],
        "revisions": [],
    }


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MONOKE_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def mock_run(router, count: int = 2):
    ids = [str(11 + index) for index in range(count)]
    router.get(PROFILE_URL).mock(return_value=httpx.Response(200, json=PROFILE))
    router.get(POSTS_URL).mock(
        return_value=httpx.Response(
            200,
            json={"total": count, "posts": [post_summary(post_id) for post_id in ids]},
        )
    )
    for post_id in ids:
        router.get(f"{BASE}/post/{post_id}").mock(
            return_value=httpx.Response(200, json=post_detail(post_id))
        )
    return ids


def test_bad_url_exits_2(data_dir, capsys):
    code = cli.main(["sync", "https://kemono.cr/patreon/user/2793377"])

    assert code == 2
    assert "old site" in capsys.readouterr().err


def test_sync_json_output(data_dir, capsys):
    with respx.mock(assert_all_called=False) as router:
        mock_run(router, 2)
        code = cli.main(["sync", CREATOR_URL, "--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert code == 0
    assert payload["status"] == "done"
    assert payload["posts_seen"] == 2
    assert payload["posts_done"] == 2
    assert payload["posts_new"] == 2
    assert payload["links_found"] == 2
    assert payload["pages"] == 1
    assert payload["errors"] == []
    assert payload["error"] is None
    assert isinstance(payload["duration_s"], float)

    conn = db.connect(get_settings())
    try:
        assert conn.execute("SELECT count(*) AS n FROM posts").fetchone()["n"] == 2
        assert conn.execute("SELECT count(*) AS n FROM links").fetchone()["n"] == 2
        run = conn.execute("SELECT * FROM runs").fetchone()
        assert run["status"] == "done"
        assert run["posts_done"] == 2
        assert run["posts_new"] == 2
    finally:
        conn.close()


def test_sync_human_report(data_dir, capsys):
    with respx.mock(assert_all_called=False) as router:
        mock_run(router, 2)
        code = cli.main(["sync", CREATOR_URL])

    out = capsys.readouterr().out
    assert code == 0
    assert "2/2 posts" in out
    assert "2 links" in out
    assert "1 pages" in out


def test_sync_limit(data_dir, capsys):
    with respx.mock(assert_all_called=False) as router:
        mock_run(router, 4)
        code = cli.main(["sync", CREATOR_URL, "--limit", "2", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload["posts_done"] == 2
    assert payload["posts_new"] == 2


def test_blocked_exits_3(data_dir, capsys):
    with respx.mock(assert_all_called=False) as router:
        router.get(PROFILE_URL).mock(
            return_value=httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text="<html>challenge</html>",
            )
        )
        code = cli.main(["sync", CREATOR_URL, "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 3
    assert payload["status"] == "failed"
    assert payload["blocked"] is True


def test_failed_run_exits_1(data_dir, capsys):
    with respx.mock(assert_all_called=False) as router:
        router.get(PROFILE_URL).mock(return_value=httpx.Response(500, text="down"))
        code = cli.main(["sync", CREATOR_URL, "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == 1
    assert payload["status"] == "failed"
    assert "500" in payload["error"]


def test_db_stats_on_empty_database(data_dir, capsys):
    code = cli.main(["db", "stats"])

    out = capsys.readouterr().out
    assert code == 0
    assert "empty" in out
    assert "database:" in out


def test_db_stats_after_sync(data_dir, capsys):
    with respx.mock(assert_all_called=False) as router:
        mock_run(router, 2)
        assert cli.main(["sync", CREATOR_URL, "--json"]) == 0

    capsys.readouterr()
    code = cli.main(["db", "stats"])

    out = capsys.readouterr().out
    assert code == 0
    assert "posts:" in out
    assert "size:" in out
