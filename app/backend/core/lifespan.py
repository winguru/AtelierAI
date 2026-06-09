"""AtelierAI application startup, schema migration, and shutdown logic.

Contains:
- All ``_ensure_*`` schema-migration helpers (additive SQLite column/index
  migrations that run safely on every startup).
- ``_backfill_*`` one-time data-migration helpers.
- ``create_initial_data()`` — delegates to the bootstrap module.
- ``lifespan()`` — FastAPI async context manager wiring everything together.
- ``task_manager`` — module-level BackgroundTaskManager singleton; import
  it wherever background tasks need to be submitted.
"""

from __future__ import annotations

import json as _json
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy import text

import atelierai.config as _app_config
from atelierai.task_manager import BackgroundTaskManager
from bootstrap import populate_initial_data
from database import Base, SessionLocal, engine
from models import (
    AuthorityTerm,
    ImageConceptObservation,
    ImageModel,
    ObservationCertainty,
    ObservationSource,
    SchemaVersion,
    TagAuthority,
)
from utils.cache import _configure_uvicorn_access_logging, _read_env_flag

if TYPE_CHECKING:
    pass

# ---------------------------------------------------------------------------
# Configuration shortcuts (sourced from atelierai.config)
# ---------------------------------------------------------------------------

IMAGE_LIBRARY_PATH: str = str(getattr(_app_config, "IMAGE_LIBRARY_PATH", "image_library"))
CURRENT_SCHEMA_VERSION: str = str(getattr(_app_config, "CURRENT_SCHEMA_VERSION", "1.0"))
DATABASE_URL: str = str(getattr(_app_config, "DATABASE_URL", "sqlite:///image_db.sqlite"))
ALLOW_SCHEMA_RESET: bool = bool(getattr(_app_config, "ALLOW_SCHEMA_RESET", False))

# ---------------------------------------------------------------------------
# Background task manager — module-level singleton
# ---------------------------------------------------------------------------

task_manager = BackgroundTaskManager(max_workers=4)

# ---------------------------------------------------------------------------
# Schema migration helpers
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

    # PRAGMA row: (cid, name, type, notnull, dflt_value, pk) — notnull=1 means NOT NULL.
    concept_id_col = col_info.get("concept_id")
    if concept_id_col is None or concept_id_col[3] == 0:
        # Already nullable — schema is current.
        return

    print(
        "  [migration] image_concept_observations has legacy NOT NULL concept_id "
        "— recreating table with current schema..."
    )

    with engine.begin() as connection:
        result = connection.execute(
            text("SELECT COUNT(*) FROM image_concept_observations")
        ).fetchone()
        row_count = result[0] if result else 0

        # Drop auxiliary indexes; auto-indexes on PK/UNIQUE drop with the table.
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

    # Create the corrected table using the ORM's current metadata.
    Base.metadata.tables["image_concept_observations"].create(engine)

    if row_count > 0:
        # Build the list of columns that exist in both old and new schemas.
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
                        term_id = cast(int, term.id)
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
        for idx_name in ("ix_images_file_hash",):
            if idx_name in indexes:
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

        connection.execute(
            text(
                "UPDATE images "
                "SET variant_group_key = file_hash "
                "WHERE (variant_group_key IS NULL OR variant_group_key = '') "
                "AND file_hash IS NOT NULL AND file_hash != ''"
            )
        )
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

            for artist_id, _artist_name, metadata_str in rows:
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


def _attempt_schema_migration_1_3_to_1_4() -> bool:
    """Attempt in-place migration from schema 1.3 to 1.4."""
    print("   Attempting in-place schema upgrade 1.3 -> 1.4...")
    try:
        Base.metadata.create_all(bind=engine, checkfirst=True)
        with SessionLocal() as db:
            db.query(SchemaVersion).delete()
            db.add(SchemaVersion(version_num=CURRENT_SCHEMA_VERSION))
            db.commit()
        print("✅ In-place schema upgrade complete.")
        return True
    except Exception as migrate_exc:
        print(f"⚠️ In-place schema upgrade failed: {migrate_exc}")
        return False


def _attempt_schema_migration_1_4_to_1_5() -> bool:
    """Attempt in-place migration from schema 1.4 to 1.5 (user_tags column)."""
    print("   Attempting in-place schema upgrade 1.4 -> 1.5 (user_tags column)...")
    try:
        _ensure_user_tags_column()
        _backfill_user_tags_from_sidecars()
        with SessionLocal() as db:
            db.query(SchemaVersion).delete()
            db.add(SchemaVersion(version_num=CURRENT_SCHEMA_VERSION))
            db.commit()
        print("✅ In-place schema upgrade complete.")
        return True
    except Exception as migrate_exc:
        print(f"⚠️ In-place schema upgrade failed: {migrate_exc}")
        return False


def _handle_schema_mismatch(
    version_result: str, db_file_path: str | None
) -> tuple[bool, str | None]:
    """Handle schema version mismatch. Returns (should_delete_db, new_version)."""
    print(
        f"⚠️ Schema version mismatch. Found {version_result}, expected {CURRENT_SCHEMA_VERSION}."
    )
    migrated = False

    if str(version_result or "") == "1.3" and str(CURRENT_SCHEMA_VERSION) == "1.4":
        migrated = _attempt_schema_migration_1_3_to_1_4()

    if str(version_result or "") == "1.4" and str(CURRENT_SCHEMA_VERSION) == "1.5":
        migrated = _attempt_schema_migration_1_4_to_1_5()

    if migrated:
        version_result = CURRENT_SCHEMA_VERSION

    if db_file_path and os.path.exists(db_file_path):
        if version_result == CURRENT_SCHEMA_VERSION:
            print("✅ Database schema is up to date.")
            return False, None
        elif ALLOW_SCHEMA_RESET:
            print("   Recreating database (ALLOW_SCHEMA_RESET=true)...")
            return True, db_file_path
        else:
            raise RuntimeError(
                "Schema mismatch detected and automatic reset is disabled. "
                "Set ALLOW_SCHEMA_RESET=true in .env for development-only "
                "auto-rebuild, or migrate/update the database manually."
            )
    else:
        raise RuntimeError(
            "Schema mismatch detected, but automatic reset is only supported "
            "for sqlite file databases. Please migrate/update the database manually."
        )


def _check_existing_database_schema(db_file_path: str | None) -> bool:
    """Check and handle schema version for existing database. Returns True if DB should be recreated."""
    try:
        with engine.connect() as connection:
            version_result = connection.execute(
                SchemaVersion.__table__.select()
            ).scalar_one_or_none()
            if version_result != CURRENT_SCHEMA_VERSION:
                should_delete, _ = _handle_schema_mismatch(str(version_result) if version_result else "", db_file_path)
                return should_delete
            else:
                print("✅ Database schema is up to date.")
                return False
    except Exception as e:
        print(f"⚠️ Could not check schema version (table might not exist): {e}")
        if db_file_path and os.path.exists(db_file_path):
            if ALLOW_SCHEMA_RESET:
                print("   Recreating database to be safe (ALLOW_SCHEMA_RESET=true)...")
                return True
            else:
                raise RuntimeError(
                    "Could not verify schema version and automatic reset is disabled. "
                    "Set ALLOW_SCHEMA_RESET=true in .env for development-only auto-rebuild, "
                    "or inspect/fix the database manually."
                ) from e
        else:
            raise RuntimeError(
                "Could not verify schema version for a non-sqlite database. "
                "Automatic reset is unavailable; inspect/fix the database manually."
            ) from e


# ---------------------------------------------------------------------------
# Initial data + lifespan
# ---------------------------------------------------------------------------


def create_initial_data() -> None:
    """Populate initial tools/licenses/authorities through bootstrap module."""
    populate_initial_data(SessionLocal)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ARG001
    """FastAPI lifespan context manager — runs startup/shutdown logic."""
    _configure_uvicorn_access_logging(
        suppress_status_get_logs=_read_env_flag(
            "ATELIER_SUPPRESS_STATUS_GET_LOGS", False
        )
    )
    print("Starting AtelierAI API...")

    # Check if the database file exists (sqlite only).
    db_file_path = None
    db_exists = True
    if DATABASE_URL.startswith("sqlite:///"):
        db_file_path = os.path.abspath(
            os.path.expanduser(DATABASE_URL.replace("sqlite:///", "", 1))
        )
        db_exists = os.path.exists(db_file_path)

    if db_exists:
        should_delete = _check_existing_database_schema(db_file_path)
        if should_delete and db_file_path:
            os.remove(db_file_path)
            db_exists = False

    if not db_exists:
        print("Creating new database and initial data...")
        Base.metadata.create_all(bind=engine, checkfirst=True)
        with SessionLocal() as db:
            existing_version = (
                db.query(SchemaVersion)
                .filter(SchemaVersion.version_num == CURRENT_SCHEMA_VERSION)
                .first()
            )
            if not existing_version:
                db.add(SchemaVersion(version_num=CURRENT_SCHEMA_VERSION))
                db.commit()
        create_initial_data()
        print("Database setup complete.")
    else:
        create_initial_data()

    # Ensure any newly added tables/columns exist for existing databases.
    Base.metadata.create_all(bind=engine, checkfirst=True)
    _ensure_image_lifecycle_columns()
    _ensure_collection_sync_columns()
    _ensure_user_nsfw_columns()
    _ensure_civitai_uuid_column()
    _ensure_civitai_hash_column()
    _ensure_user_tags_column()
    _ensure_user_negative_tags_column()
    _ensure_observation_schema_current()
    _ensure_observation_presence_columns()
    _ensure_observation_authority_term_unique_index()
    _backfill_user_tags_to_observations()
    _ensure_image_variant_columns()
    _ensure_promoted_metadata_columns()
    _ensure_original_file_name_column()
    _ensure_blurhash_column()
    _ensure_civitai_image_id_column()
    _ensure_civitai_user_columns()
    _ensure_observation_unique_constraint()
    _ensure_file_hash_nonunique()

    print("AtelierAI API is ready to go!")

    yield

    print("Shutting down AtelierAI API...")
    task_manager.shutdown()
