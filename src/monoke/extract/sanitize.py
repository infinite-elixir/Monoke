"""Allowlist HTML sanitizing and searchable plain text."""

from __future__ import annotations

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


def caption_text(html: str | None) -> str:
    """Plain text for search: no tags, decoded entities, collapsed whitespace."""
    if not html:
        return ""
    text = BeautifulSoup(html, "lxml").get_text(" ")
    return " ".join(text.split())
