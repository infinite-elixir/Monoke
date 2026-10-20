"""Jinja environment and shared template context."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Mapping

from fastapi import Request
from fastapi.templating import Jinja2Templates

from .. import db
from ..config import VERSION, Settings, get_settings

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

# Bump with the app version so browsers pick up CSS/JS after a deploy.
ASSET_VERSION = VERSION

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def asset(path: str) -> str:
    """URL for a static asset with a cache-busting version query."""
    return f"/static/{path.lstrip('/')}?v={ASSET_VERSION}"


templates.env.globals.update(asset=asset, app_version=VERSION)


def last_sync(settings: Settings | None = None) -> str | None:
    """Newest creator sync timestamp, or ``None`` if the database is empty."""
    settings = settings or get_settings()
    if not settings.db_path.exists():
        return None
    try:
        conn = db.connect(settings)
    except (OSError, sqlite3.Error):
        return None
    try:
        row = conn.execute(
            "SELECT max(last_synced_at) AS ts FROM creators"
        ).fetchone()
    except sqlite3.Error:
        return None
    finally:
        conn.close()
    return row["ts"] if row and row["ts"] else None


def render(
    request: Request,
    name: str,
    *,
    status_code: int = 200,
    headers: Mapping[str, str] | None = None,
    **context: Any,
):
    """Render a template with the shell's shared context."""
    settings = get_settings()
    context.setdefault("asset_version", ASSET_VERSION)
    context.setdefault("app_version", VERSION)
    context.setdefault("data_dir", str(settings.data_dir))
    context.setdefault("last_sync", last_sync(settings))
    return templates.TemplateResponse(
        request=request,
        name=name,
        context=context,
        status_code=status_code,
        headers=headers,
    )
