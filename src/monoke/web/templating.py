"""Jinja environment and shared template context."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.templating import Jinja2Templates

from .. import db
from ..config import VERSION, Settings, get_settings
from .filters import (
    CATEGORY_LABELS,
    CATEGORY_ORDER,
    CATEGORY_SHORT_LABELS,
    category_label,
    category_short_label,
    date_value,
)

TEMPLATES_DIR = Path(__file__).parent / "templates"
STATIC_DIR = Path(__file__).parent / "static"

# Kept for templates/tests that want the app version; asset URLs are content
# addressed below so a browser can never mix new markup with old CSS/JS (T8.5).
ASSET_VERSION = VERSION

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def asset_version(path: str) -> str:
    """Short content hash for a file under ``STATIC_DIR``.

    The bytes are hashed on every call: assets total well under 200 KB, so this
    costs microseconds and guarantees that editing a file changes its URL even
    when the app version is unchanged. A missing file falls back to the app
    version so a typo still produces a usable (404) URL instead of raising.
    """
    name = path.lstrip("/")
    try:
        digest = hashlib.sha256((STATIC_DIR / name).read_bytes()).hexdigest()
    except OSError:
        return ASSET_VERSION
    return digest[:12]


def asset(path: str) -> str:
    """URL for a static asset with a content-hash cache-busting query."""
    name = path.lstrip("/")
    return f"/static/{name}?v={asset_version(name)}"


def _as_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        stamp = value
    elif isinstance(value, (int, float)):
        try:
            stamp = datetime.fromtimestamp(float(value), UTC)
        except (OverflowError, OSError, ValueError):
            return None
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                stamp = datetime.fromtimestamp(float(text), UTC)
            except (OverflowError, OSError, ValueError):
                return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC)


def relative_time(value: Any) -> str:
    """Relative label ("2 h ago") for epoch seconds or an ISO timestamp."""
    stamp = _as_datetime(value)
    if stamp is None:
        return "never"
    seconds = int((datetime.now(UTC) - stamp).total_seconds())
    if seconds < 0:
        seconds = 0
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        return f"{seconds // 3600} h ago"
    if seconds < 30 * 86400:
        return f"{seconds // 86400} d ago"
    return stamp.strftime("%Y-%m-%d")


def short_date(value: Any) -> str:
    stamp = _as_datetime(value)
    return stamp.strftime("%Y-%m-%d") if stamp else "—"


def human_bytes(value: Any) -> str:
    if value in (None, ""):
        return "—"
    try:
        size = float(value)
    except (TypeError, ValueError):
        return "—"
    units = ("B", "KB", "MB", "GB", "TB")
    index = 0
    while size >= 1024 and index < len(units) - 1:
        size /= 1024
        index += 1
    if index == 0:
        return f"{int(size)} {units[index]}"
    return f"{size:.1f} {units[index]}"


def human_duration(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        seconds = max(0, int(round(float(value) / 1000)))
    except (TypeError, ValueError):
        return ""
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def display_url(value: Any, limit: int = 52) -> str:
    """Host plus a shortened path for display; the copy value stays complete."""
    text = str(value or "")
    if not text:
        return ""
    try:
        parts = urlsplit(text)
    except ValueError:
        return text
    if not parts.hostname:
        return text if len(text) <= limit else f"{text[: limit - 1]}…"
    path = parts.path or ""
    if len(path) > limit:
        path = f"{path[: limit - 1]}…"
    return f"{parts.hostname}{path}"


templates.env.filters.update(
    relative_time=relative_time,
    short_date=short_date,
    human_bytes=human_bytes,
    human_duration=human_duration,
    display_url=display_url,
    date_input=date_value,
)
templates.env.globals.update(
    asset=asset,
    app_version=VERSION,
    category_label=category_label,
    category_short_label=category_short_label,
    category_order=CATEGORY_ORDER,
    category_labels=CATEGORY_LABELS,
    category_short_labels=CATEGORY_SHORT_LABELS,
)


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


def _debug_mode(request: Request | None, settings: Settings) -> bool:
    """True when the server runs in debug mode.

    The flag comes from the process environment (``MONOKE_DEBUG``) or from the
    app that handled the request.
    """
    if settings.debug:
        return True
    app = getattr(request, "app", None)
    state = getattr(app, "state", None)
    return bool(getattr(state, "debug", False))


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
    context.setdefault("debug_mode", _debug_mode(request, settings))
    return templates.TemplateResponse(
        request=request,
        name=name,
        context=context,
        status_code=status_code,
        headers=headers,
    )
