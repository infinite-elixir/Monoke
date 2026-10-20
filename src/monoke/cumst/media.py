"""Edge and thumbnail URL builders.

cum.st randomizes the edge host per sha; Monoke picks one deterministically so
URLs are stable across runs and easy to test.
"""

from __future__ import annotations

from ..config import get_settings
from ..models import Attachment, CreatorProfile

_KIND_EXT = {
    "gif": "gif",
    "video": "mp4",
    "audio": "m4a",
    "file": "bin",
    "image": "jpg",
}


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
    suffix = f"?v={profile.updated}" if profile.updated else ""
    root = f"{base}/creator/{profile.service}/{profile.id}"
    return {
        "avatar_url": f"{root}/avatar.webp{suffix}",
        "header_url": f"{root}/header.webp{suffix}",
        "header_full_url": f"{root}/header-full.webp{suffix}",
    }
