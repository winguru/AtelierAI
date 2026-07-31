#!/usr/bin/env python3
"""Batch-backfill CLIP embeddings for images in the database.

Computes and stores the 512-D CLIP vector for each image row that doesn't
already have one in ``ImageModel.clip_embedding``.  This enables fast kNN
visual lookup in the concept-association studio.

Usage
-----
    cd app/
    python scripts/backfill_clip_embeddings.py --dry-run        # preview
    python scripts/backfill_clip_embeddings.py                    # run all
    python scripts/backfill_clip_embeddings.py --limit 100        # cap count
    python scripts/backfill_clip_embeddings.py --resume           # skip already-embedded
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

from path_setup import PROJECT_ROOT  # noqa: F401  (side effect: adds repo paths)

from database import SessionLocal
from models import ImageModel
from services.concept_prototype_service import ConceptPrototypeService
from services.clip_provider import get_clip_provider, set_clip_provider


# ---------------------------------------------------------------------------
# CLIP provider bootstrapping for standalone scripts
# ---------------------------------------------------------------------------

async def _ensure_clip_provider():
    """Initialise the CLIP provider if it's not already set.

    Mirrors the auto-detection logic in main.py but simplified for scripts.
    """
    if get_clip_provider() is not None:
        return True

    try:
        from services.clip_provider import LocalCLIPProvider
        provider = LocalCLIPProvider(
            model_name="ViT-B-32",
            pretrained="openai",
            force_cpu=True,  # scripts default to CPU for safety
        )
        set_clip_provider(provider)
        print(f"CLIP provider ready: {provider._device}")
        return True
    except Exception as exc:
        print(f"WARNING: Could not initialise CLIP provider: {exc}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Main backfill logic
# ---------------------------------------------------------------------------

async def run_backfill(limit: int | None, resume: bool, batch_delay: float) -> dict:
    """Backfill CLIP embeddings for images.

    Returns a summary dict.
    """
    clip_ok = await _ensure_clip_provider()

    db = SessionLocal()
    total = 0
    embedded = 0
    skipped = 0
    failed = 0

    try:
        query = db.query(ImageModel).filter(ImageModel.file_path.isnot(None))
        if resume:
            query = query.filter(ImageModel.clip_embedding.is_(None))

        if limit:
            query = query.limit(limit)

        images = query.all()
        total = len(images)
        print(f"Processing {total} images...")

        if not clip_ok:
            print("CLIP unavailable — listing only.")
            return {"total": total, "embedded": 0, "skipped": total, "failed": 0}

        svc = ConceptPrototypeService(db)

        for idx, image in enumerate(images, 1):
            # Skip if already embedded and not resuming
            if image.clip_embedding is not None:
                skipped += 1
                continue

            # Resolve local file path
            image_lib = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "image_library",
            )
            full_path = os.path.join(image_lib, image.file_path)

            if not os.path.isfile(full_path):
                failed += 1
                if idx % 100 == 0:
                    print(f"  [{idx}/{total}] file not found: {image.file_hash}")
                continue

            try:
                vector = await svc.backfill_image_embedding(image.id)
                if vector is not None:
                    embedded += 1
                else:
                    failed += 1
            except Exception as exc:
                failed += 1
                if idx % 100 == 0:
                    print(f"  [{idx}/{total}] error: {exc}")

            if idx % 50 == 0:
                print(f"  [{idx}/{total}] embedded={embedded} skipped={skipped} failed={failed}")

            if batch_delay > 0:
                await asyncio.sleep(batch_delay)
    finally:
        db.close()

    return {"total": total, "embedded": embedded, "skipped": skipped, "failed": failed}


def main():
    parser = argparse.ArgumentParser(description="Batch-backfill CLIP embeddings for images")
    parser.add_argument("--dry-run", action="store_true", help="Preview: list images that would be processed")
    parser.add_argument("--limit", type=int, default=None, help="Max images to process")
    parser.add_argument("--resume", action="store_true", help="Skip images that already have embeddings")
    parser.add_argument("--delay", type=float, default=0.0, help="Delay between images (seconds)")
    args = parser.parse_args()

    if args.dry_run:
        db = SessionLocal()
        try:
            query = db.query(ImageModel).filter(ImageModel.file_path.isnot(None))
            if args.resume:
                query = query.filter(ImageModel.clip_embedding.is_(None))
            if args.limit:
                query = query.limit(args.limit)
            count = query.count()
            print(f"DRY RUN: {count} images would be processed.")
        finally:
            db.close()
        return 0

    print("Starting CLIP embedding backfill...")
    start = time.time()

    summary = asyncio.run(run_backfill(limit=args.limit, resume=args.resume, batch_delay=args.delay))

    elapsed = time.time() - start
    print(f"\n{'='*60}")
    print(f"Total:     {summary['total']}")
    print(f"Embedded:  {summary['embedded']}")
    print(f"Skipped:   {summary['skipped']}")
    print(f"Failed:    {summary['failed']}")
    print(f"Elapsed:   {elapsed:.1f}s")
    print(f"{'='*60}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
