#!/usr/bin/env python3
"""Scrape Danbooru tags and import them into the taxonomy database.

Fetches all active (non-deprecated) Danbooru tags ordered by popularity via the
public JSON API and upserts them as taxonomy concepts + authority terms.

Each Danbooru tag produces:
  - A Concept (canonical_name = normalized tag name)
  - A canonical ConceptAlias
  - A 'danbooru_word' ConceptAlias for each entry in the tag's `words` array
  - An AuthorityTerm (authority = 'danbooru', external_tag_id = str(danbooru id))

The operation is fully idempotent; re-running updates `last_seen_at`,
`post_count` metadata, and links any previously unlinked concepts.

Usage:
    python scripts/import_danbooru_tags.py
    python scripts/import_danbooru_tags.py --dry-run
    python scripts/import_danbooru_tags.py --pages 5
    python scripts/import_danbooru_tags.py --start-page 3 --pages 10
    python scripts/import_danbooru_tags.py --commit-every 500
    python scripts/import_danbooru_tags.py --no-new-concepts
    python scripts/import_danbooru_tags.py --fresh-start
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Optional, cast
from urllib.parse import quote

import requests

from path_setup import PROJECT_ROOT  # noqa: F401  # side effect: adds repo paths
from sqlalchemy.orm import Session

from backend.database import SessionLocal
from backend.models import AuthorityTerm, Concept, ConceptAlias, TagAuthority


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DANBOORU_BASE_URL = (
    "https://danbooru.donmai.us/tags.json"
    "?limit=1000"
    "&search[hide_empty]=yes"
    "&search[is_deprecated]=no"
    "&search[order]=count"
    "&page={page}"
)

# Danbooru category IDs
_DANBOORU_CATEGORIES: dict[int, str] = {
    0: "general",
    1: "artist",
    3: "copyright",
    4: "character",
    5: "meta",
}

# Polite delay between HTTP requests so we don't hammer the server.
_REQUEST_DELAY_SECONDS = 0.5
_REQUEST_TIMEOUT_SECONDS = 30
_MAX_RETRIES = 3
_RETRY_BACKOFF_SECONDS = 5.0

SPACE_RE = re.compile(r"\s+")
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@dataclass
class ImportStats:
    pages_fetched: int = 0
    tags_fetched: int = 0
    tags_skipped: int = 0
    concepts_created: int = 0
    concepts_reused: int = 0
    concepts_skipped: int = 0
    aliases_created: int = 0
    aliases_reused: int = 0
    authority_terms_created: int = 0
    authority_terms_updated: int = 0
    name_collisions: int = 0
    http_errors: int = 0
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize_danbooru_name(name: str) -> str:
    """Normalize a Danbooru tag name to a canonical concept name.

    Replaces underscores with spaces, collapses whitespace, lowercases.
    """
    text = (name or "").strip().replace("_", " ")
    text = SPACE_RE.sub(" ", text).lower()
    return text


def normalize_danbooru_wiki_url(url: str) -> str:
    text = str(url or "").strip()
    if not text:
        return ""
    text = re.sub(r"/wiki\s+pages/", "/wiki_pages/", text, flags=re.IGNORECASE)
    return text


def sanitize_danbooru_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(metadata or {})
    wiki_url = cleaned.get("wiki_url")
    if wiki_url is not None:
        cleaned["wiki_url"] = normalize_danbooru_wiki_url(str(wiki_url))

    raw_examples = cleaned.get("examples")
    if isinstance(raw_examples, list):
        normalized_examples: list[str] = []
        seen: set[str] = set()
        for item in raw_examples:
            text = normalize_danbooru_wiki_url(str(item))
            if not text or text in seen:
                continue
            seen.add(text)
            normalized_examples.append(text)
        cleaned["examples"] = normalized_examples
    return cleaned


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


# ---------------------------------------------------------------------------
# HTTP fetching
# ---------------------------------------------------------------------------

def fetch_danbooru_page(
    session: requests.Session,
    page: int,
    stats: ImportStats,
) -> list[dict[str, Any]]:
    """Fetch one page of Danbooru tags. Returns a list of tag dicts or [] on error."""
    url = DANBOORU_BASE_URL.format(page=page)

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = session.get(url, timeout=_REQUEST_TIMEOUT_SECONDS)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, list):
                stats.errors.append(f"Page {page}: unexpected response type {type(data).__name__}")
                return []
            return data
        except requests.exceptions.HTTPError as exc:
            stats.http_errors += 1
            if exc.response is not None and exc.response.status_code == 429:
                wait = _RETRY_BACKOFF_SECONDS * attempt
                print(f"  [rate-limit] page {page}, waiting {wait:.1f}s before retry {attempt}/{_MAX_RETRIES}")
                time.sleep(wait)
            elif attempt < _MAX_RETRIES:
                time.sleep(_RETRY_BACKOFF_SECONDS)
            else:
                msg = f"Page {page}: HTTP error after {_MAX_RETRIES} attempts: {exc}"
                stats.errors.append(msg)
                print(f"  [error] {msg}")
                return []
        except Exception as exc:
            stats.http_errors += 1
            if attempt < _MAX_RETRIES:
                time.sleep(_RETRY_BACKOFF_SECONDS)
            else:
                msg = f"Page {page}: request failed after {_MAX_RETRIES} attempts: {exc}"
                stats.errors.append(msg)
                print(f"  [error] {msg}")
                return []

    return []


def iter_danbooru_pages(
    start_page: int,
    max_pages: Optional[int],
    stats: ImportStats,
) -> Iterator[tuple[int, list[dict[str, Any]]]]:
    """Yield (page_number, tag_list) until the API returns an empty page."""
    http_session = requests.Session()
    http_session.headers["User-Agent"] = "AtelierAI-TaxonomyImporter/1.0"

    end_page = (start_page + max_pages - 1) if max_pages is not None else 1000
    for page in range(start_page, end_page + 1):
        tags = fetch_danbooru_page(http_session, page, stats)
        if not tags:
            # Empty page means we've exhausted all results.
            print(f"  [done] page {page} returned no results — stopping.")
            return
        stats.pages_fetched += 1
        yield page, tags
        time.sleep(_REQUEST_DELAY_SECONDS)


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

def get_or_create_danbooru_authority(db: Session) -> TagAuthority:
    existing = db.query(TagAuthority).filter(TagAuthority.name == "danbooru").first()
    if existing is not None:
        return existing

    authority = TagAuthority(
        name="danbooru",
        description="Danbooru tag authority and IDs.",
        is_external=True,
        base_url="https://danbooru.donmai.us",
    )
    db.add(authority)
    db.flush()
    print("  Created 'danbooru' TagAuthority.")
    return authority


def get_or_create_concept(
    db: Session,
    canonical_name: str,
    stats: ImportStats,
) -> Concept:
    existing = db.query(Concept).filter(Concept.canonical_name == canonical_name).first()
    if existing is not None:
        stats.concepts_reused += 1
        return existing

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
    stats: ImportStats,
    authority_id: Optional[int] = None,
    external_tag_id: Optional[str] = None,
) -> None:
    normalized_alias = normalize_danbooru_name(alias)
    if not normalized_alias:
        return

    existing = (
        db.query(ConceptAlias)
        .filter(
            ConceptAlias.concept_id == cast(Any, concept).id,
            ConceptAlias.normalized_alias == normalized_alias,
        )
        .first()
    )

    if existing is not None:
        existing_any = cast(Any, existing)
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

    row = ConceptAlias(
        concept_id=cast(Any, concept).id,
        alias=alias,
        normalized_alias=normalized_alias,
        alias_type=alias_type,
        is_preferred=is_preferred,
        authority_id=authority_id,
        external_tag_id=external_tag_id,
    )
    db.add(row)
    db.flush()
    stats.aliases_created += 1


def upsert_authority_term(
    db: Session,
    authority_id: int,
    external_tag_id: str,
    external_name: str,
    normalized_external_name: str,
    concept_id: Optional[int],
    metadata: dict[str, Any],
    stats: ImportStats,
) -> None:
    metadata = sanitize_danbooru_metadata(metadata)

    # Try to find by exact external_tag_id first (most reliable).
    by_id = (
        db.query(AuthorityTerm)
        .filter(
            AuthorityTerm.authority_id == authority_id,
            AuthorityTerm.external_tag_id == external_tag_id,
        )
        .first()
    )
    if by_id is not None:
        row = cast(Any, by_id)
        changed = False
        if row.concept_id != concept_id:
            row.concept_id = concept_id
            changed = True
        if row.external_name != external_name:
            row.external_name = external_name
            changed = True
        if row.normalized_external_name != normalized_external_name:
            row.normalized_external_name = normalized_external_name
            changed = True
        # Always refresh metadata (post_count changes over time).
        existing_meta = sanitize_danbooru_metadata(row.metadata_json or {})
        merged_meta = sanitize_danbooru_metadata({**existing_meta, **metadata})
        row.metadata_json = merged_meta
        row.last_seen_at = utcnow()
        if changed:
            row.updated_at = utcnow()
            stats.authority_terms_updated += 1
        return

    # Fall back to name-based lookup to avoid unique constraint violations.
    by_name = (
        db.query(AuthorityTerm)
        .filter(
            AuthorityTerm.authority_id == authority_id,
            AuthorityTerm.normalized_external_name == normalized_external_name,
        )
        .first()
    )
    if by_name is not None:
        row = cast(Any, by_name)
        existing_meta = sanitize_danbooru_metadata(row.metadata_json or {})
        merged_meta = sanitize_danbooru_metadata({**existing_meta, **metadata})
        row.metadata_json = merged_meta
        if row.concept_id != concept_id:
            row.concept_id = concept_id
            row.updated_at = utcnow()
            stats.authority_terms_updated += 1
        row.last_seen_at = utcnow()
        stats.name_collisions += 1
        return

    row = AuthorityTerm(
        authority_id=authority_id,
        external_tag_id=external_tag_id,
        external_name=external_name,
        normalized_external_name=normalized_external_name,
        concept_id=concept_id,
        metadata_json=metadata,
        created_at=utcnow(),
        updated_at=utcnow(),
        last_seen_at=utcnow(),
    )
    db.add(row)
    db.flush()
    stats.authority_terms_created += 1


# ---------------------------------------------------------------------------
# Core import logic
# ---------------------------------------------------------------------------

def process_tag(
    db: Session,
    tag: dict[str, Any],
    authority_id: int,
    stats: ImportStats,
    *,
    no_new_concepts: bool = False,
) -> None:
    raw_id = tag.get("id")
    raw_name = tag.get("name") or ""
    post_count = tag.get("post_count") or 0
    is_deprecated = bool(tag.get("is_deprecated", False))
    words: list[str] = tag.get("words") or []
    category_int = tag.get("category")

    if not raw_id or not raw_name:
        stats.tags_skipped += 1
        return

    if is_deprecated:
        stats.tags_skipped += 1
        return

    external_tag_id = str(raw_id)
    canonical_name = normalize_danbooru_name(raw_name)
    if not canonical_name:
        stats.tags_skipped += 1
        return

    stats.tags_fetched += 1

    # Build metadata payload for the authority term.
    metadata: dict[str, Any] = {
        "origin": "danbooru_scrape",
        "post_count": post_count,
    }
    if category_int is not None:
        metadata["category"] = category_int
        metadata["category_name"] = _DANBOORU_CATEGORIES.get(int(category_int), "unknown")
    if words:
        metadata["words"] = words

    # Add wiki URL example. Keep tag text as-is and URL-encode it.
    # Only the base path is normalized to '/wiki_pages/'.
    encoded_tag_name = quote(raw_name, safe='')
    metadata["wiki_url"] = f"https://danbooru.donmai.us/wiki_pages/{encoded_tag_name}"

    concept_id: Optional[int] = None

    if no_new_concepts:
        # Look for an existing concept with the same canonical name
        existing_concept = db.query(Concept).filter(
            Concept.canonical_name == canonical_name
        ).first()
        if existing_concept is not None:
            concept_id = cast(int, cast(Any, existing_concept).id)
            stats.concepts_reused += 1
        else:
            stats.concepts_skipped += 1
    else:
        # Concept
        concept = get_or_create_concept(db, canonical_name, stats)
        concept_id = cast(int, cast(Any, concept).id)

        # Primary canonical alias
        ensure_alias(
            db=db,
            concept=concept,
            alias=canonical_name,
            alias_type="canonical",
            is_preferred=True,
            stats=stats,
            authority_id=authority_id,
            external_tag_id=external_tag_id,
        )

        # Word aliases (Danbooru search words — stored but not preferred)
        for word in words:
            normalized_word = normalize_danbooru_name(word)
            # Skip if it's the same as the canonical name to avoid duplication.
            if not normalized_word or normalized_word == canonical_name:
                continue
            ensure_alias(
                db=db,
                concept=concept,
                alias=normalized_word,
                alias_type="danbooru_word",
                is_preferred=False,
                stats=stats,
                authority_id=authority_id,
                external_tag_id=external_tag_id,
            )

    # Authority term
    upsert_authority_term(
        db=db,
        authority_id=authority_id,
        external_tag_id=external_tag_id,
        external_name=canonical_name,
        normalized_external_name=canonical_name,
        concept_id=concept_id,
        metadata=metadata,
        stats=stats,
    )


def run_import(
    db: Session,
    *,
    dry_run: bool,
    start_page: int,
    max_pages: Optional[int],
    commit_every: int,
    no_new_concepts: bool = False,
    fresh_start: bool = False,
) -> ImportStats:
    stats = ImportStats()

    authority = get_or_create_danbooru_authority(db)
    authority_id = cast(int, cast(Any, authority).id)

    if fresh_start and not dry_run:
        # Delete all existing Danbooru authority terms before import
        deleted_count = db.query(AuthorityTerm).filter(
            AuthorityTerm.authority_id == authority_id
        ).delete(synchronize_session=False)
        db.commit()
        print(f"  Deleted {deleted_count:,} existing Danbooru authority terms.")

    tag_count_since_commit = 0

    for page, tags in iter_danbooru_pages(start_page, max_pages, stats):
        page_processed = 0
        for tag in tags:
            process_tag(db, tag, authority_id, stats, no_new_concepts=no_new_concepts)
            tag_count_since_commit += 1
            page_processed += 1

            if not dry_run and commit_every > 0 and tag_count_since_commit >= commit_every:
                db.commit()
                tag_count_since_commit = 0

        category_counts = _DANBOORU_CATEGORIES  # noqa: just for display help
        print(
            f"  page {page:4d} | {page_processed:4d} tags | "
            f"total fetched {stats.tags_fetched:7,d} | "
            f"concepts created {stats.concepts_created:7,d} | "
            f"terms created {stats.authority_terms_created:7,d}"
        )

    # Final commit for any remainder.
    if dry_run:
        db.rollback()
    else:
        db.commit()

    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Scrape Danbooru tags and import them as taxonomy concepts + authority terms. "
            "Safe to re-run; all operations are idempotent."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Process and print stats but roll back all DB changes.",
    )
    parser.add_argument(
        "--start-page",
        type=int,
        default=1,
        metavar="N",
        help="First page to fetch (1-indexed). Default: 1.",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=None,
        metavar="N",
        help="Maximum number of pages to fetch. Default: fetch until empty page.",
    )
    parser.add_argument(
        "--commit-every",
        type=int,
        default=200,
        metavar="N",
        help="Commit to DB after processing every N tags. Default: 200.",
    )
    parser.add_argument(
        "--no-new-concepts",
        action="store_true",
        help=(
            "Do not create new concepts. If a concept with the same name as the Danbooru tag "
            "(with underscores converted to spaces) already exists, associate the tag to it. "
            "Otherwise skip concept association. Useful for bulk tag ingestion."
        ),
    )
    parser.add_argument(
        "--fresh-start",
        action="store_true",
        help="Delete all existing Danbooru tags before importing. Use with caution.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    mode = "DRY RUN" if args.dry_run else "LIVE"
    print(f"Danbooru tag import [{mode}]")
    print(f"  start_page    : {args.start_page}")
    print(f"  max_pages     : {args.pages or 'unlimited (up to 1000)'}")
    print(f"  commit_every  : {args.commit_every}")
    print(f"  no_new_concepts: {args.no_new_concepts}")
    print(f"  fresh_start   : {args.fresh_start}")
    print()

    db = SessionLocal()
    try:
        stats = run_import(
            db,
            dry_run=args.dry_run,
            start_page=args.start_page,
            max_pages=args.pages,
            commit_every=args.commit_every,
            no_new_concepts=args.no_new_concepts,
            fresh_start=args.fresh_start,
        )
    except KeyboardInterrupt:
        db.rollback()
        print("\n[interrupted] DB rolled back for the current uncommitted batch.")
        return 130
    except Exception as exc:
        db.rollback()
        print(f"\n[fatal] Import failed: {exc}")
        raise
    finally:
        db.close()

    print()
    print(f"Danbooru tag import complete [{mode}].")
    print(f"  pages_fetched         : {stats.pages_fetched:,}")
    print(f"  tags_fetched          : {stats.tags_fetched:,}")
    print(f"  tags_skipped          : {stats.tags_skipped:,}")
    print(f"  concepts_created      : {stats.concepts_created:,}")
    print(f"  concepts_reused       : {stats.concepts_reused:,}")
    print(f"  concepts_skipped      : {stats.concepts_skipped:,}")
    print(f"  aliases_created       : {stats.aliases_created:,}")
    print(f"  aliases_reused        : {stats.aliases_reused:,}")
    print(f"  authority_terms_created: {stats.authority_terms_created:,}")
    print(f"  authority_terms_updated: {stats.authority_terms_updated:,}")
    print(f"  name_collisions       : {stats.name_collisions:,}")
    print(f"  http_errors           : {stats.http_errors:,}")
    if stats.errors:
        print(f"\n  Errors ({len(stats.errors)}):")
        for err in stats.errors:
            print(f"    - {err}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
