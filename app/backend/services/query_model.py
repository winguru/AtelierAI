# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/query-model.md
# ──────────────────────────────────────────────────────────────────────────────
"""Pydantic models for the unified gallery query API.

These models define the structured JSON request/response contract for
``POST /api/query``, replacing the CGI-string-based 4-concept filter
(ParsedGalleryFilter / FilterTerm / gallery_filter_service.py).

Design notes
------------
- **AND-of-ORs**: Multi-value filter fields accept three shapes:
      str             → single required value
      [str, …]        → all must match (AND)
      [[str, …], …]   → outer AND, inner OR
  Example: ``"collection": [["MLP", "Pony"], ["Favorites"]]``
  means (MLP OR Pony) AND Favorites.

- **Omit = skip**: If a request section (``summary``, ``images``, ``tags``)
  is omitted, the server skips that computation entirely.

- **Cursor pagination**: ``ImagePageSpec.cursor`` provides opaque keyset
  pagination; the response echoes ``page.next_cursor``.  The server also
  supports legacy ``offset``/``limit`` for backward compatibility.

- **Authority-terms only**: Tag filtering and counting use
  ``authority_terms.external_name`` joined through
  ``image_concept_observations`` — not ``concepts`` / ``concept_aliases``.
"""

from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Shared types
# ---------------------------------------------------------------------------

#: A single value, an AND-list, or AND-of-ORs nested list.
FilterValue = Union[str, list[str], list[list[str]]]


# ---------------------------------------------------------------------------
# Filter models
# ---------------------------------------------------------------------------

class GalleryFilter(BaseModel):
    """Structured gallery filter (replaces ParsedGalleryFilter).

    ``included`` and ``excluded`` map term types to values.
    Recognised term types:
        tag, artist, collection, model, source, software, mimetype, feature

    ``hidden`` maps hiding types to values.
    Recognised hiding types:
        nsfw, nsfw_safety

    ``missing`` lists missing-data keys.
    Recognised keys:
        artist, prompt, tags, model, checkpoint, lora, nsfw, generation,
        exif, source, civitai_meta, a1111_hires, a1111_regional_prompter,
        a1111_adetailer
    """

    included: dict[str, FilterValue] = Field(
        default_factory=dict,
        description="Terms that must be present, keyed by type.",
    )
    excluded: dict[str, FilterValue] = Field(
        default_factory=dict,
        description="Terms that must be absent, keyed by type.",
    )
    hidden: dict[str, FilterValue] = Field(
        default_factory=dict,
        description="Hide images matching these values (e.g. nsfw ratings).",
    )
    missing: list[str] = Field(
        default_factory=list,
        description="Missing-data keys (e.g. 'artist', 'model').",
    )


# ---------------------------------------------------------------------------
# Request section models
# ---------------------------------------------------------------------------

class SummarySpec(BaseModel):
    """Request summary counts for the filtered image set."""

    fields: list[str] = Field(
        default_factory=lambda: ["total_images", "total_tags", "top_tags"],
        description="Which summary fields to compute.",
    )
    top_tags_limit: int = Field(
        default=50,
        ge=1,
        le=500,
        description="Maximum number of top tags to return.",
    )


class ImagePageSpec(BaseModel):
    """Request a page of image results."""

    limit: int = Field(default=50, ge=1, le=500)
    offset: Optional[int] = Field(default=None, ge=0)
    cursor: Optional[str] = Field(
        default=None,
        description="Opaque cursor from previous page response.",
    )
    sort: Literal[
        "date_added", "date_created", "date_modified",
        "file_size", "random", "relevance",
    ] = "date_added"
    order: Literal["asc", "desc"] = "desc"
    group_variants: bool = Field(
        default=False,
        description="Collapse variant-group members, over-fetching to fill page.",
    )


class TagDetailSpec(BaseModel):
    """Request tag details for the filtered image set."""

    fields: list[str] = Field(
        default_factory=lambda: ["name", "count", "source"],
        description="Which tag fields to include.",
    )
    limit: int = Field(default=100, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)
    source: Optional[str] = Field(
        default=None,
        description="Filter to a single authority source (e.g. 'civitai').",
    )


# ---------------------------------------------------------------------------
# Top-level request / response
# ---------------------------------------------------------------------------

class GalleryQueryRequest(BaseModel):
    """Unified gallery query — ``POST /api/query``."""

    search: Optional[str] = Field(
        default=None,
        description="Free-text search across title, prompt, description.",
    )
    filter: GalleryFilter = Field(
        default_factory=GalleryFilter,
        description="Structured filter (included / excluded / hidden / missing).",
    )
    summary: Optional[SummarySpec] = Field(
        default=None,
        description="Omit to skip summary computation.",
    )
    images: Optional[ImagePageSpec] = Field(
        default=None,
        description="Omit to skip image page computation.",
    )
    tags: Optional[TagDetailSpec] = Field(
        default=None,
        description="Omit to skip tag detail computation.",
    )


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------

class TopTag(BaseModel):
    """A single tag with usage count."""

    name: str
    count: int
    source: Optional[str] = None


class SummaryResult(BaseModel):
    """Summary counts for the filtered image set."""

    total_images: Optional[int] = None
    total_tags: Optional[int] = None
    top_tags: Optional[list[TopTag]] = None


class PageInfo(BaseModel):
    """Pagination metadata."""

    offset: int = 0
    limit: int
    total: Optional[int] = None
    next_cursor: Optional[str] = None
    has_more: bool = False


class ImageItem(BaseModel):
    """A single image in the response.

    This is a **reference shape** — the actual payload is built by
    ``_build_display_items_for_image()`` in main.py which produces a rich
    dict with exif, civitai data, variant info, etc.  The model validates
    that the minimum required fields are present but allows additional
    fields via ``model_config``.
    """

    model_config = ConfigDict(extra="allow")

    base_image_id: int
    file_hash: str
    file_name: str
    file_path: str
    thumbnail_path: Optional[str] = None


class TagDetailItem(BaseModel):
    """A single tag detail row."""

    name: str
    count: int
    source: Optional[str] = None


class GalleryQueryResponse(BaseModel):
    """Response for ``POST /api/query``."""

    filter_echo: GalleryFilter = Field(
        description="Echo of the resolved filter (after normalisation).",
    )
    summary: Optional[SummaryResult] = None
    page: Optional[PageInfo] = None
    images: Optional[list[ImageItem]] = None
    tags: Optional[list[TagDetailItem]] = None


# ---------------------------------------------------------------------------
# Search-suggest request
# ---------------------------------------------------------------------------

class SuggestRequest(BaseModel):
    """Autocomplete suggestion request — ``POST /api/search/suggest``.

    ``q`` is the partial text the user has typed (LIKE pattern).
    ``search`` is the optional full-text search term from the gallery
    filter bar (narrows the image pool).
    ``filter`` mirrors the same ``GalleryFilter`` used by ``/api/query``,
    allowing the suggest endpoint to reuse cached constrained IDs.
    """

    q: str = Field(min_length=1, max_length=200)
    limit: int = Field(default=15, ge=1, le=50)
    search: Optional[str] = None
    filter: GalleryFilter = Field(default_factory=GalleryFilter)


# ---------------------------------------------------------------------------
# Models-tree state request
# ---------------------------------------------------------------------------

class ModelsTreeStateRequest(BaseModel):
    """Models tree state request — ``POST /api/models/tree/state``.

    ``search`` is the optional full-text search term from the gallery
    filter bar (narrows the gallery-scope image pool).
    ``filter`` mirrors the same ``GalleryFilter`` used by ``/api/query``,
    allowing the tree-state endpoint to reuse cached constrained IDs.
    ``selected_keys`` is an optional list of image keys for the "selected"
    scope (typically a single image).
    """

    search: Optional[str] = None
    filter: GalleryFilter = Field(default_factory=GalleryFilter)
    selected_keys: Optional[list[str]] = None


def filter_cache_key(
    gallery_filter: GalleryFilter,
    search: Optional[str],
) -> str:
    """Return a deterministic cache key for a (filter, search) pair.

    The key is a canonical JSON string so it can be used directly with
    ``_build_search_cache_key("constrained_ids", payload=...)``.
    """
    import json  # noqa: PLC0415 – avoid top-level import for stdlib

    payload: dict[str, object] = {
        "filter": gallery_filter.model_dump(),
    }
    if search:
        payload["search"] = search.strip().lower()
    # Deterministic serialisation — sort keys, compact separators.
    return json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True)
