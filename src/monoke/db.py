"""SQLite access: schema migrations and a thin repository layer.

Everything that touches the database goes through this module. Repositories take a
``sqlite3.Connection`` and return plain dicts so callers never depend on sqlite3.Row.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Settings, get_settings

SCHEMA_VERSION = 2

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

MIGRATIONS: tuple[tuple[str, ...], ...] = (_V1, _V2)


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
             JOIN posts p ON p.id = f.post_id WHERE p.creator_id = c.id) AS file_count
        FROM creators c
        ORDER BY COALESCE(c.last_synced_at, '') DESC, c.id DESC
        LIMIT ? OFFSET ?
        """,
        (limit, offset),
    ).fetchall()
    return _rows(rows)


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
    "title_asc": "COALESCE(p.title, '') COLLATE NOCASE ASC, p.id DESC",
    "added_desc": "COALESCE(p.added, 0) DESC, p.id DESC",
}


def list_posts(
    conn: sqlite3.Connection,
    creator_id: int,
    *,
    category: str | None = None,
    tag: str | None = None,
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

    where = ["p.creator_id = ?"]
    params: list[Any] = [creator_id]

    if category:
        where.append(
            "EXISTS (SELECT 1 FROM links l WHERE l.post_id = p.id AND l.category = ?)"
        )
        params.append(category)
    if tag:
        where.append("EXISTS (SELECT 1 FROM tags t WHERE t.post_id = p.id AND t.slug = ?)")
        params.append(tag)
    if has_links is not None:
        op = "EXISTS" if has_links else "NOT EXISTS"
        where.append(f"{op} (SELECT 1 FROM links l WHERE l.post_id = p.id)")
    if has_attachments is not None:
        op = "EXISTS" if has_attachments else "NOT EXISTS"
        where.append(f"{op} (SELECT 1 FROM files f WHERE f.post_id = p.id)")
    if missing is not None:
        where.append("p.missing_upstream = ?")
        params.append(1 if missing else 0)
    if date_from is not None:
        where.append("COALESCE(p.published, 0) >= ?")
        params.append(date_from)
    if date_to is not None:
        where.append("COALESCE(p.published, 0) <= ?")
        params.append(date_to)
    if q:
        needle = f"%{q}%"
        where.append(
            """(
              p.title LIKE ?
              OR p.caption_text LIKE ?
              OR EXISTS (SELECT 1 FROM links l WHERE l.post_id = p.id AND l.url LIKE ?)
            )"""
        )
        params.extend([needle, needle, needle])

    where_sql = " AND ".join(where)

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
              post_id, position, sha256, kind, mime_type, bytes, width, height,
              duration_ms, original_filename, storage_key, variants_json, url, thumb_url
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
