"""URL normalization and host-first category classification."""

from __future__ import annotations

from enum import Enum
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

DEFAULT_PORTS = {"http": 80, "https": 443}
TRACKING_PARAMS = {"fbclid", "gclid"}
DIRECT_MEDIA_EXT = (".mp4", ".webm", ".mov", ".m4v", ".gif", ".gifv")
IMAGE_EXT = (".jpg", ".jpeg", ".png", ".webp", ".avif", ".bmp")


class Category(str, Enum):
    GOOGLE_DRIVE = "google_drive"
    MEGA = "mega"
    DIRECT_MEDIA = "direct_media"
    VIDEO_HOST = "video_host"
    SHORTENER = "shortener"
    SOCIAL = "social"
    CLOUD_OTHER = "cloud_other"
    SITE_ASSET = "site_asset"
    IMAGE = "image"
    OTHER = "other"


SHORTENER_HOSTS = frozenset(
    {
        "bit.ly",
        "t.co",
        "tinyurl.com",
        "is.gd",
        "goo.gl",
        "ow.ly",
        "buff.ly",
        "rebrand.ly",
        "cutt.ly",
        "shorturl.at",
        "t.ly",
    }
)
SOCIAL_HOSTS = frozenset(
    {
        "twitter.com",
        "x.com",
        "tumblr.com",
        "pixiv.net",
        "instagram.com",
        "bsky.app",
        "facebook.com",
        "reddit.com",
    }
)
CLOUD_HOSTS = frozenset(
    {"dropbox.com", "dropboxusercontent.com", "1drv.ms", "onedrive.live.com", "mega.io"}
)


def _host_matches(host: str, domains: frozenset[str] | set[str] | tuple[str, ...]) -> bool:
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def _split(url: str | None):
    try:
        return urlsplit((url or "").strip())
    except (AttributeError, ValueError):
        return None


def normalize(url: str | None) -> str:
    """Stable identity for a URL; malformed input is returned trimmed, never raised."""
    if url is None:
        return ""
    try:
        text = url.strip() if isinstance(url, str) else str(url).strip()
    except (TypeError, ValueError):
        return ""
    if not text:
        return text

    try:
        parts = urlsplit(text)
    except ValueError:
        return text

    scheme = parts.scheme.lower()
    if scheme == "magnet":
        return text
    if not scheme:
        try:
            parts = urlsplit("https://" + text.lstrip("/"))
        except ValueError:
            return text
        scheme = "https"

    host = (parts.hostname or "").lower()
    if not host:
        return text

    try:
        port = parts.port
    except ValueError:
        port = None
    netloc = host
    if port is not None and port != DEFAULT_PORTS.get(scheme):
        netloc = f"{host}:{port}"

    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"

    pairs = []
    try:
        pairs = parse_qsl(parts.query, keep_blank_values=True)
    except ValueError:
        pairs = []
    pairs = [
        (key, value)
        for key, value in pairs
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMS
    ]
    pairs.sort()

    return urlunsplit((scheme, netloc, path, urlencode(pairs), ""))


def classify(url: str | None, label: str | None = None) -> Category:
    """Classify by host and path; unknown/malformed URLs are ``other``.

    ``label`` is accepted for API compatibility but never changes the category:
    classification is host-first, and a label such as "MEGA" on an unknown host
    must stay ``other`` rather than inventing a category.
    """
    del label
    parts = _split(url)
    if parts is None:
        return Category.OTHER

    host = (parts.hostname or "").lower()
    if not host:
        return Category.OTHER

    path = (parts.path or "").lower()

    if _host_matches(host, {"drive.google.com", "docs.google.com"}):
        return Category.GOOGLE_DRIVE
    if _host_matches(host, {"mega.nz", "mega.co.nz"}):
        return Category.MEGA
    if _host_matches(host, SHORTENER_HOSTS):
        return Category.SHORTENER
    if _host_matches(host, {"e1.cum.st", "e2.cum.st", "e3.cum.st", "img.cum.st"}):
        return Category.SITE_ASSET
    if _host_matches(host, {"redgifs.com", "gfycat.com"}):
        return Category.VIDEO_HOST
    if _host_matches(host, {"twitter.com", "x.com"}):
        return Category.VIDEO_HOST if ("/media" in path or "/video/" in path) else Category.SOCIAL
    if _host_matches(host, {"tumblr.com", "pixiv.net"}):
        return Category.SOCIAL
    if _host_matches(host, SOCIAL_HOSTS):
        return Category.SOCIAL
    if _host_matches(host, CLOUD_HOSTS):
        return Category.CLOUD_OTHER
    if _host_matches(host, {"catbox.moe"}):
        return Category.DIRECT_MEDIA
    if path.endswith(DIRECT_MEDIA_EXT):
        return Category.DIRECT_MEDIA
    if path.endswith(IMAGE_EXT):
        return Category.IMAGE

    return Category.OTHER


def looks_like_image(url: str) -> bool:
    parts = _split(url)
    return parts is not None and (parts.path or "").lower().endswith(IMAGE_EXT)
