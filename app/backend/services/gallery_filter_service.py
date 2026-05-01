# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/gallery-filter.md
# ──────────────────────────────────────────────────────────────────────────────
"""Unified gallery filter service.

Parses the 4-concept CGI filter model (included, excluded, hidden, missing)
into a ParsedGalleryFilter and delegates to existing ImageQueryService filter
functions for actual query construction.

Term format: ``type:value``  e.g. ``tag:portrait``, ``artist:Rembrandt``,
``nsfw:xxx``, ``missing:model``.

Missing feature terms use a double-colon qualifier:
``missing:feature:a1111_hires``.
"""

from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Query, Session

from models import (
    CivitaiModel,
    ImageConceptObservation,
    ImageModel,
    ModelObservation,
    TagAuthority,
)
from schemas import (
    EXCLUDED_TERM_TYPES,
    HIDDEN_TERM_TYPES,
    INCLUDED_TERM_TYPES,
    MISSING_TERMS,
    FilterTerm,
    ParsedGalleryFilter,
)
from services.image_query_service import ImageQueryService

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _parse_term(raw: str) -> Optional[FilterTerm]:
    """Parse a single ``type:value`` or ``type:qualifier:value`` string."""
    if not raw or ":" not in raw:
        return None
    first_colon = raw.index(":")
    term_type = raw[:first_colon].strip().lower()
    remainder = raw[first_colon + 1:].strip()
    if not term_type or not remainder:
        return None

    # Check for qualifier (type:qualifier:value)
    if ":" in remainder:
        second_colon = remainder.index(":")
        qualifier = remainder[:second_colon].strip().lower()
        value = remainder[second_colon + 1:].strip()
        if qualifier and value:
            return FilterTerm(type=term_type, value=value, qualifier=qualifier)

    return FilterTerm(type=term_type, value=remainder)


def parse_gallery_filter(
    included: Optional[list[str]] = None,
    excluded: Optional[list[str]] = None,
    hidden: Optional[list[str]] = None,
    missing: Optional[list[str]] = None,
) -> ParsedGalleryFilter:
    """Parse the 4 CGI filter lists into a structured ParsedGalleryFilter.

    Unknown or malformed terms are silently ignored (logged at debug level).
    """
    included_by_type: dict[str, list[str]] = {}
    excluded_by_type: dict[str, list[str]] = {}
    hidden_by_type: dict[str, list[str]] = {}
    missing_keys: set[str] = set()
    missing_features: set[str] = set()

    def _add_to_group(group: dict[str, list[str]], term: FilterTerm) -> None:
        group.setdefault(term.type, []).append(term.value)

    # --- Included ---
    for raw in (included or []):
        term = _parse_term(raw)
        if term is None:
            logger.debug("Skipping malformed included term: %r", raw)
            continue
        if term.type in INCLUDED_TERM_TYPES:
            _add_to_group(included_by_type, term)
        else:
            logger.debug("Skipping unknown included term type %r: %s", term.type, raw)

    # --- Excluded ---
    for raw in (excluded or []):
        term = _parse_term(raw)
        if term is None:
            logger.debug("Skipping malformed excluded term: %r", raw)
            continue
        if term.type in EXCLUDED_TERM_TYPES:
            _add_to_group(excluded_by_type, term)
        else:
            logger.debug("Skipping unknown excluded term type %r: %s", term.type, raw)

    # --- Hidden ---
    for raw in (hidden or []):
        term = _parse_term(raw)
        if term is None:
            logger.debug("Skipping malformed hidden term: %r", raw)
            continue
        if term.type in HIDDEN_TERM_TYPES:
            _add_to_group(hidden_by_type, term)
        else:
            logger.debug("Skipping unknown hidden term type %r: %s", term.type, raw)

    # --- Missing ---
    for raw in (missing or []):
        term = _parse_term(raw)
        if term is None:
            # Support bare keys without prefix (e.g. "artist" instead of "missing:artist")
            key = raw.strip().lower()
            if key in MISSING_TERMS:
                missing_keys.add(key)
            else:
                logger.debug("Skipping unrecognized missing term: %r", raw)
            continue
        # "missing:feature:a1111_hires" → type="missing", qualifier="feature", value="a1111_hires"
        if term.type == "missing":
            if term.qualifier == "feature" and term.value:
                missing_features.add(term.value)
            elif term.value in MISSING_TERMS:
                missing_keys.add(term.value)
            else:
                logger.debug("Skipping unknown missing value: %r", term.value)
        elif term.type == "feature" and term.value:
            # Shorthand: "feature:a1111_hires" treated as missing feature
            missing_features.add(term.value)
        else:
            logger.debug("Skipping unrecognized missing term: %r", raw)

    # Build raw_text representation for caching/debugging
    raw_parts: list[str] = []
    if included:
        raw_parts.append("included=" + ",".join(included))
    if excluded:
        raw_parts.append("excluded=" + ",".join(excluded))
    if hidden:
        raw_parts.append("hidden=" + ",".join(hidden))
    if missing:
        raw_parts.append("missing=" + ",".join(missing))

    return ParsedGalleryFilter(
        included_by_type=included_by_type,
        excluded_by_type=excluded_by_type,
        hidden_by_type=hidden_by_type,
        missing_keys=missing_keys,
        missing_features=missing_features,
        raw_text="; ".join(raw_parts),
        dry_run=False,
    )


# ---------------------------------------------------------------------------
# Filter Application
# ---------------------------------------------------------------------------

def apply_gallery_filter(
    images_query: Query,
    parsed: ParsedGalleryFilter,
    db: Session,
    query_service: ImageQueryService,
) -> tuple[Query, Optional[set[int]]]:
    """Apply a parsed gallery filter to an images query.

    Returns:
        (modified_query, pre_filtered_ids) where:
        - modified_query has relational filters (source, mimetype, artist,
          collection, search) applied directly
        - pre_filtered_ids is a set of image IDs satisfying tag/feature/nsfw/
          missing filters (to be AND-combined with the query by the caller)
        - pre_filtered_ids is None when no ID-based filters were needed
    """
    # Apply relational filters directly to the query
    images_query = _apply_relational_filters(images_query, parsed, db, query_service)

    # Collect ID-based filter results
    id_filters: list[set[int]] = []
    id_filters.extend(_apply_tag_filters(images_query, parsed, query_service))
    id_filters.extend(_apply_feature_filters(images_query, parsed, query_service))
    id_filters.extend(_apply_nsfw_filters(images_query, parsed, query_service))
    id_filters.extend(_apply_missing_filters(images_query, parsed, db))

    # Intersect all ID sets (AND logic)
    if not id_filters:
        return images_query, None

    result: set[int] = id_filters[0]
    for id_set in id_filters[1:]:
        result &= id_set
    return images_query, result


# ---------------------------------------------------------------------------
# Relational filters (modify the Query directly)
# ---------------------------------------------------------------------------

def _apply_relational_filters(
    images_query: Query,
    parsed: ParsedGalleryFilter,
    db: Session,
    query_service: ImageQueryService,
) -> Query:
    """Apply source, mimetype, artist, collection, model filters to the query."""
    from models import (
        Artist,
        CollectionModel,
        ImageCollectionMembership,
    )

    inc = parsed.included_by_type
    exc = parsed.excluded_by_type

    # --- Source site ---
    source_sites = inc.get("source", [])
    exclude_sources = exc.get("source", [])
    if source_sites:
        normalized = query_service.normalize_query_values(source_sites)
        images_query = images_query.filter(
            func.lower(ImageModel.source_site).in_(normalized)
        )
    if exclude_sources:
        normalized = query_service.normalize_query_values(exclude_sources)
        images_query = images_query.filter(
            ~func.lower(ImageModel.source_site).in_(normalized)
        )

    # --- Mimetype ---
    mimetypes = inc.get("mimetype", [])
    exclude_mimetypes = exc.get("mimetype", [])
    if mimetypes:
        normalized = query_service.normalize_query_values(mimetypes)
        images_query = images_query.filter(
            func.lower(ImageModel.mimetype).in_(normalized)
        )
    if exclude_mimetypes:
        normalized = query_service.normalize_query_values(exclude_mimetypes)
        images_query = images_query.filter(
            ~func.lower(ImageModel.mimetype).in_(normalized)
        )

    # --- Artist ---
    artist_names = inc.get("artist", [])
    exclude_artists = exc.get("artist", [])
    if artist_names:
        normalized = query_service.normalize_query_values(artist_names)
        numeric_ids = _safe_int_ids(normalized)
        conditions = [
            func.lower(Artist.name).in_(normalized),
            func.lower(Artist.civitai_user_original_name).in_(normalized),
        ]
        if numeric_ids:
            conditions.append(Artist.civitai_user_id.in_(numeric_ids))
        images_query = images_query.filter(
            ImageModel.artist.has(or_(*conditions))
        )
    if exclude_artists:
        normalized = query_service.normalize_query_values(exclude_artists)
        numeric_ids = _safe_int_ids(normalized)
        conditions = [
            func.lower(Artist.name).in_(normalized),
            func.lower(Artist.civitai_user_original_name).in_(normalized),
        ]
        if numeric_ids:
            conditions.append(Artist.civitai_user_id.in_(numeric_ids))
        excluded_sq = (
            select(ImageModel.id)
            .join(Artist, ImageModel.artist_id == Artist.id)
            .where(or_(*conditions))
            .correlate(None)
            .scalar_subquery()
        )
        images_query = images_query.filter(ImageModel.id.notin_(excluded_sq))

    # --- Collection ---
    collection_names = inc.get("collection", [])
    exclude_collections = exc.get("collection", [])
    if collection_names:
        normalized = query_service.normalize_query_values(collection_names)
        images_query = images_query.filter(
            ImageModel.collections.any(
                func.lower(CollectionModel.name).in_(normalized)
            )
        )
    if exclude_collections:
        normalized = query_service.normalize_query_values(exclude_collections)
        excluded_sq = (
            select(ImageCollectionMembership.image_id)
            .join(CollectionModel, CollectionModel.id == ImageCollectionMembership.collection_id)
            .where(func.lower(CollectionModel.name).in_(normalized))
            .correlate(None)
            .scalar_subquery()
        )
        images_query = images_query.filter(ImageModel.id.notin_(excluded_sq))

    # --- Model (checkpoint/lora via ModelObservation) ---
    model_names = inc.get("model", [])
    if model_names:
        images_query = _filter_by_model_names(images_query, model_names, include=True)
    exclude_models = exc.get("model", [])
    if exclude_models:
        images_query = _filter_by_model_names(images_query, exclude_models, include=False)

    # --- Generation software ---
    software_names = inc.get("software", [])
    if software_names:
        ids = query_service.filter_image_ids_by_generation_software(
            images_query, software_names
        )
        if ids is not None:
            images_query = images_query.filter(ImageModel.id.in_(ids))

    return images_query


# ---------------------------------------------------------------------------
# ID-based filters (return sets for intersection)
# ---------------------------------------------------------------------------

def _apply_tag_filters(
    images_query: Query,
    parsed: ParsedGalleryFilter,
    query_service: ImageQueryService,
) -> list[set[int]]:
    """Apply include/exclude tag filters, return ID sets to intersect."""
    results: list[set[int]] = []
    inc_tags = parsed.included_by_type.get("tag", [])
    exc_tags = parsed.excluded_by_type.get("tag", [])
    if not inc_tags and not exc_tags:
        return results
    ids = query_service.filter_image_ids_by_tag_names(
        images_query,
        include_tags=inc_tags if inc_tags else None,
        exclude_tags=exc_tags if exc_tags else None,
    )
    if ids is not None:
        results.append(set(ids))
    return results


def _apply_feature_filters(
    images_query: Query,
    parsed: ParsedGalleryFilter,
    query_service: ImageQueryService,
) -> list[set[int]]:
    """Apply A1111 feature filters, return ID sets to intersect."""
    results: list[set[int]] = []
    inc_features = parsed.included_by_type.get("feature", [])
    exc_features = parsed.excluded_by_type.get("feature", [])
    if not inc_features and not exc_features:
        return results

    # Map feature names to A1111 filter params
    feature_map = {
        "a1111_hires": "a1111_hires",
        "a1111_regional_prompter": "a1111_regional_prompter",
        "a1111_adetailer": "a1111_adetailer",
        "hires": "a1111_hires",
        "regional_prompter": "a1111_regional_prompter",
        "adetailer": "a1111_adetailer",
        "rp": "a1111_regional_prompter",
    }

    # Included features: present
    inc_params: dict[str, list[str]] = {}
    for feat in inc_features:
        mapped = feature_map.get(feat.lower())
        if mapped:
            inc_params.setdefault(mapped, []).append("present")

    # Excluded features: absent
    exc_params: dict[str, list[str]] = {}
    for feat in exc_features:
        mapped = feature_map.get(feat.lower())
        if mapped:
            exc_params.setdefault(mapped, []).append("absent")

    # Merge: if same feature appears in both, include wins
    for key in exc_params:
        if key not in inc_params:
            inc_params[key] = exc_params[key]

    if inc_params:
        ids = query_service.filter_image_ids_by_a1111_features(
            images_query,
            a1111_hires=inc_params.get("a1111_hires"),
            a1111_regional_prompter=inc_params.get("a1111_regional_prompter"),
            a1111_adetailer=inc_params.get("a1111_adetailer"),
        )
        if ids is not None:
            results.append(set(ids))
    return results


def _apply_nsfw_filters(
    images_query: Query,
    parsed: ParsedGalleryFilter,
    query_service: ImageQueryService,
) -> list[set[int]]:
    """Apply NSFW rating / safety class filters, return ID sets to intersect."""
    results: list[set[int]] = []
    nsfw_values = parsed.hidden_by_type.get("nsfw", [])
    nsfw_safety_values = parsed.hidden_by_type.get("nsfw_safety", [])

    if nsfw_values:
        ids = query_service.filter_image_ids_by_nsfw_ratings(
            images_query, nsfw_values
        )
        if ids is not None:
            results.append(set(ids))

    if nsfw_safety_values:
        ids = query_service.filter_image_ids_by_nsfw_safety_classes(
            images_query, nsfw_safety_values
        )
        if ids is not None:
            results.append(set(ids))

    return results


def _apply_missing_filters(
    images_query: Query,
    parsed: ParsedGalleryFilter,
    db: Session,
) -> list[set[int]]:
    """Apply missing-data / missing-source / missing-model / missing-feature filters.

    Uses local implementations to avoid circular imports from main.py.
    The missing-data conditions mirror ``_build_missing_data_condition`` in main.py.
    """
    results: list[set[int]] = []

    # Map our missing keys to the existing missing_data format
    key_to_missing_data_key: dict[str, str] = {
        "artist": "no artist",
        "prompt": "no prompt",
        "generation": "no generation info",
        "exif": "no a1111 metadata",
        "source": "no source url",
        "civitai_meta": "no a1111 metadata",
    }

    # Source-specific missing (different from missing_data — checks tag sources)
    source_missing_keys = {"tags"}  # maps to missing_source logic
    source_name_map: dict[str, str] = {
        "tags": "civitai",  # default: check civitai tag source
    }

    missing_data_keys: list[str] = []
    missing_source_keys: list[str] = []

    for key in parsed.missing_keys:
        if key in key_to_missing_data_key:
            missing_data_keys.append(key_to_missing_data_key[key])
        elif key in source_missing_keys:
            missing_source_keys.append(source_name_map.get(key, key))
        elif key == "model":
            # Model-specific missing filter
            ids = _filter_image_ids_by_missing_model(images_query)
            if ids is not None:
                results.append(set(ids))
        elif key == "checkpoint":
            ids = _filter_image_ids_by_missing_checkpoint(images_query)
            if ids is not None:
                results.append(set(ids))
        elif key == "lora":
            ids = _filter_image_ids_by_missing_lora(images_query)
            if ids is not None:
                results.append(set(ids))
        elif key == "nsfw":
            ids = _filter_image_ids_by_missing_nsfw(images_query)
            if ids is not None:
                results.append(set(ids))

    # Missing features (e.g. a1111_hires)
    for feat in parsed.missing_features:
        feat_to_missing_key = {
            "a1111_hires": "no a1111 hires upscale",
            "a1111_regional_prompter": "no a1111 regional prompter",
            "a1111_adetailer": "no a1111 adetailer",
            "hires": "no a1111 hires upscale",
            "regional_prompter": "no a1111 regional prompter",
            "adetailer": "no a1111 adetailer",
        }
        mapped = feat_to_missing_key.get(feat)
        if mapped:
            missing_data_keys.append(mapped)

    if missing_data_keys:
        ids = _local_filter_by_missing_data(images_query, missing_data_keys)
        if ids is not None:
            results.append(set(ids))

    if missing_source_keys:
        ids = _local_filter_by_missing_source(images_query, missing_source_keys)
        if ids is not None:
            results.append(set(ids))

    return results


# ---------------------------------------------------------------------------
# Local missing-data / missing-source implementations
# (mirrors main.py logic to avoid circular imports)
# ---------------------------------------------------------------------------

def _build_missing_data_condition(key: str) -> list:
    """Build SQLAlchemy filter expressions for a missing-data condition."""
    Im = ImageModel
    if key == "no artist":
        return [or_(Im.artist_id.is_(None), Im.artist_id == 0)]
    if key == "no source url":
        return [or_(Im.source_url.is_(None), Im.source_url == "")]
    if key == "no generation info":
        return [or_(Im.generation_software.is_(None), Im.generation_software == "")]
    if key == "no prompt":
        return [Im.has_generation_prompt == False]  # noqa: E712
    if key == "no a1111 metadata":
        return [Im.has_a1111_metadata == False]  # noqa: E712
    if key == "no a1111 hires upscale":
        return [Im.a1111_hires == False]  # noqa: E712
    if key == "no a1111 regional prompter":
        return [Im.a1111_regional_prompter == False]  # noqa: E712
    if key == "no a1111 adetailer":
        return [Im.a1111_adetailer == False]  # noqa: E712
    return []


def _local_filter_by_missing_data(
    images_query: Query,
    missing_data: list[str],
) -> Optional[set[int]]:
    """Return image IDs matching ALL missing-data conditions (AND logic)."""
    if not missing_data:
        return None
    conditions: list = []
    for raw in missing_data:
        key = raw.strip().lower()
        if not key.startswith("no "):
            key = f"no {key}"
        for expr in _build_missing_data_condition(key):
            conditions.append(expr)
    if not conditions:
        return None
    combined = and_(*conditions) if len(conditions) > 1 else conditions[0]
    rows = images_query.with_entities(ImageModel.id).filter(combined).all()
    return {row[0] for row in rows}


def _build_missing_source_condition(source: str) -> list:
    """Build SQLAlchemy filter for images missing tags from *source*."""
    Im = ImageModel
    source_lower = source.strip().lower()
    if source_lower == "civitai":
        return [
            ~Im.id.in_(
                select(ImageConceptObservation.image_id).where(
                    and_(
                        ImageConceptObservation.image_id == Im.id,
                        ImageConceptObservation.authority_id == (
                            select(TagAuthority.id).where(
                                TagAuthority.name == "civitai"
                            )
                        ),
                    )
                )
            )
        ]
    if source_lower == "prompt":
        return [Im.has_generation_prompt == False]  # noqa: E712
    if source_lower == "user":
        return [
            ~Im.id.in_(
                select(ImageConceptObservation.image_id).where(
                    and_(
                        ImageConceptObservation.image_id == Im.id,
                        ImageConceptObservation.authority_id == (
                            select(TagAuthority.id).where(
                                TagAuthority.name == "user"
                            )
                        ),
                    )
                )
            )
        ]
    return []


def _local_filter_by_missing_source(
    images_query: Query,
    missing_source: list[str],
) -> Optional[set[int]]:
    """Return image IDs with zero tags from ALL specified sources (AND)."""
    if not missing_source:
        return None
    conditions: list = []
    for source in missing_source:
        for expr in _build_missing_source_condition(source):
            conditions.append(expr)
    if not conditions:
        return None
    combined = and_(*conditions) if len(conditions) > 1 else conditions[0]
    rows = images_query.with_entities(ImageModel.id).filter(combined).all()
    return {row[0] for row in rows}


# ---------------------------------------------------------------------------
# Model-specific missing filters
# ---------------------------------------------------------------------------

def _filter_image_ids_by_missing_model(images_query: Query) -> Optional[set[int]]:
    """Return IDs of images with no ModelObservation of any type."""
    has_model = (
        images_query.session.query(ModelObservation.image_id)
        .distinct()
        .subquery()
        .select()
    )
    result = (
        images_query.options()
        .with_entities(ImageModel.id)
        .filter(ImageModel.id.notin_(has_model))
        .all()
    )
    if not result:
        return set()
    return {row[0] for row in result}


def _filter_image_ids_by_missing_checkpoint(images_query: Query) -> Optional[set[int]]:
    """Return IDs of images with no checkpoint ModelObservation."""
    has_checkpoint = (
        images_query.session.query(ModelObservation.image_id)
        .filter(ModelObservation.resource_type == "Checkpoint")
        .distinct()
        .subquery()
        .select()
    )
    result = (
        images_query.options()
        .with_entities(ImageModel.id)
        .filter(ImageModel.id.notin_(has_checkpoint))
        .all()
    )
    if not result:
        return set()
    return {row[0] for row in result}


def _filter_image_ids_by_missing_lora(images_query: Query) -> Optional[set[int]]:
    """Return IDs of images with no lora ModelObservation."""
    has_lora = (
        images_query.session.query(ModelObservation.image_id)
        .filter(ModelObservation.resource_type == "LORA")
        .distinct()
        .subquery()
        .select()
    )
    result = (
        images_query.options()
        .with_entities(ImageModel.id)
        .filter(ImageModel.id.notin_(has_lora))
        .all()
    )
    if not result:
        return set()
    return {row[0] for row in result}


def _filter_image_ids_by_missing_nsfw(images_query: Query) -> Optional[set[int]]:
    """Return IDs of images with no NSFW classification (user or civitai)."""
    result = (
        images_query.options()
        .with_entities(ImageModel.id)
        .filter(
            ImageModel.user_nsfw_rating.is_(None),
            ImageModel.user_nsfw_safety_class.is_(None),
            ImageModel.civitai_nsfw_level.is_(None),
        )
        .all()
    )
    if not result:
        return set()
    return {row[0] for row in result}


# ---------------------------------------------------------------------------
# Model name filter (include/exclude by model name)
# ---------------------------------------------------------------------------

def _filter_by_model_names(
    images_query: Query,
    model_names: list[str],
    *,
    include: bool,
) -> Query:
    """Filter images by model name (checkpoint or lora) via ModelObservation -> CivitaiModel."""
    normalized = [n.strip().lower() for n in model_names if n and n.strip()]
    if not normalized:
        return images_query

    model_ids_sq = (
        select(ModelObservation.image_id)
        .join(CivitaiModel, ModelObservation.civitai_model_id == CivitaiModel.civitai_model_id)
        .filter(func.lower(CivitaiModel.name).in_(normalized))
        .correlate(None)
        .scalar_subquery()
    )
    if include:
        return images_query.filter(ImageModel.id.in_(model_ids_sq))
    else:
        return images_query.filter(ImageModel.id.notin_(model_ids_sq))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_int_ids(values: list[str]) -> list[int]:
    """Attempt to parse strings as integers for ID-based lookups."""
    result: list[int] = []
    for v in values:
        try:
            result.append(int(v))
        except (ValueError, TypeError):
            pass
    return result
