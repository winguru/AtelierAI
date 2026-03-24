#!/usr/bin/env python3
"""Remove root concepts that have no child concepts.

Default mode is dry-run. Use --apply to execute changes.

By default this script also requires the root concept to have zero associated
authority terms (safer). Pass --include-tagged to also delete tagged root leaves.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

from path_setup import PROJECT_ROOT  # noqa: F401  # side effect: import path setup
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.database import SessionLocal
from backend.models import (
    AuthorityTerm,
    Concept,
    ConceptAlias,
    ConceptGroupMembership,
    ImageConceptObservation,
)


@dataclass
class Candidate:
    concept_id: int
    canonical_name: str
    authority_term_count: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Remove root concepts with no child concepts",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes (default is dry-run)",
    )
    parser.add_argument(
        "--include-tagged",
        action="store_true",
        help="Also delete root leaf concepts that still have authority terms",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional max number of concepts to process (0 = no limit)",
    )
    return parser.parse_args()


def find_candidates(db: Session, include_tagged: bool) -> list[Candidate]:
    child_count_sq = (
        db.query(
            Concept.parent_concept_id.label("pid"),
            func.count(Concept.id).label("child_count"),
        )
        .group_by(Concept.parent_concept_id)
        .subquery()
    )

    term_count_sq = (
        db.query(
            AuthorityTerm.concept_id.label("cid"),
            func.count(AuthorityTerm.id).label("term_count"),
        )
        .group_by(AuthorityTerm.concept_id)
        .subquery()
    )

    rows = (
        db.query(
            Concept.id,
            Concept.canonical_name,
            func.coalesce(term_count_sq.c.term_count, 0),
        )
        .outerjoin(child_count_sq, child_count_sq.c.pid == Concept.id)
        .outerjoin(term_count_sq, term_count_sq.c.cid == Concept.id)
        .filter(Concept.parent_concept_id.is_(None))
        .filter(func.coalesce(child_count_sq.c.child_count, 0) == 0)
        .order_by(Concept.id.asc())
        .all()
    )

    candidates: list[Candidate] = []
    for concept_id, canonical_name, term_count in rows:
        term_count_i = int(term_count or 0)
        if not include_tagged and term_count_i > 0:
            continue
        candidates.append(
            Candidate(
                concept_id=int(concept_id),
                canonical_name=str(canonical_name or ""),
                authority_term_count=term_count_i,
            )
        )
    return candidates


def delete_concepts(db: Session, candidates: list[Candidate]) -> tuple[int, int, int, int, int]:
    concept_ids = [c.concept_id for c in candidates]
    if not concept_ids:
        return (0, 0, 0, 0, 0)

    # Unlink optional references first.
    terms_unlinked = (
        db.query(AuthorityTerm)
        .filter(AuthorityTerm.concept_id.in_(concept_ids))
        .update({AuthorityTerm.concept_id: None}, synchronize_session=False)
    )

    # Delete dependent rows that require a concept_id.
    aliases_deleted = (
        db.query(ConceptAlias)
        .filter(ConceptAlias.concept_id.in_(concept_ids))
        .delete(synchronize_session=False)
    )
    memberships_deleted = (
        db.query(ConceptGroupMembership)
        .filter(ConceptGroupMembership.concept_id.in_(concept_ids))
        .delete(synchronize_session=False)
    )
    observations_deleted = (
        db.query(ImageConceptObservation)
        .filter(ImageConceptObservation.concept_id.in_(concept_ids))
        .delete(synchronize_session=False)
    )
    concepts_deleted = (
        db.query(Concept)
        .filter(Concept.id.in_(concept_ids))
        .delete(synchronize_session=False)
    )

    return (
        int(concepts_deleted or 0),
        int(terms_unlinked or 0),
        int(aliases_deleted or 0),
        int(memberships_deleted or 0),
        int(observations_deleted or 0),
    )


def main() -> int:
    args = parse_args()

    db = SessionLocal()
    try:
        candidates = find_candidates(db, include_tagged=bool(args.include_tagged))
        if args.limit and args.limit > 0:
            candidates = candidates[: args.limit]

        print("Root leaf concept cleanup")
        print(f"Mode: {'APPLY' if args.apply else 'DRY-RUN'}")
        print(f"Include tagged concepts: {bool(args.include_tagged)}")
        print(f"Candidates: {len(candidates)}")

        preview = candidates[:20]
        if preview:
            print("Preview (first 20):")
            for item in preview:
                suffix = f" terms={item.authority_term_count}" if item.authority_term_count else ""
                print(f"  - #{item.concept_id} {item.canonical_name}{suffix}")

        if not args.apply:
            print("No changes applied. Re-run with --apply to execute.")
            return 0

        deleted, terms_unlinked, aliases_deleted, memberships_deleted, observations_deleted = delete_concepts(db, candidates)
        db.commit()

        print("Completed:")
        print(f"  concepts deleted: {deleted}")
        print(f"  authority terms unlinked: {terms_unlinked}")
        print(f"  concept aliases deleted: {aliases_deleted}")
        print(f"  group memberships deleted: {memberships_deleted}")
        print(f"  observations deleted: {observations_deleted}")
        return 0
    except Exception as exc:
        db.rollback()
        print(f"ERROR: {exc}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
