"""Allowlist HTML sanitizing and searchable plain text."""

from __future__ import annotations

from urllib.parse import urlsplit

import nh3
from bs4 import BeautifulSoup

ALLOWED_TAGS = {
    "p",
    "br",
    "a",
    "strong",
    "b",
    "em",
    "i",
    "u",
    "s",
    "ul",
    "ol",
    "li",
    "blockquote",
    "code",
    "pre",
    "img",
    "h1",
    "h2",
    "h3",
    "h4",
    "table",
    "thead",
    "tbody",
    "tr",
    "td",
    "th",
    "span",
    "div",
}
ALLOWED_ATTRIBUTES = {
    "a": {"href", "title", "target", "referrerpolicy"},
    "img": {"src", "alt", "title"},
    "*": {"class"},
}
ALLOWED_SCHEMES = {"http", "https", "magnet"}


def sanitize_html(html: str | None) -> str:
    if not html:
        return ""
    return nh3.clean(
        html,
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        url_schemes=ALLOWED_SCHEMES,
        link_rel="noopener noreferrer nofollow",
        set_tag_attribute_values={"a": {"target": "_blank"}},
    )


def lazy_images(
    html: str | None, *, allowed_hosts: set[str] | frozenset[str] | None = None
) -> str:
    """Mark caption images lazy and drop hosts the page is not allowed to fetch.

    The post page's network contract is narrow: on load it may request
    ``img.cum.st`` thumbnails and nothing else. Caption images from other hosts
    are removed; the link itself stays in the links table.
    """
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    images = soup.find_all("img")
    if not images:
        return html
    for image in images:
        src = str(image.get("src") or "")
        if allowed_hosts is not None:
            try:
                host = (urlsplit(src).hostname or "").lower()
            except ValueError:
                host = ""
            if host not in allowed_hosts:
                image.decompose()
                continue
        image["loading"] = "lazy"
        image["decoding"] = "async"
        if not image.get("alt"):
            image["alt"] = ""
    return str(soup)


def caption_text(html: str | None) -> str:
    """Plain text for search: no tags, decoded entities, collapsed whitespace."""
    if not html:
        return ""
    text = BeautifulSoup(html, "lxml").get_text(" ")
    return " ".join(text.split())
