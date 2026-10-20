from __future__ import annotations

from fastapi.testclient import TestClient

from monoke.web.app import create_app


def test_healthz() -> None:
    client = TestClient(create_app())
    response = client.get("/healthz")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["version"] == "0.1.0"


def test_index_renders() -> None:
    client = TestClient(create_app())
    response = client.get("/")

    assert response.status_code == 200
    assert "Monoke" in response.text
