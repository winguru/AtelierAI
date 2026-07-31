#!/usr/bin/env python3
"""Seed an art-style spectrum taxonomy and build text-seeded prototypes.

Creates a parent "Art Style" concept with a spectrum of child style concepts
(from sketch to hyper-realistic), then builds CLIP text-prototypes for each
so that ground-zero images can be matched visually.

This is the bootstrap step for the concept-association studio: the text-seeded
prototypes break the chicken-and-egg problem where images with no tags have
no observations, and thus never get matched.

The script is **idempotent**: concepts that already exist (by canonical_name)
are reused, not duplicated.

Usage
-----
    cd app/
    python scripts/seed_style_taxonomy.py --dry-run     # preview
    python scripts/seed_style_taxonomy.py                # run for real
    python scripts/seed_style_taxonomy.py --skip-prototypes   # concepts only
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from path_setup import PROJECT_ROOT  # noqa: F401  (side effect: adds repo paths)

from database import SessionLocal
from models import Concept
from services.concept_prototype_service import ConceptPrototypeService


# ---------------------------------------------------------------------------
# Style spectrum definition
# ---------------------------------------------------------------------------
# Each entry defines:
#   name    — canonical concept name
#   prompts — CLIP text prompts to seed the prototype
#
# Ordered from most abstract/stylised to most realistic.

STYLE_SPECTRUM: list[dict] = [
    {
        "name": "Sketch",
        "prompts": ["a sketch", "pencil sketch", "rough sketch drawing", "hand-drawn sketch"],
    },
    {
        "name": "Line Art",
        "prompts": ["line art", "clean line drawing", "ink line art", "monochrome line art"],
    },
    {
        "name": "Flat Comic",
        "prompts": ["flat comic style", "comic book art", "flat color illustration", "comic strip art"],
    },
    {
        "name": "Cel Anime",
        "prompts": ["anime cel shading", "cel-shaded anime", "anime illustration", "manga anime style"],
    },
    {
        "name": "Smooth Shading",
        "prompts": ["smooth digital painting", "soft shading illustration", "gradient shaded art", "painterly digital art"],
    },
    {
        "name": "Semi-Realistic",
        "prompts": ["semi-realistic digital art", "stylised realistic painting", "concept art style", "semi-realistic illustration"],
    },
    {
        "name": "Realistic",
        "prompts": ["realistic digital painting", "photorealistic art", "detailed realistic illustration", "lifelike digital painting"],
    },
    {
        "name": "Hyper-Realistic",
        "prompts": ["hyper-realistic art", "ultra-detailed digital painting", "photographic realism art", "extremely detailed realistic painting"],
    },
]

PARENT_NAME = "Art Style"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(name: str) -> str:
    """Generate a URL-safe slug from a name."""
    return name.lower().replace(" ", "-").replace("/", "-")


def get_or_create_concept(
    db,
    name: str,
    parent_id: int | None = None,
    concept_type: str = "style",
    description: str | None = None,
) -> tuple[Concept, bool]:
    """Find or create a concept by canonical_name and parent.

    Returns ``(concept, created)``.
    """
    existing = (
        db.query(Concept)
        .filter(
            Concept.canonical_name == name,
            Concept.parent_concept_id == parent_id if parent_id else Concept.parent_concept_id.is_(None),
        )
        .first()
    )
    if existing:
        return existing, False

    concept = Concept(
        canonical_name=name,
        slug=_slugify(name),
        parent_concept_id=parent_id,
        concept_type=concept_type,
        status="active",
        description=description,
    )
    db.add(concept)
    db.commit()
    db.refresh(concept)
    return concept, True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(dry_run: bool, skip_prototypes: bool) -> dict:
    """Seed the style taxonomy and build prototypes.

    Returns a summary dict.
    """
    db = SessionLocal()
    results: list[dict] = []

    try:
        # --- Parent concept ---
        parent, parent_created = get_or_create_concept(
            db,
            PARENT_NAME,
            parent_id=None,
            concept_type="category",
            description="Root concept for art style classification",
        )
        results.append({
            "name": PARENT_NAME,
            "concept_id": parent.id,
            "created": parent_created,
            "prototype": None,
        })

        # --- Child concepts ---
        for style in STYLE_SPECTRUM:
            if dry_run:
                results.append({
                    "name": style["name"],
                    "concept_id": None,
                    "created": True,
                    "prototype": "DRY-RUN",
                    "prompts": style["prompts"],
                })
                continue

            concept, created = get_or_create_concept(
                db,
                style["name"],
                parent_id=parent.id,
                concept_type="style",
                description=f"Art style: {style['name']}",
            )

            proto_status = "skipped"
            if not skip_prototypes:
                svc = ConceptPrototypeService(db)
                vector = await svc.build_prototype_from_text(concept.id, style["prompts"])
                proto_status = "built" if vector is not None else "failed"

            results.append({
                "name": style["name"],
                "concept_id": concept.id,
                "created": created,
                "prototype": proto_status,
            })
    finally:
        db.close()

    return {
        "parent": PARENT_NAME,
        "styles": results,
        "total": len(results),
        "created": sum(1 for r in results if r["created"]),
        "prototypes_built": sum(1 for r in results if r.get("prototype") == "built"),
    }


def main():
    parser = argparse.ArgumentParser(description="Seed art-style taxonomy with text-seeded prototypes")
    parser.add_argument("--dry-run", action="store_true", help="Preview without DB changes")
    parser.add_argument("--skip-prototypes", action="store_true", help="Create concepts only, skip CLIP prototype building")
    args = parser.parse_args()

    print(f"{'DRY RUN: ' if args.dry_run else ''}Seeding style taxonomy...")

    summary = asyncio.run(run(dry_run=args.dry_run, skip_prototypes=args.skip_prototypes))

    print(f"\n{'='*60}")
    print(f"Parent: {summary['parent']}")
    print(f"Total entries: {summary['total']}")
    print(f"Created: {summary['created']}")
    print(f"Prototypes built: {summary['prototypes_built']}")
    print(f"{'='*60}")
    for r in summary["styles"]:
        flag = "NEW" if r["created"] else "exists"
        proto = r.get("prototype", "")
        proto_str = f" proto={proto}" if proto else ""
        print(f"  [{flag}] {r['name']:20s}{proto_str}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
