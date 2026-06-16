# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/taxonomy-import.md
# ──────────────────────────────────────────────────────────────────────────────
"""Taxonomy snapshot import service.

Imports ``atelierai.taxonomy.snapshot`` v1 files — a flat JSON structure
containing authorities, concepts, aliases, authority_terms, and user_bindings
— into an existing AtelierAI database.

Design principles:
  * Enrich, don't overwrite.
  * Hard-stop on non-ephemeral data mismatches (report for investigation).
  * Merge ephemeral data (counts, timestamps) keeping larger/newer values.
  * Graft existing concept-tree children onto imported branches.
  * Stage unmatched user_bindings for later application by the ingestion pipeline.
  * Support dry-run and pre-import DB backup.
"""

from __future__ import annotations

import re
import shutil
import time
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from models import (
    AuthorityTerm,
    Concept,
    ConceptAlias,
    ImageModel,
    PendingUserBinding,
    TagAuthority,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SNAPSHOT_FORMAT = "atelierai.taxonomy.snapshot"
_SNAPSHOT_VERSION = 1
_BATCH_SIZE = 500


# ---------------------------------------------------------------------------
# Slug generation
# ---------------------------------------------------------------------------

def _generate_slug(canonical_name: str) -> str:
    """Normalise *canonical_name* into a URL-safe slug."""
    slug = unicodedata.normalize("NFKD", canonical_name)
    slug = slug.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    slug = slug.strip("-")
    return slug or "concept"


def _ensure_unique_slug(db: Session, base_slug: str) -> str:
    """Append ``-2``, ``-3``, … until the slug is unique."""
    slug = base_slug
    idx = 2
    while db.query(Concept.id).filter(Concept.slug == slug).first() is not None:
        slug = f"{base_slug}-{idx}"
        idx += 1
    return slug


# ---------------------------------------------------------------------------
# Metadata merge helpers
# ---------------------------------------------------------------------------

_EPHEMERAL_COUNT_FIELDS = {"post_count", "follower_count", "usage_count", "score"}
_EPHEMERAL_TIMESTAMP_FIELDS = {"last_seen", "updated_at", "created_at"}
# Local-observation booleans: keep existing value (local DB is authoritative).
_LOCAL_OBSERVATION_FIELDS = {"automated"}


def _merge_metadata(
    existing: dict[str, Any] | None,
    imported: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    """Merge two metadata dicts.

    * Count fields: keep the larger value.
    * Timestamp fields: keep the newer value.
    * All other fields: must be identical; any mismatch is a conflict.

    Returns ``(merged_dict, conflict_descriptions)``.
    """
    if existing is None:
        return dict(imported or {}), []
    if imported is None:
        return dict(existing), []

    merged: dict[str, Any] = dict(existing)
    conflicts: list[str] = []

    all_keys = set(existing.keys()) | set(imported.keys())
    for key in sorted(all_keys):
        ev = existing.get(key)
        iv = imported.get(key)

        if key in _EPHEMERAL_COUNT_FIELDS:
            # Keep the larger numeric value.
            try:
                en = float(ev) if ev is not None else 0
                im = float(iv) if iv is not None else 0
                merged[key] = en if en >= im else iv
            except (TypeError, ValueError):
                # Non-numeric — treat as regular field
                if ev != iv:
                    conflicts.append(f"metadata.{key}: existing={ev!r}, imported={iv!r}")
                merged[key] = ev
        elif key in _EPHEMERAL_TIMESTAMP_FIELDS:
            # Keep the newer value.
            newer = _newer_timestamp(ev, iv)
            if newer is not None:
                merged[key] = newer
        else:
            if ev is None and iv is not None:
                # Enrichment: existing field absent, imported has a value
                merged[key] = iv
            elif iv is None and ev is not None:
                # Keep existing value (import doesn't remove fields)
                pass
            elif key in _LOCAL_OBSERVATION_FIELDS:
                # Local observation: keep existing value
                pass
            elif ev != iv:
                conflicts.append(f"metadata.{key}: existing={ev!r}, imported={iv!r}")

    return merged, conflicts


def _newer_timestamp(a: Any, b: Any) -> Optional[str]:
    """Return the lexicographically newer timestamp string (or the non-None one)."""
    if a is None:
        return b
    if b is None:
        return a
    return a if str(a) >= str(b) else b


# ---------------------------------------------------------------------------
# Pre-flight validation
# ---------------------------------------------------------------------------

def validate_snapshot(data: dict[str, Any]) -> list[str]:  # noqa: C901
    """Validate internal referential integrity of *data*.

    Returns a list of error strings.  An empty list means the snapshot is valid.
    """
    errors: list[str] = []

    # Format / version
    fmt = data.get("format")
    if fmt != _SNAPSHOT_FORMAT:
        errors.append(f"Unsupported snapshot format: {fmt!r} (expected {_SNAPSHOT_FORMAT!r})")
        return errors  # no point checking further

    version = data.get("version")
    if version != _SNAPSHOT_VERSION:
        errors.append(f"Unsupported snapshot version: {version!r} (expected {_SNAPSHOT_VERSION!r})")
        return errors

    # Required sections
    for section in ("authorities", "concepts", "aliases", "authority_terms", "user_bindings"):
        if section not in data:
            errors.append(f"Missing required section: {section}")

    if errors:
        return errors

    # --- Build lookup indexes ---
    concepts_by_name: dict[str, dict] = {
        c["canonical_name"]: c for c in data["concepts"]
    }
    auth_names: set[str] = {a["name"] for a in data["authorities"]}

    # Duplicate concept canonical_name
    concept_names = [c["canonical_name"] for c in data["concepts"]]
    dupes = {k: v for k, v in Counter(concept_names).items() if v > 1}
    if dupes:
        errors.append(f"Duplicate concept canonical_name: {list(dupes.keys())[:20]}")

    # Missing parent references
    for c in data["concepts"]:
        pn = c.get("parent_canonical_name")
        if pn and pn not in concepts_by_name:
            errors.append(f"Concept '{c['canonical_name']}' references missing parent '{pn}'")

    # Alias integrity
    for a in data["aliases"]:
        cn = a.get("concept_canonical_name")
        if cn and cn not in concepts_by_name:
            errors.append(f"Alias '{a.get('alias')}' references missing concept '{cn}'")
        an = a.get("authority_name")
        if an and an not in auth_names:
            errors.append(f"Alias '{a.get('alias')}' references missing authority '{an}'")

    # Authority term integrity
    term_keys_id: list[tuple] = []
    term_keys_name: list[tuple] = []
    for t in data["authority_terms"]:
        an = t.get("authority_name")
        if an and an not in auth_names:
            errors.append(f"AuthorityTerm '{t.get('external_name')}' references missing authority '{an}'")
        cn = t.get("concept_canonical_name")
        if cn and cn not in concepts_by_name:
            errors.append(f"AuthorityTerm '{t.get('external_name')}' references missing concept '{cn}'")
        if an:
            eid = t.get("external_tag_id")
            if eid is not None:
                term_keys_id.append((an, eid))
            term_keys_name.append((an, t.get("normalized_external_name", "")))

    dupes_id = {k: v for k, v in Counter(term_keys_id).items() if v > 1}
    if dupes_id:
        errors.append(f"Duplicate (authority, external_tag_id): {list(dupes_id.keys())[:20]}")
    dupes_name = {k: v for k, v in Counter(term_keys_name).items() if v > 1}
    if dupes_name:
        errors.append(f"Duplicate (authority, normalized_external_name): {list(dupes_name.keys())[:20]}")

    # Alias duplicate check
    alias_keys: list[tuple] = []
    for a in data["aliases"]:
        alias_keys.append((a.get("concept_canonical_name", ""), a.get("normalized_alias", "")))
    dupes_alias = {k: v for k, v in Counter(alias_keys).items() if v > 1}
    if dupes_alias:
        errors.append(f"Duplicate (concept, normalized_alias): {list(dupes_alias.keys())[:20]}")

    return errors


# ---------------------------------------------------------------------------
# DB backup
# ---------------------------------------------------------------------------

def _backup_database(db_url: str) -> str:
    """Create a timestamped copy of the SQLite database.

    Returns the backup file path.
    """
    if not db_url.startswith("sqlite:///"):
        return ""

    db_path = Path(db_url.removeprefix("sqlite:///"))
    if not db_path.exists():
        return ""

    backup_dir = db_path.parent / "data" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"{db_path.stem}_{timestamp}.sqlite"
    shutil.copy2(str(db_path), str(backup_path))
    return str(backup_path)


# ---------------------------------------------------------------------------
# Concept depth ordering
# ---------------------------------------------------------------------------

def _order_concepts_by_depth(concepts: list[dict]) -> list[dict]:
    """Sort concepts so parents always appear before children."""
    by_name = {c["canonical_name"]: c for c in concepts}

    # Build depth cache
    depth_cache: dict[str, int] = {}

    def _depth(name: str) -> int:
        if name in depth_cache:
            return depth_cache[name]
        c = by_name.get(name)
        if not c:
            depth_cache[name] = 0
            return 0
        pn = c.get("parent_canonical_name")
        if not pn:
            depth_cache[name] = 0
            return 0
        d = 1 + _depth(pn)
        depth_cache[name] = d
        return d

    for c in concepts:
        _depth(c["canonical_name"])

    return sorted(concepts, key=lambda c: depth_cache.get(c["canonical_name"], 0))


# ---------------------------------------------------------------------------
# Main import orchestration
# ---------------------------------------------------------------------------

def import_snapshot(  # noqa: C901
    db: Session,
    *,
    data: dict[str, Any],
    dry_run: bool = True,
    backup_db: bool = True,
    source_file: str = "",
    db_url: str = "",
) -> dict[str, Any]:
    """Import a taxonomy snapshot into the database.

    Returns a result dict suitable for the API response.
    """
    start_time = time.monotonic()
    result: dict[str, Any] = {
        "status": "dry_run" if dry_run else "completed",
        "snapshot_format": data.get("format", ""),
        "snapshot_version": data.get("version", 0),
        "source_file": source_file,
        "backup_path": None,
        "imported": {},
        "conflicts": [],
        "errors": [],
        "warnings": [],
        "stats": {},
    }

    # ---- Phase 0: Pre-flight validation ----
    validation_errors = validate_snapshot(data)
    if validation_errors:
        result["status"] = "aborted"
        result["errors"] = validation_errors
        return result

    # ---- Phase 1: DB backup ----
    if not dry_run and backup_db and db_url:
        try:
            backup_path = _backup_database(db_url)
            result["backup_path"] = backup_path
            if backup_path:
                result["warnings"].append(f"Database backed up to {backup_path}")
        except Exception as exc:
            result["status"] = "aborted"
            result["errors"].append(f"DB backup failed: {exc}")
            return result

    try:
        # ---- Phase 2: Import authorities ----
        auth_stats = _import_authorities(db, data.get("authorities", []), result["conflicts"])
        result["imported"]["authorities"] = auth_stats
        if result["conflicts"]:
            result["status"] = "aborted"
            _rollback_or_leave(db, dry_run)
            return result

        # ---- Build authority name→id cache ----
        authority_cache: dict[str, TagAuthority] = {}
        for row in db.query(TagAuthority).all():
            authority_cache[row.name.lower()] = row

        # ---- Phase 3: Import concepts ----
        concept_stats = _import_concepts(
            db, data.get("concepts", []), authority_cache, result["conflicts"]
        )
        result["imported"]["concepts"] = concept_stats
        if result["conflicts"]:
            result["status"] = "aborted"
            _rollback_or_leave(db, dry_run)
            return result

        # ---- Build concept name→id cache ----
        concept_cache: dict[str, Concept] = {}
        for row in db.query(Concept).all():
            concept_cache[row.canonical_name] = row

        # ---- Phase 4: Import aliases ----
        alias_stats = _import_aliases(
            db, data.get("aliases", []), concept_cache, authority_cache, result["conflicts"]
        )
        result["imported"]["aliases"] = alias_stats
        if result["conflicts"]:
            result["status"] = "aborted"
            _rollback_or_leave(db, dry_run)
            return result

        # ---- Phase 5: Import authority_terms ----
        term_stats = _import_authority_terms(
            db, data.get("authority_terms", []), authority_cache, concept_cache, result["conflicts"]
        )
        result["imported"]["authority_terms"] = term_stats
        if result["conflicts"]:
            result["status"] = "aborted"
            _rollback_or_leave(db, dry_run)
            return result

        # ---- Phase 6: Import user_bindings ----
        binding_stats = _import_user_bindings(
            db, data.get("user_bindings", []), source_file, result["warnings"]
        )
        result["imported"]["user_bindings"] = binding_stats

        # ---- Phase 7: Post-import validation ----
        post_errors = _post_import_validation(db)
        if post_errors:
            result["warnings"].extend(post_errors)

    except Exception as exc:
        result["status"] = "aborted"
        result["errors"].append(f"Unexpected error during import: {exc}")
        _rollback_or_leave(db, dry_run)
        return result

    # ---- Commit or rollback ----
    elapsed = time.monotonic() - start_time
    total_rows = sum(
        s.get("processed", 0) for s in result["imported"].values() if isinstance(s, dict)
    )
    result["stats"] = {
        "elapsed_seconds": round(elapsed, 2),
        "rows_per_second": round(total_rows / elapsed, 1) if elapsed > 0 else 0,
        "total_rows": total_rows,
    }

    if dry_run:
        db.rollback()
        result["status"] = "dry_run"
    else:
        db.commit()

    return result


def _rollback_or_leave(db: Session, dry_run: bool) -> None:
    """Rollback the session."""
    db.rollback()


# ---------------------------------------------------------------------------
# Phase 2: Authorities
# ---------------------------------------------------------------------------

def _import_authorities(
    db: Session,
    authorities: list[dict],
    conflicts: list[dict],
) -> dict[str, int]:
    stats = {"processed": 0, "created": 0, "unchanged": 0}
    for auth_data in authorities:
        stats["processed"] += 1
        name = (auth_data.get("name") or "").strip().lower()
        if not name:
            continue

        existing = db.query(TagAuthority).filter(func.lower(TagAuthority.name) == name).first()
        if existing is not None:
            # Check non-ephemeral fields for conflicts
            imported_desc = auth_data.get("description")
            imported_is_ext = auth_data.get("is_external", True)
            imported_base_url = auth_data.get("base_url")

            if (
                (imported_desc is not None and existing.description != imported_desc)
                or (imported_is_ext is not None and existing.is_external != imported_is_ext)
                or (imported_base_url is not None and existing.base_url != imported_base_url)
            ):
                conflicts.append({
                    "phase": "authorities",
                    "authority": name,
                    "detail": f"Non-ephemeral field mismatch for existing authority '{name}'",
                    "existing": {
                        "description": existing.description,
                        "is_external": existing.is_external,
                        "base_url": existing.base_url,
                    },
                    "imported": {
                        "description": imported_desc,
                        "is_external": imported_is_ext,
                        "base_url": imported_base_url,
                    },
                })
                return stats  # stop on conflict

            stats["unchanged"] += 1
        else:
            db.add(TagAuthority(
                name=name,
                description=auth_data.get("description", ""),
                is_external=auth_data.get("is_external", True),
                base_url=auth_data.get("base_url"),
            ))
            db.flush()
            stats["created"] += 1

    return stats


# ---------------------------------------------------------------------------
# Phase 3: Concepts
# ---------------------------------------------------------------------------

def _import_concepts(
    db: Session,
    concepts: list[dict],
    authority_cache: dict[str, TagAuthority],
    conflicts: list[dict],
) -> dict[str, int]:
    stats = {"processed": 0, "created": 0, "grafted": 0, "unchanged": 0}

    ordered = _order_concepts_by_depth(concepts)
    existing_children_of: dict[Optional[int], list[Concept]] = defaultdict(list)
    # Pre-load existing parent→children mapping for grafting
    for c in db.query(Concept).all():
        existing_children_of[c.parent_concept_id].append(c)

    # name→Concept for newly created/imported concepts in this session
    imported_concepts: dict[str, Concept] = {}

    for concept_data in ordered:
        stats["processed"] += 1
        canonical_name = (concept_data.get("canonical_name") or "").strip()
        if not canonical_name:
            continue

        existing = db.query(Concept).filter(
            Concept.canonical_name == canonical_name
        ).first()

        parent_concept_id = _resolve_parent_id(
            db, concept_data.get("parent_canonical_name"), imported_concepts
        )

        if existing is not None:
            # Collision — imported wins on data fields; graft existing children
            # Update description if imported has one
            imported_desc = concept_data.get("description")
            if imported_desc and existing.description != imported_desc:
                existing.description = imported_desc
                existing.updated_at = datetime.utcnow()

            # Update status if differs
            imported_status = concept_data.get("status", "active")
            if imported_status and existing.status != imported_status:
                existing.status = imported_status
                existing.updated_at = datetime.utcnow()

            # Re-parent if imported has a different parent
            if existing.parent_concept_id != parent_concept_id:
                existing.parent_concept_id = parent_concept_id
                existing.updated_at = datetime.utcnow()

            # Graft: existing children NOT in the snapshot stay as children
            # (they remain pointing to this concept as parent — nothing to do
            #  because we only change the parent of the matched concept, not its children)

            imported_concepts[canonical_name] = existing
            stats["grafted"] += 1
        else:
            # Create new concept
            base_slug = _generate_slug(canonical_name)
            slug = _ensure_unique_slug(db, base_slug)

            new_concept = Concept(
                canonical_name=canonical_name,
                slug=slug,
                description=concept_data.get("description"),
                status=concept_data.get("status", "active"),
                parent_concept_id=parent_concept_id,
                created_at=_parse_optional_datetime(concept_data.get("created_at")),
                updated_at=(
                    _parse_optional_datetime(concept_data.get("updated_at"))
                    or datetime.utcnow()
                ),
            )
            db.add(new_concept)
            db.flush()
            imported_concepts[canonical_name] = new_concept
            stats["created"] += 1

    return stats


def _resolve_parent_id(
    db: Session,
    parent_name: Optional[str],
    imported_concepts: dict[str, Concept],
) -> Optional[int]:
    """Resolve a parent canonical_name to a concept ID."""
    if not parent_name:
        return None
    # Check recently imported first
    ic = imported_concepts.get(parent_name)
    if ic is not None:
        return ic.id
    # Check DB
    c = db.query(Concept).filter(Concept.canonical_name == parent_name).first()
    if c is not None:
        return c.id
    return None


# ---------------------------------------------------------------------------
# Phase 4: Aliases
# ---------------------------------------------------------------------------

def _import_aliases(
    db: Session,
    aliases: list[dict],
    concept_cache: dict[str, Concept],
    authority_cache: dict[str, TagAuthority],
    conflicts: list[dict],
) -> dict[str, int]:
    stats = {"processed": 0, "created": 0, "unchanged": 0}

    for alias_data in aliases:
        stats["processed"] += 1
        concept_name = (alias_data.get("concept_canonical_name") or "").strip()
        if not concept_name:
            continue

        concept = concept_cache.get(concept_name)
        if concept is None:
            # Concept might have been created in Phase 3 — try DB
            concept = db.query(Concept).filter(
                Concept.canonical_name == concept_name
            ).first()
            if concept is not None:
                concept_cache[concept_name] = concept
            else:
                # Skip — should not happen after pre-flight validation
                continue

        normalized_alias = _normalize_text(alias_data.get("alias", ""))

        existing = db.query(ConceptAlias).filter(
            ConceptAlias.concept_id == concept.id,
            ConceptAlias.normalized_alias == normalized_alias,
        ).first()

        if existing is not None:
            # Check non-ephemeral fields
            imported_type = alias_data.get("alias_type", "synonym")
            imported_preferred = alias_data.get("is_preferred", False)
            if (
                (imported_type and existing.alias_type != imported_type)
                or (existing.is_preferred != imported_preferred)
            ):
                conflicts.append({
                    "phase": "aliases",
                    "alias": alias_data.get("alias"),
                    "concept": concept_name,
                    "detail": f"Non-ephemeral field mismatch for existing alias",
                    "existing": {
                        "alias_type": existing.alias_type,
                        "is_preferred": existing.is_preferred,
                    },
                    "imported": {
                        "alias_type": imported_type,
                        "is_preferred": imported_preferred,
                    },
                })
                return stats

            stats["unchanged"] += 1
        else:
            # Resolve authority
            auth_name = (alias_data.get("authority_name") or "").strip().lower()
            authority_id = None
            if auth_name:
                auth = authority_cache.get(auth_name)
                if auth is None:
                    auth = db.query(TagAuthority).filter(
                        func.lower(TagAuthority.name) == auth_name
                    ).first()
                    if auth is not None:
                        authority_cache[auth_name] = auth
                authority_id = auth.id if auth else None

            external_tag_id = alias_data.get("external_tag_id")
            if external_tag_id is not None:
                try:
                    external_tag_id = int(external_tag_id)
                except (TypeError, ValueError):
                    external_tag_id = None

            db.add(ConceptAlias(
                concept_id=concept.id,
                alias=alias_data.get("alias", ""),
                normalized_alias=normalized_alias,
                alias_type=alias_data.get("alias_type", "synonym"),
                is_preferred=alias_data.get("is_preferred", False),
                authority_id=authority_id,
                external_tag_id=external_tag_id,
                notes=alias_data.get("notes"),
            ))
            db.flush()
            stats["created"] += 1

    return stats


# ---------------------------------------------------------------------------
# Phase 5: Authority Terms
# ---------------------------------------------------------------------------

def _import_authority_terms(  # noqa: C901
    db: Session,
    terms: list[dict],
    authority_cache: dict[str, TagAuthority],
    concept_cache: dict[str, Concept],
    conflicts: list[dict],
) -> dict[str, int]:
    stats = {"processed": 0, "created": 0, "merged": 0, "unchanged": 0}

    for i, term_data in enumerate(terms):
        stats["processed"] += 1

        auth_name = (term_data.get("authority_name") or "").strip().lower()
        if not auth_name:
            continue

        # Resolve authority
        auth = authority_cache.get(auth_name)
        if auth is None:
            auth = db.query(TagAuthority).filter(
                func.lower(TagAuthority.name) == auth_name
            ).first()
            if auth is not None:
                authority_cache[auth_name] = auth
            else:
                continue  # should not happen after pre-flight

        # Resolve concept
        concept_name = term_data.get("concept_canonical_name")
        concept_id: Optional[int] = None
        if concept_name:
            concept = concept_cache.get(concept_name)
            if concept is None:
                concept = db.query(Concept).filter(
                    Concept.canonical_name == concept_name
                ).first()
                if concept is not None:
                    concept_cache[concept_name] = concept
            concept_id = concept.id if concept else None

        external_name = term_data.get("external_name", "")
        normalized_name = _normalize_text(external_name)
        external_tag_id = term_data.get("external_tag_id")
        if external_tag_id is not None:
            try:
                external_tag_id = int(external_tag_id)
            except (TypeError, ValueError):
                external_tag_id = None

        # Find existing term
        existing = _find_existing_term(db, auth.id, external_tag_id, normalized_name)

        if existing is not None:
            # --- Compare non-ephemeral fields ---
            if existing.external_name != external_name:
                # Allow case-only differences (normalized form already matches)
                if existing.normalized_external_name != normalized_name:
                    conflicts.append({
                        "phase": "authority_terms",
                        "authority": auth_name,
                        "external_name": external_name,
                        "detail": f"external_name mismatch: existing={existing.external_name!r}, imported={external_name!r}",
                    })
                    return stats

            # Check concept linkage
            existing_concept_id = existing.concept_id
            # None → some_id is enrichment (linking an unlinked term), not a conflict.
            # Only flag when an existing link is being *changed* to a different concept.
            if (
                existing_concept_id is not None
                and concept_id is not None
                and existing_concept_id != concept_id
            ):
                conflicts.append({
                    "phase": "authority_terms",
                    "authority": auth_name,
                    "external_name": external_name,
                    "detail": f"concept linkage mismatch: existing_concept_id={existing_concept_id}, imported_concept_id={concept_id}",
                })
                return stats

            # --- Merge metadata ---
            existing_meta = existing.metadata_json if isinstance(existing.metadata_json, dict) else {}
            imported_meta = term_data.get("metadata")
            if imported_meta is not None and not isinstance(imported_meta, dict):
                try:
                    imported_meta = dict(imported_meta)
                except (TypeError, ValueError):
                    imported_meta = None

            merged_meta, meta_conflicts = _merge_metadata(existing_meta, imported_meta)
            if meta_conflicts:
                for mc in meta_conflicts:
                    conflicts.append({
                        "phase": "authority_terms",
                        "authority": auth_name,
                        "external_name": external_name,
                        "detail": mc,
                    })
                return stats

            # Apply merged values
            changed = False

            # Enrich: link previously unlinked terms
            if existing.concept_id is None and concept_id is not None:
                existing.concept_id = concept_id
                changed = True

            if merged_meta != existing_meta:
                existing.metadata_json = merged_meta
                changed = True

            # Update timestamps
            imported_updated = _parse_optional_datetime(term_data.get("updated_at"))
            if imported_updated and (existing.updated_at is None or imported_updated > existing.updated_at):
                existing.updated_at = imported_updated
                changed = True

            imported_last_seen = _parse_optional_datetime(term_data.get("last_seen_at"))
            if imported_last_seen and (existing.last_seen_at is None or imported_last_seen > existing.last_seen_at):
                existing.last_seen_at = imported_last_seen
                changed = True

            if changed:
                stats["merged"] += 1
            else:
                stats["unchanged"] += 1
        else:
            # Create new authority term
            db.add(AuthorityTerm(
                authority_id=auth.id,
                external_tag_id=external_tag_id,
                external_name=external_name,
                normalized_external_name=normalized_name,
                concept_id=concept_id,
                metadata_json=term_data.get("metadata"),
                created_at=(
                    _parse_optional_datetime(term_data.get("created_at"))
                    or datetime.utcnow()
                ),
                updated_at=(
                    _parse_optional_datetime(term_data.get("updated_at"))
                    or datetime.utcnow()
                ),
                last_seen_at=(
                    _parse_optional_datetime(term_data.get("last_seen_at"))
                    or datetime.utcnow()
                ),
            ))
            db.flush()
            stats["created"] += 1

        # Periodic flush for large imports
        if stats["processed"] % _BATCH_SIZE == 0:
            db.flush()

    return stats


def _find_existing_term(
    db: Session,
    authority_id: int,
    external_tag_id: Optional[int],
    normalized_name: str,
) -> Optional[AuthorityTerm]:
    """Find an existing authority term by unique key."""
    # Try (authority_id, external_tag_id) first if tag_id is present
    if external_tag_id is not None:
        term = db.query(AuthorityTerm).filter(
            AuthorityTerm.authority_id == authority_id,
            AuthorityTerm.external_tag_id == external_tag_id,
        ).first()
        if term is not None:
            return term

    # Fallback to (authority_id, normalized_external_name)
    return db.query(AuthorityTerm).filter(
        AuthorityTerm.authority_id == authority_id,
        AuthorityTerm.normalized_external_name == normalized_name,
    ).first()


# ---------------------------------------------------------------------------
# Phase 6: User Bindings
# ---------------------------------------------------------------------------

def _import_user_bindings(
    db: Session,
    bindings: list[dict],
    source_file: str,
    warnings: list[str],
) -> dict[str, int]:
    stats = {"processed": 0, "matched": 0, "staged": 0}

    for binding in bindings:
        stats["processed"] += 1
        file_hash = (binding.get("file_hash") or "").strip()
        if not file_hash:
            continue

        # Find matching images
        matching_images = db.query(ImageModel).filter(
            ImageModel.file_hash == file_hash
        ).all()

        if matching_images:
            # Enrich user_tags on each matching image
            imported_tags = binding.get("user_tags") or []
            imported_neg_tags = binding.get("user_negative_tags") or []

            for img in matching_images:
                changed = False
                existing_tags = img.user_tags or []
                if isinstance(existing_tags, list) and isinstance(imported_tags, list):
                    new_tags = list(dict.fromkeys(existing_tags + imported_tags))
                    if len(new_tags) > len(existing_tags):
                        img.user_tags = new_tags
                        changed = True

                existing_neg = img.user_negative_tags or []
                if isinstance(existing_neg, list) and isinstance(imported_neg_tags, list):
                    new_neg = list(dict.fromkeys(existing_neg + imported_neg_tags))
                    if len(new_neg) > len(existing_neg):
                        img.user_negative_tags = new_neg
                        changed = True

                if changed:
                    img.updated_at = datetime.utcnow()

            db.flush()
            stats["matched"] += 1
        else:
            # Stage for later application
            db.add(PendingUserBinding(
                file_hash=file_hash,
                file_path=binding.get("file_path"),
                user_tags=binding.get("user_tags"),
                user_negative_tags=binding.get("user_negative_tags"),
                source_snapshot=source_file,
            ))
            db.flush()
            stats["staged"] += 1

    return stats


# ---------------------------------------------------------------------------
# Phase 7: Post-import validation
# ---------------------------------------------------------------------------

def _post_import_validation(db: Session) -> list[str]:
    """Run sanity checks after import."""
    warnings: list[str] = []

    # Check for concepts with invalid parent_concept_id
    orphan_parents = db.query(Concept).filter(
        Concept.parent_concept_id.isnot(None),
        ~Concept.parent_concept_id.in_(db.query(Concept.id)),
    ).count()
    if orphan_parents:
        warnings.append(f"Found {orphan_parents} concepts with invalid parent_concept_id")

    # Check for authority_terms with invalid concept_id
    orphan_terms = db.query(AuthorityTerm).filter(
        AuthorityTerm.concept_id.isnot(None),
        ~AuthorityTerm.concept_id.in_(db.query(Concept.id)),
    ).count()
    if orphan_terms:
        warnings.append(f"Found {orphan_terms} authority_terms with invalid concept_id")

    return warnings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize_text(value: str) -> str:
    """Normalize taxonomy text (matches TaxonomyService.normalize_text)."""
    normalized = (value or "").strip().replace("_", " ").lower()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def _parse_optional_datetime(value: Any) -> Optional[datetime]:
    """Parse an optional datetime from string or return as-is."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        # Try ISO format
        for fmt in (
            "%Y-%m-%dT%H:%M:%S.%fZ",
            "%Y-%m-%dT%H:%M:%SZ",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
    return None
