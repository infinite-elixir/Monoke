"""Typed models for the cum.st API payloads.

Field names are snake_case in Python; the API's camelCase keys are mapped with
aliases. Every top-level model carries the raw response text plus ``fetched_at``
and ``from_cache`` so the crawler can persist exactly what it saw.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    """Top-level API response: keeps transport metadata out of serialization."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    raw: str = Field(default="", exclude=True, repr=False)
    fetched_at: str = Field(default="", exclude=True, repr=False)
    from_cache: bool = Field(default=False, exclude=True, repr=False)


class CreatorProfile(ApiModel):
    id: str
    name: str
    service: str
    display_name: str | None = Field(default=None, alias="displayName")
    avatar_thumbhash: str | None = Field(default=None, alias="avatarThumbhash")
    header_thumbhash: str | None = Field(default=None, alias="headerThumbhash")
    indexed: int | None = None
    updated: int | None = None
    content_updated: int | None = Field(default=None, alias="contentUpdated")
    bookmarked: int | None = None
    has_tags: bool | None = Field(default=None, alias="hasTags")
    gender: str | None = None


class SummaryAttachment(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    position: int | None = None
    id: str | None = None
    sha256: str
    kind: str
    original_filename: str | None = Field(default=None, alias="originalFilename")
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = Field(default=None, alias="durationMs")
    preview_thumbhash: str | None = Field(default=None, alias="previewThumbhash")


class PostSummary(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str
    service: str
    title: str | None = None
    caption_text: str | None = Field(default=None, alias="captionText")
    added: int | None = None
    published: int | None = None
    bookmarked: int | None = None
    attachments: list[SummaryAttachment] = Field(default_factory=list)


class PostsPage(ApiModel):
    total: int
    posts: list[PostSummary] = Field(default_factory=list)


class Variant(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    name: str
    bytes: int | None = None


class Attachment(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    position: int | None = None
    id: str | None = None
    sha256: str
    kind: str
    mime_type: str | None = Field(default=None, alias="mimeType")
    bytes: int | None = None
    storage_key: str | None = Field(default=None, alias="storageKey")
    variants: list[Variant] = Field(default_factory=list)
    original_filename: str | None = Field(default=None, alias="originalFilename")
    width: int | None = None
    height: int | None = None
    duration_ms: int | None = Field(default=None, alias="durationMs")
    preview_thumbhash: str | None = Field(default=None, alias="previewThumbhash")


class Tag(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    slug: str
    label: str | None = None


class PostRef(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str


class CreatorRef(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    name: str | None = None
    display_name: str | None = Field(default=None, alias="displayName")


class PostDetail(ApiModel):
    id: str
    service: str
    caption: str | None = None
    caption_html: str | None = Field(default=None, alias="captionHtml")
    title: str | None = None
    added: int | None = None
    published: int | None = None
    attachments: list[Attachment] = Field(default_factory=list)
    links: list[dict[str, Any] | str] = Field(default_factory=list)
    tags: list[Tag] = Field(default_factory=list)
    prev: PostRef | None = None
    next: PostRef | None = None
    creator: CreatorRef | None = None
    linked: list[PostRef] = Field(default_factory=list)
    revisions: list[dict[str, Any]] = Field(default_factory=list)
