# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/query-model.md
# ──────────────────────────────────────────────────────────────────────────────
"""Unified gallery query engine.

``GalleryQuery`` accepts a ``GalleryQueryRequest`` (structured JSON) and
produces a ``GalleryQueryResponse`` containing any combination of:

- **summary** — total image count, tag count, top tags
- **images**  — a paginated page of display-item dicts
- **tags**    — tag detail rows for the filtered set

It replaces the CGI-string-based flow:
    parse_gallery_filter() → apply_gallery_filter() → _load_display_image_items_unified()

Internally it delegates to the existing ``ImageQueryService`` low-level
filter methods and reuses the display-item builders in ``main.py``.
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import func, text
from sqlalchemy.orm import Session, joinedload

from models import (
    ImageModel,
    ImageVariantGroupMembership,
)
from services.image_query_service import ImageQueryService
from services.query_model import (
    GalleryFilter,
    GalleryQueryRequest,
    GalleryQueryResponse,
    ImageItem,
    ImagePageSpec,
    PageInfo,
    SummaryResult,
    SummarySpec,
    TagDetailItem,
    TagDetailSpec,
    TopTag,
)

# Over-fetch multiplier when group_variants collapses rows.
_OVERFETCH_FACTOR = 3

# ── Unfiltered aggregate cache ──────────────────────────────────────────────
# When constrained_ids is None (no filter), COUNT and tag aggregation are
# deterministic until data changes.  We cache these with a short TTL so the
# first request of each batch pays the cost but subsequent ones are free.
_UNFILTERED_CACHE_TTL = max(1.0, float(os.getenv("ATELIER_UNFILTERED_CACHE_TTL", "30")))
_unfiltered_cache_lock = threading.Lock()
_unfiltered_cache: dict[str, tuple[float, Any]] = {}


def _get_unfiltered(key: str) -> Optional[Any]:
    """Return cached value if still valid, else None."""
    entry = _unfiltered_cache.get(key)
    if entry is None:
        return None
    if time.monotonic() - entry[0] > _UNFILTERED_CACHE_TTL:
        del _unfiltered_cache[key]
        return None
    return entry[1]


def _set_unfiltered(key: str, value: Any) -> None:
    """Store value with current timestamp."""
    _unfiltered_cache[key] = (time.monotonic(), value)


class GalleryQuery:
    """Execute a unified gallery query.

    Usage::

        gq = GalleryQuery(db=db, query_service=srv, config=_config_dict)
        resp = gq.execute(request)
    """

    def __init__(
        self,
        *,
        db: Session,
        query_service: ImageQueryService,
        image_library_path: str,
        image_resources_path: str,
        # Lazy-imported callables from main.py to avoid circular imports.
        active_image_filter: Any = None,
        apply_image_list_filters: Any = None,
        build_display_items_for_image: Any = None,
        merge_duplicate_grouped_items: Any = None,
        read_nsfw_ratings_for_image: Any = None,
        get_video_poster_path: Any = None,
        get_video_thumbnail_path: Any = None,
        image_data_from_db: Any = None,
    ):
        self._db = db
        self._qs = query_service
        self._image_library_path = image_library_path
        self._image_resources_path = image_resources_path
        # External callables (injected to avoid importing main.py)
        self._active_image_filter = active_image_filter
        self._apply_image_list_filters = apply_image_list_filters
        self._build_display_items_for_image = build_display_items_for_image
        self._merge_duplicate_grouped_items = merge_duplicate_grouped_items
        self._read_nsfw_ratings_for_image = read_nsfw_ratings_for_image
        self._get_video_poster_path = get_video_poster_path
        self._get_video_thumbnail_path = get_video_thumbnail_path
        self._image_data_from_db = image_data_from_db

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def execute(self, request: GalleryQueryRequest) -> GalleryQueryResponse:
        """Execute the full query and return a ``GalleryQueryResponse``."""
        # 1. Resolve filter → constrained image IDs (or None for unfiltered)
        constrained_ids = self._resolve_filter(request.filter, request.search)

        # 2. Pre-compute shared values that multiple sections may need.
        #    - total_count: used by both summary.total_images and page.total
        #    - tag_rows:    used by both summary.top_tags and tags section
        total_count: Optional[int] = None
        tag_rows: Optional[list[tuple]] = None

        need_total_count = (
            (request.summary is not None
             and "total_images" in (request.summary.fields or []))
            or request.images is not None
        )
        need_tag_rows = (
            (request.summary is not None
             and ("total_tags" in (request.summary.fields or [])
                  or "top_tags" in (request.summary.fields or [])))
            or request.tags is not None
        )

        if need_total_count:
            total_count = self._count_images(constrained_ids, request.search)

        if need_tag_rows:
            # When tags section is also requested, we need ALL rows so that
            # _compute_tag_details can paginate/slice in Python.  Otherwise
            # we only need the summary's top-N.
            if request.tags is not None:
                limit = 0  # unlimited
            else:
                limit = (request.summary.top_tags_limit
                         if request.summary is not None else 20)
            tag_rows = self._query_top_tags(constrained_ids, limit)

        # 3. Compute optional sections using shared intermediates.
        summary = None
        if request.summary is not None:
            summary = self._compute_summary(
                request.summary, constrained_ids, request.search,
                _total_count=total_count,
                _tag_rows=tag_rows,
            )

        images = None
        page = None
        if request.images is not None:
            images, page = self._fetch_image_page(
                request.images, constrained_ids, request.search,
                _total_count=total_count,
            )

        tags = None
        if request.tags is not None:
            tags = self._compute_tag_details(
                request.tags, constrained_ids,
                _tag_rows=tag_rows,
            )

        return GalleryQueryResponse(
            filter_echo=request.filter.model_dump(),
            summary=summary,
            page=page,
            images=images,
            tags=tags,
        )

    # ------------------------------------------------------------------
    # Filter resolution
    # ------------------------------------------------------------------

    def _resolve_filter(
        self,
        gallery_filter: GalleryFilter,
        search: Optional[str],
    ) -> Optional[set[int]]:
        """Apply all filter clauses and return constrained IDs (or None)."""
        from services.gallery_filter_service import (
            apply_gallery_filter,
            parse_gallery_filter,
        )

        # Build base query
        images_query = (
            self._db.query(ImageModel)
            .options(
                joinedload(ImageModel.artist),
                joinedload(ImageModel.license),
                joinedload(ImageModel.collections),
            )
            .filter(self._active_image_filter())
        )

        # Text search
        if search:
            images_query = self._apply_image_list_filters(
                images_query, search=search,
            )

        # Convert structured filter to the flat format parse_gallery_filter expects
        included_strs = self._flatten_filter_to_strings(gallery_filter.included)
        excluded_strs = self._flatten_filter_to_strings(gallery_filter.excluded)
        hidden_strs = self._flatten_filter_to_strings(gallery_filter.hidden)
        missing_strs = gallery_filter.missing  # already flat list

        parsed = parse_gallery_filter(included_strs, excluded_strs, hidden_strs, missing_strs)
        _, constrained_ids = apply_gallery_filter(
            images_query, parsed, self._db, self._qs,
        )
        return constrained_ids

    @staticmethod
    def _flatten_filter_to_strings(
        filter_dict: dict[str, Any],
    ) -> list[str]:
        """Convert structured filter dict to flat "type:value" strings.

        AND-of-ORs values are flattened — the current filter service
        doesn't support OR groups natively, so each value becomes a
        separate AND term.  This preserves the existing behaviour while
        the service is being migrated.

        Example input:
            {"tag": "anthro", "collection": [["MLP", "Pony"], ["Favorites"]]}
        Output:
            ["tag:anthro", "collection:MLP", "collection:Pony", "collection:Favorites"]
        """
        result: list[str] = []
        for term_type, value in filter_dict.items():
            if isinstance(value, str):
                result.append(f"{term_type}:{value}")
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, str):
                        result.append(f"{term_type}:{item}")
                    elif isinstance(item, list):
                        for sub in item:
                            result.append(f"{term_type}:{sub}")
        return result

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def _compute_summary(
        self,
        spec: SummarySpec,
        constrained_ids: Optional[set[int]],
        search: Optional[str],
        *,
        _total_count: Optional[int] = None,
        _tag_rows: Optional[list[tuple]] = None,
    ) -> SummaryResult:
        """Compute summary counts for the filtered set."""
        result = SummaryResult()
        fields = set(spec.fields)

        if "total_images" in fields:
            if _total_count is not None:
                result.total_images = _total_count
            else:
                result.total_images = self._count_images(constrained_ids, search)

        if "total_tags" in fields or "top_tags" in fields:
            if _tag_rows is not None:
                tag_rows = _tag_rows
            else:
                tag_rows = self._query_top_tags(constrained_ids, spec.top_tags_limit)
            if "total_tags" in fields:
                result.total_tags = sum(r[1] for r in tag_rows)
            if "top_tags" in fields:
                result.top_tags = [
                    TopTag(name=r[0], count=r[1], source=r[2] if len(r) > 2 else None)
                    for r in tag_rows
                ]

        return result

    def _count_images(
        self,
        constrained_ids: Optional[set[int]],
        search: Optional[str],
    ) -> int:
        if constrained_ids is not None:
            return len(constrained_ids)
        # Unfiltered count: try cache first.
        cache_key = f"count:{search or ''}"
        cached = _get_unfiltered(cache_key)
        if cached is not None:
            return cached
        q = (
            self._db.query(func.count(ImageModel.id))
            .filter(self._active_image_filter())
        )
        if search:
            q = self._apply_image_list_filters(q, search=search)
        result = q.scalar() or 0
        _set_unfiltered(cache_key, result)
        return result

    def _query_top_tags(
        self,
        constrained_ids: Optional[set[int]],
        limit: int,
    ) -> list[tuple]:
        """Query top tags from authority_terms for the filtered set.

        Returns list of (name, count, source) tuples, sorted by count DESC.
        When *constrained_ids* is None (unfiltered), the full aggregation is
        cached with a short TTL.
        """
        # Unfiltered case: try cache first.
        if constrained_ids is None:
            cache_key = "top_tags:all"
            cached = _get_unfiltered(cache_key)
            if cached is not None:
                rows = cached
                if limit > 0:
                    rows = rows[:limit]
                return rows

        from models import (
            AuthorityTerm,
            TagAuthority,
            ImageConceptObservation,
        )

        # Base join: authority_terms → image_concept_observations
        q = (
            self._db.query(
                AuthorityTerm.external_name,
                func.count(ImageConceptObservation.id).label("cnt"),
                TagAuthority.name.label("source"),
            )
            .join(
                ImageConceptObservation,
                ImageConceptObservation.authority_term_id == AuthorityTerm.id,
            )
            .join(
                TagAuthority,
                TagAuthority.id == AuthorityTerm.authority_id,
            )
            .group_by(AuthorityTerm.external_name, TagAuthority.name)
        )

        if constrained_ids is not None:
            q = q.filter(
                ImageConceptObservation.image_id.in_(list(constrained_ids))
            )

        q = q.order_by(func.count(ImageConceptObservation.id).desc())

        if constrained_ids is None and limit == 0:
            # Cache the full unlimited result.
            rows = q.all()
            _set_unfiltered("top_tags:all", rows)
            return rows

        if limit > 0:
            q = q.limit(limit)
        return q.all()

    # ------------------------------------------------------------------
    # Image page
    # ------------------------------------------------------------------

    def _fetch_image_page(
        self,
        spec: ImagePageSpec,
        constrained_ids: Optional[set[int]],
        search: Optional[str],
        *,
        _total_count: Optional[int] = None,
    ) -> tuple[list[ImageItem], PageInfo]:
        """Fetch a page of images with display-item building.

        Returns (display_items, page_info).
        """
        db = self._db
        limit = spec.limit

        # Base query
        images_query = (
            db.query(ImageModel)
            .options(
                joinedload(ImageModel.artist),
                joinedload(ImageModel.license),
                joinedload(ImageModel.collections),
            )
            .filter(self._active_image_filter())
        )

        if search:
            images_query = self._apply_image_list_filters(
                images_query, search=search,
            )

        # Constrained IDs
        if constrained_ids is not None:
            if constrained_ids:
                images_query = images_query.filter(
                    ImageModel.id.in_(list(constrained_ids))
                )
            else:
                images_query = images_query.filter(text("1 = 0"))

        # Sort
        sort = spec.sort
        order = spec.order
        if sort in ("date_added", "last_added"):
            if order == "desc":
                images_query = images_query.order_by(ImageModel.id.desc())
            else:
                images_query = images_query.order_by(ImageModel.id.asc())
        elif sort == "date_modified":
            images_query = images_query.order_by(
                ImageModel.date_modified.desc() if order == "desc"
                else ImageModel.date_modified.asc()
            )
        elif sort == "file_size":
            images_query = images_query.order_by(
                ImageModel.file_size.desc() if order == "desc"
                else ImageModel.file_size.asc()
            )
        else:
            # Default: id-based sort
            if order == "desc":
                images_query = images_query.order_by(ImageModel.id.desc())
            else:
                images_query = images_query.order_by(ImageModel.id.asc())

        # Cursor / offset pagination
        use_overfetch = spec.group_variants
        use_cursor = use_overfetch or spec.cursor is not None
        cursor_id = None
        if spec.cursor is not None:
            try:
                cursor_id = int(spec.cursor)
            except (ValueError, TypeError):
                cursor_id = None

        if use_overfetch or cursor_id is not None:
            if cursor_id is not None:
                if order == "desc":
                    images_query = images_query.filter(ImageModel.id < cursor_id)
                else:
                    images_query = images_query.filter(ImageModel.id > cursor_id)
            if use_overfetch:
                images_query = images_query.limit(limit * _OVERFETCH_FACTOR)
            else:
                images_query = images_query.limit(limit)
        else:
            if spec.offset is not None and spec.offset > 0:
                images_query = images_query.offset(spec.offset)
            images_query = images_query.limit(limit)

        images = images_query.all()

        # Build display items
        image_to_variant_group: dict[int, int] = {}
        if spec.group_variants:
            image_ids = [img.id for img in images]
            try:
                membership_rows = (
                    db.query(
                        ImageVariantGroupMembership.image_id,
                        ImageVariantGroupMembership.group_id,
                    )
                    .filter(ImageVariantGroupMembership.image_id.in_(image_ids))
                    .all()
                )
                for img_id, grp_id in membership_rows:
                    if img_id not in image_to_variant_group:
                        image_to_variant_group[img_id] = grp_id
            except Exception:
                pass

        display_items: list[dict[str, Any]] = []
        for image in images:
            db_dict = self._image_data_from_db(image).to_dict()
            db_dict["exif_data"] = None
            db_dict["civitai_data"] = None
            db_dict["json_metadata"] = None
            merged = db_dict

            if (image.mimetype or "").lower().startswith("video/"):
                image_path = Path(self._image_library_path) / str(image.file_path)
                poster_path = self._get_video_poster_path(
                    image_path, self._image_resources_path,
                )
                thumb_path = self._get_video_thumbnail_path(
                    image_path, self._image_resources_path,
                )
                if poster_path.exists() and poster_path.is_file():
                    merged["video_poster_url"] = f"/api/images/{image.file_hash}/video_poster"
                if thumb_path.exists() and thumb_path.is_file():
                    merged["video_thumbnail_url"] = (
                        f"/api/images/{image.file_hash}/video_thumbnail"
                    )

            merged["collection_names"] = [c.name for c in image.collections]
            merged["collection_ids"] = [c.id for c in image.collections]
            merged["artist_name"] = (
                image.artist.name if image.artist is not None else None
            )
            merged["artist_deleted"] = (
                image.artist.civitai_user_deleted if image.artist is not None else None
            )
            merged["artist_original_name"] = (
                image.artist.civitai_user_original_name if image.artist is not None else None
            )
            nsfw_ratings = self._read_nsfw_ratings_for_image(image)
            merged["nsfw_ratings"] = nsfw_ratings
            merged["nsfw_rating"] = nsfw_ratings[0] if nsfw_ratings else None
            merged["user_nsfw_rating"] = image.user_nsfw_rating
            merged["user_nsfw_safety_class"] = image.user_nsfw_safety_class
            db_user_tags = getattr(image, "user_tags", None)
            if isinstance(db_user_tags, list) and db_user_tags:
                merged["user_tags"] = db_user_tags
            db_user_neg_tags = getattr(image, "user_negative_tags", None)
            if isinstance(db_user_neg_tags, list) and db_user_neg_tags:
                merged["user_negative_tags"] = db_user_neg_tags

            display_items.extend(
                self._build_display_items_for_image(
                    image,
                    merged,
                    group_variants=spec.group_variants,
                    variant_group_id=image_to_variant_group.get(image.id),
                )
            )

        if spec.group_variants:
            display_items = self._merge_duplicate_grouped_items(display_items)

        # Pagination info
        next_cursor: Optional[str] = None
        has_more = False
        if use_cursor or use_overfetch:
            if len(display_items) > limit:
                display_items = display_items[:limit]
                has_more = True
            if images and len(display_items) >= limit:
                next_cursor = str(images[-1].id)
            else:
                next_cursor = None
                has_more = False

        # Total count
        if _total_count is not None:
            total_count = _total_count
        else:
            total_count = self._count_images(constrained_ids, search)

        page_info = PageInfo(
            offset=spec.offset or 0,
            limit=limit,
            total=total_count,
            next_cursor=next_cursor,
            has_more=has_more,
        )

        # Wrap raw dicts as ImageItem (allows extra fields)
        image_items = [ImageItem(**item) for item in display_items]
        return image_items, page_info

    # ------------------------------------------------------------------
    # Tag details
    # ------------------------------------------------------------------

    def _compute_tag_details(
        self,
        spec: TagDetailSpec,
        constrained_ids: Optional[set[int]],
        *,
        _tag_rows: Optional[list[tuple]] = None,
    ) -> list[TagDetailItem]:
        """Compute tag detail rows for the filtered set.

        When ``_tag_rows`` is provided (pre-computed by the caller from
        ``_query_top_tags``), we slice/filter them in Python instead of
        hitting the DB again.  This is a significant win when both summary
        and tags are requested in the same call.
        """
        # Fast path: reuse pre-computed rows.
        if _tag_rows is not None:
            rows = _tag_rows
            # Apply optional source filter.
            if spec.source is not None:
                src_lower = spec.source.lower()
                rows = [r for r in rows if len(r) > 2 and r[2].lower() == src_lower]
            # Apply offset/limit in Python.
            offset = spec.offset or 0
            rows = rows[offset: offset + spec.limit]
            return [
                TagDetailItem(name=r[0], count=r[1], source=r[2] if len(r) > 2 else None)
                for r in rows
            ]

        # Slow path: query DB.
        from models import (
            AuthorityTerm,
            TagAuthority,
            ImageConceptObservation,
        )

        q = (
            self._db.query(
                AuthorityTerm.external_name,
                func.count(ImageConceptObservation.id).label("cnt"),
                TagAuthority.name.label("source"),
            )
            .join(
                ImageConceptObservation,
                ImageConceptObservation.authority_term_id == AuthorityTerm.id,
            )
            .join(
                TagAuthority,
                TagAuthority.id == AuthorityTerm.authority_id,
            )
            .group_by(AuthorityTerm.external_name, TagAuthority.name)
            .order_by(func.count(ImageConceptObservation.id).desc())
        )

        if constrained_ids is not None:
            q = q.filter(
                ImageConceptObservation.image_id.in_(list(constrained_ids))
            )

        if spec.source is not None:
            q = q.filter(func.lower(TagAuthority.name) == spec.source.lower())

        q = q.offset(spec.offset).limit(spec.limit)
        rows = q.all()

        return [
            TagDetailItem(name=r[0], count=r[1], source=r[2])
            for r in rows
        ]
