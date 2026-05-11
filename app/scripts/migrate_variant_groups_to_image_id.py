#!/usr/bin/env python3
"""Migrate variant groups from civitai:{hash} to civitai:{image_id}.

The old grouping used civitai_hash (file hash), which incorrectly lumped
unrelated images together (images from different CivitAI posts can share the
same file hash). The new grouping uses civitai_image_id, which correctly
groups images that are variants of the same CivitAI multi-resource post.

This script:
1. Deletes old civitai_multi_resource variant groups (hash-based keys)
2. Creates new variant groups keyed by civitai:{image_id}
3. Links images sharing the same civitai_image_id into groups

Usage:
    python scripts/migrate_variant_groups_to_image_id.py --dry-run
    python scripts/migrate_variant_groups_to_image_id.py
"""

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

# Ensure project root is on path
APP_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_ROOT))
sys.path.insert(0, str(APP_ROOT / "backend"))

from database import SessionLocal  # noqa: E402
from sqlalchemy import text  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate variant groups from hash-based to image-id-based keys"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show what would change without modifying DB"
    )
    args = parser.parse_args()

    db = SessionLocal()

    try:
        # ── 1. Count old groups ────────────────────────────────────────
        old_groups = db.execute(
            text(
                "SELECT id, group_key FROM variant_groups "
                "WHERE group_type = 'civitai_multi_resource'"
            )
        ).fetchall()

        print(f"Found {len(old_groups)} old civitai_multi_resource variant groups")
        for g in old_groups:
            members = db.execute(
                text("SELECT COUNT(*) FROM image_variant_groups WHERE group_id = :gid"),
                {"gid": g[0]},
            ).scalar()
            print(f"  id={g[0]} key={g[1]} members={members}")

        # ── 2. Count new groups needed ─────────────────────────────────
        dupes = db.execute(
            text(
                "SELECT civitai_image_id, COUNT(*) as cnt, "
                "GROUP_CONCAT(id) as image_ids "
                "FROM images "
                "WHERE civitai_image_id IS NOT NULL "
                "AND civitai_image_id != 0 "
                "AND image_status != 'tombstoned' "
                "GROUP BY civitai_image_id "
                "HAVING cnt > 1 "
                "ORDER BY cnt DESC"
            )
        ).fetchall()

        print(f"\nFound {len(dupes)} civitai_image_id values with multiple images")

        if args.dry_run:
            print("\n[DRY RUN] Would make these changes:")
            print(f"  DELETE {len(old_groups)} old variant groups")
            print(f"  CREATE {len(dupes)} new variant groups (image-id-based)")
            for d in dupes:
                print(f"    civitai:{d[0]}  count={d[1]}  images={d[2]}")
            return

        # ── 3. Backup ──────────────────────────────────────────────────
        db_path = APP_ROOT / "image_db.sqlite"
        if db_path.exists():
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            bak_path = APP_ROOT / f"image_db.sqlite.bak_pre_variant_migration_{ts}"
            print(f"\nBacking up DB to {bak_path.name}...")
            shutil.copy2(str(db_path), str(bak_path))
            # Also copy WAL/SHM if present
            for ext in ("wal", "shm"):
                src = db_path.parent / f"{db_path.name}-{ext}"
                if src.exists():
                    shutil.copy2(str(src), str(bak_path.parent / f"{bak_path.name}-{ext}"))

        # ── 4. Delete old groups and memberships ───────────────────────
        print("\nDeleting old civitai variant groups...")
        # Delete memberships first (FK constraint)
        for g in old_groups:
            result = db.execute(
                text("DELETE FROM image_variant_groups WHERE group_id = :gid"),
                {"gid": g[0]},
            )
            print(f"  Deleted {result.rowcount} memberships from group {g[0]}")

        result = db.execute(
            text("DELETE FROM variant_groups WHERE group_type = 'civitai_multi_resource'")
        )
        print(f"  Deleted {result.rowcount} old variant groups")

        # ── 5. Create new groups ───────────────────────────────────────
        print("\nCreating new image-id-based variant groups...")
        created_groups = 0
        created_memberships = 0

        for dupe in dupes:
            civitai_image_id = dupe[0]
            image_ids_str = dupe[2]
            image_ids = [int(x) for x in image_ids_str.split(",")]

            group_key = f"civitai:{civitai_image_id}"

            # Create group
            db.execute(
                text(
                    "INSERT INTO variant_groups (group_key, group_type, group_label, cover_preference) "
                    "VALUES (:key, 'civitai_multi_resource', 'CivitAI Multi-Resource', 'sort_order')"
                ),
                {"key": group_key},
            )
            group_id = db.execute(text("SELECT last_insert_rowid()")).scalar()
            created_groups += 1

            # Add memberships
            for sort_idx, image_id in enumerate(image_ids):
                db.execute(
                    text(
                        "INSERT INTO image_variant_groups "
                        "(image_id, group_id, role_in_group, sort_index, source) "
                        "VALUES (:iid, :gid, 'member', :sort, 'auto_civitai')"
                    ),
                    {"iid": image_id, "gid": group_id, "sort": sort_idx},
                )
                created_memberships += 1

            print(f"  Created group '{group_key}' (id={group_id}) with {len(image_ids)} images")

        db.commit()
        print(f"\nDone! Created {created_groups} groups with {created_memberships} memberships.")

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
