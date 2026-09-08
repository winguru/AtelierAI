#!/usr/bin/env python3
"""Restore CivitAI tombstone placeholder rows purged by library rescans.

Problem
-------
``ImageCollection._cleanup_orphaned_records`` (library rescan) treated every
DB row whose file was absent from disk as an orphan and deleted it. CivitAI
remote-unavailable placeholders are *virtual* rows — their ``file_path``
(``placeholders/…/*.placeholder``) never exists on disk by design — so every
"Rescan Library" wiped them, including the 404 tombstones
(``civitai_deleted_at`` set) created by the tag-maintenance scan.

The cleanup has been fixed to preserve ``placeholders/`` rows; this script
re-creates the rows that were already purged.

Source of truth for restoration
-------------------------------
``civitai_api_cache`` rows with ``endpoint='image.get'`` and
``http_status=404`` are permanent local truth (cached 404s are never
retried). Every cached 404 whose ``civitai_image_id`` no longer has an
``images`` row is a purged tombstone. Rows are rebuilt exactly as
``_build_civitai_unavailable_result`` builds fresh placeholders, plus
``civitai_deleted_at`` (the tombstone flag) set to the cache entry's
timestamp.

Usage
-----
    cd app/
    python scripts/restore_civitai_tombstones.py --dry-run   # preview
    python scripts/restore_civitai_tombstones.py             # run for real
"""

from __future__ import annotations

import argparse
import hashlib
from datetime import datetime, timezone

from path_setup import PROJECT_ROOT  # noqa: F401  (side effect: adds repo paths)

from sqlalchemy import text
from sqlalchemy.orm import Session

from database import SessionLocal
from models import ImageModel


def _build_civitai_image_source_url(image_id: int) -> str:
    return f"https://civitai.com/images/{image_id}"


def _collect_purged_tombstone_ids(session: Session) -> list[tuple[int, object]]:
    """Return (civitai_image_id, cached_at) for cached-404 image.get entries
    that no longer have a matching images row."""
    rows = session.execute(
        text(
            "SELECT request_key, fetched_at FROM civitai_api_cache "
            "WHERE endpoint = 'image.get' AND http_status = 404"
        )
    ).fetchall()

    existing_ids = {
        row[0]
        for row in session.execute(
            text("SELECT civitai_image_id FROM images WHERE civitai_image_id IS NOT NULL")
        )
    }

    purged: list[tuple[int, datetime | None]] = []
    for request_key, created_at in rows:
        if not isinstance(request_key, str) or not request_key.startswith("id="):
            continue
        try:
            image_id = int(request_key[len("id="):])
        except ValueError:
            continue
        if image_id not in existing_ids:
            purged.append((image_id, created_at))
    purged.sort()
    return purged


def _parse_cached_at(value: object) -> datetime | None:
    """Parse the cache's fetched_at string (raw SQL returns str, not datetime)."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    return None


def _build_placeholder_row(
    image_id: int, cached_at: object
) -> ImageModel:
    source_url = _build_civitai_image_source_url(image_id)
    placeholder_hash = hashlib.sha256(
        f"civitai-placeholder:{source_url}".encode("utf-8")
    ).hexdigest()
    placeholder_path = (
        f"placeholders/{placeholder_hash[:2]}/{placeholder_hash}.placeholder"
    )
    deleted_at = _parse_cached_at(cached_at) or datetime.now(timezone.utc).replace(tzinfo=None)
    now = datetime.now(timezone.utc).replace(tzinfo=None)

    return ImageModel(
        file_path=placeholder_path,
        file_name=f"civitai-unavailable-{image_id}.placeholder",
        file_hash=placeholder_hash,
        file_size=0,
        width=None,
        height=None,
        mimetype="application/x-civitai-placeholder",
        date_created=deleted_at,
        date_modified=now,
        image_status="placeholder",
        status_reason="civitai_remote_unavailable",
        replaced_by_image_id=None,
        source_url=source_url,
        source_site="civitai",
        civitai_image_id=image_id,
        civitai_deleted_at=deleted_at,
        exif_data={},
        json_metadata={
            "civitai": {
                "unavailable_detail": {
                    "image_id": image_id,
                    "classification": "civitai_remote_unavailable",
                    "restored_from_cache": True,
                },
                "placeholder": {
                    "kind": "civitai_remote_unavailable",
                    "updated_at": now.isoformat(),
                },
            }
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List the tombstones that would be restored without writing",
    )
    args = parser.parse_args()

    session = SessionLocal()
    try:
        purged = _collect_purged_tombstone_ids(session)
        print(f"Purged tombstones found in 404 cache: {len(purged)}")
        for image_id, cached_at in purged:
            print(f"  civitai_image_id={image_id} cached_at={cached_at}")

        if not purged:
            print("Nothing to restore.")
            return
        if args.dry_run:
            print("Dry run — no changes written.")
            return

        restored = 0
        for image_id, cached_at in purged:
            row = _build_placeholder_row(image_id, cached_at)
            session.add(row)
            restored += 1
        session.commit()
        print(f"Restored {restored} tombstone placeholder rows.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
