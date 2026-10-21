"""Turn a post detail payload into database rows (pure, no I/O)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..cumst.media import (
    classify_media_kind,
    ext_for,
    media_url,
    original_url,
    thumb_url,
)
from ..models import Attachment, PostDetail
from .links import MAX_LINKS, extract_links_report
from .sanitize import caption_text, sanitize_html

_POSITION_LAST = 10**9


@dataclass(slots=True)
class ExtractedPost:
    caption_html: str
    caption_text: str
    files: list[dict[str, Any]] = field(default_factory=list)
    links: list[dict[str, Any]] = field(default_factory=list)
    tags: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _position(attachment: Attachment) -> int:
    return attachment.position if attachment.position is not None else _POSITION_LAST


def extract_files(post: PostDetail) -> list[dict[str, Any]]:
    """One row per sha256, keeping the lowest attachment position."""
    chosen: dict[str, Attachment] = {}
    for attachment in post.attachments:
        current = chosen.get(attachment.sha256)
        if current is None or _position(attachment) < _position(current):
            chosen[attachment.sha256] = attachment

    rows: list[dict[str, Any]] = []
    for attachment in sorted(chosen.values(), key=_position):
        variants = [
            {
                "name": variant.name,
                "bytes": variant.bytes,
                "url": media_url(attachment.sha256, variant.name),
            }
            for variant in attachment.variants
        ]
        if not variants:
            variants = [
                {
                    "name": f"original.{ext_for(attachment)}",
                    "bytes": attachment.bytes,
                    "url": original_url(attachment),
                }
            ]

        rows.append(
            {
                "position": attachment.position,
                "sha256": attachment.sha256,
                "kind": classify_media_kind(
                    raw_kind=attachment.kind,
                    mime_type=attachment.mime_type,
                    original_filename=attachment.original_filename,
                    variants=variants,
                    duration_ms=attachment.duration_ms,
                ),
                "raw_kind": attachment.kind,
                "mime_type": attachment.mime_type,
                "bytes": attachment.bytes,
                "width": attachment.width,
                "height": attachment.height,
                "duration_ms": attachment.duration_ms,
                "original_filename": attachment.original_filename,
                "storage_key": attachment.storage_key,
                "variants": variants,
                "variants_json": json.dumps(variants, separators=(",", ":")),
                "url": original_url(attachment),
                "thumb_url": thumb_url(attachment.sha256),
            }
        )
    return rows


def extract_tags(post: PostDetail) -> list[dict[str, Any]]:
    seen: set[str] = set()
    rows: list[dict[str, Any]] = []
    for tag in post.tags:
        if not tag.slug or tag.slug in seen:
            continue
        seen.add(tag.slug)
        rows.append({"slug": tag.slug, "label": tag.label or tag.slug})
    return rows


def extract_post(post: PostDetail) -> ExtractedPost:
    html = sanitize_html(post.caption_html or post.caption)
    links, cap_exceeded = extract_links_report(post)
    warnings: list[str] = []
    if cap_exceeded:
        warnings.append(f"caption link cap reached ({MAX_LINKS}); extra links dropped")

    return ExtractedPost(
        caption_html=html,
        caption_text=caption_text(html),
        files=extract_files(post),
        links=[row.as_row() for row in links],
        tags=extract_tags(post),
        warnings=warnings,
    )
