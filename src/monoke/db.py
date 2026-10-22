"""SQLite access: schema migrations and a thin repository layer.

Everything that touches the database goes through this module. Repositories take a
``sqlite3.Connection`` and return plain dicts so callers never depend on sqlite3.Row.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import Settings, get_settings
from .cumst.media import classify_media_kind

SCHEMA_VERSION = 6

# Each migration is a tuple of individual statements so we can apply it inside one
# transaction (executescript would commit implicitly).
_V1: tuple[str, ...] = (
    """
    CREATE TABLE creators (
      id INTEGER PRIMARY KEY,
      service TEXT NOT NULL,
      service_id TEXT NOT NULL,
      name TEXT,
      display_name TEXT,
      avatar_url TEXT,
      header_url TEXT,
      indexed_at INTEGER,
      updated_at INTEGER,
      content_updated_at INTEGER,
      bookmarked INTEGER,
      post_count INTEGER,
      last_synced_at TEXT,
      UNIQUE(service, service_id)
    )
    """,
    """
    CREATE TABLE posts (
      id INTEGER PRIMARY KEY,
      creator_id INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
      service_id TEXT NOT NULL,
      service TEXT NOT NULL,
      title TEXT,
      caption_html TEXT,
      caption_text TEXT,
      published INTEGER,
      added INTEGER,
      bookmarked INTEGER,
      prev_id TEXT,
      next_id TEXT,
      revisions_count INTEGER NOT NULL DEFAULT 0,
      linked_count INTEGER NOT NULL DEFAULT 0,
      raw_json TEXT,
      fetched_at TEXT,
      missing_upstream INTEGER NOT NULL DEFAULT 0,
      UNIQUE(creator_id, service_id)
    )
    """,
    "CREATE INDEX IF NOT EXISTS posts_published ON posts(creator_id, published DESC)",
    "CREATE INDEX IF NOT EXISTS posts_missing ON posts(creator_id, missing_upstream)",
    """
    CREATE TABLE files (
      id INTEGER PRIMARY KEY,
      post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
      position INTEGER,
      sha256 TEXT NOT NULL,
      kind TEXT NOT NULL,
      mime_type TEXT,
      bytes INTEGER,
      width INTEGER,
      height INTEGER,
      duration_ms INTEGER,
      original_filename TEXT,
      storage_key TEXT,
      variants_json TEXT,
      url TEXT NOT NULL,
      thumb_url TEXT,
      UNIQUE(post_id, sha256)
    )
    """,
    "CREATE INDEX IF NOT EXISTS files_post ON files(post_id)",
    """
    CREATE TABLE links (
      id INTEGER PRIMARY KEY,
      post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
      url TEXT NOT NULL,
      normalized_url TEXT NOT NULL,
      label TEXT,
      category TEXT NOT NULL,
      origin TEXT NOT NULL,
      resolved_url TEXT,
      UNIQUE(post_id, normalized_url)
    )
    """,
    "CREATE INDEX IF NOT EXISTS links_category ON links(category)",
    "CREATE INDEX IF NOT EXISTS links_post ON links(post_id)",
    """
    CREATE TABLE tags (
      post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
      slug TEXT NOT NULL,
      label TEXT,
      PRIMARY KEY (post_id, slug)
    )
    """,
    "CREATE INDEX IF NOT EXISTS tags_slug ON tags(slug)",
    """
    CREATE TABLE runs (
      id INTEGER PRIMARY KEY,
      creator_id INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
      mode TEXT NOT NULL,
      status TEXT NOT NULL,
      started_at TEXT,
      finished_at TEXT,
      pages INTEGER NOT NULL DEFAULT 0,
      posts_seen INTEGER NOT NULL DEFAULT 0,
      posts_new INTEGER NOT NULL DEFAULT 0,
      posts_updated INTEGER NOT NULL DEFAULT 0,
      links_found INTEGER NOT NULL DEFAULT 0,
      files_count INTEGER NOT NULL DEFAULT 0,
      error TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS runs_creator ON runs(creator_id, started_at DESC)",
    """
    CREATE TABLE api_cache (
      url TEXT PRIMARY KEY,
      fetched_at TEXT NOT NULL,
      body TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE link_resolutions (
      url TEXT PRIMARY KEY,
      final_url TEXT,
      resolved_at TEXT
    )
    """,
)

_V2: tuple[str, ...] = (
    "ALTER TABLE runs ADD COLUMN posts_done INTEGER NOT NULL DEFAULT 0",
    "UPDATE runs SET posts_done = posts_new + posts_updated WHERE posts_done = 0",
)

# Link explorer facets ask for unresolved shorteners and sorted category pages.
_V3: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS links_category_post ON links(category, post_id)",
    "CREATE INDEX IF NOT EXISTS links_resolved ON links(category, resolved_url)",
)

# The gallery filters by kind before it joins posts; without this index a
# 782-post archive scans every file row (T4.7).
_V4: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS files_kind ON files(kind, post_id)",
)

# Normalized media kinds (T7.1): keep the upstream value for provenance while
# files.kind becomes the value every consumer trusts.
_V5: tuple[str, ...] = (
    "ALTER TABLE files ADD COLUMN raw_kind TEXT",
    "UPDATE files SET raw_kind = kind WHERE raw_kind IS NULL",
)

# Local bookmarks (T9.1). They are deliberately separate from the upstream
# ``creators.bookmarked`` / ``posts.bookmarked`` provenance columns, which the
# crawler overwrites on every sync.
#
# Media bookmarks key on ``(creator_id, sha256)`` rather than ``files.id``:
# ``replace_files`` deletes and re-inserts rows on every sync, and the same file
# can appear in several posts. There is intentionally no foreign key to
# ``files``; a bookmark survives when its attachment disappears upstream and is
# surfaced as "missing" by ``list_media_bookmarks``.
_V6: tuple[str, ...] = (
    """
    CREATE TABLE post_bookmarks (
      id INTEGER PRIMARY KEY,
      creator_id INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
      post_id INTEGER NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
      created_at TEXT NOT NULL,
      UNIQUE(post_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS post_bookmarks_creator
      ON post_bookmarks(creator_id, created_at DESC, id DESC)
    """,
    """
    CREATE TABLE media_bookmarks (
      id INTEGER PRIMARY KEY,
      creator_id INTEGER NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
      sha256 TEXT NOT NULL,
      created_at TEXT NOT NULL,
      UNIQUE(creator_id, sha256)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS media_bookmarks_creator
      ON media_bookmarks(creator_id, created_at DESC, id DESC)
    """,
)

MIGRATIONS: tuple[tuple[str, ...], ...] = (_V1, _V2, _V3, _V4, _V5, _V6)


# --------------------------------------------------------------------------- #
# connection / migrations
# --------------------------------------------------------------------------- #


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(settings: Settings | None = None) -> sqlite3.Connection:
    """Open (and create if needed) the Monoke database."""
    settings = settings or get_settings()
    settings.ensure_dirs()
    conn = sqlite3.connect(settings.db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    """Apply pending migrations. Returns the resulting schema version."""
    current = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if current > len(MIGRATIONS):
        raise RuntimeError(
            f"database schema v{current} is newer than this build (v{len(MIGRATIONS)})"
        )
    for index in range(current, len(MIGRATIONS)):
        with conn:
            for statement in MIGRATIONS[index]:
                conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {index + 1}")
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def ensure_db(settings: Settings | None = None) -> sqlite3.Connection:
    conn = connect(settings)
    migrate(conn)
    return conn


def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _rows(rows: Sequence[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


# --------------------------------------------------------------------------- #
# creators
# --------------------------------------------------------------------------- #


def upsert_creator(
    conn: sqlite3.Connection,
    *,
    service: str,
    service_id: str,
    name: str | None = None,
    display_name: str | None = None,
    avatar_url: str | None = None,
    header_url: str | None = None,
    indexed_at: int | None = None,
    updated_at: int | None = None,
    content_updated_at: int | None = None,
    bookmarked: int | None = None,
    post_count: int | None = None,
    last_synced_at: str | None = None,
) -> int:
    row = conn.execute(
        "SELECT id FROM creators WHERE service = ? AND service_id = ?",
        (service, service_id),
    ).fetchone()

    if row is None:
        cur = conn.execute(
            """
            INSERT INTO creators (
              service, service_id, name, display_name, avatar_url, header_url,
              indexed_at, updated_at, content_updated_at, bookmarked, post_count,
              last_synced_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                service,
                service_id,
                name,
                display_name,
                avatar_url,
                header_url,
                indexed_at,
                updated_at,
                content_updated_at,
                bookmarked,
                post_count,
                last_synced_at,
            ),
        )
        return int(cur.lastrowid)

    conn.execute(
        """
        UPDATE creators SET
          name = COALESCE(?, name),
          display_name = COALESCE(?, display_name),
          avatar_url = COALESCE(?, avatar_url),
          header_url = COALESCE(?, header_url),
          indexed_at = COALESCE(?, indexed_at),
          updated_at = COALESCE(?, updated_at),
          content_updated_at = COALESCE(?, content_updated_at),
          bookmarked = COALESCE(?, bookmarked),
          post_count = COALESCE(?, post_count),
          last_synced_at = COALESCE(?, last_synced_at)
        WHERE id = ?
        """,
        (
            name,
            display_name,
            avatar_url,
            header_url,
            indexed_at,
            updated_at,
            content_updated_at,
            bookmarked,
            post_count,
            last_synced_at,
            row["id"],
        ),
    )
    return int(row["id"])


def get_creator(conn: sqlite3.Connection, creator_id: int) -> dict[str, Any] | None:
    return _dict(conn.execute("SELECT * FROM creators WHERE id = ?", (creator_id,)).fetchone())


def find_creator(
    conn: sqlite3.Connection, service: str, service_id: str
) -> dict[str, Any] | None:
    return _dict(
        conn.execute(
            "SELECT * FROM creators WHERE service = ? AND service_id = ?",
            (service, service_id),
        ).fetchone()
    )


def list_creators(
    conn: sqlite3.Connection, *, limit: int = 100, offset: int = 0
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT c.*,
          (SELECT count(*) FROM posts p WHERE p.creator_id = c.id) AS post_rows,
          (SELECT count(*) FROM links l
             JOIN posts p ON p.id = l.post_id WHERE p.creator_id = c.id) AS link_count,
          (SELECT count(*) FROM files f
             JOIN posts p ON p.id = f.post_id WHERE p.creator_id = c.id) AS file_count,
          (SELECT count(DISTINCT f.sha256) FROM files f
             JOIN posts p ON p.id = f.post_id
             WHERE p.creator_id = c.id AND f.kind = 'video') AS video_count,
          (SELECT count(*) FROM links l
             JOIN posts p ON p.id = l.post_id
             WHERE p.creator_id = c.id AND l.category = 'google_drive') AS drive_count,
          (SELECT count(*) FROM links l
             JOIN posts p ON p.id = l.post_id
             WHERE p.creator_id = c.id AND l.category = 'mega') AS mega_count,
          (SELECT count(*) FROM links l
             JOIN posts p ON p.id = l.post_id
             WHERE p.creator_id = c.id AND l.category = 'direct_media') AS media_count,
          (SELECT f.thumb_url FROM files f
             JOIN posts p ON p.id = f.post_id
             WHERE p.creator_id = c.id AND f.thumb_url IS NOT NULL
             ORDER BY COALESCE(p.published, 0) DESC, p.id DESC, f.id DESC
             LIMIT 1) AS thumb_url
        FROM creators c
        ORDER BY COALESCE(c.last_synced_at, '') DESC, c.id DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()
    return _rows(rows)


def creator_summary(conn: sqlite3.Connection, creator_id: int) -> dict[str, Any]:
    """Aggregate counts for the creator page header and cards.

    Media counts are distinct ``sha256`` values so the same attachment in two
    posts is reported once; link counts stay per-link rows.
    """
    row = conn.execute(
        """
        SELECT
          (SELECT count(*) FROM posts p WHERE p.creator_id = ?) AS posts,
          (SELECT count(*) FROM links l JOIN posts p ON p.id = l.post_id
             WHERE p.creator_id = ?) AS links,
          (SELECT count(*) FROM files f JOIN posts p ON p.id = f.post_id
             WHERE p.creator_id = ?) AS files,
          (SELECT count(DISTINCT f.sha256) FROM files f JOIN posts p ON p.id = f.post_id
             WHERE p.creator_id = ? AND f.kind = 'image') AS images,
          (SELECT count(DISTINCT f.sha256) FROM files f JOIN posts p ON p.id = f.post_id
             WHERE p.creator_id = ? AND f.kind = 'video') AS videos,
          (SELECT count(DISTINCT f.sha256) FROM files f JOIN posts p ON p.id = f.post_id
             WHERE p.creator_id = ? AND f.kind = 'gif') AS gifs,
          (SELECT count(DISTINCT f.sha256) FROM files f JOIN posts p ON p.id = f.post_id
             WHERE p.creator_id = ? AND f.kind = 'audio') AS audio,
          (SELECT count(DISTINCT f.sha256) FROM files f JOIN posts p ON p.id = f.post_id
             WHERE p.creator_id = ?
               AND f.kind NOT IN ('image', 'video', 'gif', 'audio')) AS other_files,
          (SELECT count(*) FROM links l JOIN posts p ON p.id = l.post_id
             WHERE p.creator_id = ? AND l.category = 'google_drive') AS drive,
          (SELECT count(*) FROM links l JOIN posts p ON p.id = l.post_id
             WHERE p.creator_id = ? AND l.category = 'mega') AS mega,
          (SELECT count(*) FROM links l JOIN posts p ON p.id = l.post_id
             WHERE p.creator_id = ? AND l.category = 'direct_media') AS media,
          (SELECT count(*) FROM posts p
             WHERE p.creator_id = ? AND p.missing_upstream = 1) AS dead
        """,
        (creator_id,) * 12,
    ).fetchone()
    return dict(row) if row is not None else {}


def delete_creator(conn: sqlite3.Connection, creator_id: int) -> bool:
    cur = conn.execute("DELETE FROM creators WHERE id = ?", (creator_id,))
    return cur.rowcount > 0


# --------------------------------------------------------------------------- #
# posts
# --------------------------------------------------------------------------- #


def upsert_post(
    conn: sqlite3.Connection,
    *,
    creator_id: int,
    service: str,
    service_id: str,
    title: str | None = None,
    caption_html: str | None = None,
    caption_text: str | None = None,
    published: int | None = None,
    added: int | None = None,
    bookmarked: int | None = None,
    prev_id: str | None = None,
    next_id: str | None = None,
    revisions_count: int | None = 0,
    linked_count: int | None = 0,
    raw_json: str | None = None,
    fetched_at: str | None = None,
    missing_upstream: int | None = 0,
) -> int:
    row = conn.execute(
        "SELECT id FROM posts WHERE creator_id = ? AND service_id = ?",
        (creator_id, service_id),
    ).fetchone()

    if row is None:
        cur = conn.execute(
            """
            INSERT INTO posts (
              creator_id, service_id, service, title, caption_html, caption_text,
              published, added, bookmarked, prev_id, next_id, revisions_count,
              linked_count, raw_json, fetched_at, missing_upstream
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                creator_id,
                service_id,
                service,
                title,
                caption_html,
                caption_text,
                published,
                added,
                bookmarked,
                prev_id,
                next_id,
                revisions_count,
                linked_count,
                raw_json,
                fetched_at,
                missing_upstream,
            ),
        )
        return int(cur.lastrowid)

    conn.execute(
        """
        UPDATE posts SET
          service = ?,
          title = COALESCE(?, title),
          caption_html = COALESCE(?, caption_html),
          caption_text = COALESCE(?, caption_text),
          published = COALESCE(?, published),
          added = COALESCE(?, added),
          bookmarked = COALESCE(?, bookmarked),
          prev_id = ?,
          next_id = ?,
          revisions_count = COALESCE(?, revisions_count),
          linked_count = COALESCE(?, linked_count),
          raw_json = COALESCE(?, raw_json),
          fetched_at = COALESCE(?, fetched_at),
          missing_upstream = COALESCE(?, missing_upstream)
        WHERE id = ?
        """,
        (
            service,
            title,
            caption_html,
            caption_text,
            published,
            added,
            bookmarked,
            prev_id,
            next_id,
            revisions_count,
            linked_count,
            raw_json,
            fetched_at,
            missing_upstream,
            row["id"],
        ),
    )
    return int(row["id"])


def save_post_bundle(
    conn: sqlite3.Connection,
    *,
    creator_id: int,
    service: str,
    service_id: str,
    title: str | None = None,
    caption_html: str | None = None,
    caption_text: str | None = None,
    published: int | None = None,
    added: int | None = None,
    bookmarked: int | None = None,
    prev_id: str | None = None,
    next_id: str | None = None,
    revisions_count: int = 0,
    linked_count: int = 0,
    raw_json: str | None = None,
    fetched_at: str | None = None,
    files: Sequence[dict[str, Any]] = (),
    links: Sequence[dict[str, Any]] = (),
    tags: Sequence[dict[str, Any]] = (),
) -> tuple[int, bool]:
    """Write a post and its files/links/tags in one transaction.

    Returns ``(post_id, created)``. This is the unit of resumability: either the
    whole bundle lands or none of it does.
    """
    with conn:
        exists = conn.execute(
            "SELECT 1 FROM posts WHERE creator_id = ? AND service_id = ?",
            (creator_id, service_id),
        ).fetchone()
        post_id = upsert_post(
            conn,
            creator_id=creator_id,
            service=service,
            service_id=service_id,
            title=title,
            caption_html=caption_html,
            caption_text=caption_text,
            published=published,
            added=added,
            bookmarked=bookmarked,
            prev_id=prev_id,
            next_id=next_id,
            revisions_count=revisions_count,
            linked_count=linked_count,
            raw_json=raw_json,
            fetched_at=fetched_at,
            missing_upstream=0,
        )
        _write_files(conn, post_id, files)
        _write_links(conn, post_id, links)
        _write_tags(conn, post_id, tags)
    return post_id, exists is None


def post_status_map(conn: sqlite3.Connection, creator_id: int) -> dict[str, str | None]:
    """Map service post id -> fetched_at for resume decisions."""
    rows = conn.execute(
        "SELECT service_id, fetched_at FROM posts WHERE creator_id = ?",
        (creator_id,),
    ).fetchall()
    return {row["service_id"]: row["fetched_at"] for row in rows}


def get_post(conn: sqlite3.Connection, post_id: int) -> dict[str, Any] | None:
    return _dict(conn.execute("SELECT * FROM posts WHERE id = ?", (post_id,)).fetchone())


def get_post_by_service_id(
    conn: sqlite3.Connection, creator_id: int, service_id: str
) -> dict[str, Any] | None:
    return _dict(
        conn.execute(
            "SELECT * FROM posts WHERE creator_id = ? AND service_id = ?",
            (creator_id, service_id),
        ).fetchone()
    )


def set_missing_upstream(
    conn: sqlite3.Connection, post_id: int, missing: bool
) -> None:
    conn.execute(
        "UPDATE posts SET missing_upstream = ? WHERE id = ?",
        (1 if missing else 0, post_id),
    )


_SORTS = {
    "published_desc": "COALESCE(p.published, 0) DESC, p.id DESC",
    "published_asc": "COALESCE(p.published, 0) ASC, p.id ASC",
    "title": "COALESCE(p.title, '') COLLATE NOCASE ASC, p.id DESC",
    "title_asc": "COALESCE(p.title, '') COLLATE NOCASE ASC, p.id DESC",
    "added_desc": "COALESCE(p.added, 0) DESC, p.id DESC",
}

_FILTER_NAMES = frozenset(
    {
        "category",
        "tag",
        "media_kind",
        "has_links",
        "has_attachments",
        "missing",
        "date_from",
        "date_to",
        "q",
    }
)


def _post_where(
    creator_id: int,
    *,
    category: str | None = None,
    tag: str | None = None,
    media_kind: str | None = None,
    has_links: bool | None = None,
    has_attachments: bool | None = None,
    missing: bool | None = None,
    date_from: int | None = None,
    date_to: int | None = None,
    q: str | None = None,
    exclude: frozenset[str] = frozenset(),
) -> tuple[str, list[Any]]:
    """Build the shared WHERE clause for post filters.

    ``exclude`` drops dimensions from the clause, which is how facet counts are
    computed against every *other* active filter (standard faceted search).
    """
    where = ["p.creator_id = ?"]
    params: list[Any] = [creator_id]

    if category and "category" not in exclude:
        where.append(
            "EXISTS (SELECT 1 FROM links l WHERE l.post_id = p.id AND l.category = ?)"
        )
        params.append(category)
    if tag and "tag" not in exclude:
        where.append("EXISTS (SELECT 1 FROM tags t WHERE t.post_id = p.id AND t.slug = ?)")
        params.append(tag)
    if media_kind and "media" not in exclude:
        where.append(
            "EXISTS (SELECT 1 FROM files f WHERE f.post_id = p.id AND f.kind = ?)"
        )
        params.append(media_kind)
    if has_links is not None and "has_links" not in exclude:
        op = "EXISTS" if has_links else "NOT EXISTS"
        where.append(f"{op} (SELECT 1 FROM links l WHERE l.post_id = p.id)")
    if has_attachments is not None and "has_attachments" not in exclude:
        op = "EXISTS" if has_attachments else "NOT EXISTS"
        where.append(f"{op} (SELECT 1 FROM files f WHERE f.post_id = p.id)")
    if missing is not None and "missing" not in exclude:
        where.append("p.missing_upstream = ?")
        params.append(1 if missing else 0)
    if date_from is not None and "date_from" not in exclude:
        where.append("COALESCE(p.published, 0) >= ?")
        params.append(date_from)
    if date_to is not None and "date_to" not in exclude:
        where.append("COALESCE(p.published, 0) <= ?")
        params.append(date_to)
    if q and "q" not in exclude:
        needle = f"%{q}%"
        where.append(
            """(
              p.title LIKE ?
              OR p.caption_text LIKE ?
              OR EXISTS (SELECT 1 FROM links l WHERE l.post_id = p.id AND l.url LIKE ?)
            )"""
        )
        params.extend([needle, needle, needle])

    return " AND ".join(where), params


def _facet_kwargs(filters: dict[str, Any]) -> dict[str, Any]:
    return {name: filters.get(name) for name in _FILTER_NAMES}


def list_posts(
    conn: sqlite3.Connection,
    creator_id: int,
    *,
    category: str | None = None,
    tag: str | None = None,
    media_kind: str | None = None,
    has_links: bool | None = None,
    has_attachments: bool | None = None,
    missing: bool | None = None,
    date_from: int | None = None,
    date_to: int | None = None,
    q: str | None = None,
    sort: str = "published_desc",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Return (rows, total) for a creator's posts under the given filters."""
    if sort not in _SORTS:
        raise ValueError(f"unknown sort: {sort!r}")

    where_sql, params = _post_where(
        creator_id,
        category=category,
        tag=tag,
        media_kind=media_kind,
        has_links=has_links,
        has_attachments=has_attachments,
        missing=missing,
        date_from=date_from,
        date_to=date_to,
        q=q,
    )

    total = int(
        conn.execute(f"SELECT count(*) AS n FROM posts p WHERE {where_sql}", params)
        .fetchone()["n"]
    )

    rows = conn.execute(
        f"""
        SELECT p.*,
          (SELECT count(*) FROM links l WHERE l.post_id = p.id) AS link_count,
          (SELECT count(*) FROM files f WHERE f.post_id = p.id) AS file_count
        FROM posts p
        WHERE {where_sql}
        ORDER BY {_SORTS[sort]}
        LIMIT ? OFFSET ?
        """,
        [*params, limit, offset],
    ).fetchall()

    return _rows(rows), total


def post_cards(
    conn: sqlite3.Connection, post_ids: Sequence[int]
) -> dict[int, dict[str, Any]]:
    """Batch-fetch the per-post extras the creator list needs (no N+1 queries)."""
    ids = list(dict.fromkeys(int(post_id) for post_id in post_ids))
    if not ids:
        return {}

    placeholders = ",".join("?" for _ in ids)
    cards: dict[int, dict[str, Any]] = {
        post_id: {"category_counts": {}, "file_kind_counts": {}, "tags": [], "thumb_url": None}
        for post_id in ids
    }

    for row in conn.execute(
        f"""
        SELECT post_id, category, count(*) AS n
        FROM links
        WHERE post_id IN ({placeholders})
        GROUP BY post_id, category
        """,
        ids,
    ).fetchall():
        cards[int(row["post_id"])]["category_counts"][row["category"]] = int(row["n"])

    for row in conn.execute(
        f"""
        SELECT post_id, kind, count(*) AS n
        FROM files
        WHERE post_id IN ({placeholders})
        GROUP BY post_id, kind
        """,
        ids,
    ).fetchall():
        cards[int(row["post_id"])]["file_kind_counts"][row["kind"]] = int(row["n"])

    for row in conn.execute(
        f"""
        SELECT post_id, slug, label
        FROM tags
        WHERE post_id IN ({placeholders})
        ORDER BY slug
        """,
        ids,
    ).fetchall():
        cards[int(row["post_id"])]["tags"].append(
            {"slug": row["slug"], "label": row["label"] or row["slug"]}
        )

    for row in conn.execute(
        f"""
        SELECT p.id,
          (SELECT f.thumb_url FROM files f
             WHERE f.post_id = p.id AND f.thumb_url IS NOT NULL
             ORDER BY COALESCE(f.position, 0), f.id LIMIT 1) AS thumb_url
        FROM posts p
        WHERE p.id IN ({placeholders})
        """,
        ids,
    ).fetchall():
        cards[int(row["id"])]["thumb_url"] = row["thumb_url"]

    return cards


# --------------------------------------------------------------------------- #
# bookmarks (T9.1)
# --------------------------------------------------------------------------- #

# Hard cap for the library pages: the routes page anyway, but a hand-crafted
# ``limit`` must not be able to ask the database for the whole table.
MAX_BOOKMARK_LIMIT = 200


def _clamp_bookmark_page(limit: int, offset: int) -> tuple[int, int]:
    try:
        safe_limit = int(limit)
    except (TypeError, ValueError):
        safe_limit = 1
    try:
        safe_offset = int(offset)
    except (TypeError, ValueError):
        safe_offset = 0
    return (
        max(1, min(safe_limit, MAX_BOOKMARK_LIMIT)),
        max(0, safe_offset),
    )


def toggle_post_bookmark(
    conn: sqlite3.Connection, creator_id: int, post_service_id: str
) -> bool:
    """Toggle a local post bookmark and return the new state.

    Raises ``KeyError`` when the post does not belong to the creator. The
    upstream ``posts.bookmarked`` column is never read or written here.
    """
    row = conn.execute(
        "SELECT id FROM posts WHERE creator_id = ? AND service_id = ?",
        (int(creator_id), str(post_service_id)),
    ).fetchone()
    if row is None:
        raise KeyError(f"unknown post {post_service_id!r} for creator {creator_id}")

    post_id = int(row["id"])
    existing = conn.execute(
        "SELECT id FROM post_bookmarks WHERE post_id = ?", (post_id,)
    ).fetchone()
    with conn:
        if existing is not None:
            conn.execute("DELETE FROM post_bookmarks WHERE post_id = ?", (post_id,))
            return False
        conn.execute(
            "INSERT INTO post_bookmarks (creator_id, post_id, created_at) VALUES (?, ?, ?)",
            (int(creator_id), post_id, now_iso()),
        )
    return True


def toggle_media_bookmark(
    conn: sqlite3.Connection, creator_id: int, sha256: str
) -> bool:
    """Toggle a local media bookmark keyed by ``(creator_id, sha256)``.

    Inserting requires the sha to be attached to at least one post of this
    creator, otherwise ``KeyError`` is raised: junk or cross-creator shas cannot
    be bookmarked. Re-bookmarking the same file from another post is the same
    bookmark. Removing an existing bookmark is always allowed, even after the
    file disappeared upstream (T9.3's missing tile stays removable).
    """
    sha = str(sha256 or "").strip()
    if not sha:
        raise KeyError("empty sha256")

    existing = conn.execute(
        "SELECT id FROM media_bookmarks WHERE creator_id = ? AND sha256 = ?",
        (int(creator_id), sha),
    ).fetchone()
    if existing is not None:
        # Removal is always allowed, including for a file that has since
        # disappeared upstream (T9.3 needs a working remove on missing tiles).
        with conn:
            conn.execute(
                "DELETE FROM media_bookmarks WHERE creator_id = ? AND sha256 = ?",
                (int(creator_id), sha),
            )
        return False

    row = conn.execute(
        """
        SELECT 1
        FROM files f
        JOIN posts p ON p.id = f.post_id
        WHERE p.creator_id = ? AND f.sha256 = ?
        LIMIT 1
        """,
        (int(creator_id), sha),
    ).fetchone()
    if row is None:
        raise KeyError(f"sha {sha!r} is not attached to creator {creator_id}")

    with conn:
        conn.execute(
            "INSERT INTO media_bookmarks (creator_id, sha256, created_at) VALUES (?, ?, ?)",
            (int(creator_id), sha, now_iso()),
        )
    return True


def post_is_bookmarked(
    conn: sqlite3.Connection, creator_id: int, post_id: int
) -> bool:
    """True when the creator's post (internal id) has a local bookmark."""
    row = conn.execute(
        """
        SELECT 1 FROM post_bookmarks pb
        JOIN posts p ON p.id = pb.post_id
        WHERE pb.post_id = ? AND p.creator_id = ? AND pb.creator_id = ?
        LIMIT 1
        """,
        (int(post_id), int(creator_id), int(creator_id)),
    ).fetchone()
    return row is not None


def media_is_bookmarked(
    conn: sqlite3.Connection, creator_id: int, sha256: str
) -> bool:
    row = conn.execute(
        "SELECT 1 FROM media_bookmarks WHERE creator_id = ? AND sha256 = ? LIMIT 1",
        (int(creator_id), str(sha256)),
    ).fetchone()
    return row is not None


def bookmarked_post_ids(
    conn: sqlite3.Connection, creator_id: int, post_ids: Sequence[int]
) -> set[int]:
    """Bookmarked subset of ``post_ids`` for one page (one query, no N+1)."""
    ids = list(dict.fromkeys(int(post_id) for post_id in post_ids))
    if not ids:
        return set()
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        SELECT pb.post_id
        FROM post_bookmarks pb
        JOIN posts p ON p.id = pb.post_id
        WHERE p.creator_id = ? AND pb.creator_id = ?
          AND pb.post_id IN ({placeholders})
        """,
        [int(creator_id), int(creator_id), *ids],
    ).fetchall()
    return {int(row["post_id"]) for row in rows}


def bookmarked_media_shas(
    conn: sqlite3.Connection, creator_id: int, shas: Sequence[str]
) -> set[str]:
    """Bookmarked subset of ``shas`` for one page (one query, no N+1)."""
    values = list(dict.fromkeys(str(sha) for sha in shas if sha))
    if not values:
        return set()
    placeholders = ",".join("?" for _ in values)
    rows = conn.execute(
        f"""
        SELECT sha256 FROM media_bookmarks
        WHERE creator_id = ? AND sha256 IN ({placeholders})
        """,
        [int(creator_id), *values],
    ).fetchall()
    return {str(row["sha256"]) for row in rows}


_CREATOR_NAME_SQL = "COALESCE(c.display_name, c.name, c.service_id)"


def list_post_bookmarks(
    conn: sqlite3.Connection,
    *,
    creator_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Post bookmarks newest-bookmarked first, joined to their creator.

    ``post_id`` is the internal id; ``service_id``/``post_service_id`` are the
    upstream ids used in post URLs. Thumbnails follow the ``post_cards`` rule
    (first attachment with a thumbnail, by position then id).
    """
    safe_limit, safe_offset = _clamp_bookmark_page(limit, offset)
    where = ""
    params: list[Any] = []
    if creator_id is not None:
        where = "WHERE pb.creator_id = ?"
        params.append(int(creator_id))

    total = int(
        conn.execute(
            f"SELECT count(*) AS n FROM post_bookmarks pb {where}", params
        ).fetchone()["n"]
    )
    rows = conn.execute(
        f"""
        SELECT pb.id AS bookmark_id,
               pb.created_at AS created_at,
               pb.creator_id AS creator_id,
               {_CREATOR_NAME_SQL} AS creator_name,
               p.id AS post_id,
               p.service_id AS service_id,
               p.service_id AS post_service_id,
               p.service AS service,
               p.title AS title,
               p.published AS published,
               p.missing_upstream AS missing_upstream,
               (SELECT f.thumb_url FROM files f
                  WHERE f.post_id = p.id AND f.thumb_url IS NOT NULL
                  ORDER BY COALESCE(f.position, 0), f.id LIMIT 1) AS thumb_url
        FROM post_bookmarks pb
        JOIN posts p ON p.id = pb.post_id
        JOIN creators c ON c.id = pb.creator_id
        {where}
        ORDER BY pb.created_at DESC, pb.id DESC
        LIMIT ? OFFSET ?
        """,
        [*params, safe_limit, safe_offset],
    ).fetchall()
    return _rows(rows), total


def list_media_bookmarks(
    conn: sqlite3.Connection,
    *,
    creator_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Media bookmarks newest-bookmarked first with current attachment metadata.

    One row per bookmark. The attachment is chosen deterministically (lowest
    file id for that creator) so re-syncs do not reshuffle the library. When
    the file is no longer attached the row survives with ``url = NULL`` and
    ``missing = 1``.
    """
    safe_limit, safe_offset = _clamp_bookmark_page(limit, offset)
    where = ""
    params: list[Any] = []
    if creator_id is not None:
        where = "WHERE mb.creator_id = ?"
        params.append(int(creator_id))

    total = int(
        conn.execute(
            f"SELECT count(*) AS n FROM media_bookmarks mb {where}", params
        ).fetchone()["n"]
    )
    rows = conn.execute(
        f"""
        SELECT mb.id AS bookmark_id,
               mb.sha256 AS sha256,
               mb.created_at AS created_at,
               mb.creator_id AS creator_id,
               {_CREATOR_NAME_SQL} AS creator_name,
               f.id AS file_id,
               f.post_id AS post_id,
               p.service_id AS post_service_id,
               p.title AS post_title,
               f.kind AS kind,
               f.mime_type AS mime_type,
               f.url AS url,
               f.thumb_url AS thumb_url,
               f.width AS width,
               f.height AS height,
               f.duration_ms AS duration_ms,
               f.original_filename AS original_filename
        FROM media_bookmarks mb
        JOIN creators c ON c.id = mb.creator_id
        LEFT JOIN files f ON f.id = (
            SELECT f2.id
            FROM files f2
            JOIN posts p2 ON p2.id = f2.post_id
            WHERE p2.creator_id = mb.creator_id AND f2.sha256 = mb.sha256
            ORDER BY f2.id
            LIMIT 1
        )
        LEFT JOIN posts p ON p.id = f.post_id
        {where}
        ORDER BY mb.created_at DESC, mb.id DESC
        LIMIT ? OFFSET ?
        """,
        [*params, safe_limit, safe_offset],
    ).fetchall()

    result = _rows(rows)
    for row in result:
        row["missing"] = 1 if not row.get("url") else 0
        if row["missing"]:
            row["url"] = None
            row["thumb_url"] = None
    return result, total


def bookmark_overview(
    conn: sqlite3.Connection,
    *,
    view: str = "all",
    preview_limit: int = 6,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Creator sections for the unfiltered bookmark library (T9.4).

    Two windowed queries (posts and media) fetch at most ``preview_limit`` rows
    per creator each, so the number of queries is independent of the number of
    creators. The merged items are re-trimmed to ``preview_limit`` in Python.
    """
    if view not in {"all", "posts", "media"}:
        view = "all"
    try:
        limit = max(1, int(preview_limit))
    except (TypeError, ValueError):
        limit = 6

    post_rows = _rows(
        conn.execute(
            f"""
            SELECT * FROM (
              SELECT pb.id AS bookmark_id,
                     pb.created_at AS created_at,
                     pb.creator_id AS creator_id,
                     {_CREATOR_NAME_SQL} AS creator_name,
                     p.id AS post_id,
                     p.service_id AS service_id,
                     p.service_id AS post_service_id,
                     p.service AS service,
                     p.title AS title,
                     p.published AS published,
                     p.missing_upstream AS missing_upstream,
                     (SELECT f.thumb_url FROM files f
                        WHERE f.post_id = p.id AND f.thumb_url IS NOT NULL
                        ORDER BY COALESCE(f.position, 0), f.id LIMIT 1) AS thumb_url,
                     ROW_NUMBER() OVER (
                       PARTITION BY pb.creator_id
                       ORDER BY pb.created_at DESC, pb.id DESC
                     ) AS item_rank,
                     COUNT(*) OVER (PARTITION BY pb.creator_id) AS creator_total
              FROM post_bookmarks pb
              JOIN posts p ON p.id = pb.post_id
              JOIN creators c ON c.id = pb.creator_id
            ) WHERE item_rank <= ?
            ORDER BY created_at DESC, bookmark_id DESC
            """,
            (limit,),
        ).fetchall()
    )
    media_rows = _rows(
        conn.execute(
            f"""
            SELECT * FROM (
              SELECT mb.id AS bookmark_id,
                     mb.sha256 AS sha256,
                     mb.created_at AS created_at,
                     mb.creator_id AS creator_id,
                     {_CREATOR_NAME_SQL} AS creator_name,
                     f.id AS file_id,
                     f.post_id AS post_id,
                     p.service_id AS post_service_id,
                     p.title AS post_title,
                     f.kind AS kind,
                     f.mime_type AS mime_type,
                     f.url AS url,
                     f.thumb_url AS thumb_url,
                     f.width AS width,
                     f.height AS height,
                     f.duration_ms AS duration_ms,
                     f.bytes AS bytes,
                     f.original_filename AS original_filename,
                     ROW_NUMBER() OVER (
                       PARTITION BY mb.creator_id
                       ORDER BY mb.created_at DESC, mb.id DESC
                     ) AS item_rank,
                     COUNT(*) OVER (PARTITION BY mb.creator_id) AS creator_total
              FROM media_bookmarks mb
              JOIN creators c ON c.id = mb.creator_id
              LEFT JOIN files f ON f.id = (
                SELECT f2.id
                FROM files f2
                JOIN posts p2 ON p2.id = f2.post_id
                WHERE p2.creator_id = mb.creator_id AND f2.sha256 = mb.sha256
                ORDER BY f2.id
                LIMIT 1
              )
              LEFT JOIN posts p ON p.id = f.post_id
            ) WHERE item_rank <= ?
            ORDER BY created_at DESC, bookmark_id DESC
            """,
            (limit,),
        ).fetchall()
    )

    for row in post_rows:
        row["kind"] = "post"
    for row in media_rows:
        row["kind"] = "media"
        row["missing"] = 1 if not row.get("url") else 0
        if row["missing"]:
            row["url"] = None
            row["thumb_url"] = None

    post_totals = {int(row["creator_id"]): int(row["creator_total"]) for row in post_rows}
    media_totals = {int(row["creator_id"]): int(row["creator_total"]) for row in media_rows}
    post_creators = set(post_totals)
    media_creators = set(media_totals)
    totals = {
        "posts": sum(post_totals.values()),
        "media": sum(media_totals.values()),
        "all": sum(post_totals.values()) + sum(media_totals.values()),
    }

    items: dict[int, list[dict[str, Any]]] = {}
    names: dict[int, str] = {}
    if view in {"all", "posts"}:
        for row in post_rows:
            creator_id = int(row["creator_id"])
            names[creator_id] = row.get("creator_name")
            items.setdefault(creator_id, []).append(row)
    if view in {"all", "media"}:
        for row in media_rows:
            creator_id = int(row["creator_id"])
            names[creator_id] = row.get("creator_name")
            items.setdefault(creator_id, []).append(row)

    sections: list[dict[str, Any]] = []
    for creator_id, rows in items.items():
        rows.sort(
            key=lambda row: (
                str(row.get("created_at") or ""),
                str(row.get("kind") or ""),
                int(row.get("bookmark_id") or 0),
            ),
            reverse=True,
        )
        if view == "all":
            total = post_totals.get(creator_id, 0) + media_totals.get(creator_id, 0)
        elif view == "posts":
            total = post_totals.get(creator_id, 0)
        else:
            total = media_totals.get(creator_id, 0)
        sections.append(
            {
                "creator_id": creator_id,
                "creator_name": names.get(creator_id) or str(creator_id),
                "total": total,
                "preview": rows[:limit],
            }
        )

    # Newest qualifying bookmark first; ties fall back to creator name then id.
    sections.sort(
        key=lambda section: (
            (section.get("creator_name") or "").casefold(),
            section["creator_id"],
        )
    )
    sections.sort(
        key=lambda section: str(section["preview"][0].get("created_at") or ""),
        reverse=True,
    )
    return sections, totals


def bookmark_creator_facets(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Creators that actually hold a bookmark, with per-kind totals."""
    rows = conn.execute(
        f"""
        SELECT c.id AS creator_id,
               {_CREATOR_NAME_SQL} AS creator_name,
               (SELECT count(*) FROM post_bookmarks pb
                  WHERE pb.creator_id = c.id) AS post_count,
               (SELECT count(*) FROM media_bookmarks mb
                  WHERE mb.creator_id = c.id) AS media_count
        FROM creators c
        WHERE EXISTS (SELECT 1 FROM post_bookmarks pb WHERE pb.creator_id = c.id)
           OR EXISTS (SELECT 1 FROM media_bookmarks mb WHERE mb.creator_id = c.id)
        ORDER BY creator_name COLLATE NOCASE ASC, c.id ASC
        """
    ).fetchall()
    return _rows(rows)


def category_facets(
    conn: sqlite3.Connection,
    creator_id: int,
    *,
    exclude: frozenset[str] = frozenset({"category"}),
    **filters: Any,
) -> dict[str, int]:
    """Link counts per category under every active filter except ``category``."""
    where_sql, params = _post_where(
        creator_id, exclude=exclude, **_facet_kwargs(filters)
    )
    rows = conn.execute(
        f"""
        SELECT l.category AS category, count(*) AS n
        FROM links l
        JOIN posts p ON p.id = l.post_id
        WHERE {where_sql}
        GROUP BY l.category
        """,
        params,
    ).fetchall()
    return {str(row["category"]): int(row["n"]) for row in rows}


def tag_facets(
    conn: sqlite3.Connection,
    creator_id: int,
    *,
    exclude: frozenset[str] = frozenset({"tag"}),
    limit: int = 20,
    **filters: Any,
) -> list[dict[str, Any]]:
    """Top tags by post count under every active filter except ``tag``."""
    where_sql, params = _post_where(
        creator_id, exclude=exclude, **_facet_kwargs(filters)
    )
    rows = conn.execute(
        f"""
        SELECT t.slug AS slug,
               COALESCE(max(t.label), t.slug) AS label,
               count(*) AS n
        FROM tags t
        JOIN posts p ON p.id = t.post_id
        WHERE {where_sql}
        GROUP BY t.slug
        ORDER BY n DESC, t.slug ASC
        LIMIT ?
        """,
        [*params, max(1, limit)],
    ).fetchall()
    return _rows(rows)


def creator_has_tags(conn: sqlite3.Connection, creator_id: int) -> bool:
    """True when the creator has at least one ingested tag, before filtering."""
    row = conn.execute(
        """
        SELECT 1 FROM tags t JOIN posts p ON p.id = t.post_id
        WHERE p.creator_id = ? LIMIT 1
        """,
        (int(creator_id),),
    ).fetchone()
    return row is not None


def dead_count(conn: sqlite3.Connection, creator_id: int, **filters: Any) -> int:
    """Number of missing-upstream posts under every filter except ``missing``."""
    cleaned = _facet_kwargs(filters)
    cleaned["missing"] = True
    where_sql, params = _post_where(creator_id, **cleaned)
    return int(
        conn.execute(
            f"SELECT count(*) AS n FROM posts p WHERE {where_sql}", params
        ).fetchone()["n"]
    )


def _post_exists(
    conn: sqlite3.Connection, creator_id: int, service_id: str | None
) -> bool:
    if not service_id:
        return False
    row = conn.execute(
        "SELECT 1 FROM posts WHERE creator_id = ? AND service_id = ?",
        (creator_id, service_id),
    ).fetchone()
    return row is not None


def post_neighbours(
    conn: sqlite3.Connection, creator_id: int, post: dict[str, Any]
) -> tuple[str | None, str | None]:
    """Return (prev, next) service ids, falling back to published-order rows.

    This follows the site's own meaning (verified in captured payloads):
    ``prev`` is the older neighbour and ``next`` the newer one. Stored
    ``prev_id``/``next_id`` win only when they still point at a post in this
    archive, so a deleted upstream post can never turn into a broken link.
    """
    prev_id = post.get("prev_id")
    next_id = post.get("next_id")
    if not _post_exists(conn, creator_id, prev_id):
        prev_id = None
    if not _post_exists(conn, creator_id, next_id):
        next_id = None

    published = post.get("published")
    marker = int(published or 0)
    post_id = int(post["id"])

    if prev_id is None:
        row = conn.execute(
            """
            SELECT service_id FROM posts
            WHERE creator_id = ?
              AND (COALESCE(published, 0) < ?
                   OR (COALESCE(published, 0) = ? AND id < ?))
            ORDER BY COALESCE(published, 0) DESC, id DESC
            LIMIT 1
            """,
            (creator_id, marker, marker, post_id),
        ).fetchone()
        prev_id = row["service_id"] if row else None

    if next_id is None:
        row = conn.execute(
            """
            SELECT service_id FROM posts
            WHERE creator_id = ?
              AND (COALESCE(published, 0) > ?
                   OR (COALESCE(published, 0) = ? AND id > ?))
            ORDER BY COALESCE(published, 0) ASC, id ASC
            LIMIT 1
            """,
            (creator_id, marker, marker, post_id),
        ).fetchone()
        next_id = row["service_id"] if row else None

    return prev_id, next_id


# --------------------------------------------------------------------------- #
# files / links / tags
# --------------------------------------------------------------------------- #


def replace_files(
    conn: sqlite3.Connection, post_id: int, rows: Sequence[dict[str, Any]]
) -> int:
    """Replace a post's files, deduplicating by sha256. Returns rows written."""
    with conn:
        return _write_files(conn, post_id, rows)


def _write_files(
    conn: sqlite3.Connection, post_id: int, rows: Sequence[dict[str, Any]]
) -> int:
    seen: set[str] = set()
    values: list[tuple[Any, ...]] = []
    for row in rows:
        sha = row.get("sha256")
        if not sha or sha in seen:
            continue
        seen.add(sha)
        variants = row.get("variants")
        variants_json = row.get("variants_json")
        if variants_json is None and variants is not None:
            variants_json = json.dumps(variants, separators=(",", ":"))
        values.append(
            (
                post_id,
                row.get("position"),
                sha,
                row.get("kind") or "file",
                row.get("raw_kind") or row.get("kind") or "file",
                row.get("mime_type"),
                row.get("bytes"),
                row.get("width"),
                row.get("height"),
                row.get("duration_ms"),
                row.get("original_filename"),
                row.get("storage_key"),
                variants_json,
                row["url"],
                row.get("thumb_url"),
            )
        )

    conn.execute("DELETE FROM files WHERE post_id = ?", (post_id,))
    if values:
        conn.executemany(
            """
            INSERT INTO files (
              post_id, position, sha256, kind, raw_kind, mime_type, bytes, width,
              height, duration_ms, original_filename, storage_key, variants_json,
              url, thumb_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
    return len(values)


def list_files(conn: sqlite3.Connection, post_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM files WHERE post_id = ? ORDER BY COALESCE(position, 0), id",
        (post_id,),
    ).fetchall()
    return _rows(rows)


def _variants_from_json(raw: str | None) -> list[dict[str, Any]]:
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def reclassify_files(
    conn: sqlite3.Connection, *, dry_run: bool = False
) -> list[dict[str, Any]]:
    """Recompute ``files.kind`` from the stored raw fields (no network).

    Uses ``raw_kind`` (the upstream value) rather than the current ``kind``, so
    the command is idempotent: running it twice reports no further changes.
    Returns one aggregated row per transition, e.g. ``gif -> video: 3``.
    """
    rows = conn.execute(
        """
        SELECT id, kind, raw_kind, mime_type, original_filename, variants_json,
               duration_ms
        FROM files
        ORDER BY id
        """
    ).fetchall()

    transitions: dict[tuple[str, str], int] = {}
    updates: list[tuple[str, int]] = []
    for row in rows:
        current = str(row["kind"] or "file")
        raw = str(row["raw_kind"] or current)
        normalized = classify_media_kind(
            raw_kind=raw,
            mime_type=row["mime_type"],
            original_filename=row["original_filename"],
            variants=_variants_from_json(row["variants_json"]),
            duration_ms=row["duration_ms"],
        )
        if normalized == current:
            continue
        transitions[(current, normalized)] = transitions.get((current, normalized), 0) + 1
        updates.append((normalized, int(row["id"])))

    if updates and not dry_run:
        with conn:
            conn.executemany("UPDATE files SET kind = ? WHERE id = ?", updates)

    return [
        {"from_kind": source, "to_kind": target, "count": count}
        for (source, target), count in sorted(transitions.items())
    ]


def print_reclassify(*, dry_run: bool = False) -> int:
    settings = get_settings()
    path: Path = settings.db_path
    if not path.exists():
        print(f"database: {path}")
        print("status:   empty (run a sync first)")
        return 0

    conn = ensure_db(settings)
    try:
        transitions = reclassify_files(conn, dry_run=dry_run)
    finally:
        conn.close()

    total = sum(row["count"] for row in transitions)
    print(f"database: {path}")
    if not transitions:
        print("no kind changes")
    else:
        for row in transitions:
            print(f"{row['from_kind']} -> {row['to_kind']}: {row['count']}")
        print(f"total: {total}")
    if dry_run and total:
        print("dry run: nothing written")
    return 0


# Kinds the fullscreen viewer can show and the profile gallery renders as
# tiles. Audio is deliberately excluded: it has no still frame and the post
# page already plays it (T7.2, T8.1).
VIEWER_KINDS: tuple[str, ...] = ("image", "gif", "video")
GALLERY_KINDS: tuple[str, ...] = VIEWER_KINDS

# Read-time kind normalization (T8.17). Ingested rows are usually correct, but
# historical or re-synced rows can still store ``video`` on an image URL (or the
# reverse). The viewer must not depend on that single historical value, so every
# viewer/gallery query derives the effective kind from mime, filename, URL and
# duration. This mirrors ``cumst.media.classify_media_kind`` without rewriting
# the database (T7.1 owns ingestion normalization).
_VIDEO_EXTS = ("mp4", "webm", "mov", "m4v")
_AUDIO_EXTS = ("mp3", "m4a", "ogg", "wav", "opus")
_STILL_EXTS = ("jpg", "jpeg", "png", "webp", "avif", "bmp")
_GIF_EXTS = ("gif",)


def _media_ext_sql(alias: str, extensions: tuple[str, ...]) -> str:
    columns = (
        f"lower(COALESCE({alias}.original_filename, ''))",
        f"lower(COALESCE({alias}.url, ''))",
        f"lower(COALESCE({alias}.variants_json, ''))",
    )
    clauses = [
        f"{column} LIKE '%.{extension}%'"
        for column in columns
        for extension in extensions
    ]
    return "(" + " OR ".join(clauses) + ")"


def _media_kind_expr(alias: str = "f") -> str:
    """SQL expression for the effective media kind, aliased ``alias``.

    This mirrors :func:`monoke.cumst.media.classify_media_kind` statement for
    statement. The Python classifier is applied again when rows are read, and
    the two must agree or a gallery ``kind`` filter would select rows the viewer
    then re-labels (T8.17/T7.7).
    """
    return f"""CASE
      WHEN lower(COALESCE({alias}.mime_type, '')) LIKE 'video/%'
        OR {_media_ext_sql(alias, _VIDEO_EXTS)} THEN 'video'
      WHEN COALESCE({alias}.duration_ms, 0) > 0
        AND NOT {_media_ext_sql(alias, _STILL_EXTS)}
        AND NOT {_media_ext_sql(alias, _GIF_EXTS)} THEN 'video'
      WHEN lower(COALESCE({alias}.mime_type, '')) = 'image/gif'
        AND lower(COALESCE({alias}.kind, '')) = 'gif'
        AND {_media_ext_sql(alias, _GIF_EXTS)} THEN 'gif'
      WHEN lower(COALESCE({alias}.mime_type, '')) LIKE 'image/%'
        OR {_media_ext_sql(alias, _STILL_EXTS)}
        OR {_media_ext_sql(alias, _GIF_EXTS)} THEN 'image'
      WHEN lower(COALESCE({alias}.mime_type, '')) LIKE 'audio/%'
        OR {_media_ext_sql(alias, _AUDIO_EXTS)} THEN 'audio'
      ELSE lower(COALESCE({alias}.kind, 'file'))
    END"""

# T8.8: at most six still images from each adjacent post are exposed for the
# viewer's cross-post preload window. GIFs/videos are excluded on purpose.
NEIGHBOUR_PRELOAD_LIMIT = 6

# Whitelisted post-level gallery sorts (T4.11). User input is validated in the
# filter model and again here; it never reaches the SQL string. Duration sorts
# rank posts by their longest/shortest *matching* timed video; posts without a
# timed video sort last in both directions.
GALLERY_POST_SORTS: tuple[str, ...] = (
    "newest",
    "oldest",
    "duration_desc",
    "duration_asc",
)


def _gallery_media_where(
    *,
    kind: str | None = None,
    orientation: str | None = None,
    min_bytes: int | None = None,
) -> tuple[list[str], list[Any]]:
    """Media-level gallery conditions (kind/orientation/size), aliased ``f``."""
    where: list[str] = []
    params: list[Any] = []
    kind_sql = _media_kind_expr("f")

    selected = kind if kind in GALLERY_KINDS else None
    if selected:
        where.append(f"{kind_sql} = ?")
        params.append(selected)
    else:
        placeholders = ",".join("?" for _ in GALLERY_KINDS)
        where.append(f"{kind_sql} IN ({placeholders})")
        params.extend(GALLERY_KINDS)

    if min_bytes:
        where.append("COALESCE(f.bytes, 0) >= ?")
        params.append(int(min_bytes))
    if orientation == "portrait":
        where.append(
            "f.width IS NOT NULL AND f.height IS NOT NULL AND f.height > f.width"
        )
    elif orientation == "landscape":
        where.append(
            "f.width IS NOT NULL AND f.height IS NOT NULL AND f.width > f.height"
        )
    elif orientation == "square":
        where.append(
            "f.width IS NOT NULL AND f.height IS NOT NULL AND f.width = f.height"
        )

    return where, params


def _gallery_post_where(
    creator_id: int,
    *,
    q: str | None = None,
    tag: str | None = None,
    date_from: int | None = None,
    date_to: int | None = None,
) -> tuple[list[str], list[Any]]:
    """Post-level gallery conditions (search, tag and date range)."""
    where = ["p.creator_id = ?"]
    params: list[Any] = [creator_id]

    if q:
        needle = f"%{q}%"
        where.append("(p.title LIKE ? OR p.caption_text LIKE ?)")
        params.extend([needle, needle])
    if tag:
        where.append("EXISTS (SELECT 1 FROM tags t WHERE t.post_id = p.id AND t.slug = ?)")
        params.append(tag)
    if date_from is not None:
        where.append("COALESCE(p.published, 0) >= ?")
        params.append(date_from)
    if date_to is not None:
        where.append("COALESCE(p.published, 0) <= ?")
        params.append(date_to)

    return where, params


def _gallery_post_order(
    sort: str, media_sql: str
) -> tuple[str, list[Any]]:
    """Return ``(ORDER BY, params)`` for a post-grouped gallery page.

    ``media_sql`` is the already-built media condition joined with ``AND``.
    It appears in the duration subquery, so its params are repeated there.
    """
    if sort not in GALLERY_POST_SORTS:
        raise ValueError(f"unknown gallery sort: {sort!r}")

    if sort == "newest":
        return "COALESCE(p.published, 0) DESC, p.id DESC", []
    if sort == "oldest":
        return "COALESCE(p.published, 0) ASC, p.id ASC", []

    aggregate = "max" if sort == "duration_desc" else "min"
    duration = (
        f"(SELECT {aggregate}(d.duration_ms) FROM files d "
        f"WHERE d.post_id = p.id AND {_media_kind_expr('d')} = 'video' "
        f"AND d.duration_ms IS NOT NULL AND d.duration_ms > 0 AND "
        f"{media_sql.replace('f.', 'd.')})"
    )
    direction = "DESC" if sort == "duration_desc" else "ASC"
    order = (
        f"CASE WHEN {duration} IS NULL THEN 1 ELSE 0 END, "
        f"{duration} {direction}, COALESCE(p.published, 0) DESC, p.id DESC"
    )
    return order, []


def list_gallery_posts(
    conn: sqlite3.Connection,
    creator_id: int,
    *,
    kind: str | None = None,
    q: str | None = None,
    tag: str | None = None,
    orientation: str | None = None,
    min_bytes: int | None = None,
    date_from: int | None = None,
    date_to: int | None = None,
    sort: str = "newest",
    limit: int = 20,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Return ``(post_groups, total_posts)`` for a creator's gallery (T4.11).

    Pagination counts posts, not media. Each group carries the post's matching
    media in viewer order (``COALESCE(position, 0), id``) and a ``viewer_index``
    describing that playlist: when a media filter is active it ranks only the
    matching rows, otherwise it counts every viewer-kind file (T11.6). The page
    costs three queries: total, post page and one batched media fetch.
    """
    if sort not in GALLERY_POST_SORTS:
        raise ValueError(f"unknown gallery sort: {sort!r}")

    media_where, media_params = _gallery_media_where(
        kind=kind, orientation=orientation, min_bytes=min_bytes
    )
    post_where, post_params = _gallery_post_where(
        creator_id, q=q, tag=tag, date_from=date_from, date_to=date_to
    )
    media_sql = " AND ".join(media_where)
    exists_sql = (
        "EXISTS (SELECT 1 FROM files f WHERE f.post_id = p.id AND " + media_sql + ")"
    )
    where_sql = " AND ".join([*post_where, exists_sql])

    total = int(
        conn.execute(
            f"SELECT count(*) AS n FROM posts p WHERE {where_sql}",
            [*post_params, *media_params],
        ).fetchone()["n"]
    )

    order_sql, _ = _gallery_post_order(sort, media_sql)
    order_params = (
        [*media_params, *media_params]
        if sort in {"duration_desc", "duration_asc"}
        else []
    )
    post_rows = conn.execute(
        f"""
        SELECT p.id, p.service_id, p.title, p.published,
               (SELECT f.thumb_url FROM files f
                  WHERE f.post_id = p.id AND {media_sql}
                    AND f.thumb_url IS NOT NULL
                  ORDER BY COALESCE(f.position, 0), f.id LIMIT 1) AS thumb_url
        FROM posts p
        WHERE {where_sql}
        ORDER BY {order_sql}
        LIMIT ? OFFSET ?
        """,
        [*media_params, *post_params, *media_params, *order_params, limit, offset],
    ).fetchall()

    groups = [dict(row) | {"media": []} for row in post_rows]
    if not groups:
        return groups, total

    by_id = {int(group["id"]): group for group in groups}
    ids = list(by_id)
    placeholders = ",".join("?" for _ in ids)
    viewer_placeholders = ",".join("?" for _ in VIEWER_KINDS)
    kind_sql = _media_kind_expr("f")
    # The tile's viewer fallback index must describe the *filtered* playlist
    # (T11.6). Counting every viewer-kind row would open the wrong item when a
    # media filter is active, so rank only rows matching the media conditions.
    media_filtered = bool(kind or orientation or min_bytes)
    ranked_where = (
        media_sql if media_filtered else f"f.kind IN ({viewer_placeholders})"
    )
    ranked_params = media_params if media_filtered else list(VIEWER_KINDS)
    outer_where = "1 = 1" if media_filtered else media_sql.replace("f.", "r.")
    outer_params = [] if media_filtered else media_params
    media_rows = conn.execute(
        f"""
        WITH ranked AS (
          SELECT f.*, {kind_sql} AS effective_kind,
                 ROW_NUMBER() OVER (
                   PARTITION BY f.post_id
                   ORDER BY COALESCE(f.position, 0), f.id
                 ) - 1 AS viewer_index
          FROM files f
          WHERE f.post_id IN ({placeholders})
            AND {ranked_where}
        )
        SELECT r.id, r.post_id, r.position, r.sha256, r.effective_kind AS kind,
               r.mime_type, r.bytes, r.width, r.height, r.duration_ms,
               r.variants_json, r.original_filename, r.url, r.thumb_url,
               r.viewer_index
        FROM ranked r
        WHERE {outer_where}
        ORDER BY r.post_id, COALESCE(r.position, 0), r.id
        """,
        [*ids, *ranked_params, *outer_params],
    ).fetchall()

    for row in media_rows:
        by_id[int(row["post_id"])]["media"].append(dict(row))
    return groups, total


def gallery_stats(conn: sqlite3.Connection, creator_id: int) -> dict[str, Any]:
    """Header counts for the gallery page (one scan, not six subqueries)."""
    kinds_sql = ",".join("?" for _ in GALLERY_KINDS)
    row = conn.execute(
        f"""
        SELECT
          count(DISTINCT CASE WHEN f.kind = 'image' THEN f.sha256 END) AS images,
          count(DISTINCT CASE WHEN f.kind = 'video' THEN f.sha256 END) AS videos,
          count(DISTINCT CASE WHEN f.kind = 'gif' THEN f.sha256 END) AS gifs,
          count(DISTINCT CASE WHEN f.kind = 'audio' THEN f.sha256 END) AS audio,
          count(DISTINCT CASE WHEN f.kind IN ({kinds_sql}) THEN f.post_id END) AS posts,
          sum(CASE WHEN f.kind IN ({kinds_sql}) THEN 1 ELSE 0 END) AS files
        FROM files f JOIN posts p ON p.id = f.post_id
        WHERE p.creator_id = ?
        """,
        (*GALLERY_KINDS, *GALLERY_KINDS, creator_id),
    ).fetchone()
    return dict(row) if row is not None else {}


def gallery_tag_facets(
    conn: sqlite3.Connection, creator_id: int, *, limit: int = 20
) -> list[dict[str, Any]]:
    """Tags that actually occur on posts carrying eligible gallery media."""
    kinds_sql = ",".join("?" for _ in GALLERY_KINDS)
    rows = conn.execute(
        f"""
        SELECT t.slug, max(t.label) AS label, count(DISTINCT p.id) AS n
        FROM tags t JOIN posts p ON p.id = t.post_id
        WHERE p.creator_id = ?
          AND EXISTS (
            SELECT 1 FROM files f
            WHERE f.post_id = p.id AND f.kind IN ({kinds_sql})
          )
        GROUP BY t.slug
        ORDER BY n DESC, t.slug
        LIMIT ?
        """,
        (creator_id, *GALLERY_KINDS, limit),
    ).fetchall()
    return _rows(rows)


# --------------------------------------------------------------------------- #
# fullscreen viewer (T8.1)
# --------------------------------------------------------------------------- #


def _viewer_kind_placeholders() -> str:
    return ",".join("?" for _ in VIEWER_KINDS)


def latest_viewer_post(
    conn: sqlite3.Connection, creator_id: int
) -> dict[str, Any] | None:
    """Newest post carrying at least one viewer-eligible file."""
    row = conn.execute(
        f"""
        SELECT p.*
        FROM posts p
        WHERE p.creator_id = ?
          AND EXISTS (
            SELECT 1 FROM files f
            WHERE f.post_id = p.id AND f.kind IN ({_viewer_kind_placeholders()})
          )
        ORDER BY COALESCE(p.published, 0) DESC, p.id DESC
        LIMIT 1
        """,
        (creator_id, *VIEWER_KINDS),
    ).fetchone()
    return _dict(row)


def viewer_media(
    conn: sqlite3.Connection,
    creator_id: int,
    post_service_id: str,
    *,
    kind: str | None = None,
    orientation: str | None = None,
    min_bytes: int | None = None,
) -> list[dict[str, Any]]:
    """Viewer-eligible files for one post, in ``position`` order.

    The payload is deliberately post-scoped: no archive-wide sha dedupe, so the
    same attachment on two posts stays reachable from both (T8.1). When a media
    filter is active the conditions (kind / orientation / min size) are the
    gallery's, so the playlist and the grid always select the same rows
    (T11.6). Without one, the historical viewer-kind behaviour is unchanged.
    """
    if kind or orientation or min_bytes:
        media_where, media_params = _gallery_media_where(
            kind=kind, orientation=orientation, min_bytes=min_bytes
        )
        media_sql = " AND ".join(media_where)
        rows = conn.execute(
            f"""
            SELECT f.position AS post_position, f.sha256,
                   {_media_kind_expr("f")} AS kind, f.mime_type,
                   f.width, f.height, f.duration_ms, f.bytes, f.url, f.thumb_url,
                   f.variants_json, f.original_filename,
                   p.service_id AS post_service_id
            FROM files f
            JOIN posts p ON p.id = f.post_id
            WHERE p.creator_id = ? AND p.service_id = ?
              AND {media_sql}
            ORDER BY COALESCE(f.position, 0), f.id
            """,
            (creator_id, post_service_id, *media_params),
        ).fetchall()
    else:
        rows = conn.execute(
            f"""
            SELECT f.position AS post_position, f.sha256, f.kind, f.mime_type,
                   f.width, f.height, f.duration_ms, f.bytes, f.url, f.thumb_url,
                   f.variants_json, f.original_filename,
                   p.service_id AS post_service_id
            FROM files f
            JOIN posts p ON p.id = f.post_id
            WHERE p.creator_id = ? AND p.service_id = ?
              AND f.kind IN ({_viewer_kind_placeholders()})
            ORDER BY COALESCE(f.position, 0), f.id
            """,
            (creator_id, post_service_id, *VIEWER_KINDS),
        ).fetchall()

    media: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        item = dict(row)
        item["variants"] = _variants_from_json(item.pop("variants_json", None))
        item["index"] = index
        item["filename"] = item.pop("original_filename", None) or (
            f"{(item.get('sha256') or 'media')[:12]}"
        )
        media.append(item)
    return media


def viewer_neighbour(
    conn: sqlite3.Connection,
    creator_id: int,
    post: dict[str, Any],
    direction: str,
) -> str | None:
    """Nearest post with viewer media that is ``newer`` or ``older``.

    List order is ``published DESC``: newer means a higher ``published`` (and
    higher post id on a tie); older means the opposite. Posts without eligible
    media are skipped entirely, so a text-only post is never a destination.
    """
    if direction not in {"newer", "older"}:
        raise ValueError(f"unknown viewer direction: {direction!r}")

    marker = int(post.get("published") or 0)
    post_id = int(post["id"])
    if direction == "newer":
        comparison = (
            "(COALESCE(p.published, 0) > ? "
            "OR (COALESCE(p.published, 0) = ? AND p.id > ?))"
        )
        order = "COALESCE(p.published, 0) ASC, p.id ASC"
    else:
        comparison = (
            "(COALESCE(p.published, 0) < ? "
            "OR (COALESCE(p.published, 0) = ? AND p.id < ?))"
        )
        order = "COALESCE(p.published, 0) DESC, p.id DESC"

    row = conn.execute(
        f"""
        SELECT p.service_id
        FROM posts p
        WHERE p.creator_id = ?
          AND {comparison}
          AND EXISTS (
            SELECT 1 FROM files f
            WHERE f.post_id = p.id AND f.kind IN ({_viewer_kind_placeholders()})
          )
        ORDER BY {order}
        LIMIT 1
        """,
        (creator_id, marker, marker, post_id, *VIEWER_KINDS),
    ).fetchone()
    return str(row["service_id"]) if row is not None else None


def _viewer_scope_sql(
    creator_id: int,
    *,
    kind: str | None = None,
    q: str | None = None,
    tag: str | None = None,
    orientation: str | None = None,
    min_bytes: int | None = None,
    date_from: int | None = None,
    date_to: int | None = None,
    sort: str = "newest",
) -> tuple[str, list[Any], list[Any]]:
    """Return ``(ranked CTE, order-first params, where params)`` for T11.6.

    The ranked CTE lists every post the filtered gallery would show, in the
    grid's order, with a zero-based ``pos``. The same SQL powers neighbour
    lookup, rank counters and the default post for a filtered request, so the
    viewer cannot drift from ``list_gallery_posts``.
    """
    media_where, media_params = _gallery_media_where(
        kind=kind, orientation=orientation, min_bytes=min_bytes
    )
    media_sql = " AND ".join(media_where)
    post_where, post_params = _gallery_post_where(
        creator_id, q=q, tag=tag, date_from=date_from, date_to=date_to
    )
    where_sql = " AND ".join(
        [
            *post_where,
            f"EXISTS (SELECT 1 FROM files f WHERE f.post_id = p.id AND {media_sql})",
        ]
    )
    order_sql, _ = _gallery_post_order(sort, media_sql)
    # For duration sorts the ORDER BY subquery repeats the media params twice:
    # once for the null check and once for the aggregate. It appears in the
    # CTE's select list, before WHERE, so it binds first.
    order_params = (
        [*media_params, *media_params]
        if sort in {"duration_desc", "duration_asc"}
        else []
    )
    cte = f"""
        WITH ranked AS (
          SELECT p.id, p.service_id, p.published,
                 ROW_NUMBER() OVER (ORDER BY {order_sql}) - 1 AS pos
          FROM posts p
          WHERE {where_sql}
        )
    """
    return cte, order_params, [*post_params, *media_params]


def _viewer_post_window(
    conn: sqlite3.Connection,
    creator_id: int,
    post: dict[str, Any],
    *,
    kind: str | None = None,
    q: str | None = None,
    tag: str | None = None,
    orientation: str | None = None,
    min_bytes: int | None = None,
    date_from: int | None = None,
    date_to: int | None = None,
    sort: str = "newest",
) -> dict[str, Any] | None:
    """Rank plus list-order neighbours for one post in the filtered scope.

    ``pos`` is zero-based; ``rank`` is one-based. Neighbours follow the grid
    sort, so Up/Down move the same way the gallery grid does (T11.6).
    """
    cte, order_params, where_params = _viewer_scope_sql(
        creator_id,
        kind=kind,
        q=q,
        tag=tag,
        orientation=orientation,
        min_bytes=min_bytes,
        date_from=date_from,
        date_to=date_to,
        sort=sort,
    )
    row = conn.execute(
        cte
        + """
        SELECT r.pos,
               (SELECT count(*) FROM ranked) AS total,
               (SELECT service_id FROM ranked WHERE pos = r.pos - 1) AS previous_service,
               (SELECT service_id FROM ranked WHERE pos = r.pos + 1) AS next_service
        FROM ranked r
        WHERE r.id = ?
        """,
        [*order_params, *where_params, int(post["id"])],
    ).fetchone()
    if row is None:
        return None
    return {
        "rank": int(row["pos"]) + 1,
        "total": int(row["total"] or 0),
        "previous_post": (
            str(row["previous_service"]) if row["previous_service"] is not None else None
        ),
        "next_post": (
            str(row["next_service"]) if row["next_service"] is not None else None
        ),
    }


def first_viewer_post(
    conn: sqlite3.Connection,
    creator_id: int,
    *,
    kind: str | None = None,
    q: str | None = None,
    tag: str | None = None,
    orientation: str | None = None,
    min_bytes: int | None = None,
    date_from: int | None = None,
    date_to: int | None = None,
    sort: str = "newest",
) -> dict[str, Any] | None:
    """First post in the filtered gallery order (T11.6)."""
    if get_creator(conn, creator_id) is None:
        return None
    cte, order_params, where_params = _viewer_scope_sql(
        creator_id,
        kind=kind,
        q=q,
        tag=tag,
        orientation=orientation,
        min_bytes=min_bytes,
        date_from=date_from,
        date_to=date_to,
        sort=sort,
    )
    row = conn.execute(
        cte + "SELECT id FROM ranked WHERE pos = 0 LIMIT 1",
        [*order_params, *where_params],
    ).fetchone()
    if row is None:
        return None
    return get_post(conn, int(row["id"]))


def _neighbour_preload_images(
    conn: sqlite3.Connection,
    creator_id: int,
    post_service_id: str | None,
    direction: str,
    limit: int,
    *,
    kind: str | None = None,
    orientation: str | None = None,
    min_bytes: int | None = None,
) -> list[dict[str, Any]]:
    """Edge-ordered still images for one already-resolved neighbour post."""
    if not post_service_id or limit <= 0:
        return []
    # Videos-only and GIF-only views have no still image to preload (T11.6).
    if kind in {"video", "gif"}:
        return []
    if direction == "previous":
        order = "COALESCE(f.position, 0) DESC, f.id DESC"
    elif direction == "next":
        order = "COALESCE(f.position, 0) ASC, f.id ASC"
    else:
        raise ValueError(f"unknown neighbour direction: {direction!r}")

    if orientation or min_bytes or kind:
        media_where, media_params = _gallery_media_where(
            kind="image", orientation=orientation, min_bytes=min_bytes
        )
        still_sql = " AND ".join(media_where)
        rows = conn.execute(
            f"""
            SELECT p.service_id AS post_service_id, f.sha256, f.url, f.thumb_url,
                   f.width, f.height, f.original_filename
            FROM files f
            JOIN posts p ON p.id = f.post_id
            WHERE p.creator_id = ? AND p.service_id = ?
              AND {still_sql}
              AND f.url IS NOT NULL AND f.url != ''
              AND f.url NOT LIKE 'data:%'
            ORDER BY {order}
            LIMIT ?
            """,
            (creator_id, post_service_id, *media_params, int(limit)),
        ).fetchall()
    else:
        rows = conn.execute(
            f"""
            SELECT p.service_id AS post_service_id, f.sha256, f.url, f.thumb_url,
                   f.width, f.height, f.original_filename
            FROM files f
            JOIN posts p ON p.id = f.post_id
            WHERE p.creator_id = ? AND p.service_id = ?
              AND f.kind = 'image'
              AND f.url IS NOT NULL AND f.url != ''
              AND f.url NOT LIKE 'data:%'
            ORDER BY {order}
            LIMIT ?
            """,
            (creator_id, post_service_id, int(limit)),
        ).fetchall()
    images = _rows(rows)
    for image in images:
        image["kind"] = "image"
    return images


def neighbour_preload_images(
    conn: sqlite3.Connection,
    creator_id: int,
    post: dict[str, Any],
    direction: str,
    limit: int = NEIGHBOUR_PRELOAD_LIMIT,
    *,
    kind: str | None = None,
    q: str | None = None,
    tag: str | None = None,
    orientation: str | None = None,
    min_bytes: int | None = None,
    date_from: int | None = None,
    date_to: int | None = None,
    sort: str = "newest",
) -> list[dict[str, Any]]:
    """Return the edge images of the ``previous`` or ``next`` viewer post.

    ``previous`` is the post before the current one in the gallery's order and
    is ordered nearest-edge first (``position DESC``) because crossing left
    lands on its last item. ``next`` is the following post and is ordered
    nearest-edge first (``position ASC``) because crossing right lands on its
    first item (T8.8, T11.6).
    """
    if direction not in {"previous", "next"}:
        raise ValueError(f"unknown neighbour direction: {direction!r}")
    window = _viewer_post_window(
        conn,
        creator_id,
        post,
        kind=kind,
        q=q,
        tag=tag,
        orientation=orientation,
        min_bytes=min_bytes,
        date_from=date_from,
        date_to=date_to,
        sort=sort,
    )
    service_id = window[direction + "_post"] if window else None
    return _neighbour_preload_images(
        conn,
        creator_id,
        service_id,
        direction,
        int(limit),
        kind=kind,
        orientation=orientation,
        min_bytes=min_bytes,
    )


def viewer_post_rank(
    conn: sqlite3.Connection, creator_id: int, post: dict[str, Any]
) -> tuple[int, int]:
    """One-based position of ``post`` among media posts, newest first, and total.

    Mirrors the ``latest_viewer_post`` ordering so the counter announced to
    screen readers agrees with Up/Down navigation (T8.4).
    """
    marker = int(post.get("published") or 0)
    post_id = int(post["id"])
    row = conn.execute(
        f"""
        SELECT
          SUM(
            CASE WHEN COALESCE(p.published, 0) > ?
                   OR (COALESCE(p.published, 0) = ? AND p.id >= ?)
                 THEN 1 ELSE 0 END
          ) AS rank,
          COUNT(*) AS total
        FROM posts p
        WHERE p.creator_id = ?
          AND EXISTS (
            SELECT 1 FROM files f
            WHERE f.post_id = p.id AND f.kind IN ({_viewer_kind_placeholders()})
          )
        """,
        (marker, marker, post_id, creator_id, *VIEWER_KINDS),
    ).fetchone()
    if row is None:
        return 1, 1
    return int(row["rank"] or 1), int(row["total"] or 1)


def viewer_state(
    conn: sqlite3.Connection,
    creator_id: int,
    post_service_id: str | None = None,
    *,
    kind: str | None = None,
    q: str | None = None,
    tag: str | None = None,
    orientation: str | None = None,
    min_bytes: int | None = None,
    date_from: int | None = None,
    date_to: int | None = None,
    sort: str = "newest",
) -> dict[str, Any] | None:
    """Assemble the viewer payload for one post (or the latest media post).

    Returns ``None`` for an unknown creator, an unknown post, or a post with no
    viewer-eligible media. The full media list is current-post scoped; adjacent
    posts contribute only bounded image descriptors for preloading (T8.8).

    Passing gallery filters scopes the whole payload to the filtered result set
    (T11.6): the current playlist, its neighbour chain, the rank counters and
    the preload window all describe what the grid is showing.
    """
    creator = get_creator(conn, creator_id)
    if creator is None:
        return None

    filters = {
        "kind": kind,
        "q": q,
        "tag": tag,
        "orientation": orientation,
        "min_bytes": min_bytes,
        "date_from": date_from,
        "date_to": date_to,
        "sort": sort,
    }

    if post_service_id is None:
        post = None
        if any(value is not None for name, value in filters.items() if name != "sort") or sort != "newest":
            post = first_viewer_post(conn, creator_id, **filters)
        if post is None:
            post = latest_viewer_post(conn, creator_id)
    else:
        post = get_post_by_service_id(conn, creator_id, post_service_id)
    if post is None:
        return None

    media = viewer_media(
        conn,
        creator_id,
        str(post["service_id"]),
        kind=kind,
        orientation=orientation,
        min_bytes=min_bytes,
    )
    if not media:
        return None

    window = _viewer_post_window(conn, creator_id, post, **filters)
    if window is None:
        post_rank, media_post_count = 0, 0
        previous_post = None
        next_post = None
    else:
        post_rank = window["rank"]
        media_post_count = window["total"]
        previous_post = window["previous_post"]
        next_post = window["next_post"]

    bookmarked_shas = bookmarked_media_shas(
        conn, creator_id, [item["sha256"] for item in media]
    )
    for item in media:
        item["bookmarked"] = item["sha256"] in bookmarked_shas

    return {
        "creator": {
            "id": creator["id"],
            "name": creator.get("display_name")
            or creator.get("name")
            or creator.get("service_id"),
            "service": creator.get("service"),
        },
        "post": {
            "service_id": post["service_id"],
            "title": post.get("title"),
            "published": post.get("published"),
            "post_url": f"/c/{int(creator_id)}/p/{post['service_id']}",
            "media_count": len(media),
            "bookmarked": post_is_bookmarked(conn, creator_id, int(post["id"])),
        },
        "media": media,
        "index": 0,
        "post_rank": post_rank,
        "media_post_count": media_post_count,
        "previous_post": previous_post,
        "next_post": next_post,
        "neighbour_images": {
            "previous": _neighbour_preload_images(
                conn,
                creator_id,
                previous_post,
                "previous",
                NEIGHBOUR_PRELOAD_LIMIT,
                kind=kind,
                orientation=orientation,
                min_bytes=min_bytes,
            ),
            "next": _neighbour_preload_images(
                conn,
                creator_id,
                next_post,
                "next",
                NEIGHBOUR_PRELOAD_LIMIT,
                kind=kind,
                orientation=orientation,
                min_bytes=min_bytes,
            ),
        },
    }


def replace_links(
    conn: sqlite3.Connection, post_id: int, rows: Sequence[dict[str, Any]]
) -> int:
    """Replace a post's links, deduplicating by normalized_url."""
    with conn:
        return _write_links(conn, post_id, rows)


def _write_links(
    conn: sqlite3.Connection, post_id: int, rows: Sequence[dict[str, Any]]
) -> int:
    seen: set[str] = set()
    values: list[tuple[Any, ...]] = []
    for row in rows:
        normalized = row.get("normalized_url") or row.get("url")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        values.append(
            (
                post_id,
                row["url"],
                normalized,
                row.get("label"),
                row.get("category") or "other",
                row.get("origin") or "caption",
                row.get("resolved_url"),
            )
        )

    conn.execute("DELETE FROM links WHERE post_id = ?", (post_id,))
    if values:
        conn.executemany(
            """
            INSERT INTO links (
              post_id, url, normalized_url, label, category, origin, resolved_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
    return len(values)


def list_links(conn: sqlite3.Connection, post_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM links WHERE post_id = ? ORDER BY id", (post_id,)
    ).fetchall()
    return _rows(rows)


# --------------------------------------------------------------------------- #
# link explorer
# --------------------------------------------------------------------------- #

# SQLite has no URL parser; this extracts the host between "://" and the next
# slash so "sort by host" stays stable and index-friendly enough for 10k rows.
_LINK_HOST = """lower(
  substr(
    substr(l.url, instr(l.url, '://') + 3),
    1,
    CASE
      WHEN instr(substr(l.url, instr(l.url, '://') + 3), '/') > 0
        THEN instr(substr(l.url, instr(l.url, '://') + 3), '/') - 1
      ELSE length(substr(l.url, instr(l.url, '://') + 3))
    END
  )
)"""

_LINK_SORTS = {
    "date_desc": "COALESCE(p.published, 0) DESC, l.id DESC",
    "category": "l.category ASC, COALESCE(p.published, 0) DESC, l.id DESC",
    "host": f"{_LINK_HOST} ASC, COALESCE(p.published, 0) DESC, l.id DESC",
}

_LINK_SELECT = """
SELECT l.id, l.url, l.normalized_url, l.label, l.category, l.origin,
       l.resolved_url,
       p.service_id AS post_id, p.title AS post_title, p.published,
       c.id AS creator_id,
       COALESCE(NULLIF(c.display_name, ''), NULLIF(c.name, ''), c.service_id)
         AS creator,
       c.service AS service
FROM links l
JOIN posts p ON p.id = l.post_id
JOIN creators c ON c.id = p.creator_id
"""


def _link_where(
    *,
    creator_id: int | None = None,
    categories: Sequence[str] = (),
    q: str | None = None,
    has_resolved: bool | None = None,
) -> tuple[str, list[Any]]:
    where: list[str] = []
    params: list[Any] = []

    if creator_id is not None:
        where.append("p.creator_id = ?")
        params.append(int(creator_id))

    cleaned = tuple(dict.fromkeys(value for value in categories if value))
    if cleaned:
        placeholders = ",".join("?" for _ in cleaned)
        where.append(f"l.category IN ({placeholders})")
        params.extend(cleaned)

    if has_resolved is not None:
        if has_resolved:
            where.append("l.resolved_url IS NOT NULL AND l.resolved_url <> ''")
        else:
            where.append("(l.resolved_url IS NULL OR l.resolved_url = '')")

    if q:
        needle = f"%{q}%"
        where.append("(l.label LIKE ? OR l.url LIKE ?)")
        params.extend([needle, needle])

    return (" AND ".join(where) or "1=1"), params


def list_explorer_links(
    conn: sqlite3.Connection,
    *,
    creator_id: int | None = None,
    categories: Sequence[str] = (),
    q: str | None = None,
    has_resolved: bool | None = None,
    sort: str = "date_desc",
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """One page of links with post/creator context, plus the filtered total."""
    if sort not in _LINK_SORTS:
        raise ValueError(f"unknown link sort: {sort!r}")

    where_sql, params = _link_where(
        creator_id=creator_id,
        categories=categories,
        q=q,
        has_resolved=has_resolved,
    )
    total = int(
        conn.execute(
            f"""
            SELECT count(*) AS n
            FROM links l
            JOIN posts p ON p.id = l.post_id
            WHERE {where_sql}
            """,
            params,
        ).fetchone()["n"]
    )
    rows = conn.execute(
        f"""
        {_LINK_SELECT}
        WHERE {where_sql}
        ORDER BY {_LINK_SORTS[sort]}
        LIMIT ? OFFSET ?
        """,
        [*params, max(0, limit), max(0, offset)],
    ).fetchall()
    return _rows(rows), total


def iter_explorer_links(
    conn: sqlite3.Connection,
    *,
    creator_id: int | None = None,
    categories: Sequence[str] = (),
    q: str | None = None,
    has_resolved: bool | None = None,
    sort: str = "date_desc",
):
    """Stream every matching link (exports); the caller owns the connection."""
    if sort not in _LINK_SORTS:
        raise ValueError(f"unknown link sort: {sort!r}")
    where_sql, params = _link_where(
        creator_id=creator_id,
        categories=categories,
        q=q,
        has_resolved=has_resolved,
    )
    cursor = conn.execute(
        f"""
        {_LINK_SELECT}
        WHERE {where_sql}
        ORDER BY {_LINK_SORTS[sort]}
        """,
        params,
    )
    for row in cursor:
        yield dict(row)


def link_category_counts(
    conn: sqlite3.Connection,
    *,
    creator_id: int | None = None,
    q: str | None = None,
    has_resolved: bool | None = None,
) -> dict[str, int]:
    """Per-category counts under every filter except the category checkboxes."""
    where_sql, params = _link_where(
        creator_id=creator_id, q=q, has_resolved=has_resolved
    )
    rows = conn.execute(
        f"""
        SELECT l.category AS category, count(*) AS n
        FROM links l
        JOIN posts p ON p.id = l.post_id
        WHERE {where_sql}
        GROUP BY l.category
        """,
        params,
    ).fetchall()
    return {str(row["category"]): int(row["n"]) for row in rows}


def unresolved_shortener_count(
    conn: sqlite3.Connection,
    *,
    creator_id: int | None = None,
    q: str | None = None,
    has_resolved: bool | None = None,
) -> int:
    where_sql, params = _link_where(
        creator_id=creator_id, q=q, has_resolved=has_resolved
    )
    return int(
        conn.execute(
            f"""
            SELECT count(*) AS n
            FROM links l
            JOIN posts p ON p.id = l.post_id
            WHERE {where_sql}
              AND l.category = 'shortener'
              AND (l.resolved_url IS NULL OR l.resolved_url = '')
            """,
            params,
        ).fetchone()["n"]
    )


def link_creator_facets(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Creators with at least one link, for the archive-wide filter."""
    rows = conn.execute(
        """
        SELECT c.id,
               COALESCE(NULLIF(c.display_name, ''), NULLIF(c.name, ''),
                        c.service_id) AS name,
               count(l.id) AS n
        FROM creators c
        LEFT JOIN posts p ON p.creator_id = c.id
        LEFT JOIN links l ON l.post_id = p.id
        GROUP BY c.id
        HAVING count(l.id) > 0
        ORDER BY name COLLATE NOCASE ASC
        """
    ).fetchall()
    return _rows(rows)


def available_link_categories(
    conn: sqlite3.Connection, creator_id: int | None = None
) -> list[dict[str, Any]]:
    """Categories that actually occur, including unrecognised ones."""
    if creator_id is None:
        rows = conn.execute(
            """
            SELECT l.category AS category, count(*) AS n
            FROM links l
            GROUP BY l.category
            ORDER BY l.category
            """
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT l.category AS category, count(*) AS n
            FROM links l
            JOIN posts p ON p.id = l.post_id
            WHERE p.creator_id = ?
            GROUP BY l.category
            ORDER BY l.category
            """,
            (int(creator_id),),
        ).fetchall()
    return _rows(rows)


def replace_tags(
    conn: sqlite3.Connection, post_id: int, rows: Sequence[dict[str, Any]]
) -> int:
    with conn:
        return _write_tags(conn, post_id, rows)


def _write_tags(
    conn: sqlite3.Connection, post_id: int, rows: Sequence[dict[str, Any]]
) -> int:
    seen: set[str] = set()
    values: list[tuple[Any, ...]] = []
    for row in rows:
        slug = row.get("slug")
        if not slug or slug in seen:
            continue
        seen.add(slug)
        values.append((post_id, slug, row.get("label")))

    conn.execute("DELETE FROM tags WHERE post_id = ?", (post_id,))
    if values:
        conn.executemany(
            "INSERT INTO tags (post_id, slug, label) VALUES (?, ?, ?)", values
        )
    return len(values)


def list_tags(conn: sqlite3.Connection, post_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM tags WHERE post_id = ? ORDER BY slug", (post_id,)
    ).fetchall()
    return _rows(rows)


# --------------------------------------------------------------------------- #
# runs
# --------------------------------------------------------------------------- #


def cancel_stale_runs(conn: sqlite3.Connection, creator_id: int) -> int:
    """A run left ``running`` by a killed process is marked interrupted."""
    cur = conn.execute(
        """
        UPDATE runs SET status = 'cancelled', finished_at = ?,
          error = COALESCE(error, 'interrupted')
        WHERE creator_id = ? AND status = 'running'
        """,
        (now_iso(), creator_id),
    )
    return cur.rowcount


def create_run(conn: sqlite3.Connection, creator_id: int, mode: str) -> int:
    cur = conn.execute(
        "INSERT INTO runs (creator_id, mode, status, started_at) VALUES (?, ?, 'running', ?)",
        (creator_id, mode, now_iso()),
    )
    return int(cur.lastrowid)


def finish_run(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    status: str,
    pages: int = 0,
    posts_seen: int = 0,
    posts_done: int = 0,
    posts_new: int = 0,
    posts_updated: int = 0,
    links_found: int = 0,
    files_count: int = 0,
    error: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE runs SET
          status = ?, finished_at = ?, pages = ?, posts_seen = ?, posts_done = ?,
          posts_new = ?, posts_updated = ?, links_found = ?, files_count = ?, error = ?
        WHERE id = ?
        """,
        (
            status,
            now_iso(),
            pages,
            posts_seen,
            posts_done,
            posts_new,
            posts_updated,
            links_found,
            files_count,
            error,
            run_id,
        ),
    )


def get_run(conn: sqlite3.Connection, run_id: int) -> dict[str, Any] | None:
    return _dict(conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone())


def active_run(conn: sqlite3.Connection, creator_id: int) -> dict[str, Any] | None:
    """Newest run still marked ``running`` for a creator."""
    return _dict(
        conn.execute(
            """
            SELECT * FROM runs
            WHERE creator_id = ? AND status = 'running'
            ORDER BY id DESC LIMIT 1
            """,
            (int(creator_id),),
        ).fetchone()
    )


def mark_run_cancelled(
    conn: sqlite3.Connection, run_id: int, *, error: str = "cancelled by user"
) -> bool:
    """Cancel a run that has no live task (for example after a restart)."""
    cur = conn.execute(
        """
        UPDATE runs SET status = 'cancelled', finished_at = ?,
          error = COALESCE(error, ?)
        WHERE id = ? AND status = 'running'
        """,
        (now_iso(), error, int(run_id)),
    )
    return cur.rowcount > 0


def list_runs(
    conn: sqlite3.Connection,
    creator_id: int | None = None,
    *,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    if creator_id is None:
        rows = conn.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT ? OFFSET ?", (limit, offset)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM runs WHERE creator_id = ? ORDER BY id DESC LIMIT ? OFFSET ?",
            (creator_id, limit, offset),
        ).fetchall()
    return _rows(rows)


# --------------------------------------------------------------------------- #
# response cache / link resolutions
# --------------------------------------------------------------------------- #


def cache_get(conn: sqlite3.Connection, url: str) -> dict[str, Any] | None:
    return _dict(
        conn.execute("SELECT * FROM api_cache WHERE url = ?", (url,)).fetchone()
    )


def cache_put(conn: sqlite3.Connection, url: str, body: str) -> None:
    conn.execute(
        """
        INSERT INTO api_cache (url, fetched_at, body) VALUES (?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET fetched_at = excluded.fetched_at, body = excluded.body
        """,
        (url, now_iso(), body),
    )
    conn.commit()


def cache_delete(conn: sqlite3.Connection, url: str) -> None:
    conn.execute("DELETE FROM api_cache WHERE url = ?", (url,))


def cache_clear(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM api_cache")


def cache_prune(conn: sqlite3.Connection, max_age_days: int = 7) -> int:
    """Drop cached API responses older than ``max_age_days``.

    The cache is a "don't refetch" layer, not an archive, so old entries are
    removed to keep the database from growing without bound (T6.5).
    """
    cutoff = (
        datetime.now(UTC) - timedelta(days=max(1, max_age_days))
    ).isoformat(timespec="seconds")
    cursor = conn.execute("DELETE FROM api_cache WHERE fetched_at < ?", (cutoff,))
    conn.commit()
    return cursor.rowcount or 0


def get_resolution(conn: sqlite3.Connection, url: str) -> dict[str, Any] | None:
    return _dict(
        conn.execute("SELECT * FROM link_resolutions WHERE url = ?", (url,)).fetchone()
    )


def put_resolution(
    conn: sqlite3.Connection, url: str, final_url: str | None
) -> None:
    conn.execute(
        """
        INSERT INTO link_resolutions (url, final_url, resolved_at) VALUES (?, ?, ?)
        ON CONFLICT(url) DO UPDATE SET
          final_url = excluded.final_url, resolved_at = excluded.resolved_at
        """,
        (url, final_url, now_iso()),
    )
    conn.commit()


# --------------------------------------------------------------------------- #
# stats
# --------------------------------------------------------------------------- #


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    names = [
        "creators",
        "posts",
        "files",
        "links",
        "tags",
        "runs",
        "api_cache",
        "link_resolutions",
    ]
    counts: dict[str, int] = {}
    for name in names:
        try:
            row = conn.execute(f"SELECT count(*) AS n FROM {name}").fetchone()
            counts[name] = int(row["n"])
        except sqlite3.OperationalError:
            counts[name] = 0
    return counts


def print_stats() -> int:
    settings = get_settings()
    path: Path = settings.db_path
    print(f"database: {path}")
    if not path.exists():
        print("status:   empty (run a sync first)")
        return 0

    conn = connect(settings)
    try:
        counts = table_counts(conn)
    finally:
        conn.close()

    for name, count in counts.items():
        print(f"{name + ':':<18}{count}")
    size = path.stat().st_size
    print(f"{'size:':<18}{size / 1024:.1f} KiB")
    return 0
