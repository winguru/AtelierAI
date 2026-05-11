#!/usr/bin/env python3
"""Normalize CivitAI source_url values to relative paths.

Strips hostname from CivitAI URLs so they are stored as relative paths
(e.g. ``/images/12345`` instead of ``https://civitai.com/images/12345``).
The full URL is reconstructed at read-time using ``build_civitai_url()`` with
the configured ``CIVITAI_WEB_BASE_URL``.

Handles all known CivitAI hostnames:
  - civitai.com
  - civitai.red
  - (any future CivitAI domain)

This script is idempotent: rows whose ``source_url`` is already a relative
path (starts with ``/``) are left unchanged.

Usage
-----
    cd app/
    python scripts/normalize_source_urls.py --dry-run     # preview
    python scripts/normalize_source_urls.py                # run for real
    python scripts/normalize_source_urls.py --batch 1000   # custom batch size
"""

from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path

from path_setup import PROJECT_ROOT  # noqa: F401  (side effect: adds repo paths)

from sqlalchemy import text
from sqlalchemy.orm import Session

from database import SessionLocal
from utils.url_helpers import normalize_civitai_url

# ── Constants ────────────────────────────────────────────────────────────────

_DB_PATH = Path(PROJECT_ROOT) / "image_db.sqlite"

# Hostnames to strip (case-insensitive match in normalize_civitai_url)
_CIVITAI_HOSTNAMES = {"civitai.com", "civitai.red", "www.civitai.com", "www.civitai.red"}

# SQL: count rows that need normalization
_COUNT_SQL = text("""
    SELECT COUNT(*) FROM images
    WHERE source_url LIKE 'http%'
      AND source_url IS NOT NULL
      AND source_url != ''
""")

# SQL: fetch rows needing normalization
_FETCH_SQL = text("""
    SELECT id, source_url FROM images
    WHERE source_url LIKE 'http%'
      AND source_url IS NOT NULL
      AND source_url != ''
    ORDER BY id
    LIMIT :limit OFFSET :offset
""")

# SQL: update a single row
_UPDATE_SQL = text("""
    UPDATE images SET source_url = :url WHERE id = :id
""")


def _backup_db(dry_run: bool = False) -> Path | None:
    """Create a timestamped backup of the database.

    Returns the backup path, or None if no backup was needed/created.
    """
    if dry_run:
        return None

    if not _DB_PATH.exists():
        print(f"  DB not found at {_DB_PATH}, skipping backup.")
        return None

    ts = time.strftime("%Y%m%d_%H%M%S")
    backup_path = _DB_PATH.parent / f"image_db.sqlite.bak_pre_url_normalize_{ts}"
    print(f"  Backing up DB → {backup_path.name} …")
    shutil.copy2(str(_DB_PATH), str(backup_path))
    print("  Backup done.")
    return backup_path


def _count_needs_normalization(db: Session) -> int:
    """Return the number of rows whose source_url starts with http."""
    result = db.execute(_COUNT_SQL)
    return result.scalar_one()


def backfill(
    db: Session,
    *,
    batch_size: int = 1000,
    dry_run: bool = False,
) -> dict:
    """Normalize source_url for all rows that need it.

    Returns a stats dict.
    """
    stats = {
        "total_needs_norm": 0,
        "normalized": 0,
        "unchanged": 0,
        "errors": 0,
    }

    stats["total_needs_norm"] = _count_needs_normalization(db)

    if stats["total_needs_norm"] == 0:
        print("  No rows need normalization. DB is already clean.")
        return stats

    print(f"  Rows to process: {stats['total_needs_norm']}")

    offset = 0
    updated_total = 0

    while True:
        rows = db.execute(
            _FETCH_SQL, {"limit": batch_size, "offset": offset}
        ).fetchall()

        if not rows:
            break

        batch_updated = 0
        for row_id, source_url in rows:
            normalized = normalize_civitai_url(source_url)

            if normalized == source_url:
                # Not a CivitAI URL or already relative — skip
                stats["unchanged"] += 1
                continue

            if normalized is None and source_url:
                # normalize_civitai_url returned None for a non-empty URL
                # This shouldn't happen for http URLs, but handle defensively
                stats["errors"] += 1
                print(f"  WARNING: normalize returned None for id={row_id} url={source_url!r}")
                continue

            stats["normalized"] += 1
            batch_updated += 1

            if not dry_run:
                db.execute(
                    _UPDATE_SQL, {"url": normalized, "id": row_id}
                )

        updated_total += batch_updated
        print(
            f"  Processed {offset + len(rows)}/{stats['total_needs_norm']} "
            f"(normalized: {updated_total})"
        )

        offset += batch_size

    if not dry_run and updated_total > 0:
        db.commit()

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Normalize CivitAI source_url values to relative paths"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be changed without writing to DB",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=1000,
        help="Fetch batch size (default: 1000)",
    )
    args = parser.parse_args()

    print("=== source_url normalization ===")
    if args.dry_run:
        print("  (dry-run mode — no writes)")
    else:
        print("  (LIVE mode — will modify DB)")
        _backup_db(dry_run=False)

    t0 = time.monotonic()
    db = SessionLocal()
    try:
        stats = backfill(
            db,
            batch_size=args.batch,
            dry_run=args.dry_run,
        )
    finally:
        db.close()

    elapsed = time.monotonic() - t0
    print(f"\nResults ({elapsed:.1f}s):")
    print(f"  Total needing norm : {stats['total_needs_norm']}")
    print(f"  Normalized         : {stats['normalized']}")
    print(f"  Unchanged          : {stats['unchanged']}")
    print(f"  Errors             : {stats['errors']}")


if __name__ == "__main__":
    main()
