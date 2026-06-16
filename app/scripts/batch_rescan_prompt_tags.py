#!/usr/bin/env python3
"""Batch rescan all active images to populate prompt_tags and create observations.

Usage:
    cd /workspace/app
    PYTHONPATH=app/src:app/backend python scripts/batch_rescan_prompt_tags.py [--dry-run] [--limit N] [--delay SECONDS]

This script calls the rescan endpoint for each active image, which:
1. Extracts prompt text from EXIF/sidecar
2. Builds prompt tags using the improved prompt_phrases module
3. Stores prompt_tags in json_metadata
4. Creates authority_terms for prompt tags
5. Creates image_concept_observations for tags that match existing concepts

It uses the HTTP API so the app must be running on port 8000.
"""

import argparse
import sys
import time

import requests

API_BASE = "http://localhost:8000/api"


def get_active_images() -> list[dict]:
    """Fetch all active image file hashes directly from the database."""
    # Import here to avoid issues when run standalone
    sys.path.insert(0, "backend")
    from database import SessionLocal  # noqa: PLC0415
    from models import ImageModel  # noqa: PLC0415

    db = SessionLocal()
    try:
        images = (
            db.query(
                ImageModel.id,
                ImageModel.file_hash,
                ImageModel.file_path,
            )
            .filter(ImageModel.image_status == "active")
            .order_by(ImageModel.id)
            .all()
        )
        return [
            {
                "id": img.id,
                "file_hash": img.file_hash,
                "file_path": img.file_path,
            }
            for img in images
        ]
    finally:
        db.close()


def rescan_image(file_hash: str) -> dict | None:
    """Call the rescan endpoint for a single image."""
    try:
        resp = requests.post(
            f"{API_BASE}/images/{file_hash}/rescan",
            timeout=60,
        )
        if resp.status_code == 200:
            return resp.json()
        else:
            print(f"  ERROR: HTTP {resp.status_code}: {resp.text[:200]}")
            return None
    except requests.exceptions.Timeout:
        print("  ERROR: Timeout")
        return None
    except Exception as e:
        print(f"  ERROR: {e}")
        return None


def _parse_obs_count(actions: list[str]) -> int:
    """Extract observation count from actions_taken strings."""
    for action in actions:
        if "observation" not in action.lower():
            continue
        try:
            parts = action.split()
            for j, p in enumerate(parts):
                if p.isdigit() and j > 0:
                    return int(p)
        except (ValueError, IndexError):
            pass
    return 0


def _parse_authority_term_count(actions: list[str]) -> int:
    """Extract authority term creation count from actions_taken strings."""
    count = 0
    for action in actions:
        if "prompt +" not in action:
            continue
        try:
            idx = action.index("prompt +")
            rest = action[idx + 8:]
            num = rest.split(",")[0].split(")")[0]
            count += int(num)
        except (ValueError, IndexError):
            pass
    return count


def main():
    parser = argparse.ArgumentParser(description="Batch rescan images for prompt tags")
    parser.add_argument("--dry-run", action="store_true", help="List images without rescanning")
    parser.add_argument("--limit", type=int, default=0, help="Max images to process (0=all)")
    parser.add_argument("--delay", type=float, default=0.5, help="Delay between rescan calls (seconds)")
    args = parser.parse_args()

    print("Fetching active images...")
    images = get_active_images()
    print(f"Found {len(images)} active images.")

    if args.limit > 0:
        images = images[:args.limit]
        print(f"Limited to {len(images)} images.")

    if args.dry_run:
        for i, img in enumerate(images):
            print(f"  [{i+1}/{len(images)}] id={img['id']} hash={img['file_hash'][:16]}... {img['file_path']}")
        return

    total_prompt_tags = 0
    total_observations = 0
    total_authority_terms = 0
    errors = 0

    for i, img in enumerate(images):
        file_hash = img["file_hash"]
        print(f"[{i+1}/{len(images)}] Rescanning id={img['id']} hash={file_hash[:16]}...")

        result = rescan_image(file_hash)
        if result is None:
            errors += 1
            continue

        prompt_tags_count = len(result.get("prompt_tags", []))
        total_prompt_tags += prompt_tags_count

        actions = result.get("actions_taken", [])
        obs_count = _parse_obs_count(actions)
        total_observations += obs_count
        total_authority_terms += _parse_authority_term_count(actions)

        tag_info = f"tags={prompt_tags_count}" if prompt_tags_count else "no-tags"
        obs_info = f" obs={obs_count}" if obs_count else ""
        print(f"  → {tag_info}{obs_info}")

        if args.delay > 0:
            time.sleep(args.delay)

    print("\n── Batch Rescan Summary ──")
    print(f"Images processed: {len(images)}")
    print(f"Errors: {errors}")
    print(f"Total prompt tags extracted: {total_prompt_tags}")
    print(f"Total new observations created: {total_observations}")
    print(f"Total new authority terms: {total_authority_terms}")


if __name__ == "__main__":
    main()
