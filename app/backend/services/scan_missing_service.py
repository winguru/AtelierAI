# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/scan-missing.md
# ──────────────────────────────────────────────────────────────────────────────
"""Scan for images with CivitAI IDs but no tag observations.

3-tier resolution strategy:
  Tier 1 — Sidecar JSON files (data.civitai.tags)
  Tier 2 — Archived API response files (civitai_image_tag_getVotableTags_*.json)
  Tier 3 — Live CivitAI API via fetch_image_tag_records_cached()

Reports progress via SSE events.
"""

from __future__ import annotations

import json
import math
from datetime import datetime
from pathlib import Path
from typing import Callable, Generator, Optional

import sqlalchemy as sa
from sqlalchemy.orm import Session

from models import (
    AuthorityTerm,
    ImageConceptObservation,
    ImageModel,
    ObservationCertainty,
    ObservationSource,
    TagAuthority,
)
from services.taxonomy_service import TaxonomyService

# -- Constants ----------------------------------------------------------------

_CIVITAI_AUTHORITY_NAME = "civitai"
_OBSERVATION_SOURCE_IMPORT = ObservationSource.IMPORT
_OBSERVATION_CERTAINTY_LIKELY = ObservationCertainty.LIKELY

# -- Helpers ------------------------------------------------------------------


def _normalize_taxonomy_text(value: str) -> str:
    return TaxonomyService.normalize_text(value)


def _emit(event: str, data: dict) -> str:
    """Format an SSE event string."""
    import json as _json

    return f"event: {event}\ndata: {_json.dumps(data)}\n\n"


def _get_or_create_civitai_authority(db: Session) -> TagAuthority:
    """Get or create the CivitAI tag authority."""
    _svc = TaxonomyService()
    return _svc.get_or_create_authority(db, _CIVITAI_AUTHORITY_NAME)


def _upsert_authority_terms(db: Session, authority: TagAuthority, tags: list[dict]) -> dict:
    """Sync CivitAI tag records into authority_terms. Returns stats dict."""
    stats = {"terms_upserted": 0, "terms_created": 0, "terms_updated": 0}
    if not tags:
        return stats

    for tag in tags:
        if not isinstance(tag, dict):
            continue
        raw_name = str(tag.get("name") or "").strip()
        if not raw_name:
            continue
        normalized_name = _normalize_taxonomy_text(raw_name)
        raw_tag_id = tag.get("id")
        try:
            external_tag_id = int(raw_tag_id) if raw_tag_id not in (None, "") else None
        except (TypeError, ValueError):
            external_tag_id = None

        tag_meta: dict = {}
        for meta_key in ("type", "nsfwLevel", "automated", "concrete", "score"):
            val = tag.get(meta_key)
            if val is not None:
                tag_meta[meta_key] = val

        term = None
        if external_tag_id is not None:
            term = (
                db.query(AuthorityTerm)
                .filter(
                    AuthorityTerm.authority_id == authority.id,
                    AuthorityTerm.external_tag_id == external_tag_id,
                )
                .first()
            )
        if term is None:
            term = (
                db.query(AuthorityTerm)
                .filter(
                    AuthorityTerm.authority_id == authority.id,
                    AuthorityTerm.normalized_external_name == normalized_name,
                )
                .first()
            )

        now = datetime.utcnow()
        if term is None:
            term = AuthorityTerm(
                authority_id=authority.id,
                external_tag_id=external_tag_id,
                external_name=raw_name,
                normalized_external_name=normalized_name,
                concept_id=None,
                metadata_json=tag_meta if tag_meta else None,
                created_at=now,
                updated_at=now,
                last_seen_at=now,
            )
            db.add(term)
            stats["terms_created"] += 1
        else:
            changed = False
            if getattr(term, "external_tag_id", None) != external_tag_id:
                term.external_tag_id = external_tag_id
                changed = True
            if str(term.external_name or "") != raw_name:
                term.external_name = raw_name
                changed = True
            if str(term.normalized_external_name or "") != normalized_name:
                term.normalized_external_name = normalized_name
                changed = True
            if tag_meta:
                existing_meta = term.metadata_json if isinstance(term.metadata_json, dict) else {}
                merged_meta = {**existing_meta, **tag_meta}
                if merged_meta != existing_meta:
                    term.metadata_json = merged_meta
                    changed = True
            term.last_seen_at = now
            if changed:
                term.updated_at = now
                stats["terms_updated"] += 1
        stats["terms_upserted"] += 1

    if stats["terms_upserted"]:
        db.flush()
    return stats


def _create_observations_for_image(
    db: Session,
    image_id: int,
    authority: TagAuthority,
    tag_names: list[str],
    now: datetime,
) -> tuple[int, int]:
    """Create observations for an image from normalized tag names.

    Returns (created, skipped) counts.
    """
    created = 0
    skipped = 0

    tag_norms = {_normalize_taxonomy_text(n) for n in tag_names if n and n.strip()}
    if not tag_norms:
        return created, skipped

    matched_terms = (
        db.query(AuthorityTerm)
        .filter(
            AuthorityTerm.authority_id == authority.id,
            AuthorityTerm.normalized_external_name.in_(tag_norms),
        )
        .all()
    )

    seen_concept_ids: set[int] = set()
    for term in matched_terms:
        existing = (
            db.query(ImageConceptObservation.id)
            .filter(
                ImageConceptObservation.image_id == image_id,
                ImageConceptObservation.authority_term_id == term.id,
            )
            .first()
        )
        if existing is not None:
            skipped += 1
            continue
        if term.concept_id is not None:
            if term.concept_id in seen_concept_ids:
                skipped += 1
                continue
            dup_concept = (
                db.query(ImageConceptObservation.id)
                .filter(
                    ImageConceptObservation.image_id == image_id,
                    ImageConceptObservation.concept_id == term.concept_id,
                    ImageConceptObservation.authority_id == authority.id,
                )
                .first()
            )
            if dup_concept is not None:
                skipped += 1
                continue
            seen_concept_ids.add(term.concept_id)
        db.add(
            ImageConceptObservation(
                image_id=image_id,
                concept_id=term.concept_id,
                authority_id=authority.id,
                authority_term_id=term.id,
                source_type=_OBSERVATION_SOURCE_IMPORT,
                certainty_label=_OBSERVATION_CERTAINTY_LIKELY,
                is_present=True,
                is_curated=False,
                created_at=now,
                updated_at=now,
            )
        )
        created += 1

    if created:
        db.flush()
    return created, skipped


# -- Tier data extraction -----------------------------------------------------


def _extract_sidecar_tags(file_path: str, library_path: Path) -> list[dict]:
    """Tier 1: Read tags from sidecar JSON (data.civitai.tags)."""
    image_path = Path(file_path)
    sidecar = library_path / f"{image_path.stem}.json"
    if not sidecar.is_file():
        return []
    try:
        with open(sidecar, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return []
    if not isinstance(data, dict):
        return []
    civitai = data.get("civitai")
    if not isinstance(civitai, dict):
        return []
    tags = civitai.get("tags")
    if not isinstance(tags, list):
        return []
    return tags


def _build_tag_archive_index(archive_dir: Path) -> dict[int, list[dict]]:
    """Tier 2: Build a lookup of civitai_image_id → tag list from archived files.

    Scans for files matching civitai_image_tag_getVotableTags_*.json.
    Keys can be UUIDs (imageid_{id} or {uuid}), so we index by imageid_ prefix.
    """
    index: dict[int, list[dict]] = {}
    pattern = "civitai_image_tag_getVotableTags_*.json"
    for f in archive_dir.glob(pattern):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception:
            continue
        if not isinstance(data, list):
            continue
        # Extract civitai_image_id from filename: civitai_image_tag_getVotableTags_{key}.json
        key = f.stem.replace("civitai_image_tag_getVotableTags_", "")
        img_id = None
        if key.startswith("imageid_"):
            # Legacy format: imageid_12345
            try:
                img_id = int(key[len("imageid_"):])
            except ValueError:
                pass
        else:
            # Current format: plain integer ID (e.g. 12345)
            try:
                img_id = int(key)
            except ValueError:
                pass
        if img_id is not None:
            # Only keep the first (most recent) archive for each image
            if img_id not in index:
                index[img_id] = data
    return index


# -- Main SSE generator -------------------------------------------------------


def scan_missing_civitai(
    db: Session,
    *,
    dry_run: bool = True,
    api_limit: int = 100,
    emit: Optional[Callable[[str, dict], str]] = None,
) -> Generator[str, None, None]:
    """SSE generator: scan for images with CivitAI IDs but no tag observations.

    Yields SSE event strings.  Uses 3-tier resolution:
      Tier 1 — Sidecar JSON files
      Tier 2 — Archived API response files (disk)
      Tier 3 — Live CivitAI API (rate-limited)

    Args:
        db: SQLAlchemy session
        dry_run: If True, compute but don't commit changes
        api_limit: Max live API calls for Tier 3
        emit: SSE formatter (defaults to _emit)
    """
    if emit is None:
        emit = _emit

    from atelierai.config import IMAGE_LIBRARY_PATH, IMAGE_RESOURCES_PATH

    library_path = Path(IMAGE_LIBRARY_PATH)
    archive_dir = Path(IMAGE_RESOURCES_PATH) / "civitai_api_responses"

    # -- Query images with civitai_image_id but no CivitAI observations ----------
    authority = _get_or_create_civitai_authority(db)

    # Total images with CivitAI IDs (regardless of observation state)
    total_images = (
        db.query(sa.func.count(ImageModel.id))
        .filter(ImageModel.civitai_image_id.isnot(None))
        .scalar()
    )

    # Subquery: images that already have at least one CivitAI observation
    has_obs = (
        db.query(ImageConceptObservation.image_id)
        .filter(ImageConceptObservation.authority_id == authority.id)
        .distinct()
        .subquery()
    )

    missing_images = (
        db.query(
            ImageModel.id,
            ImageModel.file_path,
            ImageModel.civitai_image_id,
            ImageModel.civitai_uuid,
        )
        .filter(
            ImageModel.civitai_image_id.isnot(None),
            ~ImageModel.id.in_(sa.select(has_obs.c.image_id)),
        )
        .order_by(ImageModel.id)
        .all()
    )

    total_missing = len(missing_images)
    if total_missing == 0:
        yield emit("complete", {
            "total_missing": 0,
            "tier1_resolved": 0,
            "tier2_resolved": 0,
            "tier3_resolved": 0,
            "tier3_api_calls": 0,
            "terms_upserted": 0,
            "observations_created": 0,
            "observations_skipped": 0,
            "errors": 0,
            "dry_run": dry_run,
            "tags_processed": 0,
            "unique_tags": 0,
            "pre_existing_tags": 0,
            "new_tags": 0,
        })
        return

    # -- Tier 2: Build disk archive index ----------------------------------------
    tier2_index: dict[int, list[dict]] = {}
    if archive_dir.is_dir():
        tier2_index = _build_tag_archive_index(archive_dir)

    # -- Categorize images into tiers --------------------------------------------
    tier1_images = []  # (image_row, tags)
    tier2_images = []  # (image_row, tags)
    tier3_images = []  # image_row

    for row in missing_images:
        img_id, file_path, civitai_image_id, civitai_uuid = row

        # Tier 1: sidecar
        sidecar_tags = _extract_sidecar_tags(file_path, library_path)
        if sidecar_tags:
            tier1_images.append((row, sidecar_tags))
            continue

        # Tier 2: disk archive
        if civitai_image_id in tier2_index:
            tier2_images.append((row, tier2_index[civitai_image_id]))
            continue

        # Tier 3: live API
        tier3_images.append(row)

    # -- Stats accumulators ------------------------------------------------------
    stats = {
        "total_images": total_images,
        "total_missing": total_missing,
        "tier1_resolved": 0,
        "tier2_resolved": 0,
        "tier3_resolved": 0,
        "tier3_api_calls": 0,
        "terms_upserted": 0,
        "terms_created": 0,
        "terms_updated": 0,
        "observations_created": 0,
        "observations_skipped": 0,
        "errors": 0,
        # Phase-2 tag-centric metrics (Rescan Gallery parity)
        "tags_processed": 0,
        "pre_existing_tags": 0,
        "new_tags": 0,
    }
    unique_tag_names: set[str] = set()

    # Pre-load known authority term names for pre-existing detection
    known_term_names: set[str] = set()
    if authority:
        _rows = (
            db.query(AuthorityTerm.normalized_external_name)
            .filter(AuthorityTerm.authority_id == authority.id)
            .all()
        )
        known_term_names = {r[0] for r in _rows if r[0]}

    now = datetime.utcnow()
    processed = 0

    def _track_tag_metrics(tags: list) -> None:
        """Update tag-centric counters (tags_processed, unique, pre-existing, new)."""
        for tag in tags:
            if not isinstance(tag, dict):
                continue
            raw_name = str(tag.get("name") or "").strip()
            if not raw_name:
                continue
            stats["tags_processed"] += 1
            norm = _normalize_taxonomy_text(raw_name)
            if norm in unique_tag_names:
                continue
            unique_tag_names.add(norm)
            if norm in known_term_names:
                stats["pre_existing_tags"] += 1
            else:
                stats["new_tags"] += 1
                known_term_names.add(norm)

    def _progress(tier: int, extra: dict | None = None) -> dict:
        p = {
            "tier": tier,
            "processed": processed,
            "total_images": total_images,
            "total_missing": total_missing,
            "tier1_resolved": stats["tier1_resolved"],
            "tier2_resolved": stats["tier2_resolved"],
            "tier3_resolved": stats["tier3_resolved"],
            "terms_upserted": stats["terms_upserted"],
            "observations_created": stats["observations_created"],
            "observations_skipped": stats["observations_skipped"],
            # Phase-2 tag-centric (Rescan Gallery parity)
            "tags_processed": stats["tags_processed"],
            "unique_tags": len(unique_tag_names),
            "pre_existing_tags": stats["pre_existing_tags"],
            "new_tags": stats["new_tags"],
        }
        if extra:
            p.update(extra)
        return p

    # -- Tier 1: Sidecar scan ----------------------------------------------------
    yield emit("tier_start", {"tier": 1, "description": "Sidecar JSON files", "count": len(tier1_images)})

    for row, tags in tier1_images:
        img_id, file_path, civitai_image_id, civitai_uuid = row
        processed += 1
        try:
            _track_tag_metrics(tags)
            if not dry_run:
                term_stats = _upsert_authority_terms(db, authority, tags)
                stats["terms_upserted"] += term_stats["terms_upserted"]
                stats["terms_created"] += term_stats["terms_created"]
                stats["terms_updated"] += term_stats["terms_updated"]

                tag_names = [str(t.get("name", "")) for t in tags if isinstance(t, dict)]
                created, skipped = _create_observations_for_image(
                    db, img_id, authority, tag_names, now
                )
                stats["observations_created"] += created
                stats["observations_skipped"] += skipped
            else:
                stats["terms_upserted"] += len([t for t in tags if isinstance(t, dict)])

            stats["tier1_resolved"] += 1
        except Exception as exc:
            db.rollback()
            stats["errors"] += 1
            yield emit("error_event", {
                "tier": 1, "image_id": img_id, "civitai_image_id": civitai_image_id,
                "error": str(exc),
            })

        yield emit("progress", _progress(1))

    yield emit("tier_complete", {
        "tier": 1,
        "resolved": stats["tier1_resolved"],
        "terms_upserted": stats["terms_upserted"],
        "observations_created": stats["observations_created"],
    })

    # -- Tier 2: Disk archive files ----------------------------------------------
    yield emit("tier_start", {"tier": 2, "description": "Archived API response files", "count": len(tier2_images)})

    for row, tags in tier2_images:
        img_id, file_path, civitai_image_id, civitai_uuid = row
        processed += 1
        try:
            _track_tag_metrics(tags)
            if not dry_run:
                term_stats = _upsert_authority_terms(db, authority, tags)
                stats["terms_upserted"] += term_stats["terms_upserted"]
                stats["terms_created"] += term_stats["terms_created"]
                stats["terms_updated"] += term_stats["terms_updated"]

                tag_names = [str(t.get("name", "")) for t in tags if isinstance(t, dict)]
                created, skipped = _create_observations_for_image(
                    db, img_id, authority, tag_names, now
                )
                stats["observations_created"] += created
                stats["observations_skipped"] += skipped
            else:
                stats["terms_upserted"] += len([t for t in tags if isinstance(t, dict)])

            stats["tier2_resolved"] += 1
        except Exception as exc:
            db.rollback()
            stats["errors"] += 1
            yield emit("error_event", {
                "tier": 2, "image_id": img_id, "civitai_image_id": civitai_image_id,
                "error": str(exc),
            })

        yield emit("progress", _progress(2))

    yield emit("tier_complete", {
        "tier": 2,
        "resolved": stats["tier2_resolved"],
        "terms_upserted": stats["terms_upserted"],
        "observations_created": stats["observations_created"],
    })

    # -- Tier 3: Live API --------------------------------------------------------
    tier3_count = len(tier3_images)
    # api_limit == 0 means unlimited; otherwise cap at the limit
    api_calls_remaining = tier3_count if api_limit == 0 else min(api_limit, tier3_count)

    yield emit("tier_start", {
        "tier": 3,
        "description": "Live CivitAI API (rate-limited)",
        "count": tier3_count,
        "api_limit": api_limit,
        "will_call": api_calls_remaining,
    })

    if tier3_images:
        from atelierai.civitai.civitai_api import CivitaiAPI

        api = CivitaiAPI.get_instance()

        for i, row in enumerate(tier3_images):
            if api_limit > 0 and i >= api_limit:
                remaining = tier3_count - i
                # Estimate time: 180 TPM default → seconds = remaining / (TPM/60)
                tpm = getattr(api.http_client, "_TARGET_TPM", 180)
                est_minutes = math.ceil(remaining / tpm)
                yield emit("api_limit_warning", {
                    "remaining": remaining,
                    "api_limit": api_limit,
                    "estimated_time_min": est_minutes,
                })
                break

            img_id, file_path, civitai_image_id, civitai_uuid = row
            processed += 1

            try:
                tags = api.fetch_image_tag_records_cached(
                    civitai_image_id,
                    cache_only=False,
                )
                stats["tier3_api_calls"] += 1

                if tags:
                    _track_tag_metrics(tags)
                    if not dry_run:
                        term_stats = _upsert_authority_terms(db, authority, tags)
                        stats["terms_upserted"] += term_stats["terms_upserted"]
                        stats["terms_created"] += term_stats["terms_created"]
                        stats["terms_updated"] += term_stats["terms_updated"]

                        tag_names = [str(t.get("name", "")) for t in tags if isinstance(t, dict)]
                        created, skipped = _create_observations_for_image(
                            db, img_id, authority, tag_names, now
                        )
                        stats["observations_created"] += created
                        stats["observations_skipped"] += skipped
                    else:
                        stats["terms_upserted"] += len([t for t in tags if isinstance(t, dict)])

                    stats["tier3_resolved"] += 1
            except Exception as exc:
                db.rollback()
                stats["errors"] += 1
                yield emit("error_event", {
                    "tier": 3, "image_id": img_id, "civitai_image_id": civitai_image_id,
                    "error": str(exc),
                })

            yield emit("progress", _progress(3, {"api_calls": stats["tier3_api_calls"]}))

    yield emit("tier_complete", {
        "tier": 3,
        "resolved": stats["tier3_resolved"],
        "api_calls": stats["tier3_api_calls"],
        "terms_upserted": stats["terms_upserted"],
        "observations_created": stats["observations_created"],
    })

    # -- Final commit & summary --------------------------------------------------
    if not dry_run and (stats["observations_created"] > 0 or stats["terms_upserted"] > 0):
        try:
            db.commit()
        except Exception as exc:
            db.rollback()
            stats["errors"] += 1
            yield emit("error_event", {"error": f"commit failed: {exc}"})

    yield emit("complete", {
        "total_images": total_images,
        "total_missing": stats["total_missing"],
        "tier1_resolved": stats["tier1_resolved"],
        "tier2_resolved": stats["tier2_resolved"],
        "tier3_resolved": stats["tier3_resolved"],
        "tier3_api_calls": stats["tier3_api_calls"],
        "terms_upserted": stats["terms_upserted"],
        "terms_created": stats["terms_created"],
        "terms_updated": stats["terms_updated"],
        "observations_created": stats["observations_created"],
        "observations_skipped": stats["observations_skipped"],
        "errors": stats["errors"],
        "dry_run": dry_run,
        "tags_processed": stats["tags_processed"],
        "unique_tags": len(unique_tag_names),
        "pre_existing_tags": stats["pre_existing_tags"],
        "new_tags": stats["new_tags"],
    })
