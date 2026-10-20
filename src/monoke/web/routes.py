"""HTTP routes. The dashboard and archive pages land in T4.2–T4.6."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from ..config import VERSION, get_settings

router = APIRouter()


@router.get("/healthz", tags=["meta"])
def healthz() -> dict[str, object]:
    settings = get_settings()
    return {
        "ok": True,
        "version": VERSION,
        "data_dir": str(settings.data_dir),
    }


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def index() -> str:
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<title>Monoke</title></head><body>"
        f"<h1>Monoke {VERSION}</h1>"
        "<p>Scaffold is running. The dashboard arrives in T4.2.</p>"
        "<p><a href='/healthz'>/healthz</a> &middot; <a href='/docs'>/docs</a></p>"
        "</body></html>"
    )
