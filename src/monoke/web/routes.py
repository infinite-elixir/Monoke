"""HTTP routes for the app shell, creators, posts and archive actions."""

from __future__ import annotations

import asyncio
import json
import math
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    Response,
    JSONResponse,
    RedirectResponse,
    StreamingResponse,
)

from .. import db
from ..config import VERSION, get_settings
from ..crawler.events import bus
from ..cumst.media import classify_media_kind
from ..cumst.urls import CumstRef, UrlError, parse_url, post_url
from ..extract import export
from ..extract.sanitize import lazy_images, sanitize_html
from .filters import (
    BOOKMARK_TAB_ORDER,
    BOOKMARK_VIEWS,
    CATEGORY_LABELS,
    CATEGORY_ORDER,
    GALLERY_KIND_LABELS,
    GALLERY_MIN_BYTES,
    GALLERY_ORIENTATIONS,
    GALLERY_PER_PAGE,
    GALLERY_SORTS,
    LINK_SORTS,
    LINKS_PER_PAGE,
    MEDIA_BADGE_LABELS,
    MEDIA_LABELS,
    PER_PAGE,
    SORTS,
    BookmarkFilters,
    GalleryFilters,
    LinkFilters,
    PostFilters,
    page_items,
)
from .tasks import (
    active_run_id,
    cancel_creator,
    cancel_run,
    is_active,
    start_crawl,
)
from .templating import STATIC_DIR, render

router = APIRouter()

_POST_PRIORITY: tuple[str, ...] = (
    "google_drive",
    "mega",
    "direct_media",
    "video_host",
    "cloud_other",
    "social",
    "shortener",
    "site_asset",
    "image",
    "other",
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _connect():
    return db.ensure_db(get_settings())


def _creator_or_404(conn, creator_id: int) -> dict[str, Any]:
    creator = db.get_creator(conn, creator_id)
    if creator is None:
        raise HTTPException(status_code=404)
    return creator


def _render_index(
    request: Request,
    *,
    error: str | None = None,
    typed_url: str = "",
    status_code: int = 200,
):
    conn = _connect()
    try:
        creators = db.list_creators(conn)
    finally:
        conn.close()
    return render(
        request,
        "index.html",
        active_page="creators",
        creators=creators,
        error=error,
        typed_url=typed_url,
        status_code=status_code,
    )


def _pager(creator_id: int, filters: PostFilters, total: int) -> dict[str, Any]:
    pages = max(1, math.ceil(total / PER_PAGE)) if total else 1
    page = min(filters.page, pages)
    links: list[dict[str, Any] | None] = []
    for number in page_items(page, pages):
        if number is None:
            links.append(None)
        else:
            links.append(
                {
                    "n": number,
                    "current": number == page,
                    "url": filters.url_for(creator_id, page=number),
                }
            )
    return {
        "page": page,
        "pages": pages,
        "total": total,
        "noun": "posts",
        "prev_url": (
            filters.url_for(creator_id, page=page - 1) if page > 1 else None
        ),
        "next_url": (
            filters.url_for(creator_id, page=page + 1) if page < pages else None
        ),
        "links": links,
    }


def _link_pager(filters: LinkFilters, total: int, base: str) -> dict[str, Any]:
    pages = max(1, math.ceil(total / LINKS_PER_PAGE)) if total else 1
    page = min(filters.page, pages)
    links: list[dict[str, Any] | None] = []
    for number in page_items(page, pages):
        if number is None:
            links.append(None)
        else:
            links.append(
                {
                    "n": number,
                    "current": number == page,
                    "url": filters.url_for(base, page=number),
                }
            )
    return {
        "page": page,
        "pages": pages,
        "total": total,
        "noun": "links",
        "prev_url": filters.url_for(base, page=page - 1) if page > 1 else None,
        "next_url": filters.url_for(base, page=page + 1) if page < pages else None,
        "links": links,
    }


def _link_base(creator_id: int | None) -> str:
    return f"/c/{int(creator_id)}/links" if creator_id is not None else "/links"


def _group_link_rows(
    rows: list[dict[str, Any]], counts: dict[str, int]
) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        buckets.setdefault(row.get("category") or "other", []).append(row)

    ordered = list(CATEGORY_ORDER)
    ordered.extend(sorted(key for key in buckets if key not in CATEGORY_ORDER))

    groups: list[dict[str, Any]] = []
    for key in ordered:
        items = buckets.get(key)
        if not items:
            continue
        groups.append(
            {
                "key": key,
                "label": CATEGORY_LABELS.get(key, key.replace("_", " ").title()),
                "count": counts.get(key, len(items)),
                "links": items,
            }
        )
    return groups


def _decorate_link_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for row in rows:
        row["href"] = f"/c/{row['creator_id']}/p/{row['post_id']}"
        row["copy_value"] = row.get("resolved_url") or row.get("url") or ""
        row["copy_state"] = "resolved" if row.get("resolved_url") else "original"
        row["copy_label"] = (
            "Copy resolved URL"
            if row.get("resolved_url")
            else "Copy original URL"
        )
    return rows


def _link_context(
    request: Request, creator_id: int | None
) -> dict[str, Any]:
    filters = LinkFilters.from_query(request.query_params)
    base = _link_base(creator_id)
    conn = _connect()
    try:
        creator = _creator_or_404(conn, creator_id) if creator_id is not None else None
        if creator_id is not None:
            # A scoped page always stays scoped; ignore a stray ?creator= value
            # without dropping the requested page number.
            filters = filters.with_changes(creator=None, page=filters.page)
            db_kwargs = filters.db_kwargs()
            db_kwargs["creator_id"] = creator_id
        else:
            db_kwargs = filters.db_kwargs()

        rows, total = db.list_explorer_links(conn, **db_kwargs)
        pages = max(1, math.ceil(total / LINKS_PER_PAGE)) if total else 1
        if filters.page > pages and total:
            filters = filters.with_changes(page=pages)
            db_kwargs = filters.db_kwargs()
            if creator_id is not None:
                db_kwargs["creator_id"] = creator_id
            rows, total = db.list_explorer_links(conn, **db_kwargs)

        rows = _decorate_link_rows(rows)
        counts = db.link_category_counts(conn, **filters.facet_kwargs())
        available = {
            row["category"] for row in db.available_link_categories(conn, creator_id)
        }
        category_keys = list(CATEGORY_ORDER)
        category_keys.extend(sorted(key for key in available if key not in CATEGORY_ORDER))

        category_filters = [
            {
                "key": key,
                "label": CATEGORY_LABELS.get(key, key.replace("_", " ").title()),
                "count": counts.get(key, 0),
                "checked": filters.selected(key),
            }
            for key in category_keys
        ]
        stats = [
            {
                "key": key,
                "label": CATEGORY_LABELS.get(key, key.replace("_", " ").title()),
                "count": counts.get(key, 0),
            }
            for key in category_keys
            if counts.get(key, 0)
        ]
        creators = db.link_creator_facets(conn) if creator_id is None else []
        unresolved = db.unresolved_shortener_count(conn, **filters.facet_kwargs())
    finally:
        conn.close()

    selected_creator = filters.creator
    if creator_id is not None:
        selected_creator = creator_id

    export_query = filters.query_string(page=1)
    export_suffix = f"?{export_query}" if export_query else ""
    groups = _group_link_rows(rows, counts) if filters.grouped else []

    return {
        "active_page": "links",
        "creator": creator,
        "creator_id": creator_id,
        "base_url": base,
        "filters": filters,
        "rows": rows,
        "groups": groups,
        "grouped": filters.grouped,
        "total": total,
        "pager": _link_pager(filters, total, base),
        "category_filters": category_filters,
        "stats": stats,
        "creators": creators,
        "selected_creator": selected_creator,
        "unresolved": unresolved,
        "sorts": LINK_SORTS,
        "csv_url": f"{base}.csv{export_suffix}",
        "json_url": f"{base}.json{export_suffix}",
        "ref_url": base,
    }


def _wants_json(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    return (
        "application/json" in accept
        or request.headers.get("x-requested-with") == "fetch"
    )


def _wants_toggle_json(request: Request) -> bool:
    """JSON for fetch/Accept clients, or ``?format=json`` for plain links."""
    return _wants_json(request) or request.query_params.get("format") == "json"


def _safe_next(value: Any, fallback: str) -> str:
    """Only allow a local absolute path as a post-action redirect target.

    Rejects scheme-relative (``//host``), absolute and backslash-smuggled URLs,
    which is the open-redirect guard the bookmark forms rely on.
    """
    text = str(value or "").strip()
    if not text or not text.startswith("/") or text.startswith("//"):
        return fallback
    if "\\" in text or "\r" in text or "\n" in text:
        return fallback
    lowered = text.lower()
    if lowered.startswith("/%2f") or lowered.startswith("/%5c"):
        return fallback
    parts = urlsplit(text)
    if parts.scheme or parts.netloc:
        return fallback
    return text


async def _form_next(request: Request) -> str | None:
    """Read the optional ``next`` field from the query string or form body."""
    value = request.query_params.get("next")
    if value is not None:
        return value
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" not in content_type and (
        "multipart/form-data" not in content_type
    ):
        return None
    form = await request.form()
    value = form.get("next")
    return str(value) if value is not None else None


def _as_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(UTC)


def _run_json(run: dict[str, Any]) -> dict[str, Any]:
    started = _as_datetime(run.get("started_at"))
    finished = _as_datetime(run.get("finished_at"))
    if started is None:
        duration = 0.0
    else:
        duration = round(((finished or datetime.now(UTC)) - started).total_seconds(), 3)
    return {
        "id": run.get("id"),
        "run_id": run.get("id"),
        "creator_id": run.get("creator_id"),
        "mode": run.get("mode"),
        "status": run.get("status"),
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "pages": run.get("pages") or 0,
        "pages_total": run.get("_total_pages") or 0,
        "posts_seen": run.get("posts_seen") or 0,
        "posts_done": run.get("posts_done") or 0,
        "posts_new": run.get("posts_new") or 0,
        "posts_updated": run.get("posts_updated") or 0,
        "links_found": run.get("links_found") or 0,
        "files_count": run.get("files_count") or 0,
        "duration_s": duration,
        "error": run.get("error"),
    }


def _apply_progress(
    run: dict[str, Any], event: dict[str, Any] | None
) -> dict[str, Any]:
    """Merge the newest bus event into a running row for pollers and reloads.

    The database only records final counts, so an in-flight run needs its
    progress from the bus. Finished runs always trust the database.
    """
    if not event or run.get("status") != "running":
        return run

    merged = dict(run)
    event_type = event.get("type")
    if event.get("done") is not None:
        if event_type == "page":
            merged["posts_seen"] = max(
                int(merged.get("posts_seen") or 0), int(event["done"])
            )
            merged["pages"] = int(event.get("page") or 0)
            if event.get("pages"):
                merged["_total_pages"] = int(event["pages"])
        else:
            merged["posts_done"] = int(event["done"])
            if event.get("total"):
                merged["posts_seen"] = int(event["total"])
    if event.get("links") is not None:
        merged["links_found"] = int(event["links"])
    return merged


def _terminal_event(run: dict[str, Any]) -> dict[str, Any]:
    status = str(run.get("status") or "failed")
    return {
        "type": "done" if status in {"done", "cancelled"} else "error",
        "run_id": run.get("id"),
        "status": status,
        "result": _run_json(run),
        "error": run.get("error"),
    }


def _sse(event: dict[str, Any]) -> bytes:
    payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    return f"data: {payload}\n\n".encode("utf-8")


def _facet_context(
    conn,
    creator_id: int,
    filters: PostFilters,
    *,
    tag_limit: int = 20,
) -> dict[str, Any]:
    counts = db.category_facets(conn, creator_id, **filters.facet_kwargs())
    categories = [
        {
            "key": key,
            "label": CATEGORY_LABELS[key],
            "count": counts.get(key, 0),
            "active": filters.category == key,
            "url": filters.url_for(
                creator_id, category=(None if filters.category == key else key)
            ),
        }
        for key in CATEGORY_ORDER
    ]

    tag_rows = db.tag_facets(
        conn, creator_id, limit=tag_limit, **filters.facet_kwargs()
    )
    tags = [
        {
            "slug": row["slug"],
            "label": row["label"] or row["slug"],
            "count": int(row["n"]),
            "active": filters.tag == row["slug"],
            "url": filters.url_for(
                creator_id, tag=(None if filters.tag == row["slug"] else row["slug"])
            ),
        }
        for row in tag_rows
    ]
    if filters.tag and not any(tag["active"] for tag in tags):
        tags.insert(
            0,
            {
                "slug": filters.tag,
                "label": filters.tag,
                "count": 0,
                "active": True,
                "url": filters.url_for(creator_id, tag=None),
            },
        )

    dead = db.dead_count(conn, creator_id, **filters.facet_kwargs())
    return {
        "category_facets": categories,
        "tag_facets": tags,
        "tag_available": db.creator_has_tags(conn, creator_id),
        "dead_facet": {
            "count": dead,
            "active": filters.dead is True,
            "url": filters.url_for(
                creator_id, dead=(None if filters.dead is True else True)
            ),
        },
        "alive_facet": {
            "active": filters.dead is False,
            "url": filters.url_for(
                creator_id, dead=(None if filters.dead is False else False)
            ),
        },
    }


def _creator_context(
    request: Request, creator_id: int, *, include_facets: bool = True
) -> dict[str, Any]:
    filters = PostFilters.from_query(request.query_params)
    conn = _connect()
    try:
        creator = _creator_or_404(conn, creator_id)
        rows, total = db.list_posts(conn, creator_id, **filters.db_kwargs())
        pages = max(1, math.ceil(total / PER_PAGE)) if total else 1
        if filters.page > pages and total:
            filters = filters.with_changes(page=pages)
            rows, total = db.list_posts(conn, creator_id, **filters.db_kwargs())

        cards = db.post_cards(conn, [int(row["id"]) for row in rows])
        for row in rows:
            row.update(cards.get(int(row["id"]), {}))
        bookmarked = db.bookmarked_post_ids(
            conn, creator_id, [int(row["id"]) for row in rows]
        )
        for row in rows:
            row["is_bookmarked"] = int(row["id"]) in bookmarked
        summary = db.creator_summary(conn, creator_id)
        running = db.active_run(conn, creator_id) if is_active(creator_id) else None
        if running is not None:
            running = _apply_progress(running, bus.latest(int(running["id"])))
        context: dict[str, Any] = {
            "active_page": "creators",
            "creator": creator,
            "posts": rows,
            "total": total,
            "summary": summary,
            "filters": filters,
            "pager": _pager(creator_id, filters, total),
            "sorts": SORTS,
            "media_labels": MEDIA_LABELS,
            "media_badge_labels": MEDIA_BADGE_LABELS,
            "ref_url": f"/c/{creator_id}",
            "active_run": running,
            "rows_query": filters.query_string(),
        }
        if include_facets:
            context.update(_facet_context(conn, creator_id, filters))
    finally:
        conn.close()
    return context


def _gallery_pager(filters: GalleryFilters, creator_id: int, total: int) -> dict[str, Any]:
    pages = max(1, math.ceil(total / GALLERY_PER_PAGE)) if total else 1
    page = min(filters.page, pages)
    links: list[dict[str, Any] | None] = []
    for number in page_items(page, pages):
        if number is None:
            links.append(None)
        else:
            links.append(
                {
                    "n": number,
                    "current": number == page,
                    "url": filters.url_for(creator_id, page=number),
                }
            )
    return {
        "page": page,
        "pages": pages,
        "total": total,
        "noun": "media",
        "prev_url": filters.url_for(creator_id, page=page - 1) if page > 1 else None,
        "next_url": (
            filters.url_for(creator_id, page=page + 1) if page < pages else None
        ),
        "links": links,
    }


def _decorate_gallery_image(image: dict[str, Any], creator_id: int) -> dict[str, Any]:
    image["post_url"] = f"/c/{creator_id}/p/{image['post_id']}"
    extension = {
        "video": "mp4",
        "gif": "gif",
        "image": "jpg",
    }.get(str(image.get("kind") or ""), "bin")
    image["filename"] = image.get("original_filename") or (
        f"{(image.get('sha256') or 'media')[:12]}.{extension}"
    )
    image["is_video"] = image.get("kind") == "video"
    image["is_gif"] = image.get("kind") == "gif"
    image["dimensions"] = (
        f"{image['width']} × {image['height']}"
        if image.get("width") and image.get("height")
        else ""
    )
    image["copies"] = int(image.get("copies") or 1)
    return image


def _gallery_sorts(filters: GalleryFilters) -> dict[str, str]:
    """Sort options for the current kind.

    Stills cannot answer "longest first", so the duration keys disappear for a
    pure image gallery. A bookmarked image URL that still asks for a duration
    sort keeps that option (selected) instead of silently misreporting the
    order the grid is actually in.
    """
    sorts = dict(GALLERY_SORTS)
    if filters.kind == "image":
        sorts = {
            key: label
            for key, label in sorts.items()
            if key in {"newest", "oldest"} or key == filters.sort
        }
    return sorts


def _gallery_context(request: Request, creator_id: int) -> dict[str, Any]:
    filters = GalleryFilters.from_query(request.query_params)
    conn = _connect()
    try:
        creator = _creator_or_404(conn, creator_id)
        stats = db.gallery_stats(conn, creator_id)
        rows, total = db.list_gallery_images(conn, creator_id, **filters.db_kwargs())

        pages = max(1, math.ceil(total / GALLERY_PER_PAGE)) if total else 1
        if filters.page > pages and total:
            filters = filters.with_changes(page=pages)
            rows, total = db.list_gallery_images(conn, creator_id, **filters.db_kwargs())

        images = [_decorate_gallery_image(row, creator_id) for row in rows]
        bookmarked_shas = db.bookmarked_media_shas(
            conn, creator_id, [image.get("sha256") for image in images]
        )
        for image in images:
            image["is_bookmarked"] = image.get("sha256") in bookmarked_shas
        tags = db.gallery_tag_facets(conn, creator_id)
        tag_available = db.creator_has_tags(conn, creator_id)
    finally:
        conn.close()

    return {
        "active_page": "creators",
        "creator": creator,
        "filters": filters,
        "images": images,
        "total": total,
        "stats": stats,
        "tags": tags,
        "pager": _gallery_pager(filters, creator_id, total),
        "orientations": GALLERY_ORIENTATIONS,
        "size_floor": GALLERY_MIN_BYTES,
        "kind_labels": GALLERY_KIND_LABELS,
        "sorts": _gallery_sorts(filters),
        "tag_available": tag_available,
        "ref_url": f"/c/{creator_id}/gallery",
    }


def _group_links(links: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for link in links:
        buckets.setdefault(link.get("category") or "other", []).append(link)

    ordered = list(_POST_PRIORITY)
    ordered.extend(sorted(key for key in buckets if key not in _POST_PRIORITY))

    groups: list[dict[str, Any]] = []
    for key in ordered:
        rows = buckets.get(key)
        if not rows:
            continue
        groups.append(
            {
                "key": key,
                "label": CATEGORY_LABELS.get(key, key.replace("_", " ").title()),
                "links": rows,
            }
        )
    return groups


def _decorate_files(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for file in files:
        try:
            variants = json.loads(file.get("variants_json") or "[]")
        except (TypeError, ValueError):
            variants = []
        if not isinstance(variants, list):
            variants = []
        file["variants"] = variants
        file["filename"] = (
            file.get("original_filename")
            or f"{(file.get('sha256') or 'file')[:12]}"
        )
        # One decision drives both the data-kind attribute and the element the
        # template renders: mime, filenames and variants can overrule a wrong
        # upstream kind (T7.4).
        kind = str(file.get("kind") or "file")
        effective = classify_media_kind(
            raw_kind=file.get("raw_kind") or kind,
            mime_type=file.get("mime_type"),
            original_filename=file.get("original_filename"),
            variants=variants,
            duration_ms=file.get("duration_ms"),
        )
        if effective == "file" and kind in {"image", "gif", "video", "audio"}:
            effective = kind
        file["kind"] = effective
        file["is_video"] = effective == "video"
        file["is_audio"] = effective == "audio"
        file["is_image"] = effective in {"image", "gif"}
    return files


# --------------------------------------------------------------------------- #
# shell
# --------------------------------------------------------------------------- #


@router.get("/favicon.ico", include_in_schema=False)
def favicon() -> Response:
    """Serve the app icon; browsers request it on every page load."""
    path = STATIC_DIR / "favicon.ico"
    if path.exists():
        return FileResponse(
            path,
            media_type="image/x-icon",
            headers={"Cache-Control": "public, max-age=86400"},
        )
    return Response(status_code=204)


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
    return _render_index(request)


@router.post("/creators", response_class=HTMLResponse, include_in_schema=False)
async def add_creator(request: Request, url: str = Form("")):
    try:
        ref = parse_url(url)
    except UrlError as exc:
        return _render_index(
            request,
            error=str(exc),
            typed_url=url,
            status_code=200,
        )

    settings = get_settings()
    conn = _connect()
    try:
        existing = db.find_creator(conn, ref.service, ref.creator_id)
        if existing is not None:
            return RedirectResponse(f"/c/{existing['id']}", status_code=303)

        with conn:
            creator_id = db.upsert_creator(
                conn, service=ref.service, service_id=ref.creator_id
            )
            run_id = db.create_run(conn, creator_id, "full")
    finally:
        conn.close()

    start_crawl(
        ref,
        settings=settings,
        creator_id=creator_id,
        run_id=run_id,
    )
    return RedirectResponse(f"/c/{creator_id}", status_code=303)


@router.get("/links", response_class=HTMLResponse, include_in_schema=False)
def links(request: Request):
    return render(request, "links.html", **_link_context(request, None))


@router.get("/c/{creator_id}/links", response_class=HTMLResponse, include_in_schema=False)
def creator_links(request: Request, creator_id: int):
    return render(request, "links.html", **_link_context(request, creator_id))


def _iter_export_rows(filters: LinkFilters, creator_id: int | None):
    conn = _connect()
    try:
        kwargs = filters.export_kwargs()
        if creator_id is not None:
            kwargs["creator_id"] = creator_id
        yield from db.iter_explorer_links(conn, **kwargs)
    finally:
        conn.close()


def _links_export(
    request: Request, creator_id: int | None, extension: str
) -> StreamingResponse:
    filters = LinkFilters.from_query(request.query_params)

    conn = _connect()
    try:
        creator = _creator_or_404(conn, creator_id) if creator_id is not None else None
    finally:
        conn.close()

    if creator is not None:
        filename = export.links_filename(
            creator["service"], creator["service_id"], extension=extension
        )
    else:
        stamp = datetime.now(UTC).strftime("%Y%m%d")
        filename = f"monoke-archive-links-{stamp}.{extension}"

    rows = _iter_export_rows(filters, creator_id)
    if extension == "csv":
        body = export.links_csv(rows)
        media_type = "text/csv; charset=utf-8"
    else:
        body = export.links_json_chunks(rows)
        media_type = "application/json"

    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(body, media_type=media_type, headers=headers)


@router.get("/links.csv", include_in_schema=False)
def links_csv(request: Request):
    return _links_export(request, None, "csv")


@router.get("/links.json", include_in_schema=False)
def links_json(request: Request):
    return _links_export(request, None, "json")


@router.get("/c/{creator_id}/links.csv", include_in_schema=False)
def creator_links_csv(request: Request, creator_id: int):
    return _links_export(request, creator_id, "csv")


@router.get("/c/{creator_id}/links.json", include_in_schema=False)
def creator_links_json(request: Request, creator_id: int):
    return _links_export(request, creator_id, "json")


# --------------------------------------------------------------------------- #
# creators
# --------------------------------------------------------------------------- #


@router.get("/c/{creator_id}", response_class=HTMLResponse, include_in_schema=False)
def creator_page(request: Request, creator_id: int):
    return render(request, "creator.html", **_creator_context(request, creator_id))


@router.get(
    "/c/{creator_id}/rows",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def creator_rows(request: Request, creator_id: int):
    context = _creator_context(request, creator_id)
    context["oob_facets"] = True
    return render(request, "partials/post_rows.html", **context)


@router.get(
    "/c/{creator_id}/gallery",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def creator_gallery(request: Request, creator_id: int):
    return render(request, "gallery.html", **_gallery_context(request, creator_id))


# --------------------------------------------------------------------------- #
# fullscreen viewer (T8.1-T8.3)
# --------------------------------------------------------------------------- #


def _viewer_index(raw: Any, count: int) -> int:
    """Clamp a requested media index into ``0..count-1``."""
    if count <= 0:
        return 0
    try:
        index = int(raw)
    except (TypeError, ValueError):
        return 0
    return max(0, min(index, count - 1))


def _viewer_return_url(creator_id: int, request: Request) -> str:
    if request.query_params.get("from") == "gallery":
        return f"/c/{int(creator_id)}/gallery"
    return f"/c/{int(creator_id)}"


def _viewer_flag(request: Request, name: str) -> bool:
    """Truthy unless the query parameter is explicitly ``0``/``false``."""
    value = request.query_params.get(name)
    if value is None:
        return True
    return value.lower() not in {"0", "false", "no", "off"}


@router.get(
    "/c/{creator_id}/viewer",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def creator_viewer(request: Request, creator_id: int):
    post_id = request.query_params.get("post") or None
    conn = _connect()
    try:
        creator = _creator_or_404(conn, creator_id)
        state = db.viewer_state(conn, creator_id, post_id)
    finally:
        conn.close()

    # An explicit post must exist and carry media; the bare route still gets a
    # friendly empty state for a creator with nothing eligible yet.
    if state is None and post_id is not None:
        raise HTTPException(status_code=404)

    item = None
    if state is not None:
        state["index"] = _viewer_index(request.query_params.get("i"), len(state["media"]))
        item = state["media"][state["index"]]

    return render(
        request,
        "viewer.html",
        active_page="creators",
        creator=creator,
        state=state,
        item=item,
        return_url=_viewer_return_url(creator_id, request),
        show_hint=_viewer_flag(request, "hint"),
        quiet=request.query_params.get("quiet") == "1",
        empty=(state is None),
    )


@router.get("/c/{creator_id}/viewer/data", include_in_schema=False)
def creator_viewer_data(creator_id: int, post: str | None = None):
    conn = _connect()
    try:
        creator = db.get_creator(conn, creator_id)
        if creator is None:
            return JSONResponse({"error": "unknown creator"}, status_code=404)
        state = db.viewer_state(conn, creator_id, post)
    finally:
        conn.close()

    if state is None:
        message = (
            "unknown post" if post else "no media in this archive"
        )
        return JSONResponse({"error": message}, status_code=404)
    return JSONResponse(state)



@router.post("/c/{creator_id}/sync", include_in_schema=False)
async def sync_creator(request: Request, creator_id: int):
    settings = get_settings()
    json_response = _wants_json(request)
    conn = _connect()
    try:
        creator = _creator_or_404(conn, creator_id)
        if is_active(creator_id):
            run_id = active_run_id(creator_id)
            if run_id is None:
                running = db.active_run(conn, creator_id)
                run_id = int(running["id"]) if running is not None else None
            if json_response and run_id is not None:
                return JSONResponse({"run_id": run_id, "active": True})
            return RedirectResponse(f"/c/{creator_id}", status_code=303)
        with conn:
            db.cancel_stale_runs(conn, creator_id)
            run_id = db.create_run(conn, creator_id, "full")
    finally:
        conn.close()

    ref = CumstRef(service=creator["service"], creator_id=creator["service_id"])
    started = start_crawl(
        ref,
        settings=settings,
        creator_id=creator_id,
        run_id=run_id,
    )
    if not started:
        # A task won the race between our check and start_crawl. Do not leave a
        # second running run behind, and point the client at the live one.
        conn = _connect()
        try:
            with conn:
                db.mark_run_cancelled(
                    conn, run_id, error="another run was already active"
                )
            running = db.active_run(conn, creator_id)
        finally:
            conn.close()
        if json_response:
            if running is not None:
                return JSONResponse({"run_id": running["id"], "active": True})
            return JSONResponse({"run_id": run_id, "active": False})
    if json_response:
        return JSONResponse({"run_id": run_id, "active": True})
    return RedirectResponse(f"/c/{creator_id}", status_code=303)


# --------------------------------------------------------------------------- #
# live run progress
# --------------------------------------------------------------------------- #

HEARTBEAT_SECONDS = 15


@router.get("/runs/{run_id}", include_in_schema=False)
def run_status(run_id: int):
    conn = _connect()
    try:
        run = db.get_run(conn, run_id)
    finally:
        conn.close()
    if run is None:
        raise HTTPException(status_code=404)
    return JSONResponse(_run_json(_apply_progress(run, bus.latest(run_id))))


@router.post("/runs/{run_id}/cancel", include_in_schema=False)
async def cancel_run_route(run_id: int):
    conn = _connect()
    try:
        run = db.get_run(conn, run_id)
        if run is None:
            raise HTTPException(status_code=404)
        if run["status"] == "running":
            if not cancel_run(run_id):
                with conn:
                    db.mark_run_cancelled(conn, run_id)
                run = db.get_run(conn, run_id)
            else:
                # The task records the final cancelled state when it unwinds.
                run = dict(run)
                run["status"] = "cancelling"
    finally:
        conn.close()
    return JSONResponse(_run_json(run))


async def _event_stream(run: dict[str, Any]):
    run_id = int(run["id"])
    if run.get("status") != "running":
        yield _sse(_terminal_event(run))
        return

    stream = bus.subscribe(run_id)
    # Keep exactly one pending __anext__ so heartbeats never cancel the
    # subscription while it is waiting on the queue.
    pending = asyncio.ensure_future(anext(stream))
    try:
        while True:
            done, _ = await asyncio.wait({pending}, timeout=HEARTBEAT_SECONDS)
            if not done:
                yield b": heartbeat\n\n"
                continue

            event = pending.result()
            pending = asyncio.ensure_future(anext(stream))
            yield _sse(event)
            if event.get("type") in {"done", "error"}:
                break
    finally:
        if not pending.done():
            pending.cancel()
        with suppress(asyncio.CancelledError, StopAsyncIteration):
            await pending
        await stream.aclose()


@router.get("/events/{run_id}", include_in_schema=False)
async def run_events(run_id: int):
    conn = _connect()
    try:
        run = db.get_run(conn, run_id)
    finally:
        conn.close()
    if run is None:
        raise HTTPException(status_code=404)

    headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(
        _event_stream(run),
        media_type="text/event-stream",
        headers=headers,
    )


def _delete_creator(creator_id: int) -> RedirectResponse:
    cancel_creator(creator_id)
    conn = _connect()
    try:
        with conn:
            db.delete_creator(conn, creator_id)
    finally:
        conn.close()
    return RedirectResponse("/", status_code=303)


@router.post("/c/{creator_id}/delete", include_in_schema=False)
def delete_creator_form(creator_id: int):
    return _delete_creator(creator_id)


@router.post("/c/{creator_id}", include_in_schema=False)
def delete_creator_post(creator_id: int):
    return _delete_creator(creator_id)


@router.delete("/c/{creator_id}", include_in_schema=False)
def delete_creator(creator_id: int):
    return _delete_creator(creator_id)


# --------------------------------------------------------------------------- #
# posts
# --------------------------------------------------------------------------- #


@router.get(
    "/c/{creator_id}/p/{post_id}",
    response_class=HTMLResponse,
    include_in_schema=False,
)
def post_page(request: Request, creator_id: int, post_id: str):
    settings = get_settings()
    filters = PostFilters.from_query(request.query_params)
    query = filters.query_string()
    suffix = f"?{query}" if query else ""

    conn = _connect()
    try:
        creator = _creator_or_404(conn, creator_id)
        post = db.get_post_by_service_id(conn, creator_id, post_id)
        if post is None:
            raise HTTPException(status_code=404)

        links = db.list_links(conn, int(post["id"]))
        files = _decorate_files(db.list_files(conn, int(post["id"])))
        bookmarked_shas = db.bookmarked_media_shas(
            conn,
            creator_id,
            [file.get("sha256") for file in files if file.get("sha256")],
        )
        for file in files:
            file["is_bookmarked"] = file.get("sha256") in bookmarked_shas
        is_bookmarked = db.post_is_bookmarked(conn, creator_id, int(post["id"]))
        tags = db.list_tags(conn, int(post["id"]))
        prev_id, next_id = db.post_neighbours(conn, creator_id, post)
    finally:
        conn.close()

    ref = CumstRef(service=creator["service"], creator_id=creator["service_id"])
    original_url = post_url(ref, post["service_id"], base=settings.site_base)
    image_host = (urlsplit(settings.img_base).hostname or "img.cum.st").lower()

    return render(
        request,
        "post.html",
        active_page="creators",
        creator=creator,
        post=post,
        links=links,
        groups=_group_links(links),
        files=files,
        tags=tags,
        caption_html=lazy_images(
            sanitize_html(post.get("caption_html")),
            allowed_hosts={image_host},
        ),
        prev_url=f"/c/{creator_id}/p/{prev_id}{suffix}" if prev_id else None,
        next_url=f"/c/{creator_id}/p/{next_id}{suffix}" if next_id else None,
        creator_url=f"/c/{creator_id}{suffix}",
        return_url=f"/c/{creator_id}/p/{post['service_id']}{suffix}",
        original_url=original_url,
        filters=filters,
        filter_query=query,
        link_count=len(links),
        is_bookmarked=is_bookmarked,
    )


# --------------------------------------------------------------------------- #
# bookmarks (T9.1 / T9.3)
# --------------------------------------------------------------------------- #


@router.post(
    "/c/{creator_id}/p/{post_service_id}/bookmark", include_in_schema=False
)
async def post_bookmark_toggle(
    request: Request, creator_id: int, post_service_id: str
):
    """Toggle a local post bookmark; 303 for forms, JSON for fetch clients."""
    conn = _connect()
    try:
        _creator_or_404(conn, creator_id)
        if db.get_post_by_service_id(conn, creator_id, post_service_id) is None:
            raise HTTPException(status_code=404)
        try:
            bookmarked = db.toggle_post_bookmark(conn, creator_id, post_service_id)
        except KeyError:
            raise HTTPException(status_code=404) from None
    finally:
        conn.close()

    if _wants_toggle_json(request):
        return JSONResponse(
            {"bookmarked": bookmarked, "kind": "post", "id": post_service_id}
        )

    fallback = f"/c/{int(creator_id)}/p/{post_service_id}"
    target = _safe_next(await _form_next(request), fallback)
    return RedirectResponse(target, status_code=303)


@router.post("/c/{creator_id}/media/{sha256}/bookmark", include_in_schema=False)
async def media_bookmark_toggle(request: Request, creator_id: int, sha256: str):
    """Toggle a local media bookmark; unknown/cross-creator shas are 404."""
    conn = _connect()
    try:
        _creator_or_404(conn, creator_id)
        try:
            bookmarked = db.toggle_media_bookmark(conn, creator_id, sha256)
        except KeyError:
            raise HTTPException(status_code=404) from None
    finally:
        conn.close()

    if _wants_toggle_json(request):
        return JSONResponse(
            {"bookmarked": bookmarked, "kind": "media", "id": sha256}
        )

    fallback = f"/c/{int(creator_id)}"
    target = _safe_next(await _form_next(request), fallback)
    return RedirectResponse(target, status_code=303)


def _bookmark_pager(
    filters: BookmarkFilters, total: int, per_page: int, noun: str
) -> dict[str, Any]:
    pages = max(1, math.ceil(total / per_page)) if total else 1
    page = min(filters.page, pages)
    links: list[dict[str, Any] | None] = []
    for number in page_items(page, pages):
        if number is None:
            links.append(None)
        else:
            links.append(
                {
                    "n": number,
                    "current": number == page,
                    "url": filters.url_for(page=number),
                }
            )
    return {
        "page": page,
        "pages": pages,
        "total": total,
        "noun": noun,
        "prev_url": filters.url_for(page=page - 1) if page > 1 else None,
        "next_url": filters.url_for(page=page + 1) if page < pages else None,
        "links": links,
    }


def _decorate_library_media(row: dict[str, Any]) -> dict[str, Any]:
    """Presentation fields for one media bookmark tile."""
    extension = {
        "video": "mp4",
        "gif": "gif",
        "image": "jpg",
        "audio": "m4a",
    }.get(str(row.get("kind") or ""), "bin")
    row["filename"] = row.get("original_filename") or (
        f"{(row.get('sha256') or 'media')[:12]}.{extension}"
    )
    row["is_video"] = row.get("kind") == "video"
    row["is_gif"] = row.get("kind") == "gif"
    row["kind_label"] = MEDIA_BADGE_LABELS.get(
        str(row.get("kind") or ""), str(row.get("kind") or "Media").title()
    )
    row["dimensions"] = (
        f"{row['width']} × {row['height']}"
        if row.get("width") and row.get("height")
        else ""
    )
    creator_id = int(row["creator_id"])
    service_id = row.get("post_service_id")
    row["post_url"] = (
        f"/c/{creator_id}/p/{service_id}" if service_id else f"/c/{creator_id}"
    )
    row["creator_url"] = f"/c/{creator_id}"
    return row


def _bookmarks_context(request: Request) -> dict[str, Any]:
    filters = BookmarkFilters.from_query(request.query_params)
    conn = _connect()
    try:
        if filters.creator is not None and db.get_creator(conn, filters.creator) is None:
            filters = filters.with_changes(creator=None, page=filters.page)

        post_limit = PER_PAGE if filters.view in {"all", "posts"} else 1
        media_limit = GALLERY_PER_PAGE if filters.view in {"all", "media"} else 1
        post_offset = filters.post_offset if filters.view == "posts" else 0
        media_offset = filters.media_offset if filters.view == "media" else 0

        posts, post_total = db.list_post_bookmarks(
            conn, creator_id=filters.creator, limit=post_limit, offset=post_offset
        )
        media, media_total = db.list_media_bookmarks(
            conn, creator_id=filters.creator, limit=media_limit, offset=media_offset
        )

        # A hand-typed page past the end lands on the last real page rather
        # than an empty grid.
        if filters.view == "posts":
            pages = max(1, math.ceil(post_total / PER_PAGE)) if post_total else 1
            if filters.page > pages and post_total:
                filters = filters.with_changes(page=pages)
                posts, post_total = db.list_post_bookmarks(
                    conn,
                    creator_id=filters.creator,
                    limit=PER_PAGE,
                    offset=filters.post_offset,
                )
        elif filters.view == "media":
            pages = (
                max(1, math.ceil(media_total / GALLERY_PER_PAGE)) if media_total else 1
            )
            if filters.page > pages and media_total:
                filters = filters.with_changes(page=pages)
                media, media_total = db.list_media_bookmarks(
                    conn,
                    creator_id=filters.creator,
                    limit=GALLERY_PER_PAGE,
                    offset=filters.media_offset,
                )

        creators = db.bookmark_creator_facets(conn)
    finally:
        conn.close()

    for post in posts:
        post["creator_url"] = f"/c/{int(post['creator_id'])}"
        post["post_url"] = (
            f"/c/{int(post['creator_id'])}/p/{post['service_id']}"
        )
    media = [_decorate_library_media(row) for row in media]

    counts = {"all": post_total + media_total, "posts": post_total, "media": media_total}
    tabs = [
        {
            "key": key,
            "label": BOOKMARK_VIEWS[key],
            "count": counts[key],
            "url": filters.url_for(view=key),
            "current": filters.view == key,
        }
        for key in BOOKMARK_TAB_ORDER
    ]

    post_pager = (
        _bookmark_pager(filters, post_total, PER_PAGE, "posts")
        if filters.view == "posts"
        else None
    )
    media_pager = (
        _bookmark_pager(filters, media_total, GALLERY_PER_PAGE, "media")
        if filters.view == "media"
        else None
    )

    return {
        "active_page": "bookmarks",
        "filters": filters,
        "tabs": tabs,
        "posts": posts,
        "media": media,
        "post_total": post_total,
        "media_total": media_total,
        "total": counts["all"],
        "show_posts": filters.view in {"all", "posts"},
        "show_media": filters.view in {"all", "media"},
        "posts_more_url": (
            filters.url_for(view="posts") if filters.view == "all" else None
        ),
        "media_more_url": (
            filters.url_for(view="media") if filters.view == "all" else None
        ),
        "post_pager": post_pager,
        "media_pager": media_pager,
        "creators": creators,
        "creator_filter": filters.creator,
        "creator_filter_active": filters.creator is not None,
        "ref_url": "/bookmarks",
        "view": filters.view,
        "view_label": BOOKMARK_VIEWS[filters.view],
    }


@router.get("/bookmarks", response_class=HTMLResponse, include_in_schema=False)
def bookmarks_page(request: Request):
    return render(request, "bookmarks.html", **_bookmarks_context(request))

