"""CSV and JSON exporters.

Exporters are pure: they take rows (dicts) rather than a database connection so
the same code serves the CLI and the web endpoints. Link rows are normalized to
a fixed column order, dates become ISO-8601 strings, and CSV is emitted in
streaming batches of UTF-8 bytes with RFC 4180 quoting.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import UTC, date, datetime
from html import unescape
from typing import Any

from lxml import html as lxml_html

_TAG_RE = re.compile(r"<[^>]+>")

LINK_COLUMNS: tuple[str, ...] = (
    "creator",
    "service",
    "post_id",
    "post_title",
    "published",
    "category",
    "label",
    "url",
    "resolved_url",
    "origin",
)
# Backwards/forwards friendly alias for callers that call them "fields".
LINK_FIELDS = LINK_COLUMNS

DEFAULT_BATCH_SIZE = 500


def _as_mapping(row: Any) -> Mapping[str, Any]:
    """Accept dicts, sqlite3.Row objects, and other mapping-like query rows."""
    if isinstance(row, Mapping):
        return row
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {}


def _clean_text(value: Any) -> str | None:
    """Collapse whitespace and strip any HTML that leaked into a text field."""
    if value is None:
        return None
    text = str(value)
    if _TAG_RE.search(text):
        try:
            node = lxml_html.fragment_fromstring(text, create_parent="div")
            text = " ".join(node.itertext())
        except (ValueError, TypeError):
            text = _TAG_RE.sub(" ", text)
    text = " ".join(unescape(text).split())
    return text or None


def _iso_datetime(value: Any) -> str | None:
    """Render epoch seconds, dates, and datetimes as ISO-8601; pass strings on."""
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, UTC).isoformat()
        except (OverflowError, OSError, ValueError):
            return str(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        try:
            return datetime.fromtimestamp(int(value), UTC).isoformat()
        except (OverflowError, OSError, ValueError):
            return value
    return value


def _link_value(row: Mapping[str, Any], column: str) -> Any:
    if column == "creator":
        return _clean_text(
            row.get("creator")
            or row.get("creator_name")
            or row.get("display_name")
            or row.get("name")
        )
    if column == "post_title":
        return _clean_text(row.get("post_title") or row.get("title"))
    if column == "post_id":
        return row.get("post_id") or row.get("service_id")
    if column == "published":
        value = row.get("published")
        if value is None:
            value = row.get("published_at")
        return _iso_datetime(value)
    if column == "label":
        return _clean_text(row.get("label"))
    if column == "category":
        value = row.get("category")
        return getattr(value, "value", value)
    return row.get(column)


def normalize_link(row: Mapping[str, Any]) -> dict[str, Any]:
    """Return one link row with the fixed column order and JSON-safe values."""
    mapping = _as_mapping(row)
    return {column: _link_value(mapping, column) for column in LINK_COLUMNS}


def _csv_cell(value: Any) -> str:
    return "" if value is None else str(value)


def links_csv(
    rows: Iterable[Mapping[str, Any]], *, batch_size: int = DEFAULT_BATCH_SIZE
) -> Iterator[bytes]:
    """Yield a UTF-8 CSV stream: header first, then batches of rows.

    ``batch_size`` controls how many rows are buffered before a chunk is yielded;
    it does not affect the output.
    """
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(LINK_COLUMNS)
    yield buffer.getvalue().encode("utf-8")
    buffer.seek(0)
    buffer.truncate(0)

    for index, row in enumerate(rows, start=1):
        mapping = _as_mapping(row)
        writer.writerow(
            [_csv_cell(_link_value(mapping, column)) for column in LINK_COLUMNS]
        )
        if index % max(1, batch_size) == 0:
            yield buffer.getvalue().encode("utf-8")
            buffer.seek(0)
            buffer.truncate(0)

    remainder = buffer.getvalue()
    if remainder:
        yield remainder.encode("utf-8")


def links_csv_bytes(
    rows: Iterable[Mapping[str, Any]], *, batch_size: int = DEFAULT_BATCH_SIZE
) -> bytes:
    """Materialize :func:`links_csv` for callers that need one bytes object."""
    return b"".join(links_csv(rows, batch_size=batch_size))


def links_csv_text(
    rows: Iterable[Mapping[str, Any]], *, batch_size: int = DEFAULT_BATCH_SIZE
) -> str:
    """Materialize :func:`links_csv` as text (tests, small exports, previews)."""
    return links_csv_bytes(rows, batch_size=batch_size).decode("utf-8")


def links_json(
    rows: Iterable[Mapping[str, Any]],
    *,
    indent: int | None = None,
) -> str:
    """Return a JSON array of link rows with stable keys and ISO dates."""
    data = [normalize_link(row) for row in rows]
    separators = (",", ":") if indent is None else None
    return json.dumps(
        data,
        ensure_ascii=False,
        indent=indent,
        separators=separators,
    )


def links_json_chunks(
    rows: Iterable[Mapping[str, Any]], *, batch_size: int = DEFAULT_BATCH_SIZE
) -> Iterator[bytes]:
    """Yield a JSON array incrementally, one batch of rows at a time."""
    batch: list[dict[str, Any]] = []
    first = True

    def encode(items: list[dict[str, Any]]) -> bytes:
        nonlocal first
        prefix = b"[" if first else b","
        first = False
        return prefix + json.dumps(
            items, ensure_ascii=False, separators=(",", ":")
        )[1:-1].encode("utf-8")

    for row in rows:
        batch.append(normalize_link(row))
        if len(batch) >= max(1, batch_size):
            yield encode(batch)
            batch = []
    if batch:
        yield encode(batch)
    if first:
        yield b"["
    yield b"]"


def links_json_bytes(
    rows: Iterable[Mapping[str, Any]], *, batch_size: int = DEFAULT_BATCH_SIZE
) -> bytes:
    """Materialize the streamed JSON array as one UTF-8 bytes object."""
    return b"".join(links_json_chunks(rows, batch_size=batch_size))


def archive_json(
    creator: Mapping[str, Any] | str,
    posts: Sequence[Mapping[str, Any]],
    links: Iterable[Mapping[str, Any]],
    *,
    exported_at: str | None = None,
    indent: int | None = 2,
) -> str:
    """Return the whole-archive document ``{creator, exported_at, posts, links}``.

    Key order is fixed at the top level and link keys follow ``LINK_COLUMNS``.
    """
    payload = {
        "creator": creator if isinstance(creator, str) else _as_mapping(creator),
        "exported_at": exported_at
        or datetime.now(UTC).isoformat(timespec="seconds"),
        "posts": [_as_mapping(post) for post in posts],
        "links": [normalize_link(row) for row in links],
    }
    separators = None if indent is not None else (",", ":")
    return json.dumps(
        payload, ensure_ascii=False, indent=indent, separators=separators
    )


def archive_json_bytes(
    creator: Mapping[str, Any] | str,
    posts: Sequence[Mapping[str, Any]],
    links: Iterable[Mapping[str, Any]],
    *,
    exported_at: str | None = None,
    indent: int | None = 2,
) -> bytes:
    return archive_json(
        creator, posts, links, exported_at=exported_at, indent=indent
    ).encode("utf-8")


def links_filename(
    service: str,
    creator_id: str | int,
    *,
    extension: str = "csv",
    when: date | datetime | None = None,
) -> str:
    """``monoke-{service}-{creator_id}-links-{YYYYMMDD}.{extension}``."""
    moment = when or datetime.now(UTC)
    stamp = moment.strftime("%Y%m%d")
    return f"monoke-{service}-{creator_id}-links-{stamp}.{extension}"


# Friendly aliases for callers using export_* / to_* naming. Materialized
# variants are the default for one-shot exports; the stream stays explicit.
stream_links_csv = links_csv
stream_links_json = links_json_chunks
export_links_csv = links_csv_bytes
export_links_json = links_json
export_archive_json = archive_json
export_filename = links_filename
filename = links_filename
links_to_csv = links_csv_text
links_to_json = links_json
archive_to_json = archive_json
to_csv = links_csv_text
to_json = links_json

__all__ = [
    "LINK_COLUMNS",
    "LINK_FIELDS",
    "DEFAULT_BATCH_SIZE",
    "archive_json",
    "archive_json_bytes",
    "archive_to_json",
    "export_archive_json",
    "export_filename",
    "export_links_csv",
    "export_links_json",
    "filename",
    "links_csv",
    "links_csv_bytes",
    "links_csv_text",
    "links_filename",
    "links_json",
    "links_json_bytes",
    "links_json_chunks",
    "links_to_csv",
    "links_to_json",
    "normalize_link",
    "stream_links_csv",
    "stream_links_json",
    "to_csv",
    "to_json",
]
