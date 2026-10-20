"""cum.st URL parsing and builders.

The web path is ``/creators/{service}/{creator_id}``; the API path is
``/{service}/user/{creator_id}``. Both are built here so no other module has to
concatenate URL fragments by hand.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

SITE_BASE = "https://cum.st"
OLD_SITE_HINT = "That link is from the old site; use a cum.st/creators/... URL."

KNOWN_SERVICES: frozenset[str] = frozenset(
    {
        "patreon",
        "fansly",
        "onlyfans",
        "fanbox",
        "fantia",
        "afdian",
        "boosty",
        "gumroad",
        "subscribestar",
        "dlsite",
        "discord",
    }
)

_ALLOWED_HOSTS = frozenset({"cum.st", "www.cum.st"})
_SERVICE_RE = re.compile(r"^[a-z0-9_-]+$")
_ID_RE = re.compile(r"^\d+$")


class UrlError(ValueError):
    """Raised when the pasted text is not a usable cum.st creator/post URL."""


@dataclass(frozen=True, slots=True)
class CumstRef:
    service: str
    creator_id: str
    post_id: str | None = None

    @property
    def is_post(self) -> bool:
        return self.post_id is not None


def parse_url(raw: str) -> CumstRef:
    """Parse a pasted URL (or a bare path) into a :class:`CumstRef`."""
    if raw is None or not str(raw).strip():
        raise UrlError("Paste a cum.st creator URL, for example "
                      "https://cum.st/creators/patreon/274608.")

    text = str(raw).strip()

    if "://" not in text:
        if text.startswith(("cum.st/", "www.cum.st/")):
            text = f"https://{text}"
        elif text.startswith("/"):
            text = f"{SITE_BASE}{text}"
        elif text.startswith("creators/"):
            text = f"{SITE_BASE}/{text}"
        else:
            raise UrlError("Expected a URL like https://cum.st/creators/patreon/274608.")

    parts = urlsplit(text)
    if parts.scheme not in {"http", "https"}:
        raise UrlError("Only http(s) URLs are supported.")

    host = (parts.hostname or "").lower()
    if host.endswith("kemono.cr") or host.endswith("kemono.su") or host.endswith("coomer.party"):
        raise UrlError(OLD_SITE_HINT)
    if host not in _ALLOWED_HOSTS:
        raise UrlError(f"Expected a cum.st URL, got {host or 'no host'!r}.")

    segments = [segment for segment in parts.path.split("/") if segment]
    if len(segments) < 3 or segments[0] != "creators":
        raise UrlError("Expected /creators/{service}/{id} in the URL path.")

    service = segments[1]
    creator_id = segments[2]
    post_id: str | None = None

    if len(segments) == 5 and segments[3] == "post":
        post_id = segments[4]
    elif len(segments) != 3:
        raise UrlError(
            "Expected /creators/{service}/{id} or /creators/{service}/{id}/post/{post_id}."
        )

    if not _SERVICE_RE.match(service):
        raise UrlError(f"Invalid service {service!r} (lowercase letters, digits, - and _ only).")
    if not _ID_RE.match(creator_id):
        raise UrlError(f"Invalid creator id {creator_id!r} (digits only).")
    if post_id is not None and not _ID_RE.match(post_id):
        raise UrlError(f"Invalid post id {post_id!r} (digits only).")

    return CumstRef(service=service, creator_id=creator_id, post_id=post_id)


def creator_path(ref: CumstRef) -> str:
    return f"/creators/{ref.service}/{ref.creator_id}"


def creator_url(ref: CumstRef, *, base: str = SITE_BASE) -> str:
    return f"{base.rstrip('/')}{creator_path(ref)}"


def post_url(ref: CumstRef, post_id: str | None = None, *, base: str = SITE_BASE) -> str:
    pid = post_id or ref.post_id
    if not pid:
        raise UrlError("post_url needs a post id.")
    return f"{base.rstrip('/')}{creator_path(ref)}/post/{pid}"


def api_path(ref: CumstRef) -> str:
    return f"/{ref.service}/user/{ref.creator_id}"


def profile_path(ref: CumstRef) -> str:
    return f"{api_path(ref)}/profile"


def posts_path(ref: CumstRef) -> str:
    return f"{api_path(ref)}/posts"


def post_path(ref: CumstRef, post_id: str | None = None) -> str:
    pid = post_id or ref.post_id
    if not pid:
        raise UrlError("post_path needs a post id.")
    return f"{api_path(ref)}/post/{pid}"
