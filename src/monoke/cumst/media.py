"""Edge and thumbnail URL builders.

cum.st randomizes the edge host per sha; Monoke picks one deterministically so
URLs are stable across runs and easy to test.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from ..config import get_settings
from ..models import Attachment, CreatorProfile

_KIND_EXT = {
    "gif": "gif",
    "video": "mp4",
    "audio": "m4a",
    "file": "bin",
    "image": "jpg",
}

VIDEO_EXTENSIONS = frozenset({"mp4", "webm", "mov", "m4v"})
AUDIO_EXTENSIONS = frozenset({"mp3", "m4a", "ogg", "wav", "opus"})
IMAGE_EXTENSIONS = frozenset({"jpg", "jpeg", "png", "webp", "avif", "gif"})
STILL_EXTENSIONS = IMAGE_EXTENSIONS - {"gif"}


def _extension(name: Any) -> str | None:
    text = str(name or "").strip().lower()
    if "." not in text:
        return None
    ext = text.rsplit(".", 1)[1]
    return ext if ext.isalnum() and 1 <= len(ext) <= 5 else None


def _variant_names(variants: Any) -> list[str]:
    if not variants:
        return []
    if isinstance(variants, Mapping):
        return [str(key) for key in variants]
    if not isinstance(variants, Iterable) or isinstance(variants, (str, bytes)):
        return []
    names: list[str] = []
    for variant in variants:
        if isinstance(variant, Mapping):
            name = variant.get("name")
        else:
            name = getattr(variant, "name", None)
        if name:
            names.append(str(name))
    return names


def classify_media_kind(
    *,
    raw_kind: str | None = None,
    mime_type: str | None = None,
    original_filename: str | None = None,
    variants: Any = None,
    duration_ms: int | None = None,
) -> str:
    """Derive one trustworthy media kind from the upstream signals.

    Upstream ``kind`` is unreliable: it labels short silent MP4s as ``gif``.
    The mime type, filenames/variants and duration decide instead; the raw
    value is kept by the caller for provenance.
    """
    mime = str(mime_type or "").strip().lower()
    extensions = {
        ext
        for ext in (
            _extension(original_filename),
            *(_extension(name) for name in _variant_names(variants)),
        )
        if ext
    }

    has_video = bool(extensions & VIDEO_EXTENSIONS)
    has_gif = "gif" in extensions
    has_still = bool(extensions & STILL_EXTENSIONS)
    has_audio = bool(extensions & AUDIO_EXTENSIONS)

    if mime.startswith("video/") or has_video:
        return "video"
    if duration_ms and duration_ms > 0 and not (has_still or has_gif):
        return "video"

    if mime == "image/gif" and has_gif and str(raw_kind or "").lower() == "gif":
        return "gif"
    if mime.startswith("image/") or has_still or has_gif:
        return "image"

    if mime.startswith("audio/") or has_audio:
        return "audio"
    return "file"


def ext_for(attachment: Attachment) -> str:
    """Extension from the filename, falling back to the attachment kind."""
    name = attachment.original_filename or ""
    if "." in name:
        ext = name.rsplit(".", 1)[1].strip().lower()
        if ext.isalnum() and 1 <= len(ext) <= 5:
            return ext
    return _KIND_EXT.get(attachment.kind, "bin")


def edge_host(sha256: str, *, hosts: tuple[str, ...] | None = None) -> str:
    hosts = hosts or get_settings().edge_hosts
    index = int(sha256[0], 16) % len(hosts)
    return hosts[index].rstrip("/")


def media_url(
    sha256: str, name: str, *, hosts: tuple[str, ...] | None = None
) -> str:
    return f"{edge_host(sha256, hosts=hosts)}/media/{sha256}/{name}"


def original_url(
    attachment: Attachment, *, hosts: tuple[str, ...] | None = None
) -> str:
    return media_url(
        attachment.sha256, f"original.{ext_for(attachment)}", hosts=hosts
    )


def variant_urls(
    attachment: Attachment, *, hosts: tuple[str, ...] | None = None
) -> list[dict[str, str]]:
    names = [variant.name for variant in attachment.variants] or [
        f"original.{ext_for(attachment)}"
    ]
    return [
        {"name": name, "url": media_url(attachment.sha256, name, hosts=hosts)}
        for name in names
    ]


def thumb_url(sha256: str, *, img_base: str | None = None) -> str:
    base = (img_base or get_settings().img_base).rstrip("/")
    return f"{base}/thumbnail/{sha256}/preview.webp"


def creator_images(
    profile: CreatorProfile, *, img_base: str | None = None
) -> dict[str, str | None]:
    base = (img_base or get_settings().img_base).rstrip("/")
    suffix = f"?v={profile.updated}" if profile.updated is not None else ""
    root = f"{base}/creator/{profile.service}/{profile.id}"
    return {
        "avatar_url": f"{root}/avatar.webp{suffix}",
        "header_url": f"{root}/header.webp{suffix}",
        "header_full_url": f"{root}/header-full.webp{suffix}",
    }
