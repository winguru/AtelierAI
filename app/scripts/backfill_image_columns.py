#!/usr/bin/env python3
"""Backfill promoted metadata columns and tag observations for all images.

This script is idempotent and can be run multiple times safely.  It serves
two purposes:

1. **Existing instance** — populate new ImageModel columns
   (``generation_software``, ``civitai_nsfw_level``, A1111/ComfyUI/prompt
   flags) and create ``image_concept_observations`` for every tag across all
   four sources (CivitAI, Danbooru, prompt, user).

2. **Fresh instance migration** — given only image files + sidecar JSON files,
   rebuild the tag observations and metadata columns from scratch.

Data is read **sidecar-first**: the ``.json`` sidecar file is the primary
source; DB columns (``json_metadata``, ``exif_data``) are used as fallback
when no sidecar exists.

Usage
-----
    cd app/
    python scripts/backfill_image_columns.py --dry-run     # preview
    python scripts/backfill_image_columns.py                # run for real
    python scripts/backfill_image_columns.py --batch 500    # custom batch size
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from path_setup import PROJECT_ROOT  # noqa: F401  (side effect: adds repo paths)

from sqlalchemy import func
from sqlalchemy.orm import Session

from config import IMAGE_LIBRARY_PATH
from database import SessionLocal, engine
from models import (
    AuthorityTerm,
    Concept,
    ImageConceptObservation,
    ImageModel,
    TagAuthority,
)

# Import services using paths that match how backend/ modules import each other.
# path_setup adds app/backend/ to sys.path so flat imports work.
from services.gallery_tag_service import GalleryTagService
from services.metadata_extraction import (
    compute_promoted_columns,
    extract_civitai_nsfw_level,
    extract_generation_software,
    detect_a1111_features,
    detect_comfyui,
    detect_generation_prompt,
)
from services.taxonomy_service import TaxonomyService

from sqlalchemy import text
from database import Base


def _ensure_schema() -> None:
    """Ensure new columns and tables exist before querying.

    This mirrors the startup migrations in main.py so the script can run
    independently of the web server.
    """
    # Create any missing tables (e.g. image_concept_observations)
    Base.metadata.create_all(bind=engine, checkfirst=True)

    with engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(images)")).fetchall()
        }

        new_columns = {
            "generation_software": "ALTER TABLE images ADD COLUMN generation_software VARCHAR",
            "civitai_nsfw_level": "ALTER TABLE images ADD COLUMN civitai_nsfw_level INTEGER",
            "has_a1111_metadata": "ALTER TABLE images ADD COLUMN has_a1111_metadata BOOLEAN NOT NULL DEFAULT 0",
            "a1111_hires": "ALTER TABLE images ADD COLUMN a1111_hires BOOLEAN NOT NULL DEFAULT 0",
            "a1111_regional_prompter": "ALTER TABLE images ADD COLUMN a1111_regional_prompter BOOLEAN NOT NULL DEFAULT 0",
            "a1111_adetailer": "ALTER TABLE images ADD COLUMN a1111_adetailer BOOLEAN NOT NULL DEFAULT 0",
            "has_comfyui_metadata": "ALTER TABLE images ADD COLUMN has_comfyui_metadata BOOLEAN NOT NULL DEFAULT 0",
            "has_generation_prompt": "ALTER TABLE images ADD COLUMN has_generation_prompt BOOLEAN NOT NULL DEFAULT 0",
        }

        added = []
        for col_name, ddl in new_columns.items():
            if col_name not in existing:
                connection.execute(text(ddl))
                added.append(col_name)

        if added:
            print(f"  Added missing columns: {', '.join(added)}")

        # Ensure unique constraint index on observations
        existing_indexes = {
            row[1]
            for row in connection.execute(
                text("PRAGMA index_list(image_concept_observations)")
            ).fetchall()
        }
        idx_name = "uq_obs_image_concept_authority"
        if idx_name not in existing_indexes:
            connection.execute(
                text(
                    f"CREATE UNIQUE INDEX IF NOT EXISTS {idx_name} "
                    "ON image_concept_observations (image_id, concept_id, authority_id)"
                )
            )
            print(f"  Created index: {idx_name}")


# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

@dataclass
class BackfillStats:
    images_scanned: int = 0
    columns_updated: int = 0
    observations_created: int = 0
    observations_skipped: int = 0
    concepts_created: int = 0
    authority_terms_created: int = 0
    authority_terms_reused: int = 0
    errors: int = 0
    elapsed_seconds: float = 0.0

    def summary(self) -> str:
        lines = [
            f"  Images scanned:        {self.images_scanned}",
            f"  Column values updated: {self.columns_updated}",
            f"  Observations created:  {self.observations_created}",
            f"  Observations skipped:  {self.observations_skipped}",
            f"  Concepts created:      {self.concepts_created}",
            f"  Authority terms new:   {self.authority_terms_created}",
            f"  Authority terms reused:{self.authority_terms_reused}",
            f"  Errors:                {self.errors}",
            f"  Elapsed:               {self.elapsed_seconds:.1f}s",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Payload helpers
# ---------------------------------------------------------------------------

def _load_sidecar(image_library_path: str, file_path: str) -> dict[str, Any]:
    """Read sidecar JSON for an image.  Returns {} on any failure."""
    sidecar = (Path(image_library_path) / str(file_path)).with_suffix(".json")
    if not sidecar.exists():
        return {}
    try:
        with open(sidecar, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _merge_payload(image: ImageModel, sidecar: dict[str, Any]) -> dict[str, Any]:
    """Build a merged payload, sidecar-first then DB fallback."""
    db_json = image.json_metadata if isinstance(image.json_metadata, dict) else {}
    db_exif = image.exif_data if isinstance(image.exif_data, dict) else {}

    # Sidecar is primary; DB columns fill gaps.
    payload: dict[str, Any] = {**db_json, **sidecar}

    # Ensure exif_data is present (sidecar may have its own).
    sidecar_exif = sidecar.get("exif_data")
    if isinstance(sidecar_exif, dict):
        payload["exif_data"] = {**db_exif, **sidecar_exif}
    elif db_exif:
        payload.setdefault("exif_data", db_exif)

    # Ensure civitai_data is present
    if "civitai_data" not in payload and "civitai" in payload:
        payload["civitai_data"] = payload["civitai"]

    # Ensure user_tags from DB
    if image.user_tags and not payload.get("user_tags"):
        payload["user_tags"] = image.user_tags

    return payload


# ---------------------------------------------------------------------------
# Tag observation helpers
# ---------------------------------------------------------------------------

class _TermCache:
    """In-memory cache for authority IDs and authority terms."""

    def __init__(self) -> None:
        self._authorities: dict[str, int] = {}
        self._terms: dict[tuple[int, str], int] = {}  # (authority_id, normalized_name) -> term_id
        self._concepts: dict[str, int] = {}  # normalized_name -> concept_id

    def get_authority_id(self, db: Session, tax: TaxonomyService, name: str) -> int:
        key = name.lower().strip()
        if key not in self._authorities:
            authority = tax.get_or_create_authority(db, key)
            self._authorities[key] = authority.id
        return self._authorities[key]

    def get_or_create_term(
        self,
        db: Session,
        tax: TaxonomyService,
        *,
        authority_id: int,
        tag_name: str,
        stats: BackfillStats,
    ) -> tuple[int, int]:
        """Return (authority_term_id, concept_id) for a tag name + authority.

        Creates AuthorityTerm and Concept as needed.
        """
        normalized = tax.normalize_text(tag_name)
        if not normalized:
            raise ValueError(f"Empty tag name after normalization: {tag_name!r}")

        cache_key = (authority_id, normalized)
        if cache_key in self._terms:
            term_id = self._terms[cache_key]
            # Look up concept_id from our concept cache or DB
            concept_id = self._resolve_concept_for_term(db, tax, term_id, normalized, stats)
            stats.authority_terms_reused += 1
            return term_id, concept_id

        # Check DB for existing term
        existing = (
            db.query(AuthorityTerm)
            .filter(
                AuthorityTerm.authority_id == authority_id,
                AuthorityTerm.normalized_external_name == normalized,
            )
            .first()
        )

        if existing is not None:
            self._terms[cache_key] = existing.id
            concept_id = self._resolve_concept_for_term(db, tax, existing.id, normalized, stats, existing)
            stats.authority_terms_reused += 1
            return existing.id, concept_id

        # Create new term — need a concept first
        concept_id = self._get_or_create_concept(db, tax, normalized, stats)

        now = datetime.now(timezone.utc)
        term = AuthorityTerm(
            authority_id=authority_id,
            external_name=tag_name.strip(),
            normalized_external_name=normalized,
            concept_id=concept_id,
            created_at=now,
            updated_at=now,
            last_seen_at=now,
        )
        db.add(term)
        db.flush()
        self._terms[cache_key] = term.id
        stats.authority_terms_created += 1
        return term.id, concept_id

    def _resolve_concept_for_term(
        self,
        db: Session,
        tax: TaxonomyService,
        term_id: int,
        normalized_name: str,
        stats: BackfillStats,
        term_obj: Optional[AuthorityTerm] = None,
    ) -> int:
        """Return concept_id for a term, creating if needed."""
        if term_obj is None:
            term_obj = db.query(AuthorityTerm).get(term_id)
        if term_obj is not None and term_obj.concept_id is not None:
            return term_obj.concept_id

        # Term has no concept — create one and link it
        concept_id = self._get_or_create_concept(db, tax, normalized_name, stats)
        if term_obj is not None:
            term_obj.concept_id = concept_id
            db.flush()
        return concept_id

    def _get_or_create_concept(
        self, db: Session, tax: TaxonomyService, normalized_name: str, stats: BackfillStats
    ) -> int:
        if normalized_name in self._concepts:
            return self._concepts[normalized_name]

        concept = tax.get_or_create_concept(db, normalized_name)
        self._concepts[normalized_name] = concept.id
        # Check if this was newly created (no existing rows before flush)
        if concept.id is not None:
            # We count only truly new ones; get_or_create_concept might return existing
            stats.concepts_created += 1
        return concept.id


def _create_observations(
    db: Session,
    image_id: int,
    tags_by_source: dict[str, set[str]],
    term_cache: _TermCache,
    tax: TaxonomyService,
    stats: BackfillStats,
    dry_run: bool,
) -> None:
    """Create image_concept_observations for all tags across sources."""
    now = datetime.now(timezone.utc)
    _seen_obs: set[tuple[int, int, int]] = set()

    for source, tag_names in tags_by_source.items():
        if not tag_names:
            continue

        authority_id = term_cache.get_authority_id(db, tax, source)

        for tag_name in tag_names:
            try:
                term_id, concept_id = term_cache.get_or_create_term(
                    db, tax,
                    authority_id=authority_id,
                    tag_name=tag_name,
                    stats=stats,
                )
            except ValueError:
                continue

            # Track (image_id, concept_id, authority_id) tuples we've
            # already queued in this session to avoid flush-time constraint
            # violations when multiple tag strings map to the same concept.
            obs_key = (image_id, concept_id, authority_id)
            if obs_key in _seen_obs:
                stats.observations_skipped += 1
                continue

            # Check DB for pre-existing observation (from previous run)
            existing = (
                db.query(ImageConceptObservation.id)
                .filter(
                    ImageConceptObservation.image_id == image_id,
                    ImageConceptObservation.concept_id == concept_id,
                    ImageConceptObservation.authority_id == authority_id,
                )
                .first()
            )
            if existing is not None:
                stats.observations_skipped += 1
                _seen_obs.add(obs_key)
                continue

            if not dry_run:
                db.add(ImageConceptObservation(
                    image_id=image_id,
                    concept_id=concept_id,
                    authority_id=authority_id,
                    authority_term_id=term_id,
                    source_type="import",
                    source_label=f"backfill from {source}",
                    certainty_label="likely",
                    dimension="general",
                    polarity="present",
                    is_curated=False,
                    created_at=now,
                    updated_at=now,
                ))
            _seen_obs.add(obs_key)
            stats.observations_created += 1


# ---------------------------------------------------------------------------
# Main backfill logic
# ---------------------------------------------------------------------------

def backfill_image(
    db: Session,
    image: ImageModel,
    *,
    image_library_path: str,
    gallery_tag_svc: GalleryTagService,
    tax: TaxonomyService,
    term_cache: _TermCache,
    stats: BackfillStats,
    dry_run: bool,
) -> None:
    """Backfill one image: update columns + create tag observations."""
    stats.images_scanned += 1

    sidecar = _load_sidecar(image_library_path, image.file_path)
    payload = _merge_payload(image, sidecar)
    exif = payload.get("exif_data") or {}
    if not isinstance(exif, dict):
        exif = {}

    # ---- Phase A: Promoted columns ----
    promoted = compute_promoted_columns(payload, exif=exif)
    updates: dict[str, Any] = {}

    for col_name, new_value in promoted.items():
        current = getattr(image, col_name, None)
        # Only update if the new value is truthy and the current is empty/default
        if isinstance(new_value, bool):
            if new_value and not current:
                updates[col_name] = new_value
        elif new_value is not None and not current:
            updates[col_name] = new_value

    if updates and not dry_run:
        db.query(ImageModel).filter(ImageModel.id == image.id).update(
            updates, synchronize_session="fetch"
        )
    stats.columns_updated += len(updates)

    # ---- Phase B: Tag observations ----
    tags_by_source = gallery_tag_svc.extract_image_scope_tag_names(
        payload, normalize_taxonomy_text=tax.normalize_text
    )
    _create_observations(
        db, image.id, tags_by_source, term_cache, tax, stats, dry_run
    )


def run_backfill(*, batch_size: int = 200, dry_run: bool = False) -> BackfillStats:
    """Run the full backfill across all active images."""
    stats = BackfillStats()
    tax = TaxonomyService()
    gallery_tag_svc = GalleryTagService()
    term_cache = _TermCache()
    image_library_path = IMAGE_LIBRARY_PATH

    t0 = time.time()
    mode_label = "DRY RUN" if dry_run else "LIVE"
    print(f"\n{'=' * 60}")
    print(f"  Backfill image columns + tag observations ({mode_label})")
    print(f"  Image library: {image_library_path}")
    print(f"  Batch size:    {batch_size}")
    print(f"{'=' * 60}\n")

    with SessionLocal() as db:
        # Ensure columns/tables exist before querying
        _ensure_schema()

        total = (
            db.query(func.count(ImageModel.id))
            .filter(ImageModel.image_status == "active")
            .scalar()
        ) or 0
        print(f"Total active images: {total}")

        offset = 0
        while offset < total:
            images = (
                db.query(ImageModel)
                .filter(ImageModel.image_status == "active")
                .order_by(ImageModel.id)
                .offset(offset)
                .limit(batch_size)
                .all()
            )
            if not images:
                break

            for image in images:
                try:
                    backfill_image(
                        db, image,
                        image_library_path=image_library_path,
                        gallery_tag_svc=gallery_tag_svc,
                        tax=tax,
                        term_cache=term_cache,
                        stats=stats,
                        dry_run=dry_run,
                    )
                except Exception as exc:
                    stats.errors += 1
                    print(f"  ERROR image {image.id} ({image.file_name}): {exc}")

            if not dry_run:
                db.commit()

            offset += batch_size
            pct = min(100, int(offset / total * 100)) if total else 100
            print(
                f"  Progress: {min(offset, total)}/{total} ({pct}%) "
                f"| obs: {stats.observations_created} | cols: {stats.columns_updated}"
            )

        if not dry_run:
            db.commit()

    stats.elapsed_seconds = time.time() - t0
    print(f"\n{'=' * 60}")
    print(f"  Backfill complete ({mode_label})")
    print(stats.summary())
    print(f"{'=' * 60}\n")
    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill promoted metadata columns and tag observations."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing to the database.",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=200,
        help="Number of images per batch commit (default: 200).",
    )
    args = parser.parse_args()

    stats = run_backfill(batch_size=args.batch, dry_run=args.dry_run)
    if stats.errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
