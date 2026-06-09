#!/usr/bin/env python3
"""Backfill civitai_post_id column for existing CivitAI images.

Three-phase approach (fastest to slowest):

Phase 1 — Local archive scan (instant, no API calls)
    Parse archived ``image.getInfinite`` response files under
    ``app/data/getinfinite_*/`` and build an ``{image_id: post_id}``
    mapping.

Phase 2 — Collection getInfinite scan (50 images/page, fast)
    For images linked to known CivitAI collections, page through each
    collection using ``image.getInfinite`` and extract ``postId`` from
    each item.  Covers ~94% of images at 50× the throughput of individual
    calls.

Phase 3 — Individual image.get fallback (1 image/call, slow)
    For the remaining images not in any collection, call ``image.get``
    per image.  Throttled to ~1 request/second.

This script is idempotent: rows that already have ``civitai_post_id``
set are skipped by default (use ``--force`` to re-extract and overwrite).

Usage
-----
    cd app/
    python scripts/backfill_civitai_post_id.py --dry-run       # preview
    python scripts/backfill_civitai_post_id.py                  # run for real
    python scripts/backfill_civitai_post_id.py --skip-archive   # skip local archive
    python scripts/backfill_civitai_post_id.py --skip-collections  # skip collection scan
    python scripts/backfill_civitai_post_id.py --skip-api       # skip individual fallback
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import time

from path_setup import PROJECT_ROOT  # noqa: F401  (side effect: adds repo paths)

from sqlalchemy import text
from sqlalchemy.orm import Session

from database import SessionLocal, engine, Base
from models import ImageModel


# ---------------------------------------------------------------------------
# Phase 1: Local archive scan
# ---------------------------------------------------------------------------

def _build_archive_map() -> dict[int, int]:
    """Scan archived getInfinite JSON files for image_id -> post_id pairs."""
    archive_dir = os.path.join(PROJECT_ROOT, "data")
    pattern = os.path.join(archive_dir, "getinfinite_*", "page_*.json")
    files = sorted(glob.glob(pattern))

    if not files:
        print("  No archived getInfinite files found.")
        return {}

    print(f"  Scanning {len(files)} archived getInfinite pages …")

    image_to_post: dict[int, int] = {}
    errors = 0
    for f in files:
        try:
            with open(f) as fh:
                data = json.load(fh)
            for item in data.get("items", []):
                img_id = item.get("id")
                post_id = item.get("postId")
                if img_id and post_id:
                    image_to_post[img_id] = post_id
        except Exception:
            errors += 1

    print(f"  Found {len(image_to_post)} image→post mappings from archive "
          f"({errors} parse errors)")
    return image_to_post


def _apply_map(
    db: Session,
    image_to_post: dict[int, int],
    *,
    dry_run: bool = False,
    batch_size: int = 500,
    force: bool = False,
    label: str = "",
) -> dict[str, int]:
    """Apply an {image_id: post_id} map to matching DB rows."""
    prefix = f"  {label} " if label else "  "

    stats: dict[str, int] = {
        "candidates": 0,
        "updated": 0,
        "already_set": 0,
    }

    if not image_to_post:
        return stats

    query = db.query(ImageModel).filter(
        ImageModel.source_site == "civitai",
        ImageModel.civitai_image_id.isnot(None),
    )
    if not force:
        query = query.filter(ImageModel.civitai_post_id.is_(None))

    rows = query.all()
    updated = 0
    for row in rows:
        post_id = image_to_post.get(row.civitai_image_id)
        if post_id is None:
            continue

        stats["candidates"] += 1

        if row.civitai_post_id is not None and not force:
            stats["already_set"] += 1
            continue

        if not dry_run:
            row.civitai_post_id = post_id
            updated += 1
            if updated % batch_size == 0:
                db.flush()
                print(f"{prefix}flush: {updated} rows …")

    if not dry_run and updated > 0:
        db.flush()
        db.commit()

    stats["updated"] = updated
    return stats


# ---------------------------------------------------------------------------
# Phase 2: Collection getInfinite scan
# ---------------------------------------------------------------------------

def _scan_collections(
    db: Session,
    *,
    dry_run: bool = False,
    batch_size: int = 500,
    force: bool = False,
    throttle_seconds: float = 1.0,
) -> dict[str, int]:
    """Page through each CivitAI collection via getInfinite to extract postId.

    Each page returns up to 50 images with postId — far more efficient than
    individual image.get calls.
    """
    from atelierai.civitai.civitai_api import CivitaiAPI

    api = CivitaiAPI.get_instance()

    stats: dict[str, int] = {
        "collections_scanned": 0,
        "collection_images_seen": 0,
        "map_size": 0,
        "candidates": 0,
        "updated": 0,
        "already_set": 0,
        "pages_fetched": 0,
    }

    # Get all collections with civitai IDs (junction table first, then legacy column)
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT c.id, c.name, m.civitai_collection_id "
            "FROM collection_civitai_mappings m "
            "JOIN collections c ON c.id = m.collection_id "
            "UNION "
            "SELECT id, name, civitai_collection_id FROM collections "
            "WHERE civitai_collection_id IS NOT NULL "
            "AND civitai_collection_id NOT IN ("
            " SELECT civitai_collection_id FROM collection_civitai_mappings"
            ") "
            "ORDER BY id"
        )).fetchall()

    if not rows:
        print("  No CivitAI collections found in DB.")
        return stats

    # Build the set of image_ids that still need post_id
    needed_query = db.query(ImageModel.civitai_image_id).filter(
        ImageModel.source_site == "civitai",
        ImageModel.civitai_image_id.isnot(None),
    )
    if not force:
        needed_query = needed_query.filter(ImageModel.civitai_post_id.is_(None))

    needed_ids: set[int] = {r for (r,) in needed_query.all()}
    print(f"  {len(needed_ids)} images still need post_id")

    if not needed_ids:
        return stats

    # Accumulate all image_id -> post_id mappings across collections
    image_to_post: dict[int, int] = {}

    for row in rows:
        coll_db_id, coll_name, civitai_coll_id = row
        stats["collections_scanned"] += 1

        print(f"\n  Collection: {coll_name} (civitai_id={civitai_coll_id})")

        payload_data: dict = {
            **api.default_params,
            "collectionId": int(civitai_coll_id),
            "cursor": None,
        }

        pages = 0
        coll_images = 0
        coll_matched = 0

        while True:
            # Rate-limit between pages
            if pages > 0:
                time.sleep(throttle_seconds)

            try:
                response = api._make_request(
                    endpoint="image.getInfinite",
                    payload_data=payload_data,
                )
            except Exception as exc:
                print(f"    Error on page {pages + 1}: {exc}")
                break

            if not response:
                break

            stats["pages_fetched"] += 1
            pages += 1

            items = api._find_deep_image_list(response)
            if not items:
                break

            for item in items:
                if not isinstance(item, dict):
                    continue
                img_id = item.get("id")
                post_id = item.get("postId")
                if img_id and post_id:
                    image_to_post[img_id] = post_id
                    coll_images += 1
                    if img_id in needed_ids:
                        coll_matched += 1

            # Check for next cursor
            next_cursor = None
            if isinstance(response, dict):
                next_cursor = response.get("nextCursor")
            if not next_cursor:
                break
            payload_data["cursor"] = next_cursor

        print(f"    {pages} pages, {coll_images} images with postId, "
              f"{coll_matched} matched to needed images")

        stats["collection_images_seen"] += coll_images

    stats["map_size"] = len(image_to_post)
    print(f"\n  Total: {stats['map_size']} unique image→post mappings from "
          f"{stats['collections_scanned']} collections "
          f"({stats['pages_fetched']} pages)")

    # Apply the map
    apply_stats = _apply_map(
        db, image_to_post,
        dry_run=dry_run,
        batch_size=batch_size,
        force=force,
        label="[collections]",
    )
    stats["candidates"] = apply_stats["candidates"]
    stats["updated"] = apply_stats["updated"]
    stats["already_set"] = apply_stats["already_set"]

    return stats


# ---------------------------------------------------------------------------
# Phase 3: Individual API fetch for remaining images
# ---------------------------------------------------------------------------

def _apply_api_fetch(
    db: Session,
    *,
    dry_run: bool = False,
    batch_size: int = 500,
    force: bool = False,
    throttle_seconds: float = 1.0,
) -> dict[str, int]:
    """Fetch post_id from CivitAI API for images that still lack it."""
    from atelierai.civitai.civitai_api import CivitaiAPI

    api = CivitaiAPI.get_instance()

    stats: dict[str, int] = {
        "api_candidates": 0,
        "api_updated": 0,
        "api_not_found": 0,
        "api_no_post_id": 0,
        "api_errors": 0,
    }

    query = db.query(ImageModel).filter(
        ImageModel.source_site == "civitai",
        ImageModel.civitai_image_id.isnot(None),
    )
    if not force:
        query = query.filter(ImageModel.civitai_post_id.is_(None))

    rows = query.order_by(ImageModel.id).all()
    stats["api_candidates"] = len(rows)

    if not rows:
        print("  No images need API fetch.")
        return stats

    print(f"  Fetching post_id for {len(rows)} images via API …")
    print(f"  Estimated time: ~{len(rows) * throttle_seconds / 60:.0f} minutes "
          f"(at {throttle_seconds}s/call)")

    updated = 0
    for i, row in enumerate(rows):
        image_id = row.civitai_image_id

        # Respect global rate limit from the API client
        try:
            if hasattr(api, "is_rate_limited") and api.is_rate_limited():
                wait = getattr(api, "rate_limit_remaining_seconds", lambda: 30)()
                print(f"    Rate limited — waiting {wait:.0f}s …")
                time.sleep(max(wait, 1))
        except Exception:
            pass

        try:
            basic_info = api.fetch_basic_info(image_id)
        except Exception as exc:
            stats["api_errors"] += 1
            print(f"    [{i+1}/{len(rows)}] Error fetching image {image_id}: {exc}")
            time.sleep(throttle_seconds)
            continue

        if basic_info is None:
            stats["api_not_found"] += 1
            if (i + 1) % 100 == 0:
                print(f"    [{i+1}/{len(rows)}] Not found: {image_id}")
            time.sleep(throttle_seconds)
            continue

        raw_post_id = basic_info.get("postId")
        if raw_post_id is None:
            stats["api_no_post_id"] += 1
            if (i + 1) % 100 == 0:
                print(f"    [{i+1}/{len(rows)}] No postId in response: {image_id}")
            time.sleep(throttle_seconds)
            continue

        try:
            post_id = int(raw_post_id)
        except (TypeError, ValueError):
            stats["api_no_post_id"] += 1
            time.sleep(throttle_seconds)
            continue

        if not dry_run:
            row.civitai_post_id = post_id
            updated += 1
            if updated % batch_size == 0:
                db.flush()
                print(f"    API flush: {updated} rows …")

        # Progress logging every 200 images
        if (i + 1) % 200 == 0:
            print(f"    [{i+1}/{len(rows)}] "
                  f"updated={updated} not_found={stats['api_not_found']} "
                  f"no_post={stats['api_no_post_id']} errors={stats['api_errors']}")

        time.sleep(throttle_seconds)

    if not dry_run and updated > 0:
        db.flush()
        db.commit()

    stats["api_updated"] = updated
    return stats


# ---------------------------------------------------------------------------
# Ensure column exists
# ---------------------------------------------------------------------------

def _ensure_column() -> None:
    """Ensure civitai_post_id column exists in the images table."""
    Base.metadata.create_all(bind=engine, checkfirst=True)

    with engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(images)")).fetchall()
        }
        if "civitai_post_id" not in existing:
            print("Adding civitai_post_id column …")
            connection.execute(
                text("ALTER TABLE images ADD COLUMN civitai_post_id INTEGER")
            )
            connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_images_civitai_post_id "
                    "ON images(civitai_post_id)"
                )
            )
            print("Column added and indexed.")
        else:
            print("Column civitai_post_id already exists.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill civitai_post_id for existing CivitAI images"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview what would be changed without writing to DB",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-extract even for rows that already have civitai_post_id set",
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=500,
        help="Flush batch size (default: 500)",
    )
    parser.add_argument(
        "--skip-archive",
        action="store_true",
        help="Skip Phase 1 (local archive scan)",
    )
    parser.add_argument(
        "--skip-collections",
        action="store_true",
        help="Skip Phase 2 (collection getInfinite scan)",
    )
    parser.add_argument(
        "--skip-api",
        action="store_true",
        help="Skip Phase 3 (individual image.get fallback)",
    )
    parser.add_argument(
        "--throttle",
        type=float,
        default=1.0,
        help="Seconds between API calls (default: 1.0)",
    )
    args = parser.parse_args()

    print("=== civitai_post_id backfill ===")
    if args.dry_run:
        print("  (dry-run mode — no writes)")
    if args.force:
        print("  (force mode — overwriting existing values)")

    _ensure_column()

    t0 = time.monotonic()
    db = SessionLocal()

    try:
        # Phase 1: Local archive scan (instant)
        if not args.skip_archive:
            print("\nPhase 1: Local archive scan")
            archive_map = _build_archive_map()
            archive_stats = _apply_map(
                db, archive_map,
                dry_run=args.dry_run,
                batch_size=args.batch,
                force=args.force,
                label="[archive]",
            )
            print(f"  Mappings: {len(archive_map)}")
            print(f"  Matched:  {archive_stats['candidates']}")
            print(f"  Updated:  {archive_stats['updated']}")
        else:
            print("\nPhase 1: Skipped (--skip-archive)")

        # Phase 2: Collection getInfinite scan (50 images/page)
        if not args.skip_collections:
            print("\nPhase 2: Collection getInfinite scan")
            coll_stats = _scan_collections(
                db,
                dry_run=args.dry_run,
                batch_size=args.batch,
                force=args.force,
                throttle_seconds=args.throttle,
            )
            print(f"\n  Collections scanned: {coll_stats['collections_scanned']}")
            print(f"  Pages fetched:       {coll_stats['pages_fetched']}")
            print(f"  Images with postId:  {coll_stats['collection_images_seen']}")
            print(f"  Map size:            {coll_stats['map_size']}")
            print(f"  Matched:             {coll_stats['candidates']}")
            print(f"  Updated:             {coll_stats['updated']}")
        else:
            print("\nPhase 2: Skipped (--skip-collections)")

        # Phase 3: Individual image.get fallback
        if not args.skip_api:
            print("\nPhase 3: Individual image.get fallback")
            api_stats = _apply_api_fetch(
                db,
                dry_run=args.dry_run,
                batch_size=args.batch,
                force=args.force,
                throttle_seconds=args.throttle,
            )
            print(f"  Candidates:  {api_stats['api_candidates']}")
            print(f"  Updated:     {api_stats['api_updated']}")
            print(f"  Not found:   {api_stats['api_not_found']}")
            print(f"  No postId:   {api_stats['api_no_post_id']}")
            print(f"  Errors:      {api_stats['api_errors']}")
        else:
            print("\nPhase 3: Skipped (--skip-api)")

    finally:
        db.close()

    elapsed = time.monotonic() - t0
    print(f"\nDone in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
