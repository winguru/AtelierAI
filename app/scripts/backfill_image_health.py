#!/usr/bin/env python3
"""Backfill is_corrupt and expected_file_size columns for existing images.

Phase 1 — **corrupt image scan**: For PNG files, uses ``pngcheck`` to
catch structural spec violations (tEXt before IHDR, NULL bytes in text
chunks) that cause browser rendering failures but which PIL silently
tolerates.  For non-PNG formats, falls back to PIL ``verify()`` + ``load()``.

Phase 2 — **expected_file_size backfill**: For CivitAI images that already
have enrichment data in ``json_metadata``, extracts the declared file size
from ``json_metadata.civitai.metadata.size`` and writes it to the
``expected_file_size`` column.

Usage
-----
    cd app/
    python scripts/backfill_image_health.py --dry-run       # preview only
    python scripts/backfill_image_health.py                  # run for real
    python scripts/backfill_image_health.py --skip-corrupt   # only file-size
    python scripts/backfill_image_health.py --skip-size      # only corrupt scan
    python scripts/backfill_image_health.py --batch 500      # custom batch
"""

from __future__ import annotations

import argparse
import subprocess
import time
from pathlib import Path

from path_setup import PROJECT_ROOT  # noqa: F401  (side effect: adds repo paths)

from PIL import Image
from sqlalchemy import text
from database import SessionLocal, engine
from models import ImageModel

# ---------------------------------------------------------------------------
# Column migrations
# ---------------------------------------------------------------------------


def _ensure_columns() -> None:
    """Add is_corrupt and expected_file_size columns if they don't exist."""
    with engine.begin() as conn:
        existing = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info(images)")).fetchall()
        }
        if "is_corrupt" not in existing:
            conn.execute(
                text(
                    "ALTER TABLE images ADD COLUMN is_corrupt BOOLEAN "
                    "NOT NULL DEFAULT 0"
                )
            )
            print("  Added is_corrupt column.")
        if "expected_file_size" not in existing:
            conn.execute(
                text(
                    "ALTER TABLE images ADD COLUMN expected_file_size INTEGER"
                )
            )
            print("  Added expected_file_size column.")


# ---------------------------------------------------------------------------
# Phase 1: Corrupt image scan
# ---------------------------------------------------------------------------


def _find_pngcheck() -> str | None:
    """Locate the pngcheck binary on the system."""
    try:
        result = subprocess.run(
            ["which", "pngcheck"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    # Common Homebrew / Linux paths
    for candidate in ("/opt/homebrew/bin/pngcheck", "/usr/local/bin/pngcheck", "/usr/bin/pngcheck"):
        if Path(candidate).exists():
            return candidate
    return None


_PNGCHECK_PATH: str | None = None


def _check_image_corrupt(image_path: Path) -> tuple[bool, bool]:
    """Return ``(is_corrupt, has_structural_warning)`` for the image.

    * **is_corrupt** — image cannot be decoded by PIL or is missing critical
      data.  These images will fail to render in browsers.

    * **has_structural_warning** — pngcheck reported a spec violation (e.g.
      tEXt chunk before IHDR, NULL bytes in tEXt data) that does NOT prevent
      rendering.  These are informational only and do NOT set ``is_corrupt``.

    The backfill only writes ``is_corrupt=True`` for images that fail PIL
    decoding — not for mere spec warnings that render fine in browsers.
    """
    global _PNGCHECK_PATH

    structural_warning = False

    if image_path.suffix.lower() == ".png":
        # Use pngcheck for PNGs — detect structural warnings
        if _PNGCHECK_PATH is None:
            _PNGCHECK_PATH = _find_pngcheck()
        if _PNGCHECK_PATH:
            try:
                result = subprocess.run(
                    [_PNGCHECK_PATH, str(image_path)],
                    capture_output=True, text=True, timeout=10,
                )
                # pngcheck exits 0 for OK, non-zero for errors/warnings
                if result.returncode != 0:
                    structural_warning = True
            except Exception:
                pass

    # The authoritative check: can PIL actually decode the image?
    try:
        with Image.open(image_path) as img:
            img.verify()
        with Image.open(image_path) as img:
            img.load()
        # PIL decoded fine — not corrupt even if pngcheck warned
        return False, structural_warning
    except Exception:
        # PIL failed — genuinely corrupt
        return True, structural_warning


def _scan_corrupt(dry_run: bool, batch: int) -> None:
    """Scan all images for corruption via PIL verify()."""
    # Resolve the image library path from config
    try:
        from config import IMAGE_LIBRARY_PATH  # pyright: ignore[reportMissingImports]
    except ImportError:
        IMAGE_LIBRARY_PATH = str(PROJECT_ROOT / "image_library")

    library = Path(IMAGE_LIBRARY_PATH)

    with SessionLocal() as db:
        total = db.query(ImageModel).filter(ImageModel.is_corrupt == False).count()  # noqa: E712
        print(f"\n[Corrupt Scan] Checking {total} images (batch={batch})")

        offset = 0
        corrupt_count = 0
        clean_count = 0
        warn_count = 0

        while True:
            images = (
                db.query(ImageModel)
                .filter(ImageModel.is_corrupt == False)  # noqa: E712
                .order_by(ImageModel.id)
                .offset(offset)
                .limit(batch)
                .all()
            )
            if not images:
                break

            for img_rec in images:
                image_path = library / str(img_rec.file_path)
                if not image_path.exists():
                    # File missing from disk — skip (not "corrupt" per se)
                    continue

                # Skip non-image mimetypes (videos, etc.)
                mime = (img_rec.mimetype or "").lower()
                if mime.startswith("video/"):
                    continue

                is_corrupt, structural_warning = _check_image_corrupt(image_path)

                if is_corrupt:
                    corrupt_count += 1
                    if not dry_run:
                        img_rec.is_corrupt = True
                    print(f"  CORRUPT: id={img_rec.id} path={img_rec.file_path}")
                elif structural_warning:
                    warn_count += 1
                    if not dry_run:
                        pass  # Warnings logged but is_corrupt stays False
                    print(f"  WARNING (renders OK): id={img_rec.id} path={img_rec.file_path}")
                else:
                    clean_count += 1

            if not dry_run:
                db.commit()

            offset += batch
            print(f"  Scanned {min(offset, total)}/{total} "
                  f"(corrupt={corrupt_count}, clean={clean_count})")

        action = "Would mark" if dry_run else "Marked"
        print(f"\n[Corrupt Scan] {action} {corrupt_count} corrupt images. "
              f"{warn_count} structural warnings (render OK). "
              f"{clean_count} clean. Total scanned: {corrupt_count + warn_count + clean_count}")


# ---------------------------------------------------------------------------
# Phase 2: Expected file size backfill from CivitAI enrichment data
# ---------------------------------------------------------------------------


def _extract_declared_size(json_metadata: dict) -> int | None:
    """Extract declared file size from json_metadata.

    Checks multiple paths in priority order:
    1. ``civitai.declared_file_size`` — set by civitai_enrichment.py
    2. ``civitai.basic_info.metadata.size`` — raw CivitAI API payload
    3. ``civitai_source_variant.declared_file_size`` — variant import records
    4. ``civitai_source_variant_static.declared_file_size`` — static variant records
    """
    # Path 1 & 2: civitai enrichment data
    civitai = json_metadata.get("civitai")
    if isinstance(civitai, dict):
        declared = civitai.get("declared_file_size")
        if declared is not None:
            try:
                return int(declared)
            except (TypeError, ValueError):
                pass

        basic_info = civitai.get("basic_info")
        if isinstance(basic_info, dict):
            metadata = basic_info.get("metadata")
            if isinstance(metadata, dict):
                raw_size = metadata.get("size")
                try:
                    return int(raw_size) if raw_size is not None else None
                except (TypeError, ValueError):
                    pass

    # Path 3 & 4: variant import records
    for variant_key in ("civitai_source_variant", "civitai_source_variant_static"):
        variant = json_metadata.get(variant_key)
        if isinstance(variant, dict):
            declared = variant.get("declared_file_size")
            if declared is not None:
                try:
                    return int(declared)
                except (TypeError, ValueError):
                    pass

    return None


def _backfill_expected_size(dry_run: bool, batch: int) -> None:
    """Backfill expected_file_size from CivitAI enrichment data."""
    with SessionLocal() as db:
        total = (
            db.query(ImageModel)
            .filter(
                ImageModel.expected_file_size.is_(None),
                ImageModel.json_metadata.is_not(None),
            )
            .count()
        )
        print(f"\n[Size Backfill] Checking {total} enriched images (batch={batch})")

        offset = 0
        updated = 0

        while True:
            images = (
                db.query(ImageModel)
                .filter(
                    ImageModel.expected_file_size.is_(None),
                    ImageModel.json_metadata.is_not(None),
                )
                .order_by(ImageModel.id)
                .offset(offset)
                .limit(batch)
                .all()
            )
            if not images:
                break

            for img_rec in images:
                if not isinstance(img_rec.json_metadata, dict):
                    continue

                declared = _extract_declared_size(img_rec.json_metadata)
                if declared is not None:
                    updated += 1
                    if not dry_run:
                        img_rec.expected_file_size = declared

            if not dry_run:
                db.commit()

            offset += batch
            print(f"  Processed {min(offset, total)}/{total} (declared_sizes={updated})")

        action = "Would update" if dry_run else "Updated"
        print(f"\n[Size Backfill] {action} {updated} images with declared file size.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill image health columns")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    parser.add_argument("--batch", type=int, default=200, help="Batch size (default: 200)")
    parser.add_argument("--skip-corrupt", action="store_true", help="Skip corrupt scan")
    parser.add_argument("--skip-size", action="store_true", help="Skip expected_size backfill")
    args = parser.parse_args()

    t0 = time.time()

    _ensure_columns()

    if not args.skip_corrupt:
        _scan_corrupt(dry_run=args.dry_run, batch=args.batch)

    if not args.skip_size:
        _backfill_expected_size(dry_run=args.dry_run, batch=args.batch)

    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s")


if __name__ == "__main__":
    main()
