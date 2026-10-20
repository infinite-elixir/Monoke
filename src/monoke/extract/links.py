"""Link extraction from raw post captions.

The site's own ``links`` array is empty on every sampled post, so anchors, bare
URLs, and caption images are parsed directly out of ``caption``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from .classify import Category, classify, normalize

SITE_BASE = "https://cum.st/"
MAX_LINKS = 500
BAD_SCHEMES = ("javascript:", "mailto:", "data:", "vbscript:")

_BARE_URL = re.compile(r"(?:https?://|magnet:\?)[^\s<>\"']+", re.IGNORECASE)
_TRAILING = ".,;:!?)]}'\"»"


@dataclass(slots=True)
class LinkRow:
    url: str
    normalized_url: str
    label: str | None
    category: Category
    origin: str = "caption"
    resolved_url: str | None = None

    def as_row(self) -> dict[str, object]:
        return {
            "url": self.url,
            "normalized_url": self.normalized_url,
            "label": self.label,
            "category": self.category.value,
            "origin": self.origin,
            "resolved_url": self.resolved_url,
        }


def _clean_label(text: str | None) -> str | None:
    if not text:
        return None
    label = " ".join(text.split())
    return label or None


def _acceptable(url: str) -> bool:
    lowered = url.strip().lower()
    return bool(lowered) and not lowered.startswith(BAD_SCHEMES)


def _resolve(url: str) -> str:
    return urljoin(SITE_BASE, url.strip())


def _hostname(url: str) -> str | None:
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    return host.lower() if host else None


def extract_links(post) -> list[LinkRow]:
    """Return deduplicated links in first-seen order, capped at ``MAX_LINKS``."""
    return extract_links_report(post)[0]


def extract_links_report(post) -> tuple[list[LinkRow], bool]:
    """Extract links and report whether the per-post cap was exceeded.

    The boolean is true only when more than ``MAX_LINKS`` distinct links were
    found, so callers can warn without falsely flagging a post with exactly
    ``MAX_LINKS`` links.
    """
    rows: dict[str, LinkRow] = {}

    def add(
        url: str,
        label: str | None,
        *,
        origin: str = "caption",
        category_hint: Category | None = None,
    ) -> None:
        if not _acceptable(url):
            return
        url = _resolve(url)
        normalized = normalize(url)
        if not normalized:
            return
        existing = rows.get(normalized)
        if existing is not None:
            if existing.label is None and label:
                existing.label = label
            return

        category = category_hint if category_hint is not None else classify(url)
        rows[normalized] = LinkRow(
            url=url,
            normalized_url=normalized,
            label=label,
            category=category,
            origin=origin,
        )

    caption = getattr(post, "caption", None) or ""
    if caption:
        soup = BeautifulSoup(caption, "lxml")

        # 1. Anchor links: raw href plus the decoded, collapsed anchor text.
        for anchor in soup.find_all("a", href=True):
            href = str(anchor.get("href") or "").strip()
            if not href:
                continue
            label = _clean_label(anchor.get_text(" ", strip=True))
            add(href, label or _hostname(_resolve(href)))

        # 2. Bare URLs in text nodes have no label.
        for match in _BARE_URL.finditer(soup.get_text(" ")):
            url = match.group(0).rstrip(_TRAILING)
            if url and not url.lower().startswith("magnet:"):
                add(url, None)

        # 3. Caption images are links too; they carry an image hint.
        for image in soup.find_all("img", src=True):
            src = str(image.get("src") or "").strip()
            if not src:
                continue
            add(
                src,
                _clean_label(str(image.get("alt") or "")) or None,
                category_hint=Category.IMAGE,
            )

    # 4. Merge the API's own links defensively; it is empty on every sampled post.
    api_links = getattr(post, "links", None) or []
    if isinstance(api_links, dict):
        api_links = [api_links]
    for entry in api_links:
        if isinstance(entry, str):
            add(entry, None, origin="api_links")
        elif isinstance(entry, dict):
            url = entry.get("url") or entry.get("href") or entry.get("link")
            if url:
                add(
                    str(url),
                    _clean_label(str(entry.get("label") or "")) or None,
                    origin="api_links",
                )

    unique = list(rows.values())
    return unique[:MAX_LINKS], len(unique) > MAX_LINKS
