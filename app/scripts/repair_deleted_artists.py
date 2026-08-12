#!/usr/bin/env python
"""Repair deleted-account artist records.

Scans the ``artists`` table for synthetic ``[deleted:USERID]`` names and
attempts to resolve the real historical username from:

1. ``user.getById`` API — works for *banned* accounts (deletedAt is null).
2. Local Search Lab data — ``civitai_search_images.artist_name`` captured the
   username at scrape time, even for users later fully deleted from CivitAI.

For each resolved artist the script:

* Renames the artist ``name`` to the real username.
* Sets ``civitai_user_original_name`` to the resolved username.
* Marks ``civitai_user_deleted = True``.
* Backfills ``civitai_user_id`` if missing.

Artists that cannot be resolved are left as ``[deleted:USERID]`` but still
have their ``civitai_user_id`` backfilled (parsed from the synthetic name).

Usage::

    cd /workspace/app
    PYTHONPATH=app/src:app/backend python -m scripts.repair_deleted_artists \\
        [--dry-run]
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from importlib import import_module
from typing import Optional

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("repair_deleted_artists")

_DELETED_RE = re.compile(r"^\[deleted:(\d+)\]$")


def _try_resolve(
    user_id: int,
    *,
    fetch_user_by_id,
    CivitaiAPI,
    db,
    CivitaiSearchImage,
) -> Optional[str]:
    """Try API then Search Lab to resolve a username."""
    # 1. Live API — works for banned/active users.
    try:
        api = CivitaiAPI.get_instance()
        user_data = api.fetch_user_by_id(user_id)
        if isinstance(user_data, dict):
            username = user_data.get("username")
            if isinstance(username, str) and username.strip():
                return username.strip()
    except Exception as exc:  # noqa: BLE001
        logger.debug("  user.getById failed for uid=%s: %s", user_id, exc)

    # 2. Search Lab data.
    try:
        row = (
            db.query(CivitaiSearchImage.artist_name)
            .filter(
                CivitaiSearchImage.artist_id == user_id,
                CivitaiSearchImage.artist_name.isnot(None),
                CivitaiSearchImage.artist_name != "",
                ~CivitaiSearchImage.artist_name.like("[deleted:%"),
            )
            .first()
        )
        if row:
            return row[0]
    except Exception as exc:  # noqa: BLE001
        logger.debug("  Search Lab lookup failed for uid=%s: %s", user_id, exc)

    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change but do not commit.",
    )
    args = parser.parse_args()

    # Lazy imports so the script can report import errors clearly.
    database_mod = import_module("backend.database")
    models_mod = import_module("backend.models")
    civitai_api_mod = import_module("atelierai.civitai.civitai_api")

    SessionLocal = getattr(database_mod, "SessionLocal")
    Artist = getattr(models_mod, "Artist")
    ImageModel = getattr(models_mod, "ImageModel")
    CivitaiSearchImage = getattr(models_mod, "CivitaiSearchImage")
    CivitaiAPI = getattr(civitai_api_mod, "CivitaiAPI")

    db = SessionLocal()
    try:
        deleted_artists = (
            db.query(Artist)
            .filter(Artist.name.like("[deleted:%"))
            .order_by(Artist.id)
            .all()
        )
        logger.info(
            "Found %d artist records with synthetic [deleted:UID] names",
            len(deleted_artists),
        )

        resolved_count = 0
        unresolvable_count = 0
        backfill_count = 0

        for artist in deleted_artists:
            match = _DELETED_RE.match(artist.name)
            if not match:
                logger.debug("  Skipping %s (no UID pattern)", artist.name)
                continue

            user_id = int(match.group(1))
            logger.info(
                "Artist id=%s name=%s uid=%s",
                artist.id,
                artist.name,
                user_id,
            )

            # Backfill civitai_user_id if missing.
            if artist.civitai_user_id is None:
                logger.info("  Backfilling civitai_user_id=%s", user_id)
                artist.civitai_user_id = user_id
                backfill_count += 1

            # Try to resolve the real username.
            resolved = _try_resolve(
                user_id,
                fetch_user_by_id=getattr(
                    CivitaiAPI, "fetch_user_by_id", None
                ),
                CivitaiAPI=CivitaiAPI,
                db=db,
                CivitaiSearchImage=CivitaiSearchImage,
            )

            if resolved:
                logger.info("  ✓ Resolved: %s → %s", artist.name, resolved)
                # Check if a real-name artist already exists (avoid duplicates).
                existing = (
                    db.query(Artist)
                    .filter(Artist.name == resolved)
                    .filter(Artist.id != artist.id)
                    .first()
                )
                if existing:
                    logger.info(
                        "  Merge: reassigning images from artist %s → %s",
                        artist.id,
                        existing.id,
                    )
                    # Reassign images to the existing real artist.
                    db.query(ImageModel).filter(
                        ImageModel.artist_id == artist.id
                    ).update({"artist_id": existing.id})
                    # Delete the synthetic placeholder FIRST so the
                    # unique constraint on civitai_user_id is freed.
                    db.flush()
                    db.delete(artist)
                    db.flush()
                    # Now safe to update existing with civitai metadata.
                    existing.civitai_user_id = user_id
                    existing.civitai_user_deleted = True
                    existing.civitai_user_original_name = resolved
                else:
                    artist.name = resolved
                    artist.civitai_user_original_name = resolved
                    artist.civitai_user_deleted = True
                resolved_count += 1
            else:
                logger.info("  ✗ Could not resolve (no API/Search Lab data)")
                artist.civitai_user_deleted = True
                unresolvable_count += 1

        if args.dry_run:
            logger.info(
                "DRY RUN — would resolve %d, backfill %d, leave %d unresolved",
                resolved_count,
                backfill_count,
                unresolvable_count,
            )
        else:
            db.commit()
            logger.info(
                "Committed: resolved %d, backfilled %d, left %d unresolved",
                resolved_count,
                backfill_count,
                unresolvable_count,
            )
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
