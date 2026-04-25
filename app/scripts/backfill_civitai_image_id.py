#!/usr/bin/env python3
"""Backfill civitai_image_id column for existing ImageModel rows.

Extracts the CivitAI image ID from ``source_url`` using the canonical
``extract_civitai_image_id()`` function and writes it to the new indexed
``civitai_image_id`` column.

This script is idempotent: rows that already have ``civitai_image_id`` set
are skipped by default (use ``--force`` to re-extract and overwrite).

Usage
-----
    cd app/
    python scripts/backfill_civitai_image_id.py --dry-run     # preview
    python scripts/backfill_civitai_image_id.py                # run for real
    python scripts/backfill_civitai_image_id.py --batch 500    # custom batch size
    python scripts/backfill_civitai_image_id.py --force        # overwrite existing
"""

from __future__ import annotations

import argparse
import time

from path_setup import PROJECT_ROOT  # noqa: F401  (side effect: adds repo paths)

from sqlalchemy import text
from sqlalchemy.orm import Session

from database import SessionLocal, engine, Base
from models import ImageModel
from civitai_enrichment import extract_civitai_image_id, is_civitai_image_url


def _ensure_column() -> None:
    """Ensure civitai_image_id column exists in the images table."""
    Base.metadata.create_all(bind=engine, checkfirst=True)

    with engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.execute(
                text("PRAGMA table_info(images)")
            )
        }
        if "civitai_image_id" not in existing:
            print("Adding civitai_image_id column …")
            connection.execute(
                text(
                    "ALTER TABLE images ADD COLUMN civitai_image_id INTEGER"
                )
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_images_civitai_image_id "
                    "ON images(civitai_image_id)"
                )
            )
            print("Column added and indexed.")
        else:
            print("Column civitai_image_id already exists.")


def backfill(
    db: Session,
    *,
    batch_size: int = 500,
    dry_run: bool = False,
    force: bool = False,
) -> dict[str, int]:
    """Backfill civitai_image_id from source_url.

    Returns a stats dict with counts.
    """
    stats = {
        "total_rows": 0,
        "civitai_rows": 0,
        "already_set": 0,
        "extracted": 0,
        "no_id_found": 0,
        "updated": 0,
    }

    # Query rows that have a CivitAI source_url
    query = db.query(ImageModel).filter(
        ImageModel.source_site == "civitai"
    )
    if not force:
        query = query.filter(
            ImageModel.civitai_image_id.is_(None)
        )

    rows = query.order_by(ImageModel.id).all()
    stats["total_rows"] = len(rows)

    updated_count = 0
    for row in rows:
        stats["civitai_rows"] += 1
        source_url = str(getattr(row, "source_url", "") or "")

        if not source_url or not is_civitai_image_url(source_url):
            stats["no_id_found"] += 1
            continue

        if row.civitai_image_id is not None and not force:
            stats["already_set"] += 1
            continue

        image_id = extract_civitai_image_id(source_url)
        if image_id is None:
            stats["no_id_found"] += 1
            continue

        stats["extracted"] += 1

        if not dry_run:
            row.civitai_image_id = image_id
            updated_count += 1

            if updated_count % batch_size == 0:
                db.flush()
                print(
                    f"  Flushed {updated_count}/{stats['civitai_rows']} rows …"
                )

    if not dry_run and updated_count > 0:
        db.flush()
        db.commit()

    stats["updated"] = updated_count
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill civitai_image_id from source_url"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be changed without writing to DB",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-extract even for rows that already have civitai_image_id set",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=500,
        help="Flush batch size (default: 500)",
    )
    args = parser.parse_args()

    print("=== civitai_image_id backfill ===")
    if args.dry_run:
        print("  (dry-run mode — no writes)")
    if args.force:
        print("  (force mode — overwriting existing values)")

    _ensure_column()

    t0 = time.monotonic()
    db = SessionLocal()
    try:
        stats = backfill(
            db,
            batch_size=args.batch,
            dry_run=args.dry_run,
            force=args.force,
        )
    finally:
        db.close()

    elapsed = time.monotonic() - t0
    print(f"\nResults ({elapsed:.1f}s):")
    print(f"  CivitAI rows scanned : {stats['civitai_rows']}")
    print(f"  Already had ID       : {stats['already_set']}")
    print(f"  IDs extracted        : {stats['extracted']}")
    print(f"  No ID in URL         : {stats['no_id_found']}")
    print(f"  Rows updated         : {stats['updated']}")


if __name__ == "__main__":
    main()
