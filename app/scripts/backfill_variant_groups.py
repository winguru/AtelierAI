#!/usr/bin/env python3
"""Backfill variant_groups and image_variant_groups from existing file_hash data.

For each distinct file_hash with >1 active image, creates a hash_duplicate
VariantGroup and adds all matching images as members.

Idempotent: re-running is safe because group_key is unique — existing groups
are found and reused rather than duplicated.

Usage:
    cd app/
    PYTHONPATH=.:/path/to/app/src python scripts/backfill_variant_groups.py
"""

import sys
import os

# Ensure backend modules are importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import func
from database import SessionLocal, engine, Base
from models import ImageModel, VariantGroup, ImageVariantGroupMembership


def backfill_hash_duplicate_groups():
    """Create hash_duplicate variant groups for all file_hash values with >1 image."""
    with SessionLocal() as db:
        # Find all file_hash values with more than one active image
        hash_counts = (
            db.query(
                ImageModel.file_hash,
                func.count(ImageModel.id).label("image_count"),
            )
            .filter(
                ImageModel.image_status == "active",
                ImageModel.file_hash.isnot(None),
                ImageModel.file_hash != "",
            )
            .group_by(ImageModel.file_hash)
            .having(func.count(ImageModel.id) > 1)
            .all()
        )

        print(f"Found {len(hash_counts)} file_hash values with duplicate images")

        created_groups = 0
        existing_groups = 0
        created_memberships = 0

        for file_hash, count in hash_counts:
            group_key = f"hash:{file_hash}"

            # Find or create the variant group
            group = (
                db.query(VariantGroup)
                .filter(VariantGroup.group_key == group_key)
                .first()
            )
            if group is None:
                group = VariantGroup(
                    group_key=group_key,
                    group_type="hash_duplicate",
                    group_label=f"Hash duplicate ({count} images)",
                    cover_preference="sort_order",
                )
                db.add(group)
                db.flush()
                created_groups += 1
            else:
                existing_groups += 1

            # Find all images with this hash
            images = (
                db.query(ImageModel)
                .filter(
                    ImageModel.file_hash == file_hash,
                    ImageModel.image_status == "active",
                )
                .order_by(ImageModel.id.asc())
                .all()
            )

            for idx, image in enumerate(images):
                # Check if membership already exists
                existing_membership = (
                    db.query(ImageVariantGroupMembership)
                    .filter(
                        ImageVariantGroupMembership.image_id == image.id,
                        ImageVariantGroupMembership.group_id == group.id,
                    )
                    .first()
                )
                if existing_membership is None:
                    membership = ImageVariantGroupMembership(
                        image_id=image.id,
                        group_id=group.id,
                        role_in_group="primary" if idx == 0 else "member",
                        sort_index=idx,
                        source="auto_hash",
                    )
                    db.add(membership)
                    created_memberships += 1

            # Set cover image to the first (oldest) image
            if images and group.cover_image_id is None:
                group.cover_image_id = images[0].id

        db.commit()
        print("✅ Backfill complete:")
        print(f"   Groups created: {created_groups}")
        print(f"   Groups already existed: {existing_groups}")
        print(f"   Memberships created: {created_memberships}")


def backfill_civitai_multi_resource_groups():
    """Create civitai_multi_resource variant groups for images with the same civitai_hash
    but different civitai_image_ids (video + still resources from CivitAI)."""
    with SessionLocal() as db:
        # Find civitai_hash values with multiple distinct civitai_image_ids
        hash_counts = (
            db.query(
                ImageModel.civitai_hash,
                func.count(func.distinct(ImageModel.civitai_image_id)).label("variant_count"),
            )
            .filter(
                ImageModel.image_status == "active",
                ImageModel.civitai_hash.isnot(None),
                ImageModel.civitai_hash != "",
                ImageModel.civitai_image_id.isnot(None),
            )
            .group_by(ImageModel.civitai_hash)
            .having(func.count(func.distinct(ImageModel.civitai_image_id)) > 1)
            .all()
        )

        print(f"\nFound {len(hash_counts)} civitai_hash values with multi-resource images")

        created_groups = 0
        created_memberships = 0

        for civitai_hash, count in hash_counts:
            group_key = f"civitai:{civitai_hash}"

            group = (
                db.query(VariantGroup)
                .filter(VariantGroup.group_key == group_key)
                .first()
            )
            if group is None:
                group = VariantGroup(
                    group_key=group_key,
                    group_type="civitai_multi_resource",
                    group_label=f"CivitAI multi-resource ({count} resources)",
                    cover_preference="sort_order",
                )
                db.add(group)
                db.flush()
                created_groups += 1

            images = (
                db.query(ImageModel)
                .filter(
                    ImageModel.civitai_hash == civitai_hash,
                    ImageModel.image_status == "active",
                )
                .order_by(ImageModel.id.asc())
                .all()
            )

            for idx, image in enumerate(images):
                existing_membership = (
                    db.query(ImageVariantGroupMembership)
                    .filter(
                        ImageVariantGroupMembership.image_id == image.id,
                        ImageVariantGroupMembership.group_id == group.id,
                    )
                    .first()
                )
                if existing_membership is None:
                    membership = ImageVariantGroupMembership(
                        image_id=image.id,
                        group_id=group.id,
                        role_in_group="primary" if idx == 0 else "member",
                        sort_index=idx,
                        source="auto_civitai",
                    )
                    db.add(membership)
                    created_memberships += 1

            if images and group.cover_image_id is None:
                group.cover_image_id = images[0].id

        db.commit()
        print("✅ CivitAI multi-resource backfill complete:")
        print(f"   Groups created: {created_groups}")
        print(f"   Memberships created: {created_memberships}")


def main():
    print("Creating tables if they don't exist...")
    Base.metadata.create_all(bind=engine, checkfirst=True)

    print("\n=== Backfilling hash duplicate groups ===")
    backfill_hash_duplicate_groups()

    print("\n=== Backfilling CivitAI multi-resource groups ===")
    backfill_civitai_multi_resource_groups()

    print("\n✅ All backfill operations complete!")


if __name__ == "__main__":
    main()
