# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/backend-startup.md
# ──────────────────────────────────────────────────────────────────────────────
"""Database schema migrations and one-time data backfill functions.

Extracted from main.py during Phase A refactoring.  Every function here is
idempotent — safe to call on every application startup.  The lifespan handler
in main.py determines the call order based on schema version and column
presence checks that these functions perform internally.

Import conventions
------------------
* ``engine`` / ``SessionLocal`` come from ``database`` (same package).
* ORM models come from ``models``.
* ``IMAGE_LIBRARY_PATH`` is resolved at module level from ``config`` so that
  sidecar-backfill helpers don't need it passed in.
"""

from __future__ import annotations

import json as _json
from datetime import datetime
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import text

from database import Base, SessionLocal, engine
from models import (
    AuthorityTerm,
    CivitaiBaseModel,
    CivitaiModelVersion,
    CivitaiUser,
    ImageConceptObservation,
    ImageModel,
    ObservationCertainty,
    ObservationSource,
    TagAuthority,
)

# Resolve IMAGE_LIBRARY_PATH at import time so backfill helpers can use it.
import atelierai.config as _cfg

IMAGE_LIBRARY_PATH: str = str(getattr(_cfg, "IMAGE_LIBRARY_PATH", "image_library"))


# ---------------------------------------------------------------------------
# Column / index addition helpers  (pure DDL, no ORM)
# ---------------------------------------------------------------------------


def _ensure_image_lifecycle_columns() -> None:
    """Backfill lifecycle columns for existing sqlite databases."""
    with engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(images)")).fetchall()
        }

        if "image_status" not in existing:
            connection.execute(
                text(
                    "ALTER TABLE images ADD COLUMN image_status VARCHAR DEFAULT 'active' NOT NULL"
                )
            )
        if "status_reason" not in existing:
            connection.execute(
                text("ALTER TABLE images ADD COLUMN status_reason VARCHAR")
            )
        if "replaced_by_image_id" not in existing:
            connection.execute(
                text("ALTER TABLE images ADD COLUMN replaced_by_image_id INTEGER")
            )

        connection.execute(
            text(
                "UPDATE images SET image_status = 'active' WHERE image_status IS NULL OR image_status = ''"
            )
        )


def _ensure_user_nsfw_columns() -> None:
    """Backfill user NSFW override columns for existing sqlite databases."""
    with engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(images)")).fetchall()
        }
        if "user_nsfw_rating" not in existing:
            connection.execute(
                text("ALTER TABLE images ADD COLUMN user_nsfw_rating VARCHAR")
            )
        if "user_nsfw_safety_class" not in existing:
            connection.execute(
                text("ALTER TABLE images ADD COLUMN user_nsfw_safety_class VARCHAR")
            )


def _ensure_collection_sync_columns() -> None:
    """Backfill CivitAI collection sync metadata columns for existing sqlite databases."""
    with engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.execute(
                text("PRAGMA table_info(collections)")
            ).fetchall()
        }

        if "civitai_head_fingerprint" not in existing:
            connection.execute(
                text("ALTER TABLE collections ADD COLUMN civitai_head_fingerprint TEXT")
            )
        if "civitai_head_item_count" not in existing:
            connection.execute(
                text(
                    "ALTER TABLE collections ADD COLUMN civitai_head_item_count INTEGER"
                )
            )
        if "civitai_head_has_more" not in existing:
            connection.execute(
                text("ALTER TABLE collections ADD COLUMN civitai_head_has_more BOOLEAN")
            )
        if "civitai_last_full_item_count" not in existing:
            connection.execute(
                text(
                    "ALTER TABLE collections ADD COLUMN civitai_last_full_item_count INTEGER"
                )
            )
        if "civitai_last_synced_at" not in existing:
            connection.execute(
                text(
                    "ALTER TABLE collections ADD COLUMN civitai_last_synced_at DATETIME"
                )
            )
        if "civitai_last_full_scan_at" not in existing:
            connection.execute(
                text(
                    "ALTER TABLE collections ADD COLUMN civitai_last_full_scan_at DATETIME"
                )
            )


def _ensure_civitai_uuid_column() -> None:
    """Backfill CivitAI UUID column for existing sqlite databases."""
    with engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(images)")).fetchall()
        }

        if "civitai_uuid" not in existing:
            connection.execute(
                text("ALTER TABLE images ADD COLUMN civitai_uuid VARCHAR")
            )


def _ensure_civitai_hash_column() -> None:
    """Backfill CivitAI perceptual hash column for existing sqlite databases."""
    with engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(images)")).fetchall()
        }

        if "civitai_hash" not in existing:
            connection.execute(
                text("ALTER TABLE images ADD COLUMN civitai_hash VARCHAR")
            )


def _ensure_user_tags_column() -> None:
    """Backfill user_tags JSON column for existing sqlite databases."""
    with engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(images)")).fetchall()
        }

        if "user_tags" not in existing:
            connection.execute(text("ALTER TABLE images ADD COLUMN user_tags JSON"))


def _ensure_user_negative_tags_column() -> None:
    """Add user_negative_tags JSON column for existing sqlite databases."""
    with engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(images)")).fetchall()
        }

        if "user_negative_tags" not in existing:
            connection.execute(
                text("ALTER TABLE images ADD COLUMN user_negative_tags JSON")
            )


def _ensure_observation_authority_term_unique_index() -> None:
    """Add a unique index on (image_id, authority_term_id) for observations.

    The existing uq_obs_image_concept_authority constraint on
    (image_id, concept_id, authority_id) does not prevent duplicates when
    concept_id is NULL (SQLite treats NULLs as distinct in unique constraints).
    Since user-tag observations will have NULL concept_id, we need a unique
    index on authority_term_id instead.
    """
    with engine.begin() as connection:
        indexes = {
            row[1]
            for row in connection.execute(
                text("PRAGMA index_list(image_concept_observations)")
            ).fetchall()
        }
        if "uq_obs_image_authority_term" not in indexes:
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_obs_image_authority_term "
                    "ON image_concept_observations (image_id, authority_term_id) "
                    "WHERE authority_term_id IS NOT NULL"
                )
            )


def _ensure_observation_schema_current() -> None:
    """Recreate image_concept_observations if the schema pre-dates the current ORM.

    Legacy schema markers that trigger recreation:
      - concept_id INTEGER NOT NULL  (should be nullable)
      - dimension / polarity VARCHAR NOT NULL  (obsolete columns)
      - source_type / certainty_label stored as VARCHAR  (should be INTEGER)

    Existing rows are migrated by copying columns shared by both schemas.
    Rows where concept_id IS NULL in the legacy table are discarded (would be
    malformed under the old NOT NULL constraint anyway).
    """
    with engine.connect() as connection:
        col_info = {
            row[1]: row
            for row in connection.execute(
                text("PRAGMA table_info(image_concept_observations)")
            ).fetchall()
        }

    concept_id_col = col_info.get("concept_id")
    if concept_id_col is None or concept_id_col[3] == 0:
        return

    print(
        "  [migration] image_concept_observations has legacy NOT NULL concept_id "
        "— recreating table with current schema..."
    )

    with engine.begin() as connection:
        row_count = connection.execute(
            text("SELECT COUNT(*) FROM image_concept_observations")
        ).fetchone()[0]

        for (idx_name,) in connection.execute(
            text(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND tbl_name='image_concept_observations' "
                "  AND name NOT LIKE 'sqlite_autoindex%'"
            )
        ).fetchall():
            connection.execute(text(f'DROP INDEX IF EXISTS "{idx_name}"'))

        connection.execute(
            text(
                "ALTER TABLE image_concept_observations "
                "RENAME TO _ico_legacy"
            )
        )

    Base.metadata.tables["image_concept_observations"].create(engine)

    if row_count > 0:
        new_col_names = {
            row[1]
            for row in engine.connect().execute(
                text("PRAGMA table_info(image_concept_observations)")
            ).fetchall()
        }
        shared_cols = ", ".join(
            c
            for c in new_col_names
            if c in col_info and c != "id"
        )
        with engine.begin() as connection:
            connection.execute(
                text(
                    f"INSERT INTO image_concept_observations ({shared_cols}) "
                    f"SELECT {shared_cols} FROM _ico_legacy "
                    f"WHERE concept_id IS NOT NULL"
                )
            )
        print(f"  [migration] Migrated {row_count} rows from legacy schema.")

    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS _ico_legacy"))

    print("  [migration] image_concept_observations schema is now current.")


def _ensure_observation_presence_columns() -> None:
    """Add observation presence/curation columns for older sqlite databases."""
    with engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.execute(
                text("PRAGMA table_info(image_concept_observations)")
            ).fetchall()
        }

        if "is_present" not in existing:
            connection.execute(
                text(
                    "ALTER TABLE image_concept_observations "
                    "ADD COLUMN is_present BOOLEAN DEFAULT 1 NOT NULL"
                )
            )
        if "is_curated" not in existing:
            connection.execute(
                text(
                    "ALTER TABLE image_concept_observations "
                    "ADD COLUMN is_curated BOOLEAN DEFAULT 0 NOT NULL"
                )
            )

        connection.execute(
            text(
                "UPDATE image_concept_observations "
                "SET is_present = 1 "
                "WHERE is_present IS NULL"
            )
        )
        connection.execute(
            text(
                "UPDATE image_concept_observations "
                "SET is_curated = 0 "
                "WHERE is_curated IS NULL"
            )
        )


def _ensure_is_corrupt_column() -> None:
    """Add is_corrupt column to flag images that failed PIL verify()."""
    with engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(images)")).fetchall()
        }
        if "is_corrupt" in existing:
            return

        connection.execute(
            text("ALTER TABLE images ADD COLUMN is_corrupt BOOLEAN DEFAULT 0 NOT NULL")
        )


def _ensure_expected_file_size_column() -> None:
    """Add expected_file_size column for CivitAI-declared size (size-mismatch detection)."""
    with engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(images)")).fetchall()
        }
        if "expected_file_size" in existing:
            return

        connection.execute(
            text("ALTER TABLE images ADD COLUMN expected_file_size INTEGER")
        )


def _ensure_file_hash_nonunique() -> None:
    """Drop the UNIQUE index on images.file_hash so CivitAI duplicate assets can coexist.

    SQLite does not support ALTER TABLE ... DROP CONSTRAINT, so we must
    identify and drop the unique index by name.  SQLAlchemy creates this as
    ``ix_images_file_hash`` when ``unique=True`` is declared on the Column.
    """
    with engine.begin() as connection:
        indexes = {
            row[1]
            for row in connection.execute(text("PRAGMA index_list(images)")).fetchall()
        }
        # SQLAlchemy auto-generates the unique index name
        for idx_name in ("ix_images_file_hash",):
            if idx_name in indexes:
                # Verify it is actually unique before dropping
                # Note: PRAGMA doesn't support parameterized queries
                is_unique = any(
                    row
                    for row in connection.execute(
                        text("PRAGMA index_list(images)")
                    ).fetchall()
                    if row[1] == idx_name and row[2] == 1
                )
                if is_unique:
                    print(f"Dropping UNIQUE index {idx_name} on images.file_hash")
                    connection.execute(text(f"DROP INDEX {idx_name}"))
                    # Recreate as non-unique for query performance
                    connection.execute(
                        text("CREATE INDEX ix_images_file_hash ON images (file_hash)")
                    )


def _ensure_image_variant_columns() -> None:
    """Backfill image variant grouping columns for existing sqlite databases."""
    with engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(images)")).fetchall()
        }

        if "variant_group_key" not in existing:
            connection.execute(
                text("ALTER TABLE images ADD COLUMN variant_group_key VARCHAR")
            )
        if "variant_sort_index" not in existing:
            connection.execute(
                text("ALTER TABLE images ADD COLUMN variant_sort_index INTEGER")
            )
        if "variant_role" not in existing:
            connection.execute(
                text("ALTER TABLE images ADD COLUMN variant_role VARCHAR")
            )

        # Default variant_group_key to file_hash so that visually identical
        # assets (same SHA256, different CivitAI IDs) are grouped as variants.
        connection.execute(
            text(
                "UPDATE images "
                "SET variant_group_key = file_hash "
                "WHERE (variant_group_key IS NULL OR variant_group_key = '') "
                "AND file_hash IS NOT NULL AND file_hash != ''"
            )
        )
        # Fix any rows that were incorrectly backfilled with file_path —
        # restore file_hash grouping so duplicate assets share a group.
        connection.execute(
            text(
                "UPDATE images "
                "SET variant_group_key = file_hash "
                "WHERE variant_group_key = file_path "
                "AND file_hash IS NOT NULL AND file_hash != ''"
            )
        )


def _ensure_promoted_metadata_columns() -> None:
    """Add promoted metadata columns for existing sqlite databases.

    These columns move filterable data out of json_metadata / sidecar JSON
    into proper indexed columns.  Values are backfilled by the standalone
    ``backfill_image_columns.py`` migration script.
    """
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

        for col_name, ddl in new_columns.items():
            if col_name not in existing:
                connection.execute(text(ddl))


def _ensure_original_file_name_column() -> None:
    """Add original_file_name column and backfill from json_metadata or file_name."""
    with engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(images)")).fetchall()
        }
        if "original_file_name" in existing:
            return

        connection.execute(
            text("ALTER TABLE images ADD COLUMN original_file_name VARCHAR")
        )
        # Backfill: try json_metadata.original_filename first, fall back to file_name
        connection.execute(
            text(
                "UPDATE images "
                "SET original_file_name = COALESCE("
                "  NULLIF(TRIM(json_extract(json_metadata, '$.original_filename')), ''), "
                "  file_name"
                ") "
                "WHERE original_file_name IS NULL"
            )
        )


def _ensure_blurhash_column() -> None:
    """Add blurhash column and backfill from civitai_hash where available."""
    with engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(images)")).fetchall()
        }
        if "blurhash" in existing:
            return

        connection.execute(text("ALTER TABLE images ADD COLUMN blurhash VARCHAR"))
        # Instant backfill: CivitAI's `hash` field IS a BlurHash string.
        connection.execute(
            text(
                "UPDATE images SET blurhash = civitai_hash "
                "WHERE civitai_hash IS NOT NULL AND blurhash IS NULL"
            )
        )


def _ensure_civitai_image_id_column() -> None:
    """Add civitai_image_id column and index, then backfill from source_url."""
    with engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(images)")).fetchall()
        }
        if "civitai_image_id" in existing:
            return

        connection.execute(
            text("ALTER TABLE images ADD COLUMN civitai_image_id INTEGER")
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_images_civitai_image_id "
                "ON images(civitai_image_id)"
            )
        )
        # Backfill: extract the numeric image ID from source_url paths like
        # "/images/12345" for existing CivitAI rows.
        connection.execute(
            text(
                "UPDATE images "
                "SET civitai_image_id = CAST("
                "  SUBSTR("
                "    source_url,"
                "    INSTR(source_url, '/images/') + 8"
                "  )"
                "  AS INTEGER"
                ") "
                "WHERE source_site = 'civitai' "
                "AND source_url LIKE '%/images/%' "
                "AND civitai_image_id IS NULL"
            )
        )


def _ensure_civitai_post_id_column() -> None:
    """Add civitai_post_id column and index, then backfill from json_metadata."""
    with engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(images)")).fetchall()
        }
        if "civitai_post_id" in existing:
            return

        connection.execute(
            text("ALTER TABLE images ADD COLUMN civitai_post_id INTEGER")
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_images_civitai_post_id "
                "ON images(civitai_post_id)"
            )
        )
        # Backfill from json_metadata.civitai.post_id where available.
        # This uses json_extract (SQLite JSON1 extension) to pull the value.
        connection.execute(
            text(
                "UPDATE images "
                "SET civitai_post_id = CAST("
                "  json_extract(json_extract(json_metadata, '$.civitai'), '$.post_id')"
                "  AS INTEGER"
                ") "
                "WHERE source_site = 'civitai' "
                "AND json_metadata IS NOT NULL "
                "AND civitai_post_id IS NULL "
                "AND json_extract(json_extract(json_metadata, '$.civitai'), '$.post_id') IS NOT NULL"
            )
        )


def _ensure_civitai_deleted_at_column() -> None:
    """Add civitai_deleted_at column to track images removed from CivitAI."""
    with engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(images)")).fetchall()
        }
        if "civitai_deleted_at" in existing:
            return

        connection.execute(
            text("ALTER TABLE images ADD COLUMN civitai_deleted_at DATETIME")
        )


def _ensure_civitai_post_title_index_columns() -> None:
    """Add civitai_post_title and civitai_post_index columns to images."""
    with engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(images)")).fetchall()
        }
        if "civitai_post_title" not in existing:
            connection.execute(
                text("ALTER TABLE images ADD COLUMN civitai_post_title VARCHAR")
            )
        if "civitai_post_index" not in existing:
            connection.execute(
                text("ALTER TABLE images ADD COLUMN civitai_post_index INTEGER")
            )


def _ensure_civitai_cdn_url_column() -> None:
    """Add civitai_cdn_url column to images.

    Stores the actual CivitAI CDN URL used to download the image, which may
    differ from ``source_url`` (the CivitAI page URL) when fallback width-based
    CDN routes are used instead of the broken ``original=true`` route.
    """
    with engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(images)")).fetchall()
        }
        if "civitai_cdn_url" not in existing:
            connection.execute(
                text("ALTER TABLE images ADD COLUMN civitai_cdn_url TEXT")
            )


def _ensure_civitai_user_columns() -> None:
    """Add civitai_user_id, civitai_user_deleted, civitai_user_original_name to artists.

    Backfills civitai_user_id from json_metadata.author_id where available.
    """
    with engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(artists)")).fetchall()
        }

        if "civitai_user_id" not in existing:
            connection.execute(
                text("ALTER TABLE artists ADD COLUMN civitai_user_id INTEGER")
            )
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS ix_artists_civitai_user_id "
                    "ON artists(civitai_user_id)"
                )
            )

        if "civitai_user_deleted" not in existing:
            connection.execute(
                text(
                    "ALTER TABLE artists ADD COLUMN civitai_user_deleted BOOLEAN "
                    "DEFAULT 0"
                )
            )

        if "civitai_user_original_name" not in existing:
            connection.execute(
                text(
                    "ALTER TABLE artists ADD COLUMN civitai_user_original_name VARCHAR"
                )
            )

        # Backfill civitai_user_id from image json_metadata where artist_id is set
        if "civitai_user_id" not in existing:
            rows = connection.execute(
                text(
                    "SELECT a.id, a.name, i.json_metadata "
                    "FROM artists a "
                    "JOIN images i ON i.artist_id = a.id "
                    "WHERE a.civitai_user_id IS NULL "
                    "AND i.json_metadata IS NOT NULL "
                    "GROUP BY a.id"
                )
            ).fetchall()

            for artist_id, artist_name, metadata_str in rows:
                try:
                    metadata = _json.loads(metadata_str) if metadata_str else {}
                    author_id = metadata.get("author_id")
                    if author_id is not None:
                        connection.execute(
                            text(
                                "UPDATE artists SET civitai_user_id = :uid "
                                "WHERE id = :aid AND civitai_user_id IS NULL"
                            ),
                            {"uid": int(author_id), "aid": artist_id},
                        )
                except (ValueError, TypeError, _json.JSONDecodeError):
                    continue


def _ensure_observation_unique_constraint() -> None:
    """Add unique constraint to image_concept_observations if missing.

    SQLite does not support ALTER TABLE ADD CONSTRAINT, so we check for the
    index that SQLAlchemy generates from the UniqueConstraint declaration and
    create it manually when absent.
    """
    index_name = "uq_obs_image_concept_authority"
    with engine.begin() as connection:
        existing_indexes = {
            row[1]
            for row in connection.execute(
                text("PRAGMA index_list(image_concept_observations)")
            ).fetchall()
        }
        if index_name not in existing_indexes:
            connection.execute(
                text(
                    f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} "
                    "ON image_concept_observations (image_id, concept_id, authority_id)"
                )
            )


# ---------------------------------------------------------------------------
# ORM-based data backfill helpers
# ---------------------------------------------------------------------------


def _backfill_user_tags_to_observations() -> None:
    """One-time migration: convert user_tags and user_negative_tags JSON into
    authority_terms + image_concept_observations rows under the 'user' authority.

    This makes user tags queryable via the same observation path as every other
    tag authority, enabling proper joins and filtering without json_each().
    """
    with SessionLocal() as db:
        # Resolve the 'user' authority (id=3).
        user_authority = (
            db.query(TagAuthority).filter(TagAuthority.name == "user").first()
        )
        if user_authority is None:
            print("   Skipping user_tags observation backfill: no 'user' authority.")
            return
        user_authority_id = user_authority.id

        # Collect all images with user_tags or user_negative_tags JSON.
        images = (
            db.query(
                ImageModel.id,
                ImageModel.user_tags,
                ImageModel.user_negative_tags,
            )
            .filter(
                sa.or_(
                    ImageModel.user_tags.isnot(None),
                    ImageModel.user_negative_tags.isnot(None),
                )
            )
            .all()
        )

        if not images:
            print("   No images with user tags to backfill.")
            return

        # Build a cache of existing user authority_terms by normalized name.
        existing_terms: dict[str, int] = {}
        for term in (
            db.query(
                AuthorityTerm.id,
                AuthorityTerm.normalized_external_name,
            )
            .filter(AuthorityTerm.authority_id == user_authority_id)
            .all()
        ):
            existing_terms[term.normalized_external_name] = term.id

        # Build a set of existing (image_id, authority_term_id) to skip dupes.
        existing_obs: set[tuple[int, int]] = set()
        for row in db.query(
            ImageConceptObservation.image_id,
            ImageConceptObservation.authority_term_id,
        ).filter(
            ImageConceptObservation.authority_id == user_authority_id,
            ImageConceptObservation.authority_term_id.isnot(None),
        ).all():
            existing_obs.add((row.image_id, row.authority_term_id))

        now = datetime.now()
        terms_created = 0
        obs_created = 0
        skipped = 0

        for img_id, user_tags, user_negative_tags in images:
            tag_pairs: list[tuple[list[str] | None, bool]] = [
                (user_tags, True),
                (user_negative_tags, False),
            ]

            for tags_json, is_present in tag_pairs:
                if not isinstance(tags_json, list):
                    continue
                for raw_tag in tags_json:
                    tag_name = str(raw_tag or "").strip()
                    if not tag_name:
                        continue
                    normalized = tag_name.lower()

                    # Find or create authority_term.
                    term_id = existing_terms.get(normalized)
                    if term_id is None:
                        term = AuthorityTerm(
                            authority_id=user_authority_id,
                            external_tag_id=None,
                            external_name=tag_name,
                            normalized_external_name=normalized,
                            created_at=now,
                            updated_at=now,
                        )
                        db.add(term)
                        db.flush()
                        term_id = term.id
                        existing_terms[normalized] = term_id
                        terms_created += 1

                    # Check for existing observation.
                    if (img_id, term_id) in existing_obs:
                        skipped += 1
                        continue

                    obs = ImageConceptObservation(
                        image_id=img_id,
                        concept_id=None,
                        authority_id=user_authority_id,
                        authority_term_id=term_id,
                        source_type=ObservationSource.IMPORT,
                        certainty_label=ObservationCertainty.LIKELY,
                        is_present=is_present,
                        is_curated=False,
                        confidence=None,
                        created_at=now,
                        updated_at=now,
                    )
                    db.add(obs)
                    existing_obs.add((img_id, term_id))
                    obs_created += 1

        if obs_created or terms_created:
            db.commit()
            print(
                f"   Backfilled user_tags → observations: "
                f"{terms_created} terms, {obs_created} observations "
                f"({skipped} already existed)."
            )
        else:
            print("   All user tags already have observations.")


def _backfill_user_tags_from_sidecars() -> None:
    """One-time migration: copy user_tags from sidecar JSON files into the DB column."""
    with SessionLocal() as db:
        images_needing_backfill = (
            db.query(ImageModel.id, ImageModel.file_path)
            .filter(ImageModel.user_tags.is_(None))
            .filter(ImageModel.file_path.isnot(None))
            .limit(5000)
            .all()
        )
        updated = 0
        for img_id, file_path in images_needing_backfill:
            try:
                sidecar_path = (Path(IMAGE_LIBRARY_PATH) / str(file_path)).with_suffix(
                    ".json"
                )
                if not sidecar_path.exists():
                    continue
                with open(sidecar_path, "r", encoding="utf-8") as f:
                    sidecar = _json.load(f)
                if not isinstance(sidecar, dict):
                    continue
                sc_user_tags = sidecar.get("user_tags")
                if isinstance(sc_user_tags, list) and sc_user_tags:
                    db.query(ImageModel).filter(ImageModel.id == img_id).update(
                        {ImageModel.user_tags: _json.dumps(sc_user_tags)},
                        synchronize_session="fetch",
                    )
                    updated += 1
                # Also backfill user_negative_tags from sidecar if missing in DB.
                sc_neg_tags = sidecar.get("user_negative_tags")
                if isinstance(sc_neg_tags, list) and sc_neg_tags:
                    db.query(ImageModel).filter(ImageModel.id == img_id).update(
                        {ImageModel.user_negative_tags: _json.dumps(sc_neg_tags)},
                        synchronize_session="fetch",
                    )
                    updated += 1
            except Exception:
                continue
        if updated:
            db.commit()
            print(f"   Backfilled user_tags from sidecars for {updated} image(s).")


def _ensure_civitai_creator_id_column() -> None:
    """Add creator_id FK column to civitai_models if missing.

    Points to the new civitai_users table. Backfills from existing
    civitai_user_id column data.
    """
    with engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(civitai_models)")).fetchall()
        }
        if "creator_id" not in existing:
            connection.execute(
                text("ALTER TABLE civitai_models ADD COLUMN creator_id INTEGER")
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_civitai_models_creator_id "
                    "ON civitai_models(creator_id)"
                )
            )
            # Backfill from existing civitai_user_id
            connection.execute(
                text(
                    "UPDATE civitai_models SET creator_id = civitai_user_id "
                    "WHERE creator_id IS NULL AND civitai_user_id IS NOT NULL"
                )
            )
            print("Backfilled civitai_models.creator_id from civitai_user_id")


def _ensure_base_model_id_column() -> None:
    """Add base_model_id FK column to civitai_model_versions if missing.

    Points to the new civitai_base_models table. Left NULL for now;
    population happens via backfill script after base models are seeded.
    """
    with engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(civitai_model_versions)")).fetchall()
        }
        if "base_model_id" not in existing:
            connection.execute(
                text("ALTER TABLE civitai_model_versions ADD COLUMN base_model_id INTEGER")
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_civitai_model_versions_base_model_id "
                    "ON civitai_model_versions(base_model_id)"
                )
            )
            print("Added civitai_model_versions.base_model_id column")


def _seed_civitai_base_models() -> None:
    """Seed civitai_base_models with canonical entries.

    Each entry has a canonical_key (matches _normalize_base_model output),
    a display label, optional family grouping, and sort_order for UI.
    Idempotent — skips rows that already exist.
    """
    seeds = [
        # key, label, base_model_type, family, sort_order
        ("sdxl", "SDXL", "sdxl", "stable-diffusion", 10),
        ("sd15", "SD 1.5", "sd", "stable-diffusion", 20),
        ("sd21", "SD 2.1", "sd", "stable-diffusion", 30),
        ("sd3", "SD 3", "sd3", "stable-diffusion", 35),
        ("pony", "Pony", "sdxl", "pony", 40),
        ("illustrious", "Illustrious", "sdxl", "illustrious", 50),
        ("noobai", "NoobAI", "sdxl", "noobai", 55),
        ("flux", "Flux", "flux", "flux", 60),
        ("animagine", "Animagine", "sd", "animagine", 70),
        ("cyberrealistic", "CyberRealistic", "sd", "stable-diffusion", 75),
        ("unknown", "Unknown", None, None, 999),
    ]

    with SessionLocal() as db:
        existing = {
            r[0]
            for r in db.query(CivitaiBaseModel.canonical_key).all()
        }
        added = 0
        for key, label, bm_type, family, sort in seeds:
            if key not in existing:
                db.add(CivitaiBaseModel(
                    canonical_key=key,
                    label=label,
                    base_model_type=bm_type,
                    family=family,
                    sort_order=sort,
                ))
                added += 1
        if added:
            db.commit()
            print(f"Seeded {added} civitai_base_models rows")


def _backfill_civitai_base_model_ids() -> None:
    """Set base_model_id on civitai_model_versions by normalizing base_model.

    Uses the same _normalize_base_model logic as the models tree router.
    Auto-creates civitai_base_models rows for previously unseen keys.
    """
    # Lazy import to avoid circular dependency at module level.
    from routers.models_tree import _normalize_base_model

    with SessionLocal() as db:
        # Find versions with NULL base_model_id but non-NULL base_model
        versions = (
            db.query(CivitaiModelVersion)
            .filter(
                CivitaiModelVersion.base_model_id.is_(None),
                CivitaiModelVersion.base_model.isnot(None),
            )
            .all()
        )
        if not versions:
            return

        # Load all existing base models into a lookup
        bm_map: dict[str, int] = {
            r.canonical_key: r.id
            for r in db.query(
                CivitaiBaseModel.id, CivitaiBaseModel.canonical_key
            ).all()
        }

        updated = 0
        for ver in versions:
            key = _normalize_base_model(ver.base_model)
            if key not in bm_map:
                # Auto-create a row for this key
                new_bm = CivitaiBaseModel(
                    canonical_key=key,
                    label=ver.base_model,
                    sort_order=900,
                )
                db.add(new_bm)
                db.flush()
                bm_map[key] = new_bm.id
                print(
                    f"Auto-created civitai_base_models row: "
                    f"{key!r} → {ver.base_model!r}"
                )
            ver.base_model_id = bm_map[key]
            updated += 1

        db.commit()
        if updated:
            print(
                f"Backfilled base_model_id for {updated} "
                f"civitai_model_versions"
            )


def _backfill_civitai_users() -> None:
    """Populate civitai_users from existing civitai_models columns.

    Extracts distinct (civitai_user_id, civitai_username,
    civitai_user_deleted) from civitai_models and creates CivitaiUser rows.
    """
    with SessionLocal() as db:
        # Check if any users exist already
        if db.query(CivitaiUser).first() is not None:
            return

        # Extract distinct users from civitai_models
        with engine.begin() as connection:
            rows = connection.execute(text("""
                SELECT civitai_user_id,
                       MAX(civitai_username) AS civitai_username,
                       MAX(COALESCE(civitai_user_deleted, 0)) AS deleted
                FROM civitai_models
                WHERE civitai_user_id IS NOT NULL
                GROUP BY civitai_user_id
            """)).fetchall()

        if not rows:
            return

        for uid, name, deleted in rows:
            db.add(CivitaiUser(
                civitai_user_id=uid,
                name=name or "",
                deleted_at=None,
                original_name=name,
            ))

        db.commit()
        print(f"Backfilled {len(rows)} civitai_users from existing models")


# ---------------------------------------------------------------------------
# Seed / bootstrap helpers
# ---------------------------------------------------------------------------


def create_initial_data() -> None:
    """Populate initial tools/licenses/authorities through bootstrap module."""
    from bootstrap import populate_initial_data

    populate_initial_data(SessionLocal)


def ensure_collection_civitai_mappings_table() -> None:
    """Create the ``collection_civitai_mappings`` junction table and backfill.

    This table implements the many-to-many mapping between local collections
    and CivitAI collection IDs.  On first run the table is created and any
    existing ``CollectionModel.civitai_collection_id`` values are copied into
    it.  The legacy column is kept for backward compatibility.
    """
    with engine.begin() as connection:
        # Check whether the junction table already exists
        existing_tables = {
            row[0]
            for row in connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
        }

        if "collection_civitai_mappings" not in existing_tables:
            connection.execute(text(
                "CREATE TABLE collection_civitai_mappings ("
                "  collection_id INTEGER NOT NULL REFERENCES collections(id),"
                "  civitai_collection_id INTEGER NOT NULL UNIQUE,"
                "  PRIMARY KEY (collection_id, civitai_collection_id)"
                ")"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_collection_civitai_mappings_civitai_collection_id "
                "ON collection_civitai_mappings (civitai_collection_id)"
            ))

        # Backfill from legacy column (idempotent — uses INSERT OR IGNORE)
        connection.execute(text(
            "INSERT OR IGNORE INTO collection_civitai_mappings (collection_id, civitai_collection_id) "
            "SELECT id, civitai_collection_id FROM collections "
            "WHERE civitai_collection_id IS NOT NULL"
        ))


def _ensure_concept_prototype_columns() -> None:
    """Add concept_type, prototype_vector, prototype_source_count, prototype_updated_at to concepts.

    These columns support CLIP-based visual prototypes.  All are nullable so
    existing concepts continue to work without prototypes.
    """
    with engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(concepts)")).fetchall()
        }
        if "concept_type" not in existing:
            connection.execute(
                text("ALTER TABLE concepts ADD COLUMN concept_type VARCHAR")
            )
        if "prototype_vector" not in existing:
            connection.execute(
                text("ALTER TABLE concepts ADD COLUMN prototype_vector BLOB")
            )
        if "prototype_source_count" not in existing:
            connection.execute(
                text("ALTER TABLE concepts ADD COLUMN prototype_source_count INTEGER")
            )
        if "prototype_updated_at" not in existing:
            connection.execute(
                text("ALTER TABLE concepts ADD COLUMN prototype_updated_at DATETIME")
            )
