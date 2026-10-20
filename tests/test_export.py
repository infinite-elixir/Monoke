from __future__ import annotations

import csv
import io
import json
import time
from datetime import date

from monoke.extract.export import (
    LINK_COLUMNS,
    archive_json,
    links_csv,
    links_csv_bytes,
    links_csv_text,
    links_filename,
    links_json,
    links_json_bytes,
    links_json_chunks,
)


def make_rows(count: int = 2):
    rows = [
        {
            "creator": "Nyl 日本語",
            "service": "patreon",
            "post_id": "172189145",
            "post_title": "<b>Public Model Release</b> 😀",
            "published": 1700000000,
            # Deliberately noisy aliases to exercise normalization.
            "category": "mega",
            "label": "<i>Backup Link</i>",
            "url": "https://mega.nz/file/abc",
            "resolved_url": None,
            "origin": "caption",
        },
        {
            "creator": "Nyl",
            "service": "patreon",
            "post_id": "151983862",
            "post_title": "Second post",
            "published": "2024-01-02T03:04:05+00:00",
            "category": "google_drive",
            "label": "Link",
            "url": "https://drive.google.com/file/d/ID/view",
            "resolved_url": "https://drive.google.com/file/d/ID/view",
            "origin": "caption",
        },
    ]
    while len(rows) < count:
        index = len(rows)
        row = dict(rows[0])
        row["post_id"] = str(172189145 + index)
        row["url"] = f"https://mega.nz/file/{index}"
        rows.append(row)
    return rows[:count]


def test_csv_headers_and_rfc4180_newlines():
    raw = links_csv_bytes(make_rows())
    assert b"\r\n" not in raw

    reader = csv.DictReader(io.StringIO(raw.decode("utf-8")))
    assert reader.fieldnames == list(LINK_COLUMNS)
    rows = list(reader)
    assert len(rows) == 2
    assert rows[0]["category"] == "mega"
    assert rows[0]["resolved_url"] == ""


def test_csv_quotes_commas_and_newlines():
    row = make_rows(1)[0]
    row["post_title"] = 'Title, with "quotes"\nand a newline'
    raw = links_csv_bytes([row]).decode("utf-8")
    parsed = list(csv.DictReader(io.StringIO(raw)))
    assert len(parsed) == 1
    assert "quotes" in parsed[0]["post_title"]


def test_unicode_survives_csv_and_json():
    rows = make_rows()
    csv_text = links_csv_text(rows)
    assert "日本語" in csv_text
    assert "😀" in csv_text

    payload = json.loads(links_json(rows))
    assert payload[0]["creator"] == "Nyl 日本語"
    assert "😀" in payload[0]["post_title"]


def test_no_html_in_exported_fields():
    rows = make_rows()
    payload = json.loads(links_json(rows))
    assert "<b>" not in payload[0]["post_title"]
    assert "<i>" not in payload[0]["label"]
    assert payload[0]["post_title"] == "Public Model Release 😀"
    assert payload[0]["label"] == "Backup Link"


def test_json_key_order_and_iso_dates():
    payload = json.loads(links_json(make_rows()))
    assert list(payload[0]) == list(LINK_COLUMNS)
    assert payload[0]["published"] == "2023-11-14T22:13:20+00:00"
    assert payload[1]["published"] == "2024-01-02T03:04:05+00:00"


def test_json_round_trip_matches_row_count():
    rows = make_rows(100)
    payload = json.loads(links_json(rows))
    assert len(payload) == len(rows)


def test_streamed_json_chunks_form_one_array():
    rows = make_rows(5)
    chunks = list(links_json_chunks(rows, batch_size=2))
    assert len(chunks) > 1
    merged = b"".join(chunks)
    assert json.loads(merged) == json.loads(links_json(rows))
    assert links_json_bytes(rows, batch_size=2) == merged


def test_csv_yields_header_before_consuming_rows():
    def exploding_rows():
        raise AssertionError("rows should be lazy")
        yield  # pragma: no cover

    stream = links_csv(exploding_rows())
    assert next(stream).decode("utf-8").startswith("creator,service,")


def test_5000_rows_export_under_a_second():
    rows = make_rows(5000)
    started = time.perf_counter()
    raw = links_csv_bytes((row for row in rows))
    elapsed = time.perf_counter() - started
    assert raw.startswith(b"creator,service,")
    assert len(list(csv.DictReader(io.StringIO(raw.decode("utf-8"))))) == 5000
    assert elapsed < 1.0


def test_archive_json_shape():
    rows = make_rows()
    document = json.loads(
        archive_json(
            {"id": 1, "service": "patreon", "service_id": "274608"},
            [{"id": 1, "title": "Post"}],
            rows,
            exported_at="2026-10-20T00:00:00+00:00",
        )
    )
    assert list(document) == ["creator", "exported_at", "posts", "links"]
    assert document["exported_at"] == "2026-10-20T00:00:00+00:00"
    assert len(document["posts"]) == 1
    assert len(document["links"]) == 2
    assert list(document["links"][0]) == list(LINK_COLUMNS)


def test_filename_convention():
    assert (
        links_filename("patreon", "274608", when=date(2026, 10, 20))
        == "monoke-patreon-274608-links-20261020.csv"
    )
    assert (
        links_filename("patreon", 274608, extension="json", when=date(2026, 1, 2))
        == "monoke-patreon-274608-links-20260102.json"
    )
