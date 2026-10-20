"""HTTP routes for the app shell and archive pages."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ..config import VERSION, get_settings
from .templating import render

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
def index(request: Request):
    return render(request, "index.html", active_page="creators")


@router.get("/links", response_class=HTMLResponse, include_in_schema=False)
def links(request: Request):
    return render(request, "links.html", active_page="links")
