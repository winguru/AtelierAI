"""Image list filter helpers: missing-data and missing-source conditions.

Provides standalone SQLAlchemy filter constructors for the gallery image list
endpoint.  Thin wrappers for image_query_service.apply_image_list_filters are
also consolidated here.

TODO Phase 3: used by the /images/* router; extract the thin wrappers from
main.py lines 513–593 as needed when that router is populated.
"""

from __future__ import annotations

from typing import Optional

import sqlalchemy as sa
from sqlalchemy.orm import Session

from models import ImageConceptObservation, ImageModel, TagAuthority


# ---------------------------------------------------------------------------
# Missing-data filter
# ---------------------------------------------------------------------------


def _build_missing_data_condition(key: str) -> list:
    """Return SQLAlchemy filter expressions for a *missing-data* condition."""
    Im = ImageModel

    if key == "no artist":
        return [sa.or_(Im.artist_id.is_(None), Im.artist_id == 0)]
    if key == "no source url":
        return [sa.or_(Im.source_url.is_(None), Im.source_url == "")]
    if key == "no generation info":
        return [
            sa.or_(Im.generation_software.is_(None), Im.generation_software == "")
        ]
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
    if key == "no comfyui metadata":
        return [Im.has_comfyui_metadata == False]  # noqa: E712
    if key == "no nsfw rating":
        return [
            sa.and_(Im.user_nsfw_rating.is_(None), Im.civitai_nsfw_level.is_(None))
        ]
    if key == "no safety class":
        return [Im.user_nsfw_safety_class.is_(None)]
    if key == "no exif data":
        return [
            sa.or_(
                Im.exif_data.is_(None),
                Im.exif_data == "{}",
                Im.exif_data == "{}\n",
            )
        ]
    if key == "no civitai meta":
        return [
            sa.or_(
                sa.func.lower(Im.source_site) != "civitai",
                Im.json_metadata.is_(None),
                Im.json_metadata == "{}",
                Im.json_metadata == "{}\n",
            )
        ]
    if key == "no tags":
        return [
            ~Im.id.in_(
                sa.select(ImageConceptObservation.image_id).where(
                    sa.and_(
                        ImageConceptObservation.image_id == Im.id,
                        ImageConceptObservation.authority_id == (
                            sa.select(TagAuthority.id).where(
                                TagAuthority.name == "user"
                            )
                        ),
                    )
                )
            )
        ]
    return []


def _normalize_missing_data_key(raw: str) -> str:
    """Normalise a missing-data label to the canonical ``"no <field>"`` form."""
    stripped = (raw or "").strip().lower()
    if not stripped:
        return ""
    if not stripped.startswith("no "):
        stripped = f"no {stripped}"
    return stripped


def _filter_image_ids_by_missing_data(
    images_query,
    missing_data: Optional[list[str]],
) -> Optional[list[int]]:
    """Return image IDs matching ALL missing-data conditions, or ``None``."""
    if not missing_data:
        return None

    conditions = []
    for raw_entry in missing_data:
        key = _normalize_missing_data_key(raw_entry)
        if not key:
            continue
        conditions.extend(_build_missing_data_condition(key))

    if not conditions:
        return None

    combined = sa.and_(*conditions) if len(conditions) > 1 else conditions[0]
    rows = images_query.with_entities(ImageModel.id).filter(combined).all()
    return [row[0] for row in rows]


# ---------------------------------------------------------------------------
# Missing-source (tag source) filter
# ---------------------------------------------------------------------------


def _build_missing_source_condition(source: str) -> list:
    """Return SQLAlchemy filter expressions for a *missing-source* condition."""
    Im = ImageModel
    source_lower = (source or "").strip().lower()

    if source_lower == "civitai":
        return [
            ~Im.id.in_(
                sa.select(ImageConceptObservation.image_id).where(
                    sa.and_(
                        ImageConceptObservation.image_id == Im.id,
                        ImageConceptObservation.authority_id == (
                            sa.select(TagAuthority.id).where(
                                TagAuthority.name == "civitai"
                            )
                        ),
                    )
                )
            )
        ]
    if source_lower == "danbooru":
        return [
            sa.and_(
                sa.not_(Im.json_metadata.cast(sa.String).like("%danbooru%")),
                sa.not_(Im.exif_data.cast(sa.String).like("%danbooru%")),
            )
        ]
    if source_lower == "prompt":
        return [Im.has_generation_prompt == False]  # noqa: E712
    if source_lower == "user":
        return [
            ~Im.id.in_(
                sa.select(ImageConceptObservation.image_id).where(
                    sa.and_(
                        ImageConceptObservation.image_id == Im.id,
                        ImageConceptObservation.authority_id == (
                            sa.select(TagAuthority.id).where(
                                TagAuthority.name == "user"
                            )
                        ),
                    )
                )
            )
        ]
    return []


def _filter_image_ids_by_missing_source(
    images_query,
    missing_source: Optional[list[str]],
) -> Optional[list[int]]:
    """Return image IDs that have zero tags from ALL specified sources."""
    if not missing_source:
        return None

    conditions = []
    for source in missing_source:
        conditions.extend(_build_missing_source_condition(source))

    if not conditions:
        return None

    combined = sa.and_(*conditions) if len(conditions) > 1 else conditions[0]
    rows = images_query.with_entities(ImageModel.id).filter(combined).all()
    return [row[0] for row in rows]
