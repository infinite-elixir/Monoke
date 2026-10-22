"""Query-string filter parsing, pagination and category presentation.

The HTTP layer stays thin: this module turns ``?q=&category=&tag=&dead=&from=&to=``
into keyword arguments for :func:`monoke.db.list_posts`, and back into
bookmarkable URLs. Nothing here touches the database.
"""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import UTC, datetime
from typing import Any, Mapping
from urllib.parse import parse_qs, urlencode

from ..extract.classify import Category

PER_PAGE = 50
LINKS_PER_PAGE = 100
GALLERY_PER_PAGE = 60
# The gallery groups media by post and pages by post (T4.11). The bookmarks
# media grid keeps GALLERY_PER_PAGE.
GALLERY_POSTS_PER_PAGE = 20

# Unfiltered /bookmarks shows this many items per creator before its
# "View all" link; the creator-filtered page is uncapped (T9.4).
BOOKMARKS_PER_CREATOR_PREVIEW = 6

# Sidebar/dropdown order. Post pages use their own priority order (below).
CATEGORY_ORDER: tuple[str, ...] = (
    Category.GOOGLE_DRIVE.value,
    Category.MEGA.value,
    Category.DIRECT_MEDIA.value,
    Category.VIDEO_HOST.value,
    Category.CLOUD_OTHER.value,
    Category.SOCIAL.value,
    Category.SHORTENER.value,
    Category.SITE_ASSET.value,
    Category.IMAGE.value,
    Category.OTHER.value,
)

CATEGORY_LABELS: dict[str, str] = {
    Category.GOOGLE_DRIVE.value: "Drive",
    Category.MEGA.value: "MEGA",
    Category.DIRECT_MEDIA.value: "Direct media",
    Category.VIDEO_HOST.value: "Video host",
    Category.CLOUD_OTHER.value: "Cloud",
    Category.SOCIAL.value: "Social",
    Category.SHORTENER.value: "Shortener",
    Category.SITE_ASSET.value: "Site asset",
    Category.IMAGE.value: "Image",
    Category.OTHER.value: "Other",
}

CATEGORY_SHORT_LABELS: dict[str, str] = {
    Category.GOOGLE_DRIVE.value: "Drive",
    Category.MEGA.value: "MEGA",
    Category.DIRECT_MEDIA.value: "MP4",
    Category.VIDEO_HOST.value: "Video",
    Category.CLOUD_OTHER.value: "Cloud",
    Category.SOCIAL.value: "Social",
    Category.SHORTENER.value: "Short",
    Category.SITE_ASSET.value: "Asset",
    Category.IMAGE.value: "Image",
    Category.OTHER.value: "Other",
}

SORTS: dict[str, str] = {
    "published_desc": "Newest first",
    "published_asc": "Oldest first",
    "title": "Title A–Z",
}

LINK_SORTS: dict[str, str] = {
    "date_desc": "Newest first",
    "category": "Category",
    "host": "Host",
}

LINK_SORT_KEYS = frozenset(LINK_SORTS)

# Gallery order. The duration keys apply to videos and GIFs; stills always sink
# below timed media so a length sort never interleaves images (T7.6).
GALLERY_SORTS: dict[str, str] = {
    "newest": "Newest first",
    "oldest": "Oldest first",
    "duration_desc": "Longest first",
    "duration_asc": "Shortest first",
}

GALLERY_ORIENTATIONS: dict[str, str] = {
    "portrait": "Portrait",
    "landscape": "Landscape",
    "square": "Square",
}

# Size floor for the gallery. Values are bytes; the labels are for the form.
GALLERY_MIN_BYTES: dict[str, str] = {
    "": "Any size",
    "1024": "1 KB or more",
    "10240": "10 KB or more",
    "102400": "100 KB or more",
}

_FILTER_FIELDS = (
    "q",
    "category",
    "tag",
    "media",
    "dead",
    "has_links",
    "date_from",
    "date_to",
)


def category_label(value: str | None) -> str:
    return CATEGORY_LABELS.get(str(value or ""), "Other")


def category_short_label(value: str | None) -> str:
    return CATEGORY_SHORT_LABELS.get(str(value or ""), "Other")


# Attachment kinds that can be filtered on the creator page (T7.3).
MEDIA_LABELS: dict[str, str] = {
    "image": "Images",
    "video": "Videos",
    "gif": "GIFs",
    "audio": "Audio",
}

MEDIA_BADGE_LABELS: dict[str, str] = {
    "image": "Image",
    "video": "Video",
    "gif": "GIF",
    "audio": "Audio",
}

# The gallery shows stills, gifs and videos; audio is deliberately excluded
# because it has no meaningful thumbnail grid (T7.2).
GALLERY_KIND_LABELS: dict[str, str] = {
    "": "All media",
    "image": "Images",
    "video": "Videos",
    "gif": "GIFs",
}


def media_label(value: str | None) -> str:
    return MEDIA_LABELS.get(str(value or ""), "Media")


def _media(value: str | None) -> str | None:
    cleaned = _text(value)
    return cleaned if cleaned in MEDIA_LABELS else None


def _gallery_kind(value: str | None) -> str | None:
    cleaned = _text(value)
    return cleaned if cleaned in {"image", "video", "gif"} else None


def _text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


def _tri(value: str | None) -> bool | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"", "any", "all"}:
        return None
    if text in {"1", "true", "yes", "on", "dead"}:
        return True
    if text in {"0", "false", "no", "off", "alive"}:
        return False
    return None


def _category(value: str | None) -> str | None:
    cleaned = _text(value)
    if cleaned in {category.value for category in Category}:
        return cleaned
    return None


def _sort(value: str | None) -> str:
    cleaned = _text(value)
    if cleaned == "title_asc":
        cleaned = "title"
    return cleaned if cleaned in SORTS else "published_desc"


def _link_sort(value: str | None) -> str:
    cleaned = _text(value)
    return cleaned if cleaned in LINK_SORT_KEYS else "date_desc"


# Plain/legacy aliases from bookmarks and hand-typed URLs. Anything else falls
# back to the default instead of surprising the user with an empty gallery.
_GALLERY_SORT_ALIASES = {
    "duration": "duration_desc",
    "length": "duration_desc",
}


def _gallery_sort(value: str | None) -> str:
    cleaned = _text(value)
    cleaned = _GALLERY_SORT_ALIASES.get(cleaned, cleaned)
    return cleaned if cleaned in GALLERY_SORTS else "newest"


def parse_date(value: str | None, *, end_of_day: bool = False) -> int | None:
    """Parse ``YYYY-MM-DD`` (or an ISO datetime) into UTC epoch seconds."""
    cleaned = _text(value)
    if not cleaned:
        return None
    date_only = len(cleaned) == 10
    try:
        stamp = datetime.fromisoformat(cleaned.replace("Z", "+00:00"))
    except ValueError:
        try:
            stamp = datetime.strptime(cleaned, "%Y-%m-%d")
        except ValueError:
            return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    if end_of_day and date_only:
        stamp = stamp.replace(hour=23, minute=59, second=59)
    return int(stamp.timestamp())


def date_value(epoch: int | None) -> str:
    """Render an epoch as the ``YYYY-MM-DD`` value a date input expects."""
    if epoch is None:
        return ""
    return datetime.fromtimestamp(int(epoch), UTC).strftime("%Y-%m-%d")


@dataclass(frozen=True, slots=True)
class PostFilters:
    q: str | None = None
    category: str | None = None
    tag: str | None = None
    media: str | None = None
    dead: bool | None = None
    has_links: bool | None = None
    date_from: int | None = None
    date_to: int | None = None
    sort: str = "published_desc"
    page: int = 1

    @classmethod
    def from_query(cls, params: Mapping[str, str]) -> "PostFilters":
        try:
            page = int(params.get("page") or 1)
        except (TypeError, ValueError):
            page = 1
        return cls(
            q=_text(params.get("q")),
            category=_category(params.get("category")),
            tag=_text(params.get("tag")),
            media=_media(params.get("media")),
            dead=_tri(params.get("dead")),
            has_links=_tri(params.get("has_links")),
            date_from=parse_date(params.get("from")),
            date_to=parse_date(params.get("to"), end_of_day=True),
            sort=_sort(params.get("sort")),
            page=max(1, page),
        )

    @property
    def offset(self) -> int:
        return (self.page - 1) * PER_PAGE

    @property
    def active(self) -> bool:
        return any(
            (
                self.q,
                self.category,
                self.tag,
                self.media,
                self.dead is not None,
                self.has_links is not None,
                self.date_from,
                self.date_to,
            )
        )

    def db_kwargs(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "tag": self.tag,
            "media_kind": self.media,
            "has_links": self.has_links,
            "missing": self.dead,
            "date_from": self.date_from,
            "date_to": self.date_to,
            "q": self.q,
            "sort": self.sort,
            "limit": PER_PAGE,
            "offset": self.offset,
        }

    def facet_kwargs(self) -> dict[str, Any]:
        """Filter dimensions only (no paging/sort) for facet queries."""
        return {
            "category": self.category,
            "tag": self.tag,
            "media_kind": self.media,
            "has_links": self.has_links,
            "missing": self.dead,
            "date_from": self.date_from,
            "date_to": self.date_to,
            "q": self.q,
        }

    def with_changes(self, **overrides: Any) -> "PostFilters":
        values = {field.name: getattr(self, field.name) for field in fields(self)}
        values.update(overrides)
        # Changing a filter dimension starts again at page 1.
        if any(name in overrides for name in _FILTER_FIELDS) and "page" not in overrides:
            values["page"] = 1
        return PostFilters(**values)

    def pairs(self) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        if self.q:
            pairs.append(("q", self.q))
        if self.category:
            pairs.append(("category", self.category))
        if self.tag:
            pairs.append(("tag", self.tag))
        if self.media:
            pairs.append(("media", self.media))
        if self.dead is not None:
            pairs.append(("dead", "1" if self.dead else "0"))
        if self.has_links is not None:
            pairs.append(("has_links", "1" if self.has_links else "0"))
        if self.date_from is not None:
            pairs.append(("from", date_value(self.date_from)))
        if self.date_to is not None:
            pairs.append(("to", date_value(self.date_to)))
        if self.sort != "published_desc":
            pairs.append(("sort", self.sort))
        if self.page > 1:
            pairs.append(("page", str(self.page)))
        return pairs

    def query_string(self, **overrides: Any) -> str:
        filters = self.with_changes(**overrides) if overrides else self
        return urlencode(filters.pairs(), safe=",")

    def url_for(self, creator_id: int, **overrides: Any) -> str:
        query = self.query_string(**overrides)
        base = f"/c/{int(creator_id)}"
        return f"{base}?{query}" if query else base


@dataclass(frozen=True, slots=True)
class GalleryFilters:
    """Filters for the per-creator image gallery (T4.7)."""

    q: str | None = None
    tag: str | None = None
    kind: str | None = None
    orientation: str | None = None
    min_bytes: int | None = None
    date_from: int | None = None
    date_to: int | None = None
    sort: str = "newest"
    page: int = 1

    @classmethod
    def from_query(cls, params: Mapping[str, str]) -> "GalleryFilters":
        raw_gf = params.get("gf")
        merged: dict[str, str] = {}
        if raw_gf:
            try:
                parsed_rows = parse_qs(str(raw_gf), keep_blank_values=True)
                merged.update({name: values[0] for name, values in parsed_rows.items() if values})
            except Exception:
                merged = {}

        for key in ("q", "tag", "kind", "orientation", "min_bytes", "from", "to", "sort", "page"):
            if params.get(key) is None:
                continue
            value = str(params.get(key))
            # Viewer URLs carry `from=gallery`, which is not a date. When a
            # context token is present, do not let that origin flag erase the
            # token's real date range (T8.19). A top-level date still wins.
            if raw_gf and key in {"from", "to"} and parse_date(value) is None:
                continue
            merged[key] = value

        try:
            page = int(merged.get("page") or 1)
        except (TypeError, ValueError):
            page = 1

        orientation = _text(merged.get("orientation"))
        if orientation not in GALLERY_ORIENTATIONS:
            orientation = None

        min_bytes = None
        raw_bytes = _text(merged.get("min_bytes"))
        if raw_bytes:
            try:
                parsed = int(raw_bytes)
            except ValueError:
                parsed = 0
            min_bytes = parsed if parsed > 0 else None

        return cls(
            q=_text(merged.get("q")),
            tag=_text(merged.get("tag")),
            kind=_gallery_kind(merged.get("kind")),
            orientation=orientation,
            min_bytes=min_bytes,
            date_from=parse_date(merged.get("from")),
            date_to=parse_date(merged.get("to"), end_of_day=True),
            sort=_gallery_sort(merged.get("sort")),
            page=max(1, page),
        )

    @property
    def offset(self) -> int:
        return (self.page - 1) * GALLERY_POSTS_PER_PAGE

    @property
    def active(self) -> bool:
        return any(
            (
                self.q,
                self.tag,
                self.kind,
                self.orientation,
                self.min_bytes,
                self.date_from,
                self.date_to,
            )
        )

    def db_kwargs(self) -> dict[str, Any]:
        return {
            "q": self.q,
            "tag": self.tag,
            "kind": self.kind,
            "orientation": self.orientation,
            "min_bytes": self.min_bytes,
            "date_from": self.date_from,
            "date_to": self.date_to,
            "sort": self.sort,
            "limit": GALLERY_POSTS_PER_PAGE,
            "offset": self.offset,
        }

    def with_changes(self, **overrides: Any) -> "GalleryFilters":
        values = {field.name: getattr(self, field.name) for field in fields(self)}
        values.update(overrides)
        filter_fields = {
            "q",
            "tag",
            "kind",
            "orientation",
            "min_bytes",
            "date_from",
            "date_to",
            "sort",
        }
        if any(name in overrides for name in filter_fields) and "page" not in overrides:
            values["page"] = 1
        return GalleryFilters(**values)

    def pairs(self) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        if self.q:
            pairs.append(("q", self.q))
        if self.tag:
            pairs.append(("tag", self.tag))
        if self.kind:
            pairs.append(("kind", self.kind))
        if self.orientation:
            pairs.append(("orientation", self.orientation))
        if self.min_bytes:
            pairs.append(("min_bytes", str(self.min_bytes)))
        if self.date_from is not None:
            pairs.append(("from", date_value(self.date_from)))
        if self.date_to is not None:
            pairs.append(("to", date_value(self.date_to)))
        if self.sort != "newest":
            pairs.append(("sort", self.sort))
        if self.page > 1:
            pairs.append(("page", str(self.page)))
        return pairs

    @property
    def context_pairs(self) -> list[tuple[str, str]]:
        return [(key, value) for key, value in self.pairs() if key != "page"]

    @property
    def context_query(self) -> str:
        return urlencode(self.context_pairs)

    def query_string(self, **overrides: Any) -> str:
        filters = self.with_changes(**overrides) if overrides else self
        return urlencode(filters.pairs())

    def url_for(self, creator_id: int, **overrides: Any) -> str:
        query = self.query_string(**overrides)
        base = f"/c/{int(creator_id)}/gallery"
        return f"{base}?{query}" if query else base


@dataclass(frozen=True, slots=True)
class LinkFilters:
    """Filters for the flat link explorer.

    ``categories`` is empty when every category is selected, which is the
    default: the explorer must never hide a link until the user asks it to.
    """

    q: str | None = None
    categories: tuple[str, ...] = ()
    creator: int | None = None
    has_resolved: bool | None = None
    sort: str = "date_desc"
    grouped: bool = True
    page: int = 1

    @classmethod
    def from_query(cls, params: Mapping[str, str]) -> "LinkFilters":
        raw_values: list[str] = []
        getlist = getattr(params, "getlist", None)
        if callable(getlist):
            raw_values = [str(value) for value in getlist("category")]
        else:
            raw = params.get("category")
            if raw:
                raw_values = [str(raw)]

        # Accept both repeated `?category=a&category=b` (checkbox form) and
        # the compact `?category=a,b` spelling used in bookmarks and tests.
        categories: list[str] = []
        for raw in raw_values:
            for part in raw.split(","):
                part = part.strip()
                if part and part not in categories:
                    categories.append(part)

        # The checkbox form submits this marker so "every box unchecked" means
        # "no categories" rather than the default "all categories".
        if not categories and params.get("category_filter"):
            categories = ["__none__"]

        creator_value = _text(params.get("creator"))
        try:
            creator = int(creator_value) if creator_value else None
        except (TypeError, ValueError):
            creator = None

        try:
            page = int(params.get("page") or 1)
        except (TypeError, ValueError):
            page = 1

        group_value = _text(params.get("group"))
        grouped = not (
            group_value is not None
            and group_value.lower() in {"0", "false", "no", "off", "flat"}
        )

        return cls(
            q=_text(params.get("q")),
            categories=tuple(categories),
            creator=creator,
            has_resolved=_tri(params.get("has_resolved")),
            sort=_link_sort(params.get("sort")),
            grouped=grouped,
            page=max(1, page),
        )

    @property
    def offset(self) -> int:
        return (self.page - 1) * LINKS_PER_PAGE

    @property
    def active(self) -> bool:
        return any(
            (
                self.q,
                self.categories,
                self.creator is not None,
                self.has_resolved is not None,
            )
        )

    def selected(self, category: str) -> bool:
        return not self.categories or category in self.categories

    def db_kwargs(self) -> dict[str, Any]:
        return {
            "creator_id": self.creator,
            "categories": self.categories,
            "q": self.q,
            "has_resolved": self.has_resolved,
            "sort": self.sort,
            "limit": LINKS_PER_PAGE,
            "offset": self.offset,
        }

    def facet_kwargs(self) -> dict[str, Any]:
        return {
            "creator_id": self.creator,
            "q": self.q,
            "has_resolved": self.has_resolved,
        }

    def export_kwargs(self) -> dict[str, Any]:
        return {
            "creator_id": self.creator,
            "categories": self.categories,
            "q": self.q,
            "has_resolved": self.has_resolved,
            "sort": self.sort,
        }

    def with_changes(self, **overrides: Any) -> "LinkFilters":
        values = {field.name: getattr(self, field.name) for field in fields(self)}
        values.update(overrides)
        filter_fields = {"q", "categories", "creator", "has_resolved"}
        if any(name in overrides for name in filter_fields) and "page" not in overrides:
            values["page"] = 1
        return LinkFilters(**values)

    def pairs(self) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        if self.q:
            pairs.append(("q", self.q))
        if self.categories:
            pairs.append(("category", ",".join(self.categories)))
        if self.creator is not None:
            pairs.append(("creator", str(self.creator)))
        if self.has_resolved is not None:
            pairs.append(("has_resolved", "1" if self.has_resolved else "0"))
        if self.sort != "date_desc":
            pairs.append(("sort", self.sort))
        if not self.grouped:
            pairs.append(("group", "0"))
        if self.page > 1:
            pairs.append(("page", str(self.page)))
        return pairs

    def query_string(self, **overrides: Any) -> str:
        filters = self.with_changes(**overrides) if overrides else self
        return urlencode(filters.pairs(), safe=",")

    def url_for(self, base: str, **overrides: Any) -> str:
        query = self.query_string(**overrides)
        return f"{base}?{query}" if query else base


BOOKMARK_VIEWS: dict[str, str] = {
    "all": "All",
    "posts": "Posts",
    "media": "Media",
}

BOOKMARK_TAB_ORDER: tuple[str, ...] = ("all", "posts", "media")


def _bookmark_view(value: str | None) -> str:
    cleaned = _text(value)
    return cleaned if cleaned in BOOKMARK_VIEWS else "all"


@dataclass(frozen=True, slots=True)
class BookmarkFilters:
    """Tab, optional creator filter and page for the bookmarks library (T9.3)."""

    view: str = "all"
    creator: int | None = None
    page: int = 1

    @classmethod
    def from_query(cls, params: Mapping[str, str]) -> "BookmarkFilters":
        creator_value = _text(params.get("creator"))
        try:
            creator = int(creator_value) if creator_value else None
        except (TypeError, ValueError):
            creator = None
        try:
            page = int(params.get("page") or 1)
        except (TypeError, ValueError):
            page = 1
        return cls(
            view=_bookmark_view(params.get("view")),
            creator=creator,
            page=max(1, page),
        )

    @property
    def post_offset(self) -> int:
        return (self.page - 1) * PER_PAGE

    @property
    def media_offset(self) -> int:
        return (self.page - 1) * GALLERY_PER_PAGE

    def with_changes(self, **overrides: Any) -> "BookmarkFilters":
        values = {field.name: getattr(self, field.name) for field in fields(self)}
        values.update(overrides)
        # Switching tabs or creator starts again at page 1.
        if any(name in overrides for name in ("view", "creator")) and "page" not in overrides:
            values["page"] = 1
        values["page"] = max(1, int(values["page"]))
        return BookmarkFilters(**values)

    def pairs(self) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        if self.view != "all":
            pairs.append(("view", self.view))
        if self.creator is not None:
            pairs.append(("creator", str(self.creator)))
        if self.page > 1:
            pairs.append(("page", str(self.page)))
        return pairs

    def query_string(self, **overrides: Any) -> str:
        filters = self.with_changes(**overrides) if overrides else self
        return urlencode(filters.pairs())

    def url_for(self, **overrides: Any) -> str:
        query = self.query_string(**overrides)
        return f"/bookmarks?{query}" if query else "/bookmarks"


def page_items(page: int, pages: int) -> list[int | None]:
    """Page numbers to render; ``None`` is an ellipsis gap.

    Up to ten pages show every number, as the ticket asks. Longer sets get a
    moving window plus the first and last page.
    """
    if pages <= 1:
        return []
    if pages <= 10:
        return list(range(1, pages + 1))

    window = {1, pages}
    start = max(2, page - 1)
    end = min(pages - 1, page + 1)
    window.update(range(start, end + 1))
    ordered = sorted(window)

    items: list[int | None] = []
    previous = 0
    for number in ordered:
        if number - previous > 1:
            items.append(None)
        items.append(number)
        previous = number
    return items
