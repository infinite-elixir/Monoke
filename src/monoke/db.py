"""SQLite access.

The schema, migrations and repositories land in T0.2. For now this module only
owns connection setup and the ``monoke db stats`` output.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .config import Settings, get_settings


def connect(settings: Settings | None = None) -> sqlite3.Connection:
    """Open (and create if needed) the Monoke database."""
    settings = settings or get_settings()
    settings.ensure_dirs()
    conn = sqlite3.connect(settings.db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


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
