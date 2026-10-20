from __future__ import annotations

import json
import socket
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from monoke import db
from monoke.config import Settings
from monoke.web.app import create_app

FIXTURES = Path(__file__).parent / "fixtures" / "cumst"


def load_fixture(name: str):
    """Load a captured cum.st API response from tests/fixtures/cumst/."""
    path = FIXTURES / name
    if not path.exists():
        raise FileNotFoundError(f"missing fixture {name!r} in {FIXTURES}")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture()
def settings(tmp_path) -> Settings:
    return Settings(data_dir=tmp_path)


@pytest.fixture()
def conn(settings):
    connection = db.connect(settings)
    db.migrate(connection)
    yield connection
    connection.close()


@pytest.fixture()
def app():
    return create_app()


@pytest.fixture()
def client(app):
    return TestClient(app)


@pytest.fixture()
def fixture():
    """Expose the fixture loader: ``payload = fixture("posts_274608_o0.json")``."""

    def _load(name: str):
        return load_fixture(name)

    return _load


@pytest.fixture()
def mock_posts():
    """Register a synthetic ``/posts`` response on a respx router."""

    def _mock(router, *, offset=0, count=50, total=782, ref=None):
        from monoke.cumst.urls import CumstRef, posts_path

        ref = ref or CumstRef(service="patreon", creator_id="274608")
        posts = [
            {
                "id": str(900000 + offset + index),
                "service": ref.service,
                "title": f"Post {offset + index}",
                "captionText": "",
                "added": 0,
                "published": 0,
                "bookmarked": 0,
                "attachments": [],
            }
            for index in range(count)
        ]
        url = f"https://cum.st/api/v1{posts_path(ref)}"
        return router.get(url).mock(
            return_value=httpx.Response(200, json={"total": total, "posts": posts})
        )

    return _mock


@pytest.fixture(autouse=True)
def _offline(request, monkeypatch):
    """Fail loudly if an offline test tries to open a real socket."""
    if request.node.get_closest_marker("live") is not None:
        return

    def deny(self, address, *args, **kwargs):
        raise RuntimeError(f"offline test attempted a network connection to {address!r}")

    monkeypatch.setattr(socket.socket, "connect", deny, raising=False)
