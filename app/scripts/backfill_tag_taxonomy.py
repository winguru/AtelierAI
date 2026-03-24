#!/usr/bin/env python3
"""Backfill legacy tags into concept taxonomy tables.

This script is intentionally idempotent: it can be run multiple times safely.
It migrates from legacy `tags` to:
- concepts
- concept_aliases
- authority_terms (under `user` authority)
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Optional, cast

from path_setup import PROJECT_ROOT  # noqa: F401  # side effect: adds repo paths
from sqlalchemy.orm import Session

from backend.database import SessionLocal
from backend.models import (
    AuthorityTerm,
    Concept,
    ConceptAlias,
    ImageModel,
    Tag,
    TagAuthority,
)


SPACE_RE = re.compile(r"\s+")
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


@dataclass
class BackfillStats:
    source_mode: str = "legacy_tags"
    tags_scanned: int = 0
    metadata_tags_scanned: int = 0
    concepts_created: int = 0
    concepts_reused: int = 0
    aliases_created: int = 0
    aliases_reused: int = 0
    authority_terms_created: int = 0
    authority_terms_updated: int = 0
    authority_name_collisions: int = 0


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_tag_name(name: str) -> str:
    """Normalize a tag name for matching while keeping concept naming readable."""
    text = (name or "").strip().replace("_", " ")
    text = SPACE_RE.sub(" ", text)
    return text.lower()


def slugify(text: str) -> str:
    normalized = NON_ALNUM_RE.sub("-", text.lower()).strip("-")
    return normalized or "concept"


def ensure_unique_slug(db: Session, base_slug: str) -> str:
    slug = base_slug
    idx = 2
    while db.query(Concept.id).filter(Concept.slug == slug).first() is not None:
        slug = f"{base_slug}-{idx}"
        idx += 1
    return slug


def get_or_create_user_authority(db: Session) -> TagAuthority:
    authority = db.query(TagAuthority).filter(TagAuthority.name == "user").first()
    if authority is not None:
        return authority

    authority = TagAuthority(
        name="user",
        description="User-curated local tags and concepts.",
        is_external=False,
        base_url=None,
    )
    db.add(authority)
    db.flush()
    return authority


def get_or_create_authority(db: Session, name: str) -> TagAuthority:
    authority = db.query(TagAuthority).filter(TagAuthority.name == name).first()
    if authority is not None:
        return authority

    defaults = {
        "civitai": {
            "description": "CivitAI native tag authority and IDs.",
            "is_external": True,
            "base_url": "https://civitai.com",
        },
        "danbooru": {
            "description": "Danbooru tag authority and IDs.",
            "is_external": True,
            "base_url": "https://danbooru.donmai.us",
        },
    }
    payload = defaults.get(
        name,
        {
            "description": f"Auto-created authority '{name}'.",
            "is_external": False,
            "base_url": None,
        },
    )
    authority = TagAuthority(name=name, **payload)
    db.add(authority)
    db.flush()
    return authority


def iter_legacy_tag_seeds(db: Session, limit: Optional[int]) -> Iterable[tuple[str, str, str]]:
    query = db.query(Tag).order_by(Tag.id.asc())
    if limit is not None and limit > 0:
        query = query.limit(limit)

    for tag in query.all():
        raw_name = (tag.name or "").strip()
        if not raw_name:
            continue
        yield raw_name, "user", str(tag.id)


def iter_metadata_tag_seeds(db: Session, limit: Optional[int]) -> Iterable[tuple[str, str, str]]:
    seen: set[tuple[str, str]] = set()
    yielded = 0
    images = db.query(ImageModel).order_by(ImageModel.id.asc()).all()

    for image in images:
        meta = image.json_metadata or {}
        if not isinstance(meta, dict):
            continue

        civitai_payload = meta.get("civitai")
        if isinstance(civitai_payload, dict):
            civitai_tags = civitai_payload.get("tags") or []
            if isinstance(civitai_tags, list):
                for item in civitai_tags:
                    name: Optional[str] = None
                    external_tag_id: Optional[str] = None
                    if isinstance(item, str):
                        name = item.strip()
                    elif isinstance(item, dict):
                        if isinstance(item.get("name"), str):
                            name = item["name"].strip()
                        ext_id = item.get("id")
                        if ext_id is not None:
                            external_tag_id = str(ext_id)

                    if not name:
                        continue
                    if external_tag_id is None:
                        external_tag_id = f"name:{normalize_tag_name(name)}"

                    dedupe_key = ("civitai", external_tag_id)
                    if dedupe_key in seen:
                        continue
                    seen.add(dedupe_key)
                    yield name, "civitai", external_tag_id
                    yielded += 1
                    if limit is not None and limit > 0 and yielded >= limit:
                        return

        prompt_tags = meta.get("prompt_tags")
        if isinstance(prompt_tags, list):
            for item in prompt_tags:
                name: Optional[str] = None
                if isinstance(item, str):
                    name = item.strip()
                elif isinstance(item, dict):
                    raw_name = item.get("name") or item.get("tag") or item.get("label")
                    if isinstance(raw_name, str):
                        name = raw_name.strip()

                if not name:
                    continue

                external_tag_id = f"prompt:{normalize_tag_name(name)}"
                dedupe_key = ("user", external_tag_id)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                yield name, "user", external_tag_id
                yielded += 1
                if limit is not None and limit > 0 and yielded >= limit:
                    return


def get_or_create_concept(db: Session, canonical_name: str, stats: BackfillStats) -> Concept:
    concept = db.query(Concept).filter(Concept.canonical_name == canonical_name).first()
    if concept is not None:
        stats.concepts_reused += 1
        return concept

    base_slug = slugify(canonical_name)
    slug = ensure_unique_slug(db, base_slug)

    concept = Concept(
        canonical_name=canonical_name,
        slug=slug,
        status="active",
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(concept)
    db.flush()
    stats.concepts_created += 1
    return concept


def ensure_alias(
    db: Session,
    concept: Concept,
    alias: str,
    alias_type: str,
    is_preferred: bool,
    stats: BackfillStats,
    authority_id: Optional[int] = None,
    external_tag_id: Optional[str] = None,
) -> None:
    normalized_alias = normalize_tag_name(alias)
    existing = (
        db.query(ConceptAlias)
        .filter(
            ConceptAlias.concept_id == concept.id,
            ConceptAlias.normalized_alias == normalized_alias,
        )
        .first()
    )
    if existing is not None:
        existing_any = cast(Any, existing)
        # Keep idempotence and enrich missing metadata when available.
        changed = False
        if authority_id is not None and existing_any.authority_id is None:
            existing_any.authority_id = authority_id
            changed = True
        if external_tag_id is not None and not existing_any.external_tag_id:
            existing_any.external_tag_id = external_tag_id
            changed = True
        if is_preferred and not existing_any.is_preferred:
            existing_any.is_preferred = True
            changed = True
        if changed:
            cast(Any, concept).updated_at = utcnow()
        stats.aliases_reused += 1
        return

    alias_row = ConceptAlias(
        concept_id=concept.id,
        alias=alias,
        normalized_alias=normalized_alias,
        alias_type=alias_type,
        is_preferred=is_preferred,
        authority_id=authority_id,
        external_tag_id=external_tag_id,
    )
    db.add(alias_row)
    db.flush()
    stats.aliases_created += 1


def upsert_authority_term(
    db: Session,
    authority_id: int,
    external_tag_id: str,
    external_name: str,
    normalized_external_name: str,
    concept_id: int,
    stats: BackfillStats,
) -> None:
    by_external_id = (
        db.query(AuthorityTerm)
        .filter(
            AuthorityTerm.authority_id == authority_id,
            AuthorityTerm.external_tag_id == external_tag_id,
        )
        .first()
    )
    if by_external_id is not None:
        by_external_id_any = cast(Any, by_external_id)
        changed = False
        if by_external_id_any.external_name != external_name:
            by_external_id_any.external_name = external_name
            changed = True
        if by_external_id_any.normalized_external_name != normalized_external_name:
            by_external_id_any.normalized_external_name = normalized_external_name
            changed = True
        if by_external_id_any.concept_id != concept_id:
            by_external_id_any.concept_id = concept_id
            changed = True
        by_external_id_any.last_seen_at = utcnow()
        if changed:
            by_external_id_any.updated_at = utcnow()
            stats.authority_terms_updated += 1
        return

    by_name = (
        db.query(AuthorityTerm)
        .filter(
            AuthorityTerm.authority_id == authority_id,
            AuthorityTerm.normalized_external_name == normalized_external_name,
        )
        .first()
    )
    if by_name is not None:
        by_name_any = cast(Any, by_name)
        # Avoid violating uq_authority_external_name while still linking concept.
        # We preserve the first seen external ID/name and only update concept link.
        if by_name_any.concept_id != concept_id:
            by_name_any.concept_id = concept_id
            by_name_any.updated_at = utcnow()
            stats.authority_terms_updated += 1
        by_name_any.last_seen_at = utcnow()
        stats.authority_name_collisions += 1
        return

    row = AuthorityTerm(
        authority_id=authority_id,
        external_tag_id=external_tag_id,
        external_name=external_name,
        normalized_external_name=normalized_external_name,
        concept_id=concept_id,
        created_at=utcnow(),
        updated_at=utcnow(),
        last_seen_at=utcnow(),
        metadata_json={"origin": "legacy_tags_backfill"},
    )
    db.add(row)
    db.flush()
    stats.authority_terms_created += 1


def run_backfill(db: Session, dry_run: bool, limit: Optional[int]) -> BackfillStats:
    stats = BackfillStats()
    get_or_create_user_authority(db)

    legacy_tag_count = db.query(Tag).count()
    if legacy_tag_count > 0:
        seed_iter = iter_legacy_tag_seeds(db, limit=limit)
        stats.source_mode = "legacy_tags"
    else:
        seed_iter = iter_metadata_tag_seeds(db, limit=limit)
        stats.source_mode = "image_metadata"

    for raw_name, authority_name, external_tag_id in seed_iter:
        stats.tags_scanned += 1

        if stats.source_mode == "image_metadata":
            stats.metadata_tags_scanned += 1

        original_name = (raw_name or "").strip()
        if not original_name:
            continue

        authority = get_or_create_authority(db, authority_name)

        canonical_name = normalize_tag_name(original_name)
        concept = get_or_create_concept(db, canonical_name, stats)

        ensure_alias(
            db=db,
            concept=concept,
            alias=canonical_name,
            alias_type="canonical",
            is_preferred=True,
            stats=stats,
        )

        if normalize_tag_name(original_name) != canonical_name or original_name != canonical_name:
            ensure_alias(
                db=db,
                concept=concept,
                alias=original_name,
                alias_type="legacy_tag",
                is_preferred=False,
                stats=stats,
                authority_id=cast(int, cast(Any, authority).id),
                external_tag_id=external_tag_id,
            )

        upsert_authority_term(
            db=db,
            authority_id=cast(int, cast(Any, authority).id),
            external_tag_id=external_tag_id,
            external_name=original_name,
            normalized_external_name=normalize_tag_name(original_name),
            concept_id=cast(int, cast(Any, concept).id),
            stats=stats,
        )

    if dry_run:
        db.rollback()
    else:
        db.commit()

    return stats


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill legacy tags into concept taxonomy tables."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and print changes, then rollback instead of commit.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process only the first N tags (ordered by id).",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db = SessionLocal()
    try:
        stats = run_backfill(db=db, dry_run=args.dry_run, limit=args.limit)
    except Exception as exc:
        db.rollback()
        print(f"Backfill failed: {exc}")
        return 1
    finally:
        db.close()

    mode = "DRY RUN" if args.dry_run else "COMMIT"
    print(f"Tag taxonomy backfill complete [{mode}].")
    print(f"  source_mode: {stats.source_mode}")
    print(f"  tags_scanned: {stats.tags_scanned}")
    print(f"  metadata_tags_scanned: {stats.metadata_tags_scanned}")
    print(f"  concepts_created: {stats.concepts_created}")
    print(f"  concepts_reused: {stats.concepts_reused}")
    print(f"  aliases_created: {stats.aliases_created}")
    print(f"  aliases_reused: {stats.aliases_reused}")
    print(f"  authority_terms_created: {stats.authority_terms_created}")
    print(f"  authority_terms_updated: {stats.authority_terms_updated}")
    print(f"  authority_name_collisions: {stats.authority_name_collisions}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
