#!/usr/bin/env python3
"""Backfill blurhash for images that don't have one.

CivitAI images should already be populated by the startup migration
(``_ensure_blurhash_column``) which copies from ``civitai_hash``.
This script handles the remaining images by computing blurhash from
the actual image file on disk.

Usage
-----
    cd app/
    python scripts/backfill_blurhash.py --dry-run     # preview
    python scripts/backfill_blurhash.py                # run for real
    python scripts/backfill_blurhash.py --batch 500    # custom batch size
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Optional

from path_setup import PROJECT_ROOT  # noqa: F401  (side effect: adds repo paths)

from PIL import Image
from sqlalchemy import text
from database import SessionLocal, engine
from models import ImageModel

try:
    import blurhash as _blurhash_mod  # pyright: ignore[reportMissingImports]
except Exception:
    _blurhash_mod = None


def _encode_blurhash(image_path: Path) -> Optional[str]:
    """Encode a single image file to a blurhash string."""
    if _blurhash_mod is None:
        return None
    try:
        with Image.open(image_path) as img:
            small = img.copy()
            small.thumbnail((128, 128), Image.LANCZOS)
            if small.mode != "RGB":
                small = small.convert("RGB")
            w, h = small.size
            raw = small.load()
            pixel_rows = [
                [raw[x, y] for x in range(w)]
                for y in range(h)
            ]
            return _blurhash_mod.encode(pixel_rows, components_x=4, components_y=3)
    except Exception:
        return None


def _ensure_blurhash_column() -> None:
    """Ensure the blurhash column exists."""
    with engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(images)")).fetchall()
        }
        if "blurhash" not in existing:
            connection.execute(text("ALTER TABLE images ADD COLUMN blurhash VARCHAR"))
            print("  Added blurhash column.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill blurhash for images")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--batch", type=int, default=200, help="Batch size (default: 200)")
    args = parser.parse_args()

    if _blurhash_mod is None:
        print("ERROR: blurhash package not installed.  pip install blurhash")
        sys.exit(1)

    _ensure_blurhash_column()

    # Count work
    with SessionLocal() as session:
        total = session.execute(
            text(
                "SELECT COUNT(*) FROM images "
                "WHERE blurhash IS NULL AND mimetype LIKE 'image/%'"
            )
        ).scalar()

    if not total:
        print("All images already have blurhash. Nothing to do.")
        return

    print(f"Images needing blurhash: {total}")
    if args.dry_run:
        print("(dry-run — no changes will be written)")

    done = 0
    errors = 0
    t0 = time.time()

    while True:
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    "SELECT id, file_path FROM images "
                    "WHERE blurhash IS NULL AND mimetype LIKE 'image/%' "
                    "LIMIT :limit"
                ),
                {"limit": args.batch},
            ).fetchall()

        if not rows:
            break

        for row_id, file_path in rows:
            abs_path = Path(PROJECT_ROOT) / file_path if not Path(file_path).is_absolute() else Path(file_path)
            if not abs_path.exists():
                errors += 1
                done += 1
                continue

            bh = _encode_blurhash(abs_path)
            if bh and not args.dry_run:
                with SessionLocal() as session:
                    session.execute(
                        text("UPDATE images SET blurhash = :bh WHERE id = :id"),
                        {"bh": bh, "id": row_id},
                    )
                    session.commit()

            done += 1
            if done % 100 == 0 or done == total:
                elapsed = time.time() - t0
                rate = done / elapsed if elapsed > 0 else 0
                print(f"  {done}/{total}  ({rate:.0f}/s)  errors={errors}")

    elapsed = time.time() - t0
    print(f"Done: {done} images in {elapsed:.1f}s ({errors} errors)")


if __name__ == "__main__":
    main()
