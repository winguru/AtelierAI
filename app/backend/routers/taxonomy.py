# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/taxonomy-import.md
# 📄 docs: app/docs/memories/image-api.md
# ──────────────────────────────────────────────────────────────────────────────
"""Taxonomy routes: concepts, aliases, tags, bootstrap, tree, and tag-maint.

All routes previously defined in main.py between lines 20328-22626 are
extracted here verbatim.  Helper functions used exclusively by these routes
are co-located in this module.

TODO: Move non-route helpers into dedicated services:
  - _gallery_tag_*_from_observations  -> services/gallery_tag_service.py
  - _concept_source_map, _is_descendant, _execute_taxonomy_bootstrap_import
      -> services/taxonomy_service.py
  - _upsert_civitai_authority_terms   -> services/civitai_service.py
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Generator, Optional
from urllib.parse import quote

import atelierai.config as app_config
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
)
from fastapi.responses import StreamingResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from database import SessionLocal, get_db
from models import (
    AuthorityTerm,
    Concept,
    ConceptAlias,
    ConceptAttributeProfile,
    ImageConceptObservation,
    ImageModel,
    ObservationCertainty,
    ObservationSource,
    TagAuthority,
)
from schemas import (
    AutoBuildPrototypeResponse,
    BatchBuildRequest,
    BatchBuildResponse,
    StreamBuildRequest,
    BuildPrototypeRequest,
    ConceptAttributeAddRequest,
    ConceptAttributeEntry,
    ConceptAttributeUpdateRequest,
    ConceptIndexResponse,
    ConceptProfileResponse,
    ConceptSearchRequest,
    ConceptSearchResponse,
    DecomposeResponse,
    PrototypeStatsResponse,
    ScoreImageRequest,
    ScoreImageResponse,
    TaxonomyAliasCreateRequest,
    TaxonomyBootstrapImportRequest,
    TaxonomyConceptTransferImportRequest,
    TaxonomyConceptCreateRequest,
    TaxonomyConceptUpdateRequest,
    TaxonomyMergeRequest,
    TaxonomyParentUpdateRequest,
    TaxonomyPurgeRootsRequest,
    TaxonomySnapshotImportResponse,
    TaxonomyTagAssociationRequest,
    TaxonomyTagDetailsUpdateRequest,
    TaxonomyTagMaintBulkDeleteRequest,
    TaxonomyTagMaintPurgeRequest,
    TaxonomyTagMaintUpdateRequest,
)
from services.concept_prototype_service import ConceptPrototypeService
from services.concept_search_service import ConceptSearchService
from services.gallery_tag_service import GalleryTagService
from services.image_service import _active_image_filter
from services.taxonomy_service import TaxonomyService
from services.taxonomy_snapshot_import import import_snapshot, validate_snapshot
from utils.cache import (
    _FILTER_OPTIONS_CACHE_TTL_SECONDS,
    _build_json_cache_headers,
    _build_search_cache_key,
    _search_cache_get,
    _search_cache_put,
    _should_return_json_not_modified,
)

# -- Module-level service instances ------------------------------------------

_taxonomy_service = TaxonomyService()
_gallery_tag_service = GalleryTagService()

# -- Router ------------------------------------------------------------------

router = APIRouter(prefix="/taxonomy", tags=["taxonomy"])

# -- Constants ---------------------------------------------------------------

_TAG_SOURCE_COLS = [
    "id",
    "name",
    "ext_id",
    "scope",
    "post_count",
    "concept_id",
    "mdtag_id",
    "mdtag_name",
]

_VALID_TAG_MAINT_SOURCES = {"civitai", "danbooru", "prompt", "user"}
_CONCEPT_TRANSFER_AUTHORITIES = ("civitai", "danbooru", "prompt", "user")
_CONCEPT_TRANSFER_VERSION = 1

# -- Delegation helpers ------------------------------------------------------


def _normalize_taxonomy_text(value: str) -> str:
    return _taxonomy_service.normalize_text(value)


def _normalize_gallery_tag_text(value: str) -> str:
    return _gallery_tag_service.normalize_text(value)


def _duplicate_key(value: str) -> str:
    return _taxonomy_service.duplicate_key(value)


def _slugify_concept_name(value: str) -> str:
    return _taxonomy_service.slugify_concept_name(value)


def _ensure_unique_concept_slug(db: Session, base_slug: str) -> str:
    return _taxonomy_service.ensure_unique_concept_slug(db, base_slug)


def _get_or_create_authority(db: Session, authority_name: str) -> TagAuthority:
    return _taxonomy_service.get_or_create_authority(db, authority_name)


def _get_or_create_concept(db: Session, canonical_name: str) -> Concept:
    return _taxonomy_service.get_or_create_concept(db, canonical_name)


def _ensure_alias_for_concept(
    db: Session,
    concept_id: int,
    alias_text: str,
    alias_type: str = "synonym",
    authority_id: Optional[int] = None,
    external_tag_id: Optional[int] = None,
) -> bool:
    return _taxonomy_service.ensure_alias_for_concept(
        db,
        concept_id,
        alias_text,
        alias_type,
        authority_id,
        external_tag_id,
    )


def _parse_bootstrap_terms(format_name: str, raw_text: str) -> list[dict]:
    return _taxonomy_service.parse_bootstrap_terms(format_name, raw_text)


# -- Gallery tag observation helpers -----------------------------------------


def _gallery_tag_names_by_source_from_observations(db: Session) -> dict[str, list[str]]:
    by_source: dict[str, set[str]] = {
        "civitai": set(),
        "danbooru": set(),
        "prompt": set(),
        "user": set(),
    }
    obs_rows = (
        db.query(TagAuthority.name, AuthorityTerm.external_name)
        .join(AuthorityTerm, AuthorityTerm.authority_id == TagAuthority.id)
        .join(
            ImageConceptObservation,
            ImageConceptObservation.authority_term_id == AuthorityTerm.id,
        )
        .join(ImageModel, ImageModel.id == ImageConceptObservation.image_id)
        .filter(_active_image_filter())
        .distinct()
        .all()
    )
    for authority_name, external_name in obs_rows:
        source_key = (authority_name or "").strip().lower()
        if source_key in by_source and external_name:
            by_source[source_key].add(_normalize_gallery_tag_text(external_name))
    return {
        source: sorted(name for name in names if name)
        for source, names in by_source.items()
    }


def _gallery_tag_usage_counts_by_source_from_observations(
    db: Session,
) -> dict[str, dict[str, int]]:
    by_source: dict[str, dict[str, int]] = {
        "civitai": {},
        "danbooru": {},
        "prompt": {},
        "user": {},
    }
    count_rows = (
        db.query(
            TagAuthority.name,
            AuthorityTerm.external_name,
            func.count(func.distinct(ImageConceptObservation.image_id)),
        )
        .join(AuthorityTerm, AuthorityTerm.authority_id == TagAuthority.id)
        .join(
            ImageConceptObservation,
            ImageConceptObservation.authority_term_id == AuthorityTerm.id,
        )
        .join(ImageModel, ImageModel.id == ImageConceptObservation.image_id)
        .filter(_active_image_filter())
        .group_by(TagAuthority.name, AuthorityTerm.external_name)
        .all()
    )
    for authority_name, external_name, image_count in count_rows:
        source_key = (authority_name or "").strip().lower()
        if source_key in by_source and external_name:
            normalized = _normalize_gallery_tag_text(external_name)
            if normalized:
                by_source[source_key][normalized] = int(image_count)
    return {
        source: {name: counts[name] for name in sorted(counts)}
        for source, counts in by_source.items()
    }


# -- Taxonomy concept helpers ------------------------------------------------


def _is_descendant(db: Session, ancestor_id: int, candidate_descendant_id: int) -> bool:
    current = db.query(Concept).filter(Concept.id == candidate_descendant_id).first()
    seen: set[int] = set()
    while current is not None and current.parent_concept_id is not None:
        current_id = int(current.id)
        if current_id in seen:
            break
        seen.add(current_id)
        if int(current.parent_concept_id) == ancestor_id:
            return True
        current = (
            db.query(Concept).filter(Concept.id == current.parent_concept_id).first()
        )
    return False


def _authority_display_name(authority_name: str) -> str:
    normalized = (authority_name or "").strip().lower()
    mapping = {
        "civitai": "CivitAI",
        "danbooru": "Danbooru",
        "prompt": "Prompt",
        "user": "User",
        "ai_agent": "AI",
    }
    return mapping.get(
        normalized, authority_name.title() if authority_name else "Unknown"
    )


def _concept_source_map(db: Session, concept_ids: list[int]) -> dict[int, list[str]]:
    if not concept_ids:
        return {}
    rows = (
        db.query(AuthorityTerm.concept_id, TagAuthority.name)
        .join(TagAuthority, TagAuthority.id == AuthorityTerm.authority_id)
        .filter(AuthorityTerm.concept_id.in_(concept_ids))
        .all()
    )
    source_map: dict[int, set[str]] = {}
    for concept_id, authority_name in rows:
        if concept_id is None:
            continue
        source_map.setdefault(int(concept_id), set()).add(str(authority_name))
    return {
        cid: sorted(_authority_display_name(name) for name in names)
        for cid, names in source_map.items()
    }


def _concept_display_prefix(source_labels: list[str]) -> str:
    if not source_labels:
        return "Concept"
    if len(source_labels) == 1:
        return source_labels[0]
    return "Concept"


# -- Bootstrap import helper -------------------------------------------------


def _execute_taxonomy_bootstrap_import(
    db: Session,
    *,
    authority_name: str,
    rows: list[dict],
    dry_run: bool,
) -> dict:
    authority = _get_or_create_authority(db, authority_name)
    stats: dict = {
        "rows_received": len(rows),
        "rows_processed": 0,
        "concepts_linked": 0,
        "aliases_created": 0,
        "authority_terms_created": 0,
        "authority_terms_updated": 0,
        "errors": [],
    }
    for idx, row in enumerate(rows, start=1):
        try:
            with db.begin_nested():
                raw_name = str(
                    (row or {}).get("name") or (row or {}).get("external_name") or ""
                ).strip()
                if not raw_name:
                    continue
                normalized_name = _normalize_taxonomy_text(raw_name)
                raw_tag_id = (row or {}).get("external_tag_id")
                try:
                    external_tag_id = (
                        int(raw_tag_id) if raw_tag_id not in (None, "") else None
                    )
                except (TypeError, ValueError):
                    external_tag_id = None
                mapped_concept_name = str(
                    (row or {}).get("concept_name") or ""
                ).strip()
                concept_name = mapped_concept_name or normalized_name
                concept = (
                    db.query(Concept)
                    .filter(
                        Concept.canonical_name
                        == _normalize_taxonomy_text(concept_name)
                    )
                    .first()
                )
                if concept is not None:
                    stats["concepts_linked"] += 1

                    if _ensure_alias_for_concept(
                        db,
                        concept_id=concept.id,
                        alias_text=raw_name,
                        alias_type="imported",
                        authority_id=authority.id,
                        external_tag_id=external_tag_id,
                    ):
                        stats["aliases_created"] += 1

                concept_id = concept.id if concept is not None else None

                term = (
                    db.query(AuthorityTerm)
                    .filter(
                        AuthorityTerm.authority_id == authority.id,
                        or_(
                            AuthorityTerm.external_tag_id == external_tag_id,
                            AuthorityTerm.normalized_external_name == normalized_name,
                        ),
                    )
                    .first()
                )
                if term is None:
                    term = AuthorityTerm(
                        authority_id=authority.id,
                        external_tag_id=external_tag_id,
                        external_name=raw_name,
                        normalized_external_name=normalized_name,
                        concept_id=concept_id,
                        created_at=datetime.utcnow(),
                        updated_at=datetime.utcnow(),
                        last_seen_at=datetime.utcnow(),
                    )
                    db.add(term)
                    db.flush()
                    stats["authority_terms_created"] += 1
                else:
                    changed = False
                    if getattr(term, "external_tag_id", None) != external_tag_id:
                        term.external_tag_id = external_tag_id
                        changed = True
                    if str(term.external_name or "") != raw_name:
                        term.external_name = raw_name
                        changed = True
                    if str(term.normalized_external_name or "") != normalized_name:
                        term.normalized_external_name = normalized_name
                        changed = True
                    if (term.concept_id or 0) != (concept_id or 0):
                        term.concept_id = concept_id
                        changed = True
                    term.last_seen_at = datetime.utcnow()
                    if changed:
                        term.updated_at = datetime.utcnow()
                        stats["authority_terms_updated"] += 1
                stats["rows_processed"] += 1
        except Exception as exc:
            stats["errors"].append(f"row {idx}: {exc}")
    if dry_run:
        db.rollback()
    else:
        db.commit()
    return {
        "message": "Taxonomy bootstrap import complete.",
        "dry_run": dry_run,
        "authority": authority.name,
        "stats": stats,
    }


def _concept_transfer_node_error(path: str, detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=f"{path}: {detail}")


def _coerce_concept_transfer_node(raw_node: Any, path: str) -> dict[str, Any]:
    if not isinstance(raw_node, dict):
        raise _concept_transfer_node_error(path, "node must be an object")

    normalized_name = _normalize_taxonomy_text(str(raw_node.get("name") or ""))
    if not normalized_name:
        raise _concept_transfer_node_error(path, "name is required")

    raw_slug = str(raw_node.get("slug") or "").strip()
    normalized_slug = _slugify_concept_name(raw_slug or normalized_name)
    if not normalized_slug:
        raise _concept_transfer_node_error(path, "slug is required")

    raw_aliases = raw_node.get("aliases")
    if raw_aliases is None:
        raw_aliases = []
    if not isinstance(raw_aliases, list):
        raise _concept_transfer_node_error(path, "aliases must be an array")

    aliases: list[str] = []
    seen_aliases: set[str] = set()
    for raw_alias in raw_aliases:
        normalized_alias = _normalize_taxonomy_text(str(raw_alias or ""))
        if not normalized_alias or normalized_alias == normalized_name:
            continue
        if normalized_alias in seen_aliases:
            continue
        seen_aliases.add(normalized_alias)
        aliases.append(normalized_alias)

    raw_tags = raw_node.get("tags")
    if raw_tags is None:
        raw_tags = {}
    if not isinstance(raw_tags, dict):
        raise _concept_transfer_node_error(path, "tags must be an object")

    tags: dict[str, list[str]] = {
        authority_name: [] for authority_name in _CONCEPT_TRANSFER_AUTHORITIES
    }
    for raw_authority, raw_terms in raw_tags.items():
        authority_name = str(raw_authority or "").strip().lower()
        if authority_name not in tags:
            continue
        if raw_terms is None:
            continue
        if not isinstance(raw_terms, list):
            raise _concept_transfer_node_error(
                path, f"tags.{authority_name} must be an array"
            )
        seen_terms: set[str] = set()
        normalized_terms: list[str] = []
        for raw_term in raw_terms:
            normalized_term = _normalize_taxonomy_text(str(raw_term or ""))
            if not normalized_term or normalized_term in seen_terms:
                continue
            seen_terms.add(normalized_term)
            normalized_terms.append(normalized_term)
        tags[authority_name] = normalized_terms

    raw_children = raw_node.get("children")
    if raw_children is None:
        raw_children = []
    if not isinstance(raw_children, list):
        raise _concept_transfer_node_error(path, "children must be an array")

    children: list[dict[str, Any]] = []
    for index, raw_child in enumerate(raw_children):
        children.append(
            _coerce_concept_transfer_node(raw_child, f"{path}.children[{index}]")
        )

    description = raw_node.get("description")
    status = str(raw_node.get("status") or "active").strip() or "active"
    return {
        "name": normalized_name,
        "slug": normalized_slug,
        "description": description if isinstance(description, str) else None,
        "status": status,
        "aliases": aliases,
        "tags": tags,
        "children": children,
    }


def _coerce_concept_transfer_document(document: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise HTTPException(status_code=400, detail="document must be an object")
    version = document.get("version")
    if version is None:
        version = _CONCEPT_TRANSFER_VERSION
    try:
        parsed_version = int(version)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="document.version must be an integer")
    if parsed_version != _CONCEPT_TRANSFER_VERSION:
        raise HTTPException(
            status_code=409,
            detail=f"Unsupported document version: {parsed_version}",
        )

    roots = document.get("roots")
    if not isinstance(roots, list):
        raise HTTPException(status_code=400, detail="document.roots must be an array")

    normalized_roots: list[dict[str, Any]] = []
    for index, raw_root in enumerate(roots):
        normalized_roots.append(_coerce_concept_transfer_node(raw_root, f"roots[{index}]"))

    authority_order = document.get("authority_order")
    parsed_authority_order: list[str] = []
    if isinstance(authority_order, list):
        for raw_authority in authority_order:
            authority_name = str(raw_authority or "").strip().lower()
            if authority_name in _CONCEPT_TRANSFER_AUTHORITIES and authority_name not in parsed_authority_order:
                parsed_authority_order.append(authority_name)
    if not parsed_authority_order:
        parsed_authority_order = list(_CONCEPT_TRANSFER_AUTHORITIES)

    return {
        "version": parsed_version,
        "authority_order": parsed_authority_order,
        "roots": normalized_roots,
    }


def _build_concept_transfer_indexes(db: Session) -> dict[str, Any]:
    concepts = db.query(Concept).all()
    concept_by_id = {int(concept.id): concept for concept in concepts}
    by_slug: dict[str, Concept] = {}
    by_name: dict[str, Concept] = {}
    by_parent: dict[Optional[int], list[Concept]] = {}
    for concept in concepts:
        by_slug[str(concept.slug or "").strip().lower()] = concept
        by_name[_normalize_taxonomy_text(concept.canonical_name or "")] = concept
        by_parent.setdefault(concept.parent_concept_id, []).append(concept)

    alias_rows = db.query(ConceptAlias.concept_id, ConceptAlias.normalized_alias).all()
    alias_to_ids: dict[str, set[int]] = {}
    for concept_id, normalized_alias in alias_rows:
        alias_key = _normalize_taxonomy_text(normalized_alias or "")
        if not alias_key or concept_id is None:
            continue
        alias_to_ids.setdefault(alias_key, set()).add(int(concept_id))

    return {
        "concept_by_id": concept_by_id,
        "by_slug": by_slug,
        "by_name": by_name,
        "by_parent": by_parent,
        "alias_to_ids": alias_to_ids,
    }


def _concept_transfer_path(concept: Concept, indexes: dict[str, Any]) -> str:
    concept_by_id: dict[int, Concept] = indexes["concept_by_id"]
    names: list[str] = []
    current: Optional[Concept] = concept
    visited: set[int] = set()
    while current is not None:
        current_id = int(current.id)
        if current_id in visited:
            break
        visited.add(current_id)
        names.append(str(current.canonical_name or ""))
        parent_id = current.parent_concept_id
        if parent_id is None:
            break
        current = concept_by_id.get(int(parent_id))
    return "/".join(reversed([name for name in names if name]))


def _match_transfer_node(
    node: dict[str, Any],
    parent_concept_id: Optional[int],
    indexes: dict[str, Any],
) -> tuple[Optional[Concept], str]:
    by_parent: dict[Optional[int], list[Concept]] = indexes["by_parent"]
    by_slug: dict[str, Concept] = indexes["by_slug"]
    by_name: dict[str, Concept] = indexes["by_name"]
    alias_to_ids: dict[str, set[int]] = indexes["alias_to_ids"]
    concept_by_id: dict[int, Concept] = indexes["concept_by_id"]

    slug_key = str(node.get("slug") or "").strip().lower()
    name_key = _normalize_taxonomy_text(str(node.get("name") or ""))

    if parent_concept_id in by_parent:
        for candidate in by_parent[parent_concept_id]:
            if slug_key and str(candidate.slug or "").strip().lower() == slug_key:
                return candidate, "parent_slug"
            if name_key and _normalize_taxonomy_text(candidate.canonical_name or "") == name_key:
                return candidate, "parent_name"

    if slug_key and slug_key in by_slug:
        return by_slug[slug_key], "global_slug"
    if name_key and name_key in by_name:
        return by_name[name_key], "global_name"

    if name_key:
        alias_candidates = alias_to_ids.get(name_key, set())
        if len(alias_candidates) == 1:
            alias_concept_id = next(iter(alias_candidates))
            alias_match = concept_by_id.get(alias_concept_id)
            if alias_match is not None:
                return alias_match, "global_alias"

    return None, "none"


def _index_concept(indexes: dict[str, Any], concept: Concept) -> None:
    concept_id = int(concept.id)
    indexes["concept_by_id"][concept_id] = concept
    slug_key = str(concept.slug or "").strip().lower()
    if slug_key:
        indexes["by_slug"][slug_key] = concept
    name_key = _normalize_taxonomy_text(concept.canonical_name or "")
    if name_key:
        indexes["by_name"][name_key] = concept
    indexes["by_parent"].setdefault(concept.parent_concept_id, []).append(concept)


def _index_alias(indexes: dict[str, Any], concept_id: int, alias_text: str) -> None:
    alias_key = _normalize_taxonomy_text(alias_text)
    if not alias_key:
        return
    indexes["alias_to_ids"].setdefault(alias_key, set()).add(int(concept_id))


def _add_transfer_aliases(
    db: Session,
    concept: Concept,
    aliases: list[str],
    indexes: dict[str, Any],
) -> int:
    created_count = 0
    for alias_text in aliases:
        if _ensure_alias_for_concept(
            db,
            concept_id=int(concept.id),
            alias_text=alias_text,
            alias_type="imported",
        ):
            created_count += 1
            _index_alias(indexes, int(concept.id), alias_text)
    return created_count


def _create_transfer_concept(
    db: Session,
    node: dict[str, Any],
    parent_concept_id: Optional[int],
    indexes: dict[str, Any],
) -> Concept:
    canonical_name = _normalize_taxonomy_text(str(node.get("name") or ""))
    requested_slug = str(node.get("slug") or "").strip().lower()
    base_slug = _slugify_concept_name(requested_slug or canonical_name)
    slug = _ensure_unique_concept_slug(db, base_slug)
    now = datetime.utcnow()
    concept = Concept(
        canonical_name=canonical_name,
        slug=slug,
        description=node.get("description"),
        status=str(node.get("status") or "active"),
        parent_concept_id=parent_concept_id,
        created_at=now,
        updated_at=now,
    )
    db.add(concept)
    db.flush()
    _ensure_alias_for_concept(
        db,
        concept_id=int(concept.id),
        alias_text=canonical_name,
        alias_type="canonical",
    )
    _index_concept(indexes, concept)
    _index_alias(indexes, int(concept.id), canonical_name)
    return concept


def _link_transfer_tags(
    db: Session,
    concept: Concept,
    node_tags: dict[str, list[str]],
    authority_cache: dict[str, TagAuthority],
    summary: dict[str, int],
    conflicts: list[dict[str, Any]],
    indexes: dict[str, Any],
) -> None:
    local_path = _concept_transfer_path(concept, indexes)
    for authority_name, term_names in node_tags.items():
        if authority_name not in _CONCEPT_TRANSFER_AUTHORITIES:
            continue
        authority = authority_cache.get(authority_name)
        if authority is None:
            authority = _get_or_create_authority(db, authority_name)
            authority_cache[authority_name] = authority
        for term_name in term_names:
            normalized_term = _normalize_taxonomy_text(term_name)
            if not normalized_term:
                continue
            existing_term = (
                db.query(AuthorityTerm)
                .filter(
                    AuthorityTerm.authority_id == authority.id,
                    AuthorityTerm.normalized_external_name == normalized_term,
                )
                .first()
            )
            if existing_term is None:
                now = datetime.utcnow()
                db.add(AuthorityTerm(
                    authority_id=authority.id,
                    external_tag_id=None,
                    external_name=term_name,
                    normalized_external_name=normalized_term,
                    concept_id=int(concept.id),
                    created_at=now,
                    updated_at=now,
                    last_seen_at=now,
                ))
                summary["tag_links_added"] += 1
                continue
            if existing_term.concept_id is None:
                existing_term.concept_id = int(concept.id)
                existing_term.updated_at = datetime.utcnow()
                summary["tag_links_added"] += 1
                continue
            if int(existing_term.concept_id) == int(concept.id):
                continue
            conflict_concept = indexes["concept_by_id"].get(int(existing_term.concept_id))
            conflicts.append({
                "type": "tag_conflict",
                "authority": authority_name,
                "term": normalized_term,
                "existing_concept": (
                    _concept_transfer_path(conflict_concept, indexes)
                    if conflict_concept is not None
                    else str(existing_term.concept_id)
                ),
                "incoming_concept": local_path,
                "action": "skipped",
            })
            summary["tag_conflicts_skipped"] += 1


def _import_transfer_branch(
    db: Session,
    node: dict[str, Any],
    *,
    parent_concept: Optional[Concept],
    root_policy: str,
    indexes: dict[str, Any],
    authority_cache: dict[str, TagAuthority],
    summary: dict[str, int],
    actions: list[dict[str, Any]],
    conflicts: list[dict[str, Any]],
    incoming_path: str,
) -> None:
    is_root = parent_concept is None
    parent_id = int(parent_concept.id) if parent_concept is not None else None
    matched, match_kind = _match_transfer_node(node, parent_id, indexes)

    if matched is None and is_root and root_policy == "strict":
        summary["roots_skipped_by_policy"] += 1
        actions.append({
            "type": "root_skipped_by_policy",
            "incoming_path": incoming_path,
            "policy": root_policy,
        })
        return

    if matched is not None:
        resolved_concept = matched
        summary["concepts_matched"] += 1
        summary["branches_grafted"] += 1
        actions.append({
            "type": "graft_existing",
            "incoming_path": incoming_path,
            "local_path": _concept_transfer_path(resolved_concept, indexes),
            "match": match_kind,
        })
    else:
        resolved_concept = _create_transfer_concept(db, node, parent_id, indexes)
        summary["concepts_created"] += 1
        summary["branches_grafted"] += 1
        if is_root:
            summary["roots_created"] += 1
        actions.append({
            "type": "create_concept",
            "incoming_path": incoming_path,
            "local_path": _concept_transfer_path(resolved_concept, indexes),
            "parent_concept_id": parent_id,
        })

    summary["aliases_added"] += _add_transfer_aliases(
        db,
        resolved_concept,
        list(node.get("aliases") or []),
        indexes,
    )
    _link_transfer_tags(
        db,
        resolved_concept,
        dict(node.get("tags") or {}),
        authority_cache,
        summary,
        conflicts,
        indexes,
    )

    for child_node in list(node.get("children") or []):
        child_name = str(child_node.get("name") or "").strip()
        child_path = f"{incoming_path}/{child_name}" if child_name else incoming_path
        _import_transfer_branch(
            db,
            child_node,
            parent_concept=resolved_concept,
            root_policy=root_policy,
            indexes=indexes,
            authority_cache=authority_cache,
            summary=summary,
            actions=actions,
            conflicts=conflicts,
            incoming_path=child_path,
        )


def _build_concept_transfer_export_document(
    db: Session,
    *,
    include_aliases: bool,
    include_descriptions: bool,
    status: str,
    authorities: Optional[list[str]],
) -> dict[str, Any]:
    concept_query = db.query(Concept)
    if status != "all":
        concept_query = concept_query.filter(Concept.status == status)
    concepts = concept_query.order_by(Concept.canonical_name.asc()).all()
    concept_ids = [int(concept.id) for concept in concepts]

    selected_authorities = list(_CONCEPT_TRANSFER_AUTHORITIES)
    if authorities:
        selected_authorities = [
            authority
            for authority in authorities
            if authority in _CONCEPT_TRANSFER_AUTHORITIES
        ]
        if not selected_authorities:
            raise HTTPException(status_code=400, detail="No valid authorities requested")

    aliases_by_concept: dict[int, list[str]] = {concept_id: [] for concept_id in concept_ids}
    if include_aliases and concept_ids:
        alias_rows = (
            db.query(ConceptAlias)
            .filter(
                ConceptAlias.concept_id.in_(concept_ids),
                ConceptAlias.alias_type != "canonical",
            )
            .order_by(ConceptAlias.id.asc())
            .all()
        )
        for alias in alias_rows:
            concept_id = int(alias.concept_id)
            normalized_alias = _normalize_taxonomy_text(alias.alias or "")
            if not normalized_alias:
                continue
            existing_aliases = aliases_by_concept.setdefault(concept_id, [])
            if normalized_alias not in existing_aliases:
                existing_aliases.append(normalized_alias)

    tags_by_concept: dict[int, dict[str, list[str]]] = {
        concept_id: {
            authority_name: [] for authority_name in selected_authorities
        }
        for concept_id in concept_ids
    }
    if concept_ids:
        tag_rows = (
            db.query(AuthorityTerm, TagAuthority)
            .join(TagAuthority, TagAuthority.id == AuthorityTerm.authority_id)
            .filter(AuthorityTerm.concept_id.in_(concept_ids))
            .order_by(TagAuthority.name.asc(), AuthorityTerm.external_name.asc())
            .all()
        )
        for term, authority in tag_rows:
            if term.concept_id is None:
                continue
            authority_name = str(authority.name or "").strip().lower()
            if authority_name not in selected_authorities:
                continue
            term_name = _normalize_taxonomy_text(term.external_name or "")
            if not term_name:
                continue
            concept_id = int(term.concept_id)
            concept_tags = tags_by_concept.setdefault(
                concept_id,
                {name: [] for name in selected_authorities},
            )
            bucket = concept_tags.setdefault(authority_name, [])
            if term_name not in bucket:
                bucket.append(term_name)

    by_parent: dict[Optional[int], list[Concept]] = {}
    for concept in concepts:
        by_parent.setdefault(concept.parent_concept_id, []).append(concept)

    def build_node(concept: Concept) -> dict[str, Any]:
        concept_id = int(concept.id)
        children = by_parent.get(concept.id, [])
        description = concept.description if include_descriptions else None
        return {
            "name": _normalize_taxonomy_text(concept.canonical_name or ""),
            "slug": str(concept.slug or "").strip().lower(),
            "description": description,
            "status": str(concept.status or "active"),
            "aliases": list(aliases_by_concept.get(concept_id, [])) if include_aliases else [],
            "tags": {
                authority_name: list(tags_by_concept.get(concept_id, {}).get(authority_name, []))
                for authority_name in selected_authorities
            },
            "children": [build_node(child) for child in children],
        }

    root_concepts = by_parent.get(None, [])
    return {
        "version": _CONCEPT_TRANSFER_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "authority_order": selected_authorities,
        "roots": [build_node(root) for root in root_concepts],
    }


# -- CivitAI authority-term upsert -------------------------------------------
# TODO: Move to services/civitai_service.py once that module is populated.


def _upsert_civitai_authority_terms(db: Session, civitai_data: dict) -> dict:
    """Sync CivitAI tag records into the taxonomy authority_terms table."""
    tag_records = civitai_data.get("tags")
    if not isinstance(tag_records, list) or not tag_records:
        return {"terms_upserted": 0, "terms_created": 0, "terms_updated": 0}
    authority = _get_or_create_authority(db, "civitai")
    stats = {"terms_upserted": 0, "terms_created": 0, "terms_updated": 0}
    for tag in tag_records:
        if not isinstance(tag, dict):
            continue
        raw_name = str(tag.get("name") or "").strip()
        if not raw_name:
            continue
        normalized_name = _normalize_taxonomy_text(raw_name)
        raw_tag_id = tag.get("id")
        try:
            external_tag_id = (
                int(raw_tag_id) if raw_tag_id not in (None, "") else None
            )
        except (TypeError, ValueError):
            external_tag_id = None
        tag_meta: dict = {}
        for meta_key in ("type", "nsfwLevel", "automated", "concrete", "score"):
            val = tag.get(meta_key)
            if val is not None:
                tag_meta[meta_key] = val
        term = None
        if external_tag_id is not None:
            term = (
                db.query(AuthorityTerm)
                .filter(
                    AuthorityTerm.authority_id == authority.id,
                    AuthorityTerm.external_tag_id == external_tag_id,
                )
                .first()
            )
        if term is None:
            term = (
                db.query(AuthorityTerm)
                .filter(
                    AuthorityTerm.authority_id == authority.id,
                    AuthorityTerm.normalized_external_name == normalized_name,
                )
                .first()
            )
        now = datetime.utcnow()
        if term is None:
            term = AuthorityTerm(
                authority_id=authority.id,
                external_tag_id=external_tag_id,
                external_name=raw_name,
                normalized_external_name=normalized_name,
                concept_id=None,
                metadata_json=tag_meta if tag_meta else None,
                created_at=now,
                updated_at=now,
                last_seen_at=now,
            )
            db.add(term)
            stats["terms_created"] += 1
        else:
            changed = False
            if getattr(term, "external_tag_id", None) != external_tag_id:
                term.external_tag_id = external_tag_id
                changed = True
            if str(term.external_name or "") != raw_name:
                term.external_name = raw_name
                changed = True
            if str(term.normalized_external_name or "") != normalized_name:
                term.normalized_external_name = normalized_name
                changed = True
            if tag_meta:
                existing_meta = (
                    term.metadata_json if isinstance(term.metadata_json, dict) else {}
                )
                merged_meta = {**existing_meta, **tag_meta}
                if merged_meta != existing_meta:
                    term.metadata_json = merged_meta
                    changed = True
            term.last_seen_at = now
            if changed:
                term.updated_at = now
                stats["terms_updated"] += 1
        stats["terms_upserted"] += 1
    if stats["terms_upserted"]:
        db.flush()
    return stats


# -- Tag-detail normalization helpers ----------------------------------------


def _normalize_str_list(values: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _normalize_taxonomy_text(value)
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _normalize_danbooru_example_url(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    text = re.sub(r"/wiki\s+pages/", "/wiki_pages/", text, flags=re.IGNORECASE)
    return text


def _normalize_example_list(values: list[str], authority_name: str) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    source = str(authority_name or "").strip().lower()
    for value in values:
        text = str(value or "").strip()
        if source == "danbooru":
            text = _normalize_danbooru_example_url(text)
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _build_default_example_url(
    authority_name: str, external_name: str, metadata: dict[str, Any]
) -> str:
    normalized_authority = str(authority_name or "").strip().lower()
    name = str(external_name or "").strip()
    if not name:
        return ""
    if normalized_authority == "civitai":
        encoded = quote(name, safe="")
        web_base = getattr(app_config, "CIVITAI_WEB_BASE_URL", "https://civitai.red")
        return f"{web_base}/search/images?tags={encoded}&sortBy=images_v6"
    if normalized_authority == "danbooru":
        wiki_url = (
            str(metadata.get("wiki_url") or "").strip()
            if isinstance(metadata, dict)
            else ""
        )
        if wiki_url:
            return _normalize_danbooru_example_url(wiki_url)
        encoded = quote(name, safe="")
        return f"https://danbooru.donmai.us/wiki_pages/{encoded}"
    if normalized_authority == "prompt":
        encoded = quote(name, safe="")
        return f"/?search={encoded}"
    return ""


def _with_source_default_example_first(
    authority_name: str,
    external_name: str,
    metadata: dict[str, Any],
    values: list[str],
) -> list[str]:
    normalized = _normalize_example_list(values, authority_name)
    default_url = str(
        _build_default_example_url(authority_name, external_name, metadata) or ""
    ).strip()
    if str(authority_name or "").strip().lower() == "danbooru":
        default_url = _normalize_danbooru_example_url(default_url)
    if not default_url:
        return normalized
    reordered = [item for item in normalized if item != default_url]
    return [default_url, *reordered]


def _get_term_concept(db: Session, term: AuthorityTerm) -> Concept | None:
    if term.concept_id is None:
        return None
    return db.query(Concept).filter(Concept.id == term.concept_id).first()


# -- CivitAI rescan SSE generator --------------------------------------------


def _rescan_civitai_observations_inner(
    db: Session,
    dry_run: bool,
    emit: Callable[[str, dict], str],
) -> Generator[str, None, None]:
    from atelierai.config import IMAGE_LIBRARY_PATH

    library_path = Path(IMAGE_LIBRARY_PATH)
    if not library_path.is_dir():
        yield emit("error_event", {"error": "Image library path not found.", "current_image": 0})
        yield emit("complete", {
            "total_images": 0, "tags_processed": 0, "unique_tags": 0,
            "pre_existing_tags": 0, "new_tags": 0,
            "observations_created": 0, "observations_skipped": 0,
            "errors": 1, "dry_run": dry_run,
        })
        return

    sidecar_files = sorted(library_path.glob("*.json"))
    total_images = len(sidecar_files)
    tags_processed = 0
    unique_tag_names: set[str] = set()
    pre_existing_tags = 0
    new_tags = 0
    observations_created = 0
    observations_skipped = 0
    error_count = 0

    authority = _get_or_create_authority(db, "civitai")
    known_term_names: set[str] = set()
    if authority:
        rows = (
            db.query(AuthorityTerm.normalized_external_name)
            .filter(AuthorityTerm.authority_id == authority.id)
            .all()
        )
        known_term_names = {r[0] for r in rows if r[0]}

    now = datetime.utcnow()

    for idx, json_file in enumerate(sidecar_files, start=1):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            error_count += 1
            yield emit("error_event", {"current_image": idx, "file": json_file.name, "error": str(exc)})
            yield emit("progress", {
                "current_image": idx, "total_images": total_images,
                "tags_processed": tags_processed, "unique_tags": len(unique_tag_names),
                "pre_existing_tags": pre_existing_tags, "new_tags": new_tags,
                "observations_created": observations_created,
                "observations_skipped": observations_skipped,
            })
            continue

        if not isinstance(data, dict):
            yield emit("progress", {
                "current_image": idx, "total_images": total_images,
                "tags_processed": tags_processed, "unique_tags": len(unique_tag_names),
                "pre_existing_tags": pre_existing_tags, "new_tags": new_tags,
                "observations_created": observations_created,
                "observations_skipped": observations_skipped,
            })
            continue

        civitai = data.get("civitai")
        if not isinstance(civitai, dict):
            yield emit("progress", {
                "current_image": idx, "total_images": total_images,
                "tags_processed": tags_processed, "unique_tags": len(unique_tag_names),
                "pre_existing_tags": pre_existing_tags, "new_tags": new_tags,
                "observations_created": observations_created,
                "observations_skipped": observations_skipped,
            })
            continue

        tags = civitai.get("tags")
        if not isinstance(tags, list) or not tags:
            yield emit("progress", {
                "current_image": idx, "total_images": total_images,
                "tags_processed": tags_processed, "unique_tags": len(unique_tag_names),
                "pre_existing_tags": pre_existing_tags, "new_tags": new_tags,
                "observations_created": observations_created,
                "observations_skipped": observations_skipped,
            })
            continue

        try:
            stats = _upsert_civitai_authority_terms(db, civitai)
            tags_processed += stats.get("terms_upserted", 0)
            for tag in tags:
                if not isinstance(tag, dict):
                    continue
                raw_name = str(tag.get("name") or "").strip()
                if not raw_name:
                    continue
                norm = _normalize_taxonomy_text(raw_name)
                if norm in unique_tag_names:
                    continue
                unique_tag_names.add(norm)
                if norm in known_term_names:
                    pre_existing_tags += 1
                else:
                    new_tags += 1
                    known_term_names.add(norm)
        except Exception as exc:
            db.rollback()
            error_count += 1
            yield emit("error_event", {
                "current_image": idx, "file": json_file.name, "error": f"upsert_terms: {exc}",
            })

        if not dry_run:
            try:
                image_stem = json_file.stem
                image_row = (
                    db.query(ImageModel.id)
                    .filter(ImageModel.file_path.like(image_stem + ".%"))
                    .first()
                )
                if image_row is not None:
                    image_id = image_row[0]
                    tag_norms = set()
                    for tag in tags:
                        if not isinstance(tag, dict):
                            continue
                        raw_name = str(tag.get("name") or "").strip()
                        if raw_name:
                            tag_norms.add(_normalize_taxonomy_text(raw_name))
                    if tag_norms and authority:
                        matched_terms = (
                            db.query(AuthorityTerm)
                            .filter(
                                AuthorityTerm.authority_id == authority.id,
                                AuthorityTerm.normalized_external_name.in_(tag_norms),
                            )
                            .all()
                        )
                        seen_concept_ids: set[int] = set()
                        for term in matched_terms:
                            existing = (
                                db.query(ImageConceptObservation.id)
                                .filter(
                                    ImageConceptObservation.image_id == image_id,
                                    ImageConceptObservation.authority_term_id == term.id,
                                )
                                .first()
                            )
                            if existing is not None:
                                observations_skipped += 1
                                continue
                            if term.concept_id is not None:
                                if term.concept_id in seen_concept_ids:
                                    observations_skipped += 1
                                    continue
                                dup_concept = (
                                    db.query(ImageConceptObservation.id)
                                    .filter(
                                        ImageConceptObservation.image_id == image_id,
                                        ImageConceptObservation.concept_id == term.concept_id,
                                        ImageConceptObservation.authority_id == authority.id,
                                    )
                                    .first()
                                )
                                if dup_concept is not None:
                                    observations_skipped += 1
                                    continue
                                seen_concept_ids.add(term.concept_id)
                            db.add(ImageConceptObservation(
                                image_id=image_id,
                                concept_id=term.concept_id,
                                authority_id=authority.id,
                                authority_term_id=term.id,
                                source_type=ObservationSource.IMPORT,
                                certainty_label=ObservationCertainty.LIKELY,
                                is_present=True,
                                is_curated=False,
                                created_at=now,
                                updated_at=now,
                            ))
                            observations_created += 1
                        if observations_created:
                            db.flush()
            except Exception as exc:
                db.rollback()
                error_count += 1
                yield emit("error_event", {
                    "current_image": idx, "file": json_file.name, "error": f"observations: {exc}",
                })

        yield emit("progress", {
            "current_image": idx, "total_images": total_images,
            "tags_processed": tags_processed, "unique_tags": len(unique_tag_names),
            "pre_existing_tags": pre_existing_tags, "new_tags": new_tags,
            "observations_created": observations_created,
            "observations_skipped": observations_skipped,
        })

    if not dry_run and (observations_created > 0 or tags_processed > 0):
        try:
            db.commit()
        except Exception as exc:
            db.rollback()
            yield emit("error_event", {
                "current_image": total_images, "error": f"commit failed: {exc}",
            })

    yield emit("complete", {
        "total_images": total_images,
        "tags_processed": tags_processed,
        "unique_tags": len(unique_tag_names),
        "pre_existing_tags": pre_existing_tags,
        "new_tags": new_tags,
        "observations_created": observations_created,
        "observations_skipped": observations_skipped,
        "errors": error_count,
        "dry_run": dry_run,
    })


# ============================================================
# Routes
# ============================================================


@router.get("/review/summary", response_model=dict)
def taxonomy_review_summary(db: Session = Depends(get_db)):
    concepts_total = db.query(Concept).count()
    concepts_active = db.query(Concept).filter(Concept.status == "active").count()
    concepts_merged = db.query(Concept).filter(Concept.status == "merged").count()
    aliases_total = db.query(ConceptAlias).count()
    terms_total = db.query(AuthorityTerm).count()
    unresolved_terms = (
        db.query(AuthorityTerm).filter(AuthorityTerm.concept_id.is_(None)).count()
    )
    observations_total = db.query(ImageConceptObservation).count()
    return {
        "concepts_total": concepts_total,
        "concepts_active": concepts_active,
        "concepts_merged": concepts_merged,
        "aliases_total": aliases_total,
        "authority_terms_total": terms_total,
        "unresolved_terms_total": unresolved_terms,
        "observations_total": observations_total,
    }


@router.get("/review/unresolved_terms", response_model=list[dict])
def taxonomy_unresolved_terms(
    authority: Optional[str] = None,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    capped_limit = max(1, min(int(limit), 1000))
    query = (
        db.query(AuthorityTerm, TagAuthority)
        .join(TagAuthority, TagAuthority.id == AuthorityTerm.authority_id)
        .filter(AuthorityTerm.concept_id.is_(None))
        .order_by(AuthorityTerm.id.asc())
    )
    if authority:
        query = query.filter(
            func.lower(TagAuthority.name) == authority.strip().lower()
        )
    rows = query.limit(capped_limit).all()
    return [
        {
            "authority_term_id": term.id,
            "authority": auth.name,
            "external_tag_id": term.external_tag_id,
            "external_name": term.external_name,
            "normalized_external_name": term.normalized_external_name,
            "last_seen_at": (
                term.last_seen_at.isoformat() if term.last_seen_at else None
            ),
        }
        for term, auth in rows
    ]


@router.get("/review/potential_duplicates", response_model=list[dict])
def taxonomy_potential_duplicates(limit: int = 200, db: Session = Depends(get_db)):
    capped_limit = max(1, min(int(limit), 2000))
    concepts = (
        db.query(Concept)
        .filter(Concept.status == "active")
        .order_by(Concept.id.asc())
        .limit(10000)
        .all()
    )
    groups: dict[str, list[Concept]] = {}
    for concept in concepts:
        key = _duplicate_key(concept.canonical_name)
        if not key:
            continue
        groups.setdefault(key, []).append(concept)
    duplicates: list[dict] = []
    for key, members in groups.items():
        if len(members) < 2:
            continue
        duplicates.append({
            "duplicate_key": key,
            "count": len(members),
            "concepts": [
                {"id": c.id, "canonical_name": c.canonical_name, "status": c.status}
                for c in members
            ],
        })
    duplicates.sort(key=lambda row: (-row["count"], row["duplicate_key"]))
    return duplicates[:capped_limit]


@router.get("/concepts", response_model=list[dict])
def taxonomy_list_concepts(
    query: Optional[str] = None,
    status: str = "active",
    limit: int = 200,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    capped_limit = max(1, min(int(limit), 1000))
    safe_offset = max(0, int(offset))
    q = db.query(Concept)
    if status != "all":
        q = q.filter(Concept.status == status)
    if query:
        needle = f"%{query.strip().lower()}%"
        q = q.filter(func.lower(Concept.canonical_name).like(needle))
    rows = (
        q.order_by(Concept.canonical_name.asc())
        .offset(safe_offset)
        .limit(capped_limit)
        .all()
    )
    source_map = _concept_source_map(db, [int(c.id) for c in rows])
    response: list[dict] = []
    for concept in rows:
        alias_count = (
            db.query(ConceptAlias).filter(ConceptAlias.concept_id == concept.id).count()
        )
        term_count = (
            db.query(AuthorityTerm).filter(AuthorityTerm.concept_id == concept.id).count()
        )
        observation_count = (
            db.query(ImageConceptObservation)
            .filter(ImageConceptObservation.concept_id == concept.id)
            .count()
        )
        source_labels = source_map.get(int(concept.id), [])
        response.append({
            "id": concept.id,
            "canonical_name": concept.canonical_name,
            "description": concept.description,
            "slug": concept.slug,
            "status": concept.status,
            "parent_concept_id": concept.parent_concept_id,
            "alias_count": alias_count,
            "authority_term_count": term_count,
            "observation_count": observation_count,
            "source_labels": source_labels,
            "display_prefix": _concept_display_prefix(source_labels),
        })
    return response


@router.patch("/concepts/{concept_id}", response_model=dict)
def taxonomy_update_concept(
    concept_id: int,
    payload: TaxonomyConceptUpdateRequest,
    db: Session = Depends(get_db),
):
    concept = db.query(Concept).filter(Concept.id == concept_id).first()
    if concept is None:
        raise HTTPException(status_code=404, detail="Concept not found")
    if payload.canonical_name is not None:
        normalized_name = _normalize_taxonomy_text(payload.canonical_name)
        if not normalized_name:
            raise HTTPException(status_code=400, detail="canonical_name cannot be empty")
        duplicate = (
            db.query(Concept)
            .filter(Concept.canonical_name == normalized_name, Concept.id != concept_id)
            .first()
        )
        if duplicate is not None:
            raise HTTPException(
                status_code=409,
                detail="Another concept already uses that canonical_name",
            )
        concept.canonical_name = normalized_name
    if payload.description is not None:
        concept.description = payload.description.strip() or None
    if payload.concept_type is not None:
        concept.concept_type = payload.concept_type.strip() or None
    if payload.status is not None:
        concept.status = payload.status.strip() or None
    concept.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(concept)
    return {
        "message": "Concept updated.",
        "concept": {
            "id": concept.id,
            "canonical_name": concept.canonical_name,
            "description": concept.description,
            "concept_type": concept.concept_type,
            "status": concept.status,
            "parent_concept_id": concept.parent_concept_id,
        },
    }


@router.post("/concepts/{concept_id}/aliases", response_model=dict)
def taxonomy_add_alias(
    concept_id: int,
    payload: TaxonomyAliasCreateRequest,
    db: Session = Depends(get_db),
):
    concept = db.query(Concept).filter(Concept.id == concept_id).first()
    if concept is None:
        raise HTTPException(status_code=404, detail="Concept not found")
    alias_raw = (payload.alias or "").strip()
    if not alias_raw:
        raise HTTPException(status_code=400, detail="Alias cannot be empty")
    normalized_alias = _normalize_taxonomy_text(alias_raw)
    existing = (
        db.query(ConceptAlias)
        .filter(
            ConceptAlias.concept_id == concept_id,
            ConceptAlias.normalized_alias == normalized_alias,
        )
        .first()
    )
    if existing is not None:
        return {
            "message": "Alias already exists for this concept.",
            "concept_id": concept_id,
            "alias_id": existing.id,
            "normalized_alias": existing.normalized_alias,
        }
    authority_id = None
    if payload.authority_name:
        authority = (
            db.query(TagAuthority)
            .filter(
                func.lower(TagAuthority.name) == payload.authority_name.strip().lower()
            )
            .first()
        )
        if authority is None:
            raise HTTPException(status_code=404, detail="Authority not found")
        authority_id = authority.id
    alias = ConceptAlias(
        concept_id=concept_id,
        alias=alias_raw,
        normalized_alias=normalized_alias,
        alias_type=payload.alias_type,
        is_preferred=payload.is_preferred,
        authority_id=authority_id,
        external_tag_id=payload.external_tag_id,
    )
    db.add(alias)
    concept.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(alias)
    return {
        "message": "Alias created.",
        "concept_id": concept_id,
        "alias": {
            "id": alias.id,
            "alias": alias.alias,
            "normalized_alias": alias.normalized_alias,
            "alias_type": alias.alias_type,
            "is_preferred": alias.is_preferred,
            "authority_id": alias.authority_id,
            "external_tag_id": alias.external_tag_id,
        },
    }


@router.delete("/concepts/{concept_id}/aliases/{alias_id}", response_model=dict)
def taxonomy_delete_alias(
    concept_id: int,
    alias_id: int,
    db: Session = Depends(get_db),
):
    """Remove an alias from a concept."""
    concept = db.query(Concept).filter(Concept.id == concept_id).first()
    if concept is None:
        raise HTTPException(status_code=404, detail="Concept not found")
    alias = (
        db.query(ConceptAlias)
        .filter(
            ConceptAlias.id == alias_id,
            ConceptAlias.concept_id == concept_id,
        )
        .first()
    )
    if alias is None:
        raise HTTPException(status_code=404, detail="Alias not found for this concept")
    db.delete(alias)
    concept.updated_at = datetime.utcnow()
    db.commit()
    return {"message": "Alias deleted.", "concept_id": concept_id, "alias_id": alias_id}


@router.post("/review/merge_concepts", response_model=dict)
def taxonomy_merge_concepts(
    payload: TaxonomyMergeRequest, db: Session = Depends(get_db)
):
    if payload.source_concept_id == payload.target_concept_id:
        raise HTTPException(
            status_code=400,
            detail="source_concept_id and target_concept_id must differ",
        )
    source = db.query(Concept).filter(Concept.id == payload.source_concept_id).first()
    if source is None:
        raise HTTPException(status_code=404, detail="Source concept not found")
    target = db.query(Concept).filter(Concept.id == payload.target_concept_id).first()
    if target is None:
        raise HTTPException(status_code=404, detail="Target concept not found")
    moved_terms = 0
    moved_observations = 0
    moved_aliases = 0
    terms = db.query(AuthorityTerm).filter(AuthorityTerm.concept_id == source.id).all()
    observations = (
        db.query(ImageConceptObservation)
        .filter(ImageConceptObservation.concept_id == source.id)
        .all()
    )
    source_aliases = (
        db.query(ConceptAlias).filter(ConceptAlias.concept_id == source.id).all()
    )
    if payload.dry_run:
        target_aliases = (
            db.query(ConceptAlias).filter(ConceptAlias.concept_id == target.id).all()
        )
        target_alias_set = {
            _normalize_taxonomy_text(a.normalized_alias or a.alias or "")
            for a in target_aliases
        }
        mergeable_aliases = 0
        duplicate_aliases = 0
        for alias in source_aliases:
            normalized_alias = _normalize_taxonomy_text(
                alias.normalized_alias or alias.alias or ""
            )
            if normalized_alias in target_alias_set:
                duplicate_aliases += 1
            else:
                mergeable_aliases += 1
        source_name_alias_conflict = (
            _normalize_taxonomy_text(source.canonical_name) in target_alias_set
        )
        projected_moved_aliases = mergeable_aliases
        if payload.create_source_alias and not source_name_alias_conflict:
            projected_moved_aliases += 1
        return {
            "message": "Dry-run merge preview.",
            "dry_run": True,
            "source_concept_id": source.id,
            "target_concept_id": target.id,
            "source_concept_name": source.canonical_name,
            "target_concept_name": target.canonical_name,
            "would_move_authority_terms": len(terms),
            "would_move_observations": len(observations),
            "would_move_aliases": projected_moved_aliases,
            "would_drop_duplicate_aliases": duplicate_aliases,
            "would_deactivate_source": payload.deactivate_source,
            "source_status_after": (
                "merged" if payload.deactivate_source else source.status
            ),
        }
    for term in terms:
        term.concept_id = target.id
        term.updated_at = datetime.utcnow()
        moved_terms += 1
    for obs in observations:
        obs.concept_id = target.id
        obs.updated_at = datetime.utcnow()
        moved_observations += 1
    for alias in source_aliases:
        normalized_alias = alias.normalized_alias or _normalize_taxonomy_text(
            alias.alias
        )
        existing_target_alias = (
            db.query(ConceptAlias)
            .filter(
                ConceptAlias.concept_id == target.id,
                ConceptAlias.normalized_alias == normalized_alias,
            )
            .first()
        )
        if existing_target_alias is not None:
            db.delete(alias)
            continue
        alias.concept_id = target.id
        moved_aliases += 1
    if payload.create_source_alias:
        normalized_source_name = _normalize_taxonomy_text(source.canonical_name)
        existing_source_alias = (
            db.query(ConceptAlias)
            .filter(
                ConceptAlias.concept_id == target.id,
                ConceptAlias.normalized_alias == normalized_source_name,
            )
            .first()
        )
        if existing_source_alias is None:
            db.add(ConceptAlias(
                concept_id=target.id,
                alias=source.canonical_name,
                normalized_alias=normalized_source_name,
                alias_type="merged_from",
                is_preferred=False,
            ))
            moved_aliases += 1
    if payload.deactivate_source:
        source.status = "merged"
        source.parent_concept_id = target.id
    source.updated_at = datetime.utcnow()
    target.updated_at = datetime.utcnow()
    db.commit()
    return {
        "message": "Concept merge complete.",
        "dry_run": False,
        "source_concept_id": source.id,
        "target_concept_id": target.id,
        "moved_authority_terms": moved_terms,
        "moved_observations": moved_observations,
        "moved_aliases": moved_aliases,
        "source_status": source.status,
    }


@router.post("/bootstrap/import", response_model=dict)
def taxonomy_bootstrap_import(
    payload: TaxonomyBootstrapImportRequest, db: Session = Depends(get_db)
):
    rows = _parse_bootstrap_terms(payload.format, payload.raw_text)
    return _execute_taxonomy_bootstrap_import(
        db,
        authority_name=payload.authority_name,
        rows=rows,
        dry_run=payload.dry_run,
    )


@router.post("/bootstrap/import_file", response_model=dict)
async def taxonomy_bootstrap_import_file(
    authority_name: str = Form("user"),
    format: str = Form("json"),
    dry_run: bool = Form(True),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    try:
        raw_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded text")
    rows = _parse_bootstrap_terms(format, raw_text)
    result = _execute_taxonomy_bootstrap_import(
        db,
        authority_name=authority_name,
        rows=rows,
        dry_run=dry_run,
    )
    result["source_file"] = file.filename
    return result


@router.get("/concepts/export", response_model=dict)
def taxonomy_export_concepts(
    include_aliases: bool = True,
    include_descriptions: bool = True,
    status: str = "active",
    authorities: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    parsed_authorities: Optional[list[str]] = None
    if authorities is not None:
        parsed_authorities = []
        for item in authorities.split(","):
            authority_name = str(item or "").strip().lower()
            if authority_name:
                parsed_authorities.append(authority_name)
    export_document = _build_concept_transfer_export_document(
        db,
        include_aliases=include_aliases,
        include_descriptions=include_descriptions,
        status=status,
        authorities=parsed_authorities,
    )
    return export_document


@router.post("/concepts/import", response_model=dict)
def taxonomy_import_concepts(
    payload: TaxonomyConceptTransferImportRequest,
    db: Session = Depends(get_db),
):
    if payload.mode != "graft":
        raise HTTPException(status_code=409, detail=f"Unsupported mode: {payload.mode}")
    if payload.root_policy not in {"strict", "permissive"}:
        raise HTTPException(
            status_code=409,
            detail=f"Unsupported root policy: {payload.root_policy}",
        )

    document = _coerce_concept_transfer_document(dict(payload.document or {}))
    indexes = _build_concept_transfer_indexes(db)
    authority_cache: dict[str, TagAuthority] = {}
    summary: dict[str, int] = {
        "roots_processed": 0,
        "roots_created": 0,
        "roots_skipped_by_policy": 0,
        "concepts_matched": 0,
        "concepts_created": 0,
        "branches_grafted": 0,
        "aliases_added": 0,
        "tag_links_added": 0,
        "tag_conflicts_skipped": 0,
        "validation_errors": 0,
    }
    conflicts: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []

    for root_node in list(document.get("roots") or []):
        summary["roots_processed"] += 1
        root_name = str(root_node.get("name") or "").strip() or "<unnamed-root>"
        _import_transfer_branch(
            db,
            root_node,
            parent_concept=None,
            root_policy=payload.root_policy,
            indexes=indexes,
            authority_cache=authority_cache,
            summary=summary,
            actions=actions,
            conflicts=conflicts,
            incoming_path=root_name,
        )

    if payload.dry_run:
        db.rollback()
    else:
        db.commit()

    return {
        "ok": True,
        "dry_run": payload.dry_run,
        "summary": summary,
        "conflicts": conflicts,
        "actions": actions,
    }


@router.post("/concepts/import_file", response_model=dict)
async def taxonomy_import_concepts_file(
    mode: str = Form("graft"),
    root_policy: str = Form("strict"),
    dry_run: bool = Form(True),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    try:
        raw_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded text")
    try:
        parsed_document = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON file: {exc}")

    result = taxonomy_import_concepts(
        TaxonomyConceptTransferImportRequest(
            document=parsed_document,
            mode=mode,
            root_policy=root_policy,
            dry_run=dry_run,
        ),
        db,
    )
    result["source_file"] = file.filename
    return result


@router.post("/concepts", response_model=dict)
def taxonomy_create_concept(
    payload: TaxonomyConceptCreateRequest, db: Session = Depends(get_db)
):
    canonical_name = _normalize_taxonomy_text(payload.canonical_name)
    if not canonical_name:
        raise HTTPException(status_code=400, detail="canonical_name is required")
    existing = (
        db.query(Concept).filter(Concept.canonical_name == canonical_name).first()
    )
    if existing is not None:
        return {
            "message": "Concept already exists.",
            "concept": {
                "id": existing.id,
                "canonical_name": existing.canonical_name,
                "slug": existing.slug,
                "status": existing.status,
                "parent_concept_id": existing.parent_concept_id,
            },
        }
    if payload.parent_concept_id is not None:
        parent = (
            db.query(Concept).filter(Concept.id == payload.parent_concept_id).first()
        )
        if parent is None:
            raise HTTPException(status_code=404, detail="Parent concept not found")
    slug = _ensure_unique_concept_slug(db, _slugify_concept_name(canonical_name))
    concept = Concept(
        canonical_name=canonical_name,
        slug=slug,
        description=payload.description,
        status="active",
        parent_concept_id=payload.parent_concept_id,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(concept)
    db.flush()
    _ensure_alias_for_concept(
        db, concept_id=concept.id, alias_text=canonical_name, alias_type="canonical"
    )
    db.commit()
    db.refresh(concept)
    return {
        "message": "Concept created.",
        "concept": {
            "id": concept.id,
            "canonical_name": concept.canonical_name,
            "slug": concept.slug,
            "status": concept.status,
            "parent_concept_id": concept.parent_concept_id,
        },
    }


@router.post("/concepts/{concept_id}/parent", response_model=dict)
def taxonomy_update_parent(
    concept_id: int,
    payload: TaxonomyParentUpdateRequest,
    db: Session = Depends(get_db),
):
    concept = db.query(Concept).filter(Concept.id == concept_id).first()
    if concept is None:
        raise HTTPException(status_code=404, detail="Concept not found")
    new_parent_id = payload.parent_concept_id
    if new_parent_id == concept_id:
        raise HTTPException(status_code=400, detail="Concept cannot be its own parent")
    if new_parent_id is not None:
        parent = db.query(Concept).filter(Concept.id == new_parent_id).first()
        if parent is None:
            raise HTTPException(status_code=404, detail="Parent concept not found")
        if _is_descendant(db, ancestor_id=concept.id, candidate_descendant_id=new_parent_id):
            raise HTTPException(
                status_code=400, detail="Parent assignment would create a cycle"
            )
    if payload.dry_run:
        return {
            "message": "Dry-run parent assignment preview.",
            "dry_run": True,
            "concept_id": concept.id,
            "current_parent_concept_id": concept.parent_concept_id,
            "new_parent_concept_id": new_parent_id,
        }
    concept.parent_concept_id = new_parent_id
    concept.updated_at = datetime.utcnow()
    db.commit()
    return {
        "message": "Concept parent updated.",
        "dry_run": False,
        "concept_id": concept.id,
        "parent_concept_id": concept.parent_concept_id,
    }


@router.delete("/concepts/{concept_id}", response_model=dict)
def taxonomy_delete_concept_branch(concept_id: int, db: Session = Depends(get_db)):
    concept = db.query(Concept).filter(Concept.id == concept_id).first()
    if concept is None:
        raise HTTPException(status_code=404, detail="Concept not found")
    to_visit = [int(concept_id)]
    branch_ids: set[int] = set()
    while to_visit:
        current = to_visit.pop()
        if current in branch_ids:
            continue
        branch_ids.add(current)
        children = (
            db.query(Concept.id).filter(Concept.parent_concept_id == current).all()
        )
        to_visit.extend(int(row.id) for row in children)
    db.query(AuthorityTerm).filter(AuthorityTerm.concept_id.in_(branch_ids)).update(
        {AuthorityTerm.concept_id: None, AuthorityTerm.updated_at: datetime.utcnow()},
        synchronize_session=False,
    )
    db.query(ImageConceptObservation).filter(
        ImageConceptObservation.concept_id.in_(branch_ids)
    ).delete(synchronize_session=False)
    db.query(ConceptAlias).filter(ConceptAlias.concept_id.in_(branch_ids)).delete(
        synchronize_session=False
    )
    db.query(Concept).filter(Concept.id.in_(branch_ids)).delete(synchronize_session=False)
    db.commit()
    return {
        "message": "Concept branch deleted.",
        "deleted_concept_ids": sorted(branch_ids),
    }


# =========================================================================
# Phase 2 — Concept Prototype & Visual Similarity
# =========================================================================


@router.post("/concepts/{concept_id}/build-prototype", response_model=dict)
async def taxonomy_build_prototype(
    concept_id: int,
    payload: BuildPrototypeRequest,
    db: Session = Depends(get_db),
):
    """Build a visual prototype for a concept from reference images.

    Computes the CLIP embedding centroid of the provided reference images
    and stores it as the concept's prototype vector.
    """
    concept = db.query(Concept).filter(Concept.id == concept_id).first()
    if concept is None:
        raise HTTPException(status_code=404, detail="Concept not found")

    svc = ConceptPrototypeService(db)
    prototype = await svc.build_prototype(concept_id, payload.image_urls)

    if prototype is None:
        from services.clip_provider import get_clip_provider
        if get_clip_provider() is None:
            raise HTTPException(
                status_code=503,
                detail="CLIP provider unavailable — cannot build prototype",
            )
        raise HTTPException(
            status_code=422,
            detail="No images could be encoded. Check that image URLs are accessible.",
        )

    db.refresh(concept)
    return {
        "message": "Prototype built.",
        "concept_id": concept_id,
        "prototype_source_count": concept.prototype_source_count,
        "prototype_updated_at": (
            concept.prototype_updated_at.isoformat()
            if concept.prototype_updated_at
            else None
        ),
    }


# ---------------------------------------------------------------------------
# Prototype Lab — auto-build, batch-build, stats
# ---------------------------------------------------------------------------


@router.get("/prototypes/stats", response_model=PrototypeStatsResponse)
def taxonomy_prototype_stats(db: Session = Depends(get_db)):
    """Return global prototype coverage statistics."""
    svc = ConceptPrototypeService(db)
    return svc.get_prototype_stats()


@router.post(
    "/concepts/{concept_id}/auto-build-prototype",
    response_model=AutoBuildPrototypeResponse,
)
async def taxonomy_auto_build_prototype(
    concept_id: int,
    max_images: int = 10,
    db: Session = Depends(get_db),
):
    """Auto-build a prototype for a concept from its observed images."""
    svc = ConceptPrototypeService(db)
    return await svc.auto_build_prototype(concept_id, max_images=max_images)


@router.post("/prototypes/batch-build", response_model=BatchBuildResponse)
async def taxonomy_batch_build_prototypes(
    payload: BatchBuildRequest,
    db: Session = Depends(get_db),
):
    """Build prototypes for multiple concepts in sequence."""
    svc = ConceptPrototypeService(db)
    results = await svc.batch_build_prototypes(
        payload.concept_ids, max_images=payload.max_images
    )
    built = sum(1 for r in results if r["status"] == "built")
    return BatchBuildResponse(
        total_requested=len(payload.concept_ids),
        built=built,
        failed=len(payload.concept_ids) - built,
        results=[AutoBuildPrototypeResponse(**r) for r in results],
    )


@router.post("/prototypes/stream-build")
async def taxonomy_stream_build_prototypes(
    payload: StreamBuildRequest,
):
    """SSE endpoint: build prototypes with real-time progress events.

    Events:
      - ``progress``: per-concept result (type=start|result|error) and running totals
      - ``complete``: final summary with built/failed counts
    """

    def _sse_event(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"

    async def event_stream():
        db = SessionLocal()
        try:
            svc = ConceptPrototypeService(db)
            async for event_name, data in svc.stream_build_prototypes(
                payload.concept_ids, max_images=payload.max_images
            ):
                yield _sse_event(event_name, data)
        finally:
            db.close()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/concepts/{concept_id}/profile", response_model=ConceptProfileResponse)
def taxonomy_concept_profile(
    concept_id: int,
    db: Session = Depends(get_db),
):
    """Return a rich concept profile with prototype stats and linked authority terms."""
    concept = db.query(Concept).filter(Concept.id == concept_id).first()
    if concept is None:
        raise HTTPException(status_code=404, detail="Concept not found")

    # Prototype stats
    prototype_info = None
    if concept.prototype_vector is not None:
        prototype_info = {
            "source_count": concept.prototype_source_count,
            "updated_at": (
                concept.prototype_updated_at.isoformat()
                if concept.prototype_updated_at
                else None
            ),
            "has_vector": True,
        }

    # Linked aliases
    aliases = [
        {"id": a.id, "alias": a.alias, "alias_type": a.alias_type}
        for a in concept.aliases
    ]

    # Linked authority terms
    authority_terms = [
        {
            "id": t.id,
            "external_name": t.external_name,
            "authority_id": t.authority_id,
            "external_tag_id": t.external_tag_id,
        }
        for t in concept.authority_terms
    ]

    # Parent concept
    parent_concept = None
    if concept.parent:
        parent_concept = {
            "id": concept.parent.id,
            "canonical_name": concept.parent.canonical_name,
        }

    # Children
    children = [
        {"id": ch.id, "canonical_name": ch.canonical_name}
        for ch in concept.children
    ]

    # Attributes (concept → its attribute concepts)
    attributes = []
    for attr in concept.attributes:
        attr_concept = attr.attribute_concept
        attributes.append(
            ConceptAttributeEntry(
                concept_id=concept.id,
                attribute_concept_id=attr_concept.id,
                attribute_concept_name=attr_concept.canonical_name,
                attribute_kind=attr.attribute_kind,
                invariance=attr.invariance,
                consistency_score=attr.consistency_score,
                notes=attr.notes,
            )
        )

    return ConceptProfileResponse(
        id=concept.id,
        canonical_name=concept.canonical_name,
        slug=concept.slug,
        description=concept.description,
        status=concept.status,
        concept_type=concept.concept_type,
        parent_concept_id=concept.parent_concept_id,
        prototype=prototype_info,
        aliases=aliases,
        authority_terms=authority_terms,
        attributes=attributes,
        parent_concept=parent_concept,
        children=children,
    )


@router.post("/concepts/{concept_id}/score", response_model=ScoreImageResponse)
async def taxonomy_score_candidate(
    concept_id: int,
    payload: ScoreImageRequest,
    db: Session = Depends(get_db),
):
    """Score a candidate image against a concept's visual prototype.

    Returns identity score (cosine similarity to prototype), optional context
    score (cosine to text query), and composite (identity × context).
    """
    concept = db.query(Concept).filter(Concept.id == concept_id).first()
    if concept is None:
        raise HTTPException(status_code=404, detail="Concept not found")

    if concept.prototype_vector is None:
        raise HTTPException(
            status_code=400,
            detail="Concept has no prototype — build one first via POST /concepts/{id}/build-prototype",
        )

    from services.clip_provider import get_clip_provider
    provider = get_clip_provider()
    if provider is None:
        return ScoreImageResponse(
            concept_id=concept_id,
            image_url=payload.image_url,
            identity_score=None,
            context_score=None,
            composite_score=None,
            clip_available=False,
        )

    svc = ConceptPrototypeService(db)
    prototype = svc.get_prototype_vector(concept_id)

    if payload.context_text:
        result = await svc.score_composite(
            payload.image_url, prototype, payload.context_text
        )
        if result is None:
            return ScoreImageResponse(
                concept_id=concept_id,
                image_url=payload.image_url,
                identity_score=None,
                context_score=None,
                composite_score=None,
                clip_available=True,
            )
        return ScoreImageResponse(
            concept_id=concept_id,
            image_url=payload.image_url,
            identity_score=result["identity"],
            context_score=result["context"],
            composite_score=result["composite"],
            clip_available=True,
        )
    else:
        identity = await svc.score_identity(payload.image_url, prototype)
        return ScoreImageResponse(
            concept_id=concept_id,
            image_url=payload.image_url,
            identity_score=identity,
            context_score=None,
            composite_score=None,
            clip_available=True,
        )


# ---------------------------------------------------------------------------
# Concept Lookup (lightweight autosuggest)
# ---------------------------------------------------------------------------


@router.get("/concept-lookup")
def concept_lookup(
    q: str = "",
    limit: int = 20,
    db: Session = Depends(get_db),
):
    """Lightweight concept name lookup for autosuggest.

    Returns concepts whose canonical_name or alias matches the query prefix.
    Results are ``{id, canonical_name}`` sorted by exact-match first, then
    prefix match, then alphabetical.
    """
    q_lower = q.strip().lower()
    if not q_lower:
        return {"results": []}

    # Exact canonical match
    exact = (
        db.query(Concept.id, Concept.canonical_name)
        .filter(func.lower(Concept.canonical_name) == q_lower)
        .first()
    )

    # Prefix matches on canonical_name
    prefix_q = (
        db.query(Concept.id, Concept.canonical_name)
        .filter(
            func.lower(Concept.canonical_name).startswith(q_lower),
            func.lower(Concept.canonical_name) != q_lower,
        )
        .order_by(Concept.canonical_name)
        .limit(limit)
        .all()
    )

    # Prefix matches on aliases
    alias_q = (
        db.query(Concept.id, Concept.canonical_name)
        .join(ConceptAlias, ConceptAlias.concept_id == Concept.id)
        .filter(
            func.lower(ConceptAlias.normalized_alias).startswith(q_lower),
        )
        .order_by(Concept.canonical_name)
        .limit(limit)
        .all()
    )

    # Merge preserving order: exact, then prefixes (deduped)
    seen_ids = set()
    results = []

    if exact:
        results.append({"id": exact[0], "canonical_name": exact[1]})
        seen_ids.add(exact[0])

    for row in prefix_q + alias_q:
        if row[0] not in seen_ids:
            results.append({"id": row[0], "canonical_name": row[1]})
            seen_ids.add(row[0])
        if len(results) >= limit:
            break

    return {"results": results}


# ---------------------------------------------------------------------------
# Concept Search Pipeline (Phase 3A)
# ---------------------------------------------------------------------------


@router.post("/concept-search", response_model=ConceptSearchResponse)
async def concept_search(
    payload: ConceptSearchRequest,
    db: Session = Depends(get_db),
):
    """Full concept-based search: decompose → candidate retrieval → visual scoring.

    Accepts a natural-language query, matches concepts from surface forms,
    retrieves candidate images, and scores them with batch CLIP.
    """
    from services.clip_provider import get_clip_provider
    provider = get_clip_provider()
    clip_available = provider is not None

    svc = ConceptSearchService(db)

    # Stage 1: Decompose query
    decomposed = svc.decompose_query(payload.query)

    # Build decomposition response
    decomp_resp = DecomposeResponse(
        original_query=decomposed.original_query,
        matched_concepts=decomposed.matched_concepts,
        context_text=decomposed.context_text,
        total_surface_forms=decomposed.total_surface_forms,
    )

    # Stage 2: Retrieve candidates (if concepts matched)
    concept_ids = [mc["concept_id"] for mc in decomposed.matched_concepts]
    candidates = svc.resolve_candidate_images(
        concept_ids,
        pool_multiplier=payload.pool_multiplier,
        limit=payload.limit,
    )
    candidates_total = len(candidates)

    # Stage 3: Visual scoring
    # Use the full original query as context text for CLIP (not the
    # stripped stop-words), so the text embedding captures the entire
    # semantic intent including matched concepts.
    if candidates and concept_ids and clip_available:
        scored = await svc.visual_score_candidates(
            candidates,
            concept_ids,
            context_text=payload.query,
            limit=payload.limit,
        )
    else:
        scored = candidates[:payload.limit]

    results = [
        {
            "image_id": c.image_id,
            "file_name": c.file_name,
            "file_hash": c.file_hash,
            "thumbnail_url": c.thumbnail_url,
            "source_url": c.source_url,
            "width": c.width,
            "height": c.height,
            "identity_score": c.identity_score,
            "context_score": c.context_score,
            "composite_score": c.composite_score,
            "concept_scores": c.concept_scores,
        }
        for c in scored
    ]

    return ConceptSearchResponse(
        query=payload.query,
        decomposition=decomp_resp,
        candidates_total=candidates_total,
        clip_available=clip_available,
        results=results,
    )


@router.post("/concept-search/decompose", response_model=DecomposeResponse)
def concept_search_decompose(
    payload: ConceptSearchRequest,
    db: Session = Depends(get_db),
):
    """Debug endpoint: decompose a query without scoring.

    Shows which concepts were matched and what context text remains.
    """
    svc = ConceptSearchService(db)
    decomposed = svc.decompose_query(payload.query)

    return DecomposeResponse(
        original_query=decomposed.original_query,
        matched_concepts=decomposed.matched_concepts,
        context_text=decomposed.context_text,
        total_surface_forms=decomposed.total_surface_forms,
    )


@router.get("/concept-search/concepts-index", response_model=ConceptIndexResponse)
def concept_search_concepts_index(
    db: Session = Depends(get_db),
):
    """Audit endpoint: list all concepts with coverage stats.

    Returns prototype status, alias list, and observation counts for
    every active concept — useful for debugging search coverage.
    """
    svc = ConceptSearchService(db)
    concepts = svc.get_concepts_index()

    return ConceptIndexResponse(
        total_concepts=len(concepts),
        concepts=concepts,
    )


@router.post("/concept-search/rescan")
def concept_search_rescan(db: Session = Depends(get_db)):
    """Re-associate orphan observations with their concept via authority terms.

    Step 0 links authority_terms to concepts by surface-form matching.
    Steps 1-3 backfill concept_id on observations from authority_terms,
    with deduplication.

    Returns counts of terms linked, observations fixed, duplicates removed,
    and remaining orphans.
    """
    from sqlalchemy import text as sql_text

    # ── Step 0: Link authority terms to concepts by name ──────────────
    # Build a lookup of normalized concept surface forms → concept_id.
    surface_map: dict[str, int] = {}
    for row in (
        db.query(Concept.id, Concept.canonical_name)
        .filter(Concept.status == "active")
        .all()
    ):
        key = (row.canonical_name or "").strip().lower()
        if key and key not in surface_map:
            surface_map[key] = row.id
    for row in (
        db.query(ConceptAlias.concept_id, ConceptAlias.normalized_alias)
        .join(Concept, ConceptAlias.concept_id == Concept.id)
        .filter(Concept.status == "active")
        .all()
    ):
        key = (row.normalized_alias or "").strip().lower()
        if key and key not in surface_map:
            surface_map[key] = row.concept_id

    # Find all authority terms with concept_id IS NULL and try to match.
    orphan_terms = (
        db.query(AuthorityTerm.id, AuthorityTerm.normalized_external_name)
        .filter(AuthorityTerm.concept_id.is_(None))
        .all()
    )
    terms_linked = 0
    for term in orphan_terms:
        matched_id = surface_map.get(term.normalized_external_name)
        if matched_id is not None:
            db.query(AuthorityTerm).filter(AuthorityTerm.id == term.id).update(
                {"concept_id": matched_id}, synchronize_session="fetch"
            )
            terms_linked += 1
    if terms_linked:
        db.flush()

    # ── Step 1: Remove duplicate observations ────────────────────────
    # Find and remove null-concept observations that would create
    # duplicate (image_id, concept_id, authority_id) tuples after backfill.
    dupes = db.execute(sql_text("""
        SELECT o_null.id
        FROM image_concept_observations o_null
        JOIN authority_terms at1 ON o_null.authority_term_id = at1.id
        WHERE o_null.concept_id IS NULL
          AND at1.concept_id IS NOT NULL
          AND EXISTS (
              SELECT 1 FROM image_concept_observations o_existing
              WHERE o_existing.image_id = o_null.image_id
                AND o_existing.concept_id = at1.concept_id
                AND o_existing.authority_id = o_null.authority_id
                AND o_existing.id != o_null.id
          )
    """)).fetchall()
    dupe_ids = [r[0] for r in dupes]
    duplicates_removed = len(dupe_ids)

    if dupe_ids:
        # Delete in chunks to avoid SQLite limits
        chunk_size = 500
        for i in range(0, len(dupe_ids), chunk_size):
            chunk = dupe_ids[i:i + chunk_size]
            db.query(ImageConceptObservation).filter(
                ImageConceptObservation.id.in_(chunk)
            ).delete(synchronize_session="fetch")

    # Step 2: Find and remove same-batch conflicts (two null observations
    # for the same image+authority that would get the same concept_id).
    batch_dupes = db.execute(sql_text("""
        SELECT o2.id
        FROM image_concept_observations o1
        JOIN authority_terms at1 ON o1.authority_term_id = at1.id
        JOIN image_concept_observations o2
            ON o1.image_id = o2.image_id
            AND o1.authority_id = o2.authority_id
            AND o1.id < o2.id
        JOIN authority_terms at2 ON o2.authority_term_id = at2.id
        WHERE o1.concept_id IS NULL
          AND o2.concept_id IS NULL
          AND at1.concept_id IS NOT NULL
          AND at2.concept_id IS NOT NULL
          AND at1.concept_id = at2.concept_id
    """)).fetchall()
    batch_dupe_ids = [r[0] for r in batch_dupes]
    batch_duplicates_removed = len(batch_dupe_ids)

    if batch_dupe_ids:
        chunk_size = 500
        for i in range(0, len(batch_dupe_ids), chunk_size):
            chunk = batch_dupe_ids[i:i + chunk_size]
            db.query(ImageConceptObservation).filter(
                ImageConceptObservation.id.in_(chunk)
            ).delete(synchronize_session="fetch")

    # Step 3: Backfill concept_id from authority_terms
    result = db.execute(sql_text("""
        UPDATE image_concept_observations
        SET concept_id = (
            SELECT at.concept_id
            FROM authority_terms at
            WHERE at.id = image_concept_observations.authority_term_id
        )
        WHERE concept_id IS NULL
          AND authority_term_id IN (
              SELECT id FROM authority_terms WHERE concept_id IS NOT NULL
          )
    """))
    observations_fixed = result.rowcount

    db.commit()

    # Count remaining orphans
    remaining = db.execute(sql_text("""
        SELECT COUNT(*) FROM image_concept_observations
        WHERE concept_id IS NULL
    """)).scalar()

    return {
        "terms_linked": terms_linked,
        "observations_fixed": observations_fixed,
        "duplicates_removed": duplicates_removed + batch_duplicates_removed,
        "remaining_orphans": remaining,
        "message": (
            f"Linked {terms_linked} authority terms to concepts, "
            f"fixed {observations_fixed} observations, "
            f"removed {duplicates_removed + batch_duplicates_removed} duplicates, "
            f"{remaining} orphans remain (authority terms with no concept)."
        ),
    }


# -- Rebuild Observations SSE generator ----------------------------------------


def _rebuild_observations_inner(
    db: Session,
    dry_run: bool,
    emit: Callable[[str, dict], str],
) -> Generator[str, None, None]:
    """Rebuild all observations from sidecar JSON tag data.

    For every image with a sidecar JSON file:
      1. Extract tags from all sources (civitai, danbooru, prompt, user)
      2. Upsert authority_terms (linking to concepts by surface-form match)
      3. Create missing observations for image + authority_term pairs

    This is more thorough than ``concept_search_rescan`` because it
    re-extracts tags and creates missing authority_terms + observations,
    not just backfilling concept_id on existing rows.
    """
    from atelierai.config import IMAGE_LIBRARY_PATH

    library_path = Path(IMAGE_LIBRARY_PATH)
    if not library_path.is_dir():
        yield emit("error_event", {"error": "Image library path not found."})
        yield emit("complete", {
            "total_images": 0, "images_processed": 0,
            "tags_extracted": 0, "terms_upserted": 0, "new_terms": 0,
            "terms_linked_to_concepts": 0, "observations_created": 0,
            "observations_skipped": 0, "errors": 1, "dry_run": dry_run,
        })
        return

    sidecar_files = sorted(library_path.glob("*.json"))
    total_images = len(sidecar_files)
    images_processed = 0
    tags_extracted = 0
    terms_upserted = 0
    new_terms = 0
    terms_linked_to_concepts = 0
    observations_created = 0
    observations_skipped = 0
    error_count = 0

    # Pre-load concept surface-form map (same as rescan Step 0)
    surface_map: dict[str, int] = {}
    for row in (
        db.query(Concept.id, Concept.canonical_name)
        .filter(Concept.status == "active")
        .all()
    ):
        key = (row.canonical_name or "").strip().lower()
        if key and key not in surface_map:
            surface_map[key] = row.id
    for row in (
        db.query(ConceptAlias.concept_id, ConceptAlias.normalized_alias)
        .join(Concept, ConceptAlias.concept_id == Concept.id)
        .filter(Concept.status == "active")
        .all()
    ):
        key = (row.normalized_alias or "").strip().lower()
        if key and key not in surface_map:
            surface_map[key] = row.concept_id

    # Pre-load authorities
    authorities_cache: dict[str, int] = {}
    for auth_row in db.query(TagAuthority).all():
        authorities_cache[auth_row.name.lower()] = int(auth_row.id)

    tax = _taxonomy_service
    now = datetime.now(timezone.utc)
    commit_interval = 50
    pending_commits = 0

    for idx, json_file in enumerate(sidecar_files, start=1):
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as exc:
            error_count += 1
            yield emit("error_event", {
                "current_image": idx, "file": json_file.name, "error": str(exc),
            })
            yield emit("progress", {
                "current_image": idx, "total_images": total_images,
                "images_processed": images_processed,
                "tags_extracted": tags_extracted,
                "terms_upserted": terms_upserted,
                "new_terms": new_terms,
                "terms_linked_to_concepts": terms_linked_to_concepts,
                "observations_created": observations_created,
                "observations_skipped": observations_skipped,
            })
            continue

        if not isinstance(data, dict):
            yield emit("progress", {
                "current_image": idx, "total_images": total_images,
                "images_processed": images_processed,
                "tags_extracted": tags_extracted,
                "terms_upserted": terms_upserted,
                "new_terms": new_terms,
                "terms_linked_to_concepts": terms_linked_to_concepts,
                "observations_created": observations_created,
                "observations_skipped": observations_skipped,
            })
            continue

        # Resolve image_id from file stem
        image_stem = json_file.stem
        image_row = (
            db.query(ImageModel.id)
            .filter(ImageModel.file_path.like(f"{image_stem}.%"))
            .first()
        )
        if image_row is None:
            yield emit("progress", {
                "current_image": idx, "total_images": total_images,
                "images_processed": images_processed,
                "tags_extracted": tags_extracted,
                "terms_upserted": terms_upserted,
                "new_terms": new_terms,
                "terms_linked_to_concepts": terms_linked_to_concepts,
                "observations_created": observations_created,
                "observations_skipped": observations_skipped,
            })
            continue

        image_id = int(image_row.id)

        # Track unique constraint tuples for this image to prevent
        # collisions between unflushed session objects and committed rows.
        seen_unique_keys: set[tuple[int, int]] = set()

        # Extract tags from all sources via GalleryTagService
        tags_by_source = _gallery_tag_service.extract_image_scope_tag_names(
            data,
            normalize_taxonomy_text=tax.normalize_text,
        )

        for source, tag_names in tags_by_source.items():
            if not tag_names:
                continue

            tags_extracted += len(tag_names)

            # Get or create authority
            authority_key = source.lower()
            authority_id = authorities_cache.get(authority_key)
            if authority_id is None:
                auth = tax.get_or_create_authority(db, source)
                authority_id = int(auth.id)
                authorities_cache[authority_key] = authority_id

            # Normalize tag names
            normalized_map = {tax.normalize_text(n): n for n in tag_names if n}
            if not normalized_map:
                continue

            # Batch-load existing terms for these names
            existing_terms = (
                db.query(AuthorityTerm)
                .filter(
                    AuthorityTerm.authority_id == authority_id,
                    AuthorityTerm.normalized_external_name.in_(normalized_map.keys()),
                )
                .all()
            )
            existing_by_name = {
                t.normalized_external_name: t for t in existing_terms
            }

            for norm_name, raw_name in normalized_map.items():
                terms_upserted += 1
                term = existing_by_name.get(norm_name)

                if term is None:
                    # Create new authority term with concept resolution
                    resolved_concept_id = surface_map.get(norm_name)
                    if resolved_concept_id is not None:
                        terms_linked_to_concepts += 1

                    term = AuthorityTerm(
                        authority_id=authority_id,
                        external_name=str(raw_name),
                        normalized_external_name=norm_name,
                        concept_id=resolved_concept_id,
                        metadata_json={},
                        created_at=now,
                        updated_at=now,
                        last_seen_at=now,
                    )
                    db.add(term)
                    db.flush()  # get the id
                    new_terms += 1
                    existing_by_name[norm_name] = term
                else:
                    # Update last_seen_at and try concept resolution if still orphan
                    term.last_seen_at = now
                    term.updated_at = now
                    if term.concept_id is None:
                        resolved_concept_id = surface_map.get(norm_name)
                        if resolved_concept_id is not None:
                            term.concept_id = resolved_concept_id
                            terms_linked_to_concepts += 1

                # Check for existing observation by authority_term_id
                existing_obs = (
                    db.query(ImageConceptObservation.id)
                    .filter(
                        ImageConceptObservation.image_id == image_id,
                        ImageConceptObservation.authority_term_id == term.id,
                    )
                    .first()
                )
                if existing_obs is not None:
                    observations_skipped += 1
                    continue

                # Guard the UNIQUE constraint (image_id, concept_id, authority_id)
                # using in-memory tracking to catch collisions with unflushed rows.
                unique_key = (term.concept_id or 0, authority_id)
                if term.concept_id is not None and unique_key in seen_unique_keys:
                    observations_skipped += 1
                    continue
                # Also check committed rows in DB
                if term.concept_id is not None:
                    conflict = (
                        db.query(ImageConceptObservation.id)
                        .filter(
                            ImageConceptObservation.image_id == image_id,
                            ImageConceptObservation.concept_id == term.concept_id,
                            ImageConceptObservation.authority_id == authority_id,
                        )
                        .first()
                    )
                    if conflict is not None:
                        observations_skipped += 1
                        continue

                # Create observation
                db.add(ImageConceptObservation(
                    image_id=image_id,
                    concept_id=term.concept_id,
                    authority_id=authority_id,
                    authority_term_id=int(term.id),
                    source_type=ObservationSource.IMPORT,
                    certainty_label=ObservationCertainty.LIKELY,
                    is_present=True,
                    is_curated=False,
                    created_at=now,
                    updated_at=now,
                ))
                observations_created += 1
                pending_commits += 1
                if term.concept_id is not None:
                    seen_unique_keys.add(unique_key)

        images_processed += 1

        # Periodic commit
        if not dry_run and pending_commits >= commit_interval:
            try:
                db.commit()
                pending_commits = 0
            except Exception as exc:
                db.rollback()
                error_count += 1
                yield emit("error_event", {
                    "current_image": idx, "file": json_file.name,
                    "error": f"commit failed: {exc}",
                })

        yield emit("progress", {
            "current_image": idx, "total_images": total_images,
            "images_processed": images_processed,
            "tags_extracted": tags_extracted,
            "terms_upserted": terms_upserted,
            "new_terms": new_terms,
            "terms_linked_to_concepts": terms_linked_to_concepts,
            "observations_created": observations_created,
            "observations_skipped": observations_skipped,
        })

    # Final commit
    if not dry_run and pending_commits > 0:
        try:
            db.commit()
        except Exception as exc:
            db.rollback()
            error_count += 1
            yield emit("error_event", {
                "current_image": total_images,
                "error": f"final commit failed: {exc}",
            })

    yield emit("complete", {
        "total_images": total_images,
        "images_processed": images_processed,
        "tags_extracted": tags_extracted,
        "terms_upserted": terms_upserted,
        "new_terms": new_terms,
        "terms_linked_to_concepts": terms_linked_to_concepts,
        "observations_created": observations_created,
        "observations_skipped": observations_skipped,
        "errors": error_count,
        "dry_run": dry_run,
    })


@router.get("/concept-search/rebuild-observations")
def concept_search_rebuild_observations(
    dry_run: bool = Query(False, description="Preview changes without committing"),
):
    """SSE endpoint: rebuild all observations from sidecar JSON tag data.

    More thorough than ``concept-search/rescan`` — re-extracts tags from
    all sources (civitai, danbooru, prompt, user), upserts authority_terms
    with concept linking, and creates missing observations.

    Event types:
      - ``progress``: emitted after each sidecar file is processed
      - ``error_event``: emitted on per-file errors
      - ``complete``: final event with full summary
    """

    def _sse_event(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"

    def event_stream():
        db = SessionLocal()
        try:
            yield from _rebuild_observations_inner(db, dry_run, _sse_event)
        finally:
            db.close()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/concept-search/similar-terms")
def concept_search_similar_terms(
    q: str,
    limit: int = 20,
    unlinked_only: bool = False,
    db: Session = Depends(get_db),
):
    """Find authority terms whose name is similar to *q*.

    Uses token-overlap scoring to surface potential matches.  Useful in the
    concept editor to discover tags that could be linked to a concept.

    Set *unlinked_only*=true to only return terms with ``concept_id IS NULL``.
    """
    from sqlalchemy import func as sa_func

    q_lower = q.strip().lower()
    if not q_lower:
        return {"query": q, "results": []}

    q_tokens = set(
        q_lower.replace("(", " ").replace(")", " ").replace("-", " ").split()
    )

    # Base query — optionally filter to unlinked only
    base_q = (
        db.query(
            AuthorityTerm.id,
            AuthorityTerm.normalized_external_name,
            AuthorityTerm.concept_id,
            AuthorityTerm.authority_id,
            sa_func.count(ImageConceptObservation.id).label("observation_count"),
        )
        .outerjoin(
            ImageConceptObservation,
            ImageConceptObservation.authority_term_id == AuthorityTerm.id,
        )
        .group_by(
            AuthorityTerm.id,
            AuthorityTerm.normalized_external_name,
            AuthorityTerm.concept_id,
            AuthorityTerm.authority_id,
        )
    )

    # Filter: name must contain the query as a substring.
    like_clause = f"%{q_lower}%"
    base_q = base_q.filter(
        AuthorityTerm.normalized_external_name.ilike(like_clause)
    )
    if unlinked_only:
        base_q = base_q.filter(AuthorityTerm.concept_id.is_(None))

    rows = base_q.order_by(AuthorityTerm.normalized_external_name).limit(500).all()

    # Score by token overlap
    def _score(name: str) -> float:
        name_tokens = set(
            name.replace("(", " ").replace(")", " ").replace("-", " ").split()
        )
        if not name_tokens:
            return 0.0
        overlap = len(q_tokens & name_tokens)
        # Bonus for exact substring match
        bonus = 0.5 if q_lower in name else 0.0
        # Bonus for exact match
        if name == q_lower:
            bonus += 1.0
        return (overlap / max(len(name_tokens), 1)) + bonus

    scored = []
    for row in rows:
        s = _score(row.normalized_external_name)
        if s > 0:
            authority_name = None
            if row.authority_id:
                auth = (
                    db.query(TagAuthority.name)
                    .filter(TagAuthority.id == row.authority_id)
                    .first()
                )
                authority_name = auth.name if auth else None
            concept_name = None
            if row.concept_id:
                concept = (
                    db.query(Concept.canonical_name)
                    .filter(Concept.id == row.concept_id)
                    .first()
                )
                concept_name = concept.canonical_name if concept else None
            scored.append({
                "authority_term_id": row.id,
                "normalized_name": row.normalized_external_name,
                "authority": authority_name,
                "concept_id": row.concept_id,
                "linked_concept": concept_name,
                "observation_count": row.observation_count,
                "score": round(s, 3),
            })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return {"query": q, "results": scored[:limit]}


def taxonomy_purge_root_concepts(
    payload: TaxonomyPurgeRootsRequest, db: Session = Depends(get_db)
):
    roots = (
        db.query(Concept.id, Concept.canonical_name)
        .filter(Concept.parent_concept_id.is_(None))
        .order_by(Concept.id.asc())
        .all()
    )
    root_ids = [int(row.id) for row in roots]
    if not root_ids:
        return {
            "message": "No root concepts found.",
            "dry_run": payload.dry_run,
            "root_concept_count": 0,
            "affected_concept_count": 0,
            "affected_authority_term_count": 0,
            "affected_alias_count": 0,
            "affected_observation_count": 0,
            "deleted_concept_ids": [],
        }
    to_visit = list(root_ids)
    branch_ids: set[int] = set()
    while to_visit:
        current = to_visit.pop()
        if current in branch_ids:
            continue
        branch_ids.add(current)
        children = (
            db.query(Concept.id).filter(Concept.parent_concept_id == current).all()
        )
        to_visit.extend(int(row.id) for row in children)
    branch_id_list = sorted(branch_ids)
    authority_term_count = (
        db.query(func.count(AuthorityTerm.id))
        .filter(AuthorityTerm.concept_id.in_(branch_id_list))
        .scalar()
        or 0
    )
    alias_count = (
        db.query(func.count(ConceptAlias.id))
        .filter(ConceptAlias.concept_id.in_(branch_id_list))
        .scalar()
        or 0
    )
    observation_count = (
        db.query(func.count(ImageConceptObservation.id))
        .filter(ImageConceptObservation.concept_id.in_(branch_id_list))
        .scalar()
        or 0
    )
    response_data = {
        "message": (
            "Dry-run purge preview." if payload.dry_run else "Root concept branches purged."
        ),
        "dry_run": payload.dry_run,
        "root_concept_count": len(root_ids),
        "affected_concept_count": len(branch_id_list),
        "affected_authority_term_count": int(authority_term_count),
        "affected_alias_count": int(alias_count),
        "affected_observation_count": int(observation_count),
        "root_concepts": [
            {"id": int(row.id), "canonical_name": row.canonical_name}
            for row in roots[:200]
        ],
        "deleted_concept_ids": branch_id_list,
    }
    if payload.dry_run:
        return response_data
    db.query(AuthorityTerm).filter(AuthorityTerm.concept_id.in_(branch_id_list)).update(
        {AuthorityTerm.concept_id: None, AuthorityTerm.updated_at: datetime.utcnow()},
        synchronize_session=False,
    )
    db.query(ImageConceptObservation).filter(
        ImageConceptObservation.concept_id.in_(branch_id_list)
    ).delete(synchronize_session=False)
    db.query(ConceptAlias).filter(ConceptAlias.concept_id.in_(branch_id_list)).delete(
        synchronize_session=False
    )
    db.query(Concept).filter(Concept.id.in_(branch_id_list)).delete(
        synchronize_session=False
    )
    db.commit()
    return response_data


@router.get("/tree", response_model=list[dict])
def taxonomy_tree(status: str = "active", db: Session = Depends(get_db)):
    query = db.query(Concept)
    if status != "all":
        query = query.filter(Concept.status == status)
    concepts = query.order_by(Concept.canonical_name.asc()).all()
    source_map = _concept_source_map(db, [int(c.id) for c in concepts])
    by_parent: dict[Optional[int], list[dict]] = {}
    for concept in concepts:
        by_parent.setdefault(concept.parent_concept_id, []).append({
            "id": concept.id,
            "canonical_name": concept.canonical_name,
            "description": concept.description,
            "status": concept.status,
            "parent_concept_id": concept.parent_concept_id,
            "source_labels": source_map.get(int(concept.id), []),
            "display_prefix": _concept_display_prefix(
                source_map.get(int(concept.id), [])
            ),
        })

    def build_node(node: dict) -> dict:
        children = by_parent.get(node["id"], [])
        return {**node, "children": [build_node(child) for child in children]}

    roots = by_parent.get(None, [])
    return [build_node(root) for root in roots]


@router.get("/tree/state", response_model=dict)
def taxonomy_tree_state(
    request: Request,
    response: Response,
    include_tag_details: bool = True,
    include_tags: bool = True,
    db: Session = Depends(get_db),
):
    cache_key = _build_search_cache_key(
        "taxonomy_tree_state",
        payload={
            "include_tag_details": bool(include_tag_details),
            "include_tags": bool(include_tags),
        },
    )
    cache_headers = _build_json_cache_headers(cache_key, max_age_seconds=30)
    for header_name, header_value in cache_headers.items():
        response.headers[header_name] = header_value
    if _should_return_json_not_modified(request, cache_headers):
        return Response(status_code=304, headers=cache_headers)
    cached_state = _search_cache_get(cache_key)
    if isinstance(cached_state, dict):
        return cached_state

    gallery_tag_names_by_source = _gallery_tag_names_by_source_from_observations(db)
    gallery_tag_usage_counts_by_source = (
        _gallery_tag_usage_counts_by_source_from_observations(db)
    )
    gallery_tag_name_sets_by_source = {
        source: set(names) for source, names in gallery_tag_names_by_source.items()
    }
    concepts = (
        db.query(Concept)
        .filter(Concept.status == "active")
        .order_by(Concept.id.asc())
        .all()
    )
    concept_ids = [int(c.id) for c in concepts]
    alias_data_by_concept: dict[int, dict[str, list[str]]] = {
        cid: {"aliases": [], "implies": []} for cid in concept_ids
    }
    if include_tag_details and concept_ids:
        aliases = (
            db.query(ConceptAlias)
            .filter(ConceptAlias.concept_id.in_(concept_ids))
            .order_by(ConceptAlias.id.asc())
            .all()
        )
        for alias in aliases:
            alias_text = str(alias.alias or "").strip()
            if not alias_text:
                continue
            concept_id = int(alias.concept_id)
            bucket = alias_data_by_concept.setdefault(
                concept_id, {"aliases": [], "implies": []}
            )
            alias_kind = str(alias.alias_type or "synonym").strip().lower()
            if alias_kind == "canonical":
                continue
            if alias_kind == "implies":
                bucket["implies"].append(alias_text)
            else:
                bucket["aliases"].append(alias_text)
    if not include_tags:
        term_rows = []
    else:
        term_rows = (
            db.query(AuthorityTerm, TagAuthority, Concept)
            .join(TagAuthority, TagAuthority.id == AuthorityTerm.authority_id)
            .outerjoin(Concept, Concept.id == AuthorityTerm.concept_id)
            .order_by(TagAuthority.name.asc(), AuthorityTerm.external_name.asc())
            .all()
        )
    danbooru_name_by_external_tag_id: dict[int, str] = {}
    for term, authority, _ in term_rows:
        authority_name = str(authority.name or "").strip().lower()
        if authority_name != "danbooru":
            continue
        ext_id = term.external_tag_id
        external_name = str(term.external_name or "").strip()
        if ext_id is not None and external_name:
            danbooru_name_by_external_tag_id[ext_id] = external_name
    tags: list[dict] = []
    normalized_term_names: set[str] = set()
    referenced_concept_ids: set[int] = set()
    for term, authority, concept in term_rows:
        taxonomy_normalized_term_name = _normalize_taxonomy_text(
            term.external_name or ""
        )
        if taxonomy_normalized_term_name:
            normalized_term_names.add(taxonomy_normalized_term_name)
        gallery_normalized_term_name = _normalize_gallery_tag_text(
            term.external_name or ""
        )
        source_name = str(authority.name or "user").strip().lower()
        if source_name not in {"civitai", "danbooru", "prompt", "user"}:
            source_name = "user"
        gallery_scope_names = gallery_tag_name_sets_by_source.get(source_name, set())
        if concept is not None:
            referenced_concept_ids.add(int(concept.id))
        concept_alias_data = (
            alias_data_by_concept.get(int(concept.id), {"aliases": [], "implies": []})
            if include_tag_details and concept
            else {"aliases": [], "implies": []}
        )
        metadata = term.metadata_json if isinstance(term.metadata_json, dict) else {}
        examples = []
        if include_tag_details:
            raw_examples = (
                metadata.get("examples") if isinstance(metadata, dict) else []
            )
            if isinstance(raw_examples, list):
                examples = [str(item) for item in raw_examples if str(item).strip()]
        post_count = None
        if source_name in {"danbooru", "civitai"}:
            raw_post_count = (
                metadata.get("post_count") if isinstance(metadata, dict) else None
            )
            try:
                parsed_post_count = (
                    int(raw_post_count) if raw_post_count is not None else None
                )
            except (TypeError, ValueError):
                parsed_post_count = None
            if parsed_post_count is not None and parsed_post_count > 0:
                post_count = parsed_post_count
        mapped_danbooru_tag_id = None
        mapped_danbooru_name = None
        external_tag_id = term.external_tag_id
        if source_name == "prompt":
            raw_mapped_danbooru_tag_id = (
                metadata.get("mapped_danbooru_tag_id")
                if isinstance(metadata, dict)
                else None
            )
            if raw_mapped_danbooru_tag_id not in (None, ""):
                try:
                    mapped_danbooru_tag_id = int(raw_mapped_danbooru_tag_id)
                except (TypeError, ValueError):
                    mapped_danbooru_tag_id = None
            elif external_tag_id is not None:
                mapped_danbooru_tag_id = external_tag_id
            if mapped_danbooru_tag_id:
                mapped_danbooru_name = danbooru_name_by_external_tag_id.get(
                    mapped_danbooru_tag_id
                )
        tag_payload = {
            "id": f"term:{term.id}",
            "authority_term_id": int(term.id),
            "name": term.external_name,
            "external_tag_id": external_tag_id,
            "source": source_name,
            "scope": (
                "gallery"
                if gallery_normalized_term_name
                and gallery_normalized_term_name in gallery_scope_names
                else "image"
            ),
            "post_count": post_count,
            "concept_id": int(concept.id) if concept else None,
            "mapped_danbooru_tag_id": mapped_danbooru_tag_id,
            "mapped_danbooru_name": mapped_danbooru_name,
        }
        if include_tag_details:
            tag_payload["description"] = concept.description if concept else ""
            tag_payload["aliases"] = concept_alias_data.get("aliases", [])
            tag_payload["implies"] = concept_alias_data.get("implies", [])
            tag_payload["examples"] = _with_source_default_example_first(
                source_name,
                str(term.external_name or ""),
                metadata,
                examples,
            )
        tags.append(tag_payload)
    child_parent_ids: set[int] = {
        int(c.parent_concept_id) for c in concepts if c.parent_concept_id is not None
    }
    filtered_concepts: list[Concept] = []
    for concept in concepts:
        concept_id = int(concept.id)
        alias_data = alias_data_by_concept.get(concept_id, {"aliases": [], "implies": []})
        has_metadata = (
            bool((concept.description or "").strip())
            or bool(alias_data.get("aliases"))
            or bool(alias_data.get("implies"))
        )
        canonical_normalized = _normalize_taxonomy_text(concept.canonical_name or "")
        is_empty_tag_stub = (
            concept.parent_concept_id is None
            and concept_id not in child_parent_ids
            and concept_id not in referenced_concept_ids
            and not has_metadata
            and canonical_normalized in normalized_term_names
        )
        if not is_empty_tag_stub:
            filtered_concepts.append(concept)
    payload_data = {
        "concepts": [
            {
                "id": int(c.id),
                "canonical_name": c.canonical_name,
                "parent_concept_id": (
                    int(c.parent_concept_id) if c.parent_concept_id is not None else None
                ),
            }
            for c in filtered_concepts
        ],
        "tags": tags,
        "gallery_tag_names_by_source": gallery_tag_names_by_source,
        "tag_usage_by_scope": {
            "gallery": gallery_tag_usage_counts_by_source,
            "selected": {source: {} for source in gallery_tag_usage_counts_by_source},
            "all": {
                source: dict(counts)
                for source, counts in gallery_tag_usage_counts_by_source.items()
            },
        },
    }
    _search_cache_put(cache_key, payload_data, ttl_seconds=_FILTER_OPTIONS_CACHE_TTL_SECONDS)
    return payload_data


@router.get("/tree/tags/{source}", response_model=dict)
def taxonomy_tree_tags_for_source(
    request: Request,
    response: Response,
    source: str,
    db: Session = Depends(get_db),
):
    valid_sources = {"civitai", "danbooru", "prompt", "user"}
    source_lower = (source or "").strip().lower()
    if source_lower not in valid_sources:
        raise HTTPException(status_code=404, detail=f"Unknown tag source: {source}")
    cache_key = _build_search_cache_key(
        "taxonomy_tags_for_source", payload={"source": source_lower}
    )
    cache_headers = _build_json_cache_headers(cache_key, max_age_seconds=30)
    for header_name, header_value in cache_headers.items():
        response.headers[header_name] = header_value
    if _should_return_json_not_modified(request, cache_headers):
        return Response(status_code=304, headers=cache_headers)
    cached = _search_cache_get(cache_key)
    if isinstance(cached, dict):
        return cached
    gallery_names_cache_key = "_shared_gallery_tag_names_by_source"
    gallery_names_all = _search_cache_get(gallery_names_cache_key)
    if not isinstance(gallery_names_all, dict):
        gallery_names_all = _gallery_tag_names_by_source_from_observations(db)
        _search_cache_put(
            gallery_names_cache_key,
            gallery_names_all,
            ttl_seconds=_FILTER_OPTIONS_CACHE_TTL_SECONDS,
        )
    gallery_scope_names: set[str] = {
        _normalize_gallery_tag_text(n)
        for n in gallery_names_all.get(source_lower, [])
        if n
    }
    danbooru_name_by_ext_id: dict[int, str] = {}
    if source_lower == "prompt":
        danbooru_names_cache_key = "_shared_danbooru_name_by_ext_id"
        cached_danbooru_names = _search_cache_get(danbooru_names_cache_key)
        if isinstance(cached_danbooru_names, dict):
            danbooru_name_by_ext_id = cached_danbooru_names
        else:
            danbooru_term_rows = (
                db.query(AuthorityTerm.external_tag_id, AuthorityTerm.external_name)
                .join(TagAuthority, TagAuthority.id == AuthorityTerm.authority_id)
                .filter(TagAuthority.name.ilike("danbooru"))
                .all()
            )
            for ext_id, ext_name in danbooru_term_rows:
                if ext_id is not None and ext_name:
                    danbooru_name_by_ext_id[ext_id] = str(ext_name).strip()
            _search_cache_put(
                danbooru_names_cache_key,
                danbooru_name_by_ext_id,
                ttl_seconds=_FILTER_OPTIONS_CACHE_TTL_SECONDS,
            )
    term_rows = (
        db.query(AuthorityTerm, Concept)
        .join(TagAuthority, TagAuthority.id == AuthorityTerm.authority_id)
        .outerjoin(Concept, Concept.id == AuthorityTerm.concept_id)
        .filter(TagAuthority.name.ilike(source_lower))
        .order_by(AuthorityTerm.external_name.asc())
        .all()
    )
    rows: list[list] = []
    for term, concept in term_rows:
        gallery_norm = _normalize_gallery_tag_text(term.external_name or "")
        scope = (
            "gallery" if (gallery_norm and gallery_norm in gallery_scope_names) else "image"
        )
        metadata = term.metadata_json if isinstance(term.metadata_json, dict) else {}
        post_count = None
        if source_lower in {"danbooru", "civitai"}:
            raw_pc = (
                metadata.get("post_count") if isinstance(metadata, dict) else None
            )
            try:
                pc = int(raw_pc) if raw_pc is not None else None
            except (TypeError, ValueError):
                pc = None
            if pc is not None and pc > 0:
                post_count = pc
        external_tag_id = term.external_tag_id
        mdtag_id = None
        mdtag_name = None
        if source_lower == "prompt":
            raw_mapped = (
                metadata.get("mapped_danbooru_tag_id")
                if isinstance(metadata, dict)
                else None
            )
            if raw_mapped not in (None, ""):
                try:
                    mdtag_id = int(raw_mapped)
                except (TypeError, ValueError):
                    mdtag_id = None
            elif external_tag_id is not None:
                mdtag_id = external_tag_id
            if mdtag_id:
                mdtag_name = danbooru_name_by_ext_id.get(mdtag_id)
        rows.append([
            int(term.id),
            term.external_name,
            external_tag_id,
            scope,
            post_count,
            int(concept.id) if concept else None,
            mdtag_id,
            mdtag_name,
        ])
    if source_lower == "user":
        existing_user_term_names: set[str] = {
            _normalize_gallery_tag_text(r[1]) for r in rows if r[1]
        }
        user_tag_name_counts: dict[str, int] = {}
        user_tag_rows = (
            db.query(ImageModel.user_tags)
            .filter(_active_image_filter())
            .filter(ImageModel.user_tags.isnot(None))
            .all()
        )
        for (user_tags_col,) in user_tag_rows:
            if not isinstance(user_tags_col, list):
                continue
            for tag_name in user_tags_col:
                normalized = _normalize_gallery_tag_text(str(tag_name))
                if normalized:
                    user_tag_name_counts[normalized] = (
                        user_tag_name_counts.get(normalized, 0) + 1
                    )
        usage_counts_cache_key = "_shared_gallery_tag_usage_counts_by_source"
        usage_counts_all = _search_cache_get(usage_counts_cache_key)
        if not isinstance(usage_counts_all, dict):
            usage_counts_all = _gallery_tag_usage_counts_by_source_from_observations(db)
            _search_cache_put(
                usage_counts_cache_key,
                usage_counts_all,
                ttl_seconds=_FILTER_OPTIONS_CACHE_TTL_SECONDS,
            )
        user_usage_counts = usage_counts_all.get("user", {})
        _synthetic_user_id = -1
        for tag_name in sorted(user_tag_name_counts):
            if tag_name in existing_user_term_names:
                continue
            gallery_count = user_usage_counts.get(
                tag_name, user_tag_name_counts.get(tag_name, 0)
            )
            synthetic_scope = "gallery" if tag_name in gallery_scope_names else "image"
            rows.append([
                _synthetic_user_id, tag_name, None, synthetic_scope,
                gallery_count if gallery_count > 0 else None, None, None, None,
            ])
            _synthetic_user_id -= 1
    payload_data = {"source": source_lower, "cols": _TAG_SOURCE_COLS, "rows": rows}
    _search_cache_put(cache_key, payload_data, ttl_seconds=_FILTER_OPTIONS_CACHE_TTL_SECONDS)
    return payload_data


@router.post("/tree/associate", response_model=dict)
def taxonomy_tree_associate_tag(
    payload: TaxonomyTagAssociationRequest, db: Session = Depends(get_db)
):
    concept = db.query(Concept).filter(Concept.id == payload.concept_id).first()
    if concept is None:
        raise HTTPException(status_code=404, detail="Concept not found")
    term_id = payload.authority_term_id
    created_term = False
    if term_id < 0:
        raw_name = (payload.tag_name or "").strip()
        source_name = (payload.tag_source or "user").strip().lower()
        if not raw_name:
            raise HTTPException(
                status_code=400, detail="tag_name is required for synthetic tag IDs"
            )
        normalized_name = _normalize_taxonomy_text(raw_name)
        if not normalized_name:
            raise HTTPException(
                status_code=400, detail="tag_name is empty after normalization"
            )
        authority = _get_or_create_authority(db, source_name)
        term = (
            db.query(AuthorityTerm)
            .filter(
                AuthorityTerm.authority_id == authority.id,
                AuthorityTerm.normalized_external_name == normalized_name,
            )
            .first()
        )
        if term is None:
            now = datetime.utcnow()
            term = AuthorityTerm(
                authority_id=authority.id,
                external_tag_id=None,
                external_name=raw_name,
                normalized_external_name=normalized_name,
                concept_id=None,
                metadata_json={"origin": "tree_associate", "source": source_name},
                created_at=now,
                updated_at=now,
                last_seen_at=now,
            )
            db.add(term)
            db.flush()
            created_term = True
        term_id = term.id
    else:
        term = db.query(AuthorityTerm).filter(AuthorityTerm.id == term_id).first()
        if term is None:
            raise HTTPException(status_code=404, detail="Authority term not found")
    term.concept_id = int(concept.id)
    term.updated_at = datetime.utcnow()
    db.commit()
    return {
        "message": "Tag associated to concept."
        + (" Authority term created." if created_term else ""),
        "authority_term_id": int(term.id),
        "concept_id": int(concept.id),
    }


@router.delete("/tree/associate/{authority_term_id}", response_model=dict)
def taxonomy_tree_disassociate_tag(
    authority_term_id: int, db: Session = Depends(get_db)
):
    term = (
        db.query(AuthorityTerm).filter(AuthorityTerm.id == authority_term_id).first()
    )
    if term is None:
        raise HTTPException(status_code=404, detail="Authority term not found")
    term.concept_id = None
    term.updated_at = datetime.utcnow()
    db.commit()
    return {
        "message": "Tag disassociated from concept.",
        "authority_term_id": int(term.id),
    }


@router.delete("/tree/tag/{authority_term_id}", response_model=dict)
def taxonomy_tree_delete_tag(authority_term_id: int, db: Session = Depends(get_db)):
    term = (
        db.query(AuthorityTerm).filter(AuthorityTerm.id == authority_term_id).first()
    )
    if term is None:
        raise HTTPException(status_code=404, detail="Authority term not found")
    authority_name = (
        str(term.authority.name or "").strip().lower()
        if term.authority is not None
        else ""
    )
    if authority_name != "prompt":
        raise HTTPException(
            status_code=409,
            detail="Only prompt tags can be deleted from tree edit mode",
        )
    deleted_name = str(term.external_name or "").strip()
    db.delete(term)
    db.commit()
    return {
        "message": "Prompt tag deleted.",
        "authority_term_id": int(authority_term_id),
        "name": deleted_name,
    }


@router.get("/tree/tag/{authority_term_id}/details", response_model=dict)
def taxonomy_tree_tag_details(authority_term_id: int, db: Session = Depends(get_db)):
    term = (
        db.query(AuthorityTerm).filter(AuthorityTerm.id == authority_term_id).first()
    )
    if term is None:
        raise HTTPException(status_code=404, detail="Authority term not found")
    authority_name = (
        str(term.authority.name or "").strip().lower()
        if term.authority is not None
        else ""
    )
    aliases: list[str] = []
    implies: list[str] = []
    description = ""
    if term.concept_id is not None:
        concept = db.query(Concept).filter(Concept.id == term.concept_id).first()
        if concept is not None:
            description = concept.description or ""
            rows = (
                db.query(ConceptAlias)
                .filter(ConceptAlias.concept_id == concept.id)
                .order_by(ConceptAlias.id.asc())
                .all()
            )
            for row in rows:
                kind = str(row.alias_type or "synonym").strip().lower()
                if kind == "canonical":
                    continue
                if kind == "implies":
                    implies.append(row.alias)
                else:
                    aliases.append(row.alias)
    metadata = term.metadata_json if isinstance(term.metadata_json, dict) else {}
    raw_examples = metadata.get("examples") if isinstance(metadata, dict) else []
    examples = (
        [str(item) for item in raw_examples] if isinstance(raw_examples, list) else []
    )
    return {
        "authority_term_id": int(term.id),
        "description": description,
        "aliases": _normalize_str_list(aliases),
        "implies": _normalize_str_list(implies),
        "examples": _with_source_default_example_first(
            authority_name,
            str(term.external_name or ""),
            metadata,
            examples,
        ),
    }


@router.patch("/tree/tag/{authority_term_id}/details", response_model=dict)
def taxonomy_tree_update_tag_details(
    authority_term_id: int,
    payload: TaxonomyTagDetailsUpdateRequest,
    db: Session = Depends(get_db),
):
    term = (
        db.query(AuthorityTerm).filter(AuthorityTerm.id == authority_term_id).first()
    )
    if term is None:
        raise HTTPException(status_code=404, detail="Authority term not found")
    authority_name = (
        str(term.authority.name or "").strip().lower()
        if term.authority is not None
        else ""
    )
    need_concept = (
        payload.description is not None
        or payload.aliases is not None
        or payload.implies is not None
    )
    concept = _get_term_concept(db, term) if need_concept else None
    if need_concept and concept is None:
        raise HTTPException(
            status_code=409,
            detail=(
                "Tag is not associated with a concept. Associate it first before "
                "editing description, aliases, or implies."
            ),
        )
    if concept is not None and payload.description is not None:
        concept.description = (payload.description or "").strip() or None
        concept.updated_at = datetime.utcnow()
    if concept is not None and payload.aliases is not None:
        db.query(ConceptAlias).filter(
            ConceptAlias.concept_id == concept.id,
            ConceptAlias.alias_type == "synonym",
        ).delete(synchronize_session=False)
        canonical = _normalize_taxonomy_text(concept.canonical_name)
        for alias in _normalize_str_list(payload.aliases):
            if alias == canonical:
                continue
            db.add(ConceptAlias(
                concept_id=concept.id,
                alias=alias,
                normalized_alias=alias,
                alias_type="synonym",
                is_preferred=False,
            ))
    if concept is not None and payload.implies is not None:
        db.query(ConceptAlias).filter(
            ConceptAlias.concept_id == concept.id,
            ConceptAlias.alias_type == "implies",
        ).delete(synchronize_session=False)
        canonical = _normalize_taxonomy_text(concept.canonical_name)
        for implied in _normalize_str_list(payload.implies):
            if implied == canonical:
                continue
            db.add(ConceptAlias(
                concept_id=concept.id,
                alias=implied,
                normalized_alias=implied,
                alias_type="implies",
                is_preferred=False,
            ))
    if payload.examples is not None:
        metadata = term.metadata_json if isinstance(term.metadata_json, dict) else {}
        metadata = dict(metadata)
        metadata["examples"] = _with_source_default_example_first(
            authority_name,
            str(term.external_name or ""),
            metadata,
            payload.examples,
        )
        term.metadata_json = metadata
        term.updated_at = datetime.utcnow()
    db.commit()
    return {
        "message": "Tag details updated.",
        "authority_term_id": int(term.id),
        "concept_id": int(term.concept_id) if term.concept_id is not None else None,
    }


@router.get("/tag-maint/{source}/export")
def taxonomy_tag_maint_export(source: str, db: Session = Depends(get_db)):
    """Export all authority_terms for a source as a JSON bootstrap archive."""
    source_lower = (source or "").strip().lower()
    if source_lower not in _VALID_TAG_MAINT_SOURCES:
        raise HTTPException(status_code=404, detail=f"Unknown tag source: {source}")
    authority = (
        db.query(TagAuthority)
        .filter(func.lower(TagAuthority.name) == source_lower)
        .first()
    )
    if authority is None:
        return {"authority": source_lower, "terms": [], "total": 0}
    term_rows = (
        db.query(AuthorityTerm)
        .filter(AuthorityTerm.authority_id == authority.id)
        .order_by(AuthorityTerm.external_name.asc())
        .all()
    )
    terms: list[dict] = []
    for t in term_rows:
        concept_name = None
        if t.concept_id is not None:
            concept = db.query(Concept).filter(Concept.id == t.concept_id).first()
            if concept is not None:
                concept_name = concept.canonical_name
        metadata = t.metadata_json if isinstance(t.metadata_json, dict) else {}
        terms.append({
            "id": int(t.id),
            "name": t.external_name,
            "external_tag_id": t.external_tag_id,
            "concept_name": concept_name,
            "metadata": metadata,
        })
    return {
        "authority": source_lower,
        "exported_at": datetime.now(timezone.utc).isoformat() + "Z",
        "total": len(terms),
        "terms": terms,
    }


@router.post("/tag-maint/{source}/purge", response_model=dict)
def taxonomy_tag_maint_purge(
    source: str,
    payload: TaxonomyTagMaintPurgeRequest,
    db: Session = Depends(get_db),
):
    """Purge all authority_terms for a source."""
    source_lower = (source or "").strip().lower()
    if source_lower not in _VALID_TAG_MAINT_SOURCES:
        raise HTTPException(status_code=404, detail=f"Unknown tag source: {source}")
    authority = (
        db.query(TagAuthority)
        .filter(func.lower(TagAuthority.name) == source_lower)
        .first()
    )
    if authority is None:
        return {
            "message": "No authority found for source.",
            "source": source_lower,
            "deleted": 0,
            "dry_run": payload.dry_run,
        }
    term_count = (
        db.query(AuthorityTerm)
        .filter(AuthorityTerm.authority_id == authority.id)
        .count()
    )
    if not payload.dry_run:
        db.query(AuthorityTerm).filter(
            AuthorityTerm.authority_id == authority.id
        ).delete(synchronize_session=False)
        db.commit()
    return {
        "message": (
            "Dry-run purge preview." if payload.dry_run else "All authority terms purged."
        ),
        "source": source_lower,
        "deleted": term_count,
        "dry_run": payload.dry_run,
    }


@router.get("/tag-maint/{source}/list", response_model=dict)
def taxonomy_tag_maint_list(
    source: str,
    page: int = 1,
    page_size: int = 100,
    sort_col: str = "name",
    sort_dir: str = "asc",
    search: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Paginated, sortable, searchable tag list for table display."""
    source_lower = (source or "").strip().lower()
    if source_lower not in _VALID_TAG_MAINT_SOURCES:
        raise HTTPException(status_code=404, detail=f"Unknown tag source: {source}")
    page = max(1, page)
    page_size = max(1, min(500, page_size))
    authority = (
        db.query(TagAuthority)
        .filter(func.lower(TagAuthority.name) == source_lower)
        .first()
    )
    if authority is None:
        return {
            "source": source_lower,
            "cols": _TAG_SOURCE_COLS,
            "rows": [],
            "total": 0,
            "page": page,
            "page_size": page_size,
        }
    q = db.query(AuthorityTerm).filter(AuthorityTerm.authority_id == authority.id)
    if search and search.strip():
        search_norm = search.strip().lower()
        q = q.filter(AuthorityTerm.external_name.ilike(f"%{search_norm}%"))
    total = q.count()
    col_map = {
        "id": AuthorityTerm.id,
        "name": AuthorityTerm.external_name,
        "ext_id": AuthorityTerm.external_tag_id,
        "concept_id": AuthorityTerm.concept_id,
    }
    sort_direction = (sort_dir or "asc").strip().lower()
    if sort_direction not in {"asc", "desc"}:
        sort_direction = "asc"
    if sort_col == "post_count":
        # Sort by observation count (subquery) with fallback to metadata post_count
        obs_count_subq = (
            db.query(
                ImageConceptObservation.authority_term_id,
                func.count(ImageConceptObservation.id).label("cnt"),
            )
            .filter(ImageConceptObservation.is_present == True)  # noqa: E712
            .group_by(ImageConceptObservation.authority_term_id)
            .subquery()
        )
        post_count_expr = func.coalesce(obs_count_subq.c.cnt, 0)
        q = q.outerjoin(obs_count_subq, AuthorityTerm.id == obs_count_subq.c.authority_term_id)
        sort_expr = (
            post_count_expr.desc() if sort_direction == "desc" else post_count_expr.asc()
        )
    else:
        sort_expr = col_map.get(sort_col, AuthorityTerm.external_name)
        sort_expr = sort_expr.desc() if sort_direction == "desc" else sort_expr.asc()
    term_rows = (
        q.order_by(sort_expr).offset((page - 1) * page_size).limit(page_size).all()
    )
    # Batch-compute observation counts for this page of terms
    term_ids_on_page = [t.id for t in term_rows]
    obs_count_rows = (
        db.query(
            ImageConceptObservation.authority_term_id,
            func.count(ImageConceptObservation.id),
        )
        .filter(
            ImageConceptObservation.authority_term_id.in_(term_ids_on_page),
            ImageConceptObservation.is_present == True,  # noqa: E712
        )
        .group_by(ImageConceptObservation.authority_term_id)
        .all()
    )
    obs_counts: dict[int, int] = {row[0]: row[1] for row in obs_count_rows}
    gallery_names_cache_key = "_shared_gallery_tag_names_by_source"
    gallery_names_all = _search_cache_get(gallery_names_cache_key)
    if not isinstance(gallery_names_all, dict):
        gallery_names_all = _gallery_tag_names_by_source_from_observations(db)
        _search_cache_put(
            gallery_names_cache_key,
            gallery_names_all,
            ttl_seconds=_FILTER_OPTIONS_CACHE_TTL_SECONDS,
        )
    gallery_scope_names: set[str] = {
        _normalize_gallery_tag_text(n)
        for n in gallery_names_all.get(source_lower, [])
        if n
    }
    rows: list[list] = []
    for term in term_rows:
        gallery_norm = _normalize_gallery_tag_text(term.external_name or "")
        scope = (
            "gallery" if (gallery_norm and gallery_norm in gallery_scope_names) else "image"
        )
        metadata = term.metadata_json if isinstance(term.metadata_json, dict) else {}
        post_count = None
        # Prefer observation count from our DB (images that have this tag)
        obs_count = obs_counts.get(term.id)
        if obs_count is not None and obs_count > 0:
            post_count = obs_count
        # Fall back to metadata post_count if available
        if post_count is None and source_lower in {"danbooru", "civitai"}:
            raw_pc = (
                metadata.get("post_count") if isinstance(metadata, dict) else None
            )
            try:
                pc = int(raw_pc) if raw_pc is not None else None
            except (TypeError, ValueError):
                pc = None
            if pc is not None and pc > 0:
                post_count = pc
        rows.append([
            int(term.id),
            term.external_name,
            term.external_tag_id,
            scope,
            post_count,
            int(term.concept_id) if getattr(term, "concept_id", None) is not None else None,
            None,
            None,
        ])
    return {
        "source": source_lower,
        "cols": _TAG_SOURCE_COLS,
        "rows": rows,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def _replace_tag_in_list(
    tags: list, old_name_lower: str, new_name: str
) -> tuple[list[str], bool]:
    """Replace *old_name* with *new_name* in a tag list, preserving order.

    Returns the rebuilt list and whether any change was made.
    """
    rebuilt: list[str] = []
    changed = False
    for tag in tags:
        if isinstance(tag, str) and tag.strip().lower() == old_name_lower:
            if new_name not in rebuilt:
                rebuilt.append(new_name)
            changed = True
        else:
            if tag not in rebuilt:
                rebuilt.append(tag)
    return rebuilt, changed


def _cascade_user_tag_rename_sidecar(
    sidecar_path: Path, old_name_lower: str, new_name: str
) -> bool:
    """Update a single sidecar JSON file's ``user_tags`` array.

    Returns True if the file was modified.
    """
    if not sidecar_path.exists():
        return False
    try:
        with open(sidecar_path, "r", encoding="utf-8") as f:
            sidecar_data = json.load(f)
    except (json.JSONDecodeError, IOError, OSError):
        return False
    sc_tags = sidecar_data.get("user_tags")
    if not isinstance(sc_tags, list):
        return False
    sc_rebuilt, changed = _replace_tag_in_list(sc_tags, old_name_lower, new_name)
    if not changed:
        return False
    sidecar_data["user_tags"] = sc_rebuilt
    try:
        with open(sidecar_path, "w", encoding="utf-8") as f:
            json.dump(sidecar_data, f, indent=2, ensure_ascii=False)
    except (IOError, OSError):
        return False
    return True


def _cascade_user_tag_rename(
    db: Session, old_name: str, new_name: str
) -> int:
    """Propagate a user tag rename to images.user_tags JSON + sidecar files.

    User tags live in *both* ``authority_terms`` (structured taxonomy) and the
    raw ``images.user_tags`` JSON column.  When an ``external_name`` is edited
    in the tag-maintenance page we must update every image whose ``user_tags``
    array contains the old name so the gallery stays in sync.

    Returns the number of images updated.
    """
    old_name_lower = old_name.strip().lower()
    if not old_name_lower or old_name.strip().lower() == new_name.strip().lower():
        return 0

    from atelierai.config import IMAGE_LIBRARY_PATH

    library_path = Path(IMAGE_LIBRARY_PATH)
    affected_images = (
        db.query(ImageModel)
        .filter(_active_image_filter())
        .filter(ImageModel.user_tags.isnot(None))
        .all()
    )
    updated = 0
    for img in affected_images:
        current_tags = img.user_tags
        if not isinstance(current_tags, list):
            continue
        rebuilt, changed = _replace_tag_in_list(current_tags, old_name_lower, new_name)
        if not changed:
            continue
        img.user_tags = rebuilt
        updated += 1
        sidecar_path = (library_path / str(img.file_path)).with_suffix(".json")
        _cascade_user_tag_rename_sidecar(sidecar_path, old_name_lower, new_name)
    return updated


@router.patch("/tag-maint/{source}/update", response_model=dict)
def taxonomy_tag_maint_update(
    source: str,
    payload: TaxonomyTagMaintUpdateRequest,
    db: Session = Depends(get_db),
):
    """Inline cell edit for a single authority_term field."""
    source_lower = (source or "").strip().lower()
    if source_lower not in _VALID_TAG_MAINT_SOURCES:
        raise HTTPException(status_code=404, detail=f"Unknown tag source: {source}")
    term = (
        db.query(AuthorityTerm)
        .filter(AuthorityTerm.id == payload.authority_term_id)
        .first()
    )
    if term is None:
        raise HTTPException(status_code=404, detail="Authority term not found")
    authority = (
        db.query(TagAuthority).filter(TagAuthority.id == term.authority_id).first()
    )
    if authority is None or authority.name.strip().lower() != source_lower:
        raise HTTPException(
            status_code=409,
            detail="Authority term does not belong to this source",
        )
    old_external_name: Optional[str] = None
    if payload.field == "external_name":
        new_name = str(payload.value or "").strip()
        if not new_name:
            raise HTTPException(status_code=400, detail="external_name cannot be empty")
        old_external_name = term.external_name
        term.external_name = new_name
        term.normalized_external_name = _normalize_taxonomy_text(new_name)
    elif payload.field == "external_tag_id":
        if payload.value is not None and str(payload.value).strip() != "":
            try:
                term.external_tag_id = int(payload.value)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400, detail="external_tag_id must be an integer"
                )
        else:
            term.external_tag_id = None
    elif payload.field == "concept_id":
        if payload.value is not None:
            concept_id = int(payload.value)
            concept = db.query(Concept).filter(Concept.id == concept_id).first()
            if concept is None:
                raise HTTPException(status_code=404, detail="Concept not found")
            term.concept_id = concept_id
        else:
            term.concept_id = None
    else:
        raise HTTPException(status_code=400, detail=f"Unknown field: {payload.field}")
    term.updated_at = datetime.now(timezone.utc)

    # ── Cascade rename to images.user_tags JSON for user-sourced tags ──────
    # User tags are stored in BOTH authority_terms and the raw images.user_tags
    # JSON column.  The gallery reads user tags from both sources, so we must
    # propagate the rename to keep them in sync.
    updated_images_count = 0
    if (
        payload.field == "external_name"
        and old_external_name is not None
        and source_lower == "user"
        and old_external_name != term.external_name
    ):
        updated_images_count = _cascade_user_tag_rename(
            db, old_external_name, term.external_name
        )

    db.commit()

    # Invalidate caches so the gallery picks up the new name immediately.
    from utils.cache import _invalidate_search_cache

    _invalidate_search_cache("tags_update")

    return {
        "message": "Tag updated.",
        "authority_term_id": int(term.id),
        "field": payload.field,
        "value": payload.value,
        "images_updated": updated_images_count,
    }


@router.post("/tag-maint/{source}/bulk-delete", response_model=dict)
def taxonomy_tag_maint_bulk_delete(
    source: str,
    payload: TaxonomyTagMaintBulkDeleteRequest,
    db: Session = Depends(get_db),
):
    """Delete multiple authority_terms by ID."""
    source_lower = (source or "").strip().lower()
    if source_lower not in _VALID_TAG_MAINT_SOURCES:
        raise HTTPException(status_code=404, detail=f"Unknown tag source: {source}")
    if not payload.authority_term_ids:
        return {"message": "No IDs provided.", "deleted": 0, "dry_run": payload.dry_run}
    authority = (
        db.query(TagAuthority)
        .filter(func.lower(TagAuthority.name) == source_lower)
        .first()
    )
    if authority is None:
        return {
            "message": "Authority not found.", "deleted": 0, "dry_run": payload.dry_run
        }
    terms = (
        db.query(AuthorityTerm)
        .filter(
            AuthorityTerm.authority_id == authority.id,
            AuthorityTerm.id.in_(payload.authority_term_ids),
        )
        .all()
    )
    deleted_count = len(terms)
    if not payload.dry_run:
        for term in terms:
            db.delete(term)
        db.commit()
    return {
        "message": (
            "Dry-run bulk delete preview." if payload.dry_run else "Tags deleted."
        ),
        "source": source_lower,
        "deleted": deleted_count,
        "dry_run": payload.dry_run,
    }


@router.get("/tag-maint/civitai/rescan-observations")
def taxonomy_tag_maint_rescan_civitai_observations(
    dry_run: bool = Query(False, description="Preview changes without committing"),
):
    """SSE endpoint: rescan gallery sidecar JSON files to populate CivitAI
    authority_terms and image_concept_observations."""

    def _sse_event(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"

    def event_stream():
        db = SessionLocal()
        try:
            yield from _rescan_civitai_observations_inner(db, dry_run, _sse_event)
        finally:
            db.close()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/tag-maint/civitai/scan-missing")
def taxonomy_tag_maint_scan_missing_civitai(
    dry_run: bool = Query(True, description="Preview changes without committing"),
    api_limit: int = Query(100, description="Max live API calls for Tier 3", ge=0),
):
    """SSE endpoint: scan for images with CivitAI IDs but no tag observations.

    3-tier resolution:
      Tier 1 — Sidecar JSON files (data.civitai.tags)
      Tier 2 — Archived API response files (disk)
      Tier 3 — Live CivitAI API (rate-limited)
    """

    def _sse_event(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"

    def event_stream():
        from services.scan_missing_service import scan_missing_civitai

        db = SessionLocal()
        try:
            yield from scan_missing_civitai(
                db, dry_run=dry_run, api_limit=api_limit, emit=_sse_event
            )
        finally:
            db.close()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/tag-maint/civitai/backfill-tag-ids")
def taxonomy_tag_maint_backfill_civitai_tag_ids(
    dry_run: bool = Query(True, description="Preview changes without committing"),
    limit: int = Query(0, description="Max sidecars to scan (0 = all)"),
    db: Session = Depends(get_db),
):
    """Backfill missing external_tag_id on CivitAI authority_terms from sidecar JSON."""
    from atelierai.config import IMAGE_LIBRARY_PATH

    library_path = Path(IMAGE_LIBRARY_PATH)
    if not library_path.is_dir():
        raise HTTPException(status_code=500, detail="Image library path not found.")
    authority = (
        db.query(TagAuthority)
        .filter(func.lower(TagAuthority.name) == "civitai")
        .first()
    )
    if authority is None:
        return {"message": "No CivitAI authority exists yet.", "resolved": 0}
    missing_before = (
        db.query(AuthorityTerm)
        .filter(
            AuthorityTerm.authority_id == authority.id,
            AuthorityTerm.external_tag_id.is_(None),
        )
        .count()
    )
    sidecars_scanned = 0
    sidecars_with_tags = 0
    total_tags_processed = 0
    cumulative_stats = {"terms_upserted": 0, "terms_created": 0, "terms_updated": 0}
    errors = 0
    for json_file in library_path.glob("*.json"):
        if limit > 0 and sidecars_scanned >= limit:
            break
        sidecars_scanned += 1
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            errors += 1
            continue
        if not isinstance(data, dict):
            continue
        civitai = data.get("civitai")
        if not isinstance(civitai, dict):
            continue
        tags = civitai.get("tags")
        if not isinstance(tags, list) or not tags:
            continue
        has_ids = any(isinstance(t, dict) and t.get("id") is not None for t in tags)
        if not has_ids:
            continue
        sidecars_with_tags += 1
        try:
            stats = _upsert_civitai_authority_terms(db, civitai)
            total_tags_processed += stats.get("terms_upserted", 0)
            for k in ("terms_upserted", "terms_created", "terms_updated"):
                cumulative_stats[k] += stats.get(k, 0)
        except Exception as exc:
            errors += 1
            print(f"   [backfill-tag-ids] Error processing {json_file.name}: {exc}")
    if not dry_run and cumulative_stats["terms_upserted"] > 0:
        db.commit()
    missing_after = (
        (
            db.query(AuthorityTerm)
            .filter(
                AuthorityTerm.authority_id == authority.id,
                AuthorityTerm.external_tag_id.is_(None),
            )
            .count()
        )
        if not dry_run
        else missing_before
    )
    return {
        "dry_run": dry_run,
        "sidecars_scanned": sidecars_scanned,
        "sidecars_with_tag_ids": sidecars_with_tags,
        "tags_processed": total_tags_processed,
        "terms_created": cumulative_stats["terms_created"],
        "terms_updated": cumulative_stats["terms_updated"],
        "missing_ids_before": missing_before,
        "missing_ids_after": missing_after,
        "resolved": missing_before - missing_after,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Snapshot Import
# ---------------------------------------------------------------------------


@router.post("/snapshot/import_file", response_model=TaxonomySnapshotImportResponse)
async def taxonomy_snapshot_import_file(
    dry_run: bool = Form(True),
    backup_db: bool = Form(True),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Import a taxonomy snapshot file (``atelierai.taxonomy.snapshot`` v1).

    The file is validated, then imported in phases (authorities → concepts →
    aliases → authority_terms → user_bindings).  Non-ephemeral data
    mismatches cause the import to abort with a conflict report.

    Use ``dry_run=true`` (default) to validate and preview the import without
    making changes.  Set ``backup_db=true`` to create a timestamped backup of
    the SQLite database before a live import.
    """
    raw_bytes = await file.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")

    try:
        raw_text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded text")

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}")

    source_name = file.filename or "snapshot.json"

    # Pre-flight validation
    validation_errors = validate_snapshot(data)
    if validation_errors:
        return TaxonomySnapshotImportResponse(
            status="aborted",
            snapshot_format=data.get("format", ""),
            snapshot_version=data.get("version", 0),
            source_file=source_name,
            errors=validation_errors,
        )

    # Run import
    result = import_snapshot(
        db,
        data=data,
        dry_run=dry_run,
        backup_db=backup_db,
        source_file=source_name,
        db_url=app_config.DATABASE_URL,
    )

    return TaxonomySnapshotImportResponse(**result)


# ── Concept Attribute CRUD ──────────────────────────────────────────────────


@router.get("/concepts/{concept_id}/attributes", response_model=list[ConceptAttributeEntry])
async def taxonomy_list_attributes(
    concept_id: int,
    db: Session = Depends(get_db),
):
    """List all attributes for a concept."""
    concept = db.query(Concept).filter(Concept.id == concept_id).first()
    if not concept:
        raise HTTPException(status_code=404, detail="Concept not found")

    results = []
    for attr in concept.attributes:
        attr_concept = attr.attribute_concept
        results.append(
            ConceptAttributeEntry(
                concept_id=concept_id,
                attribute_concept_id=attr_concept.id,
                attribute_concept_name=attr_concept.canonical_name,
                attribute_kind=attr.attribute_kind,
                invariance=attr.invariance,
                consistency_score=attr.consistency_score,
                notes=attr.notes,
            )
        )
    return results


@router.post("/concepts/{concept_id}/attributes", response_model=ConceptAttributeEntry)
async def taxonomy_add_attribute(
    concept_id: int,
    payload: ConceptAttributeAddRequest,
    db: Session = Depends(get_db),
):
    """Add an attribute (another concept) to a concept."""
    concept = db.query(Concept).filter(Concept.id == concept_id).first()
    if not concept:
        raise HTTPException(status_code=404, detail="Concept not found")

    attr_concept = db.query(Concept).filter(Concept.id == payload.attribute_concept_id).first()
    if not attr_concept:
        raise HTTPException(status_code=404, detail="Attribute concept not found")

    if payload.attribute_concept_id == concept_id:
        raise HTTPException(status_code=400, detail="A concept cannot be an attribute of itself")

    # Check for duplicate
    existing = (
        db.query(ConceptAttributeProfile)
        .filter(
            ConceptAttributeProfile.concept_id == concept_id,
            ConceptAttributeProfile.attribute_concept_id == payload.attribute_concept_id,
        )
        .first()
    )
    if existing:
        raise HTTPException(status_code=409, detail="Attribute already exists for this concept")

    attr = ConceptAttributeProfile(
        concept_id=concept_id,
        attribute_concept_id=payload.attribute_concept_id,
        attribute_kind=payload.attribute_kind,
        invariance=payload.invariance,
        consistency_score=payload.consistency_score,
        notes=payload.notes,
    )
    db.add(attr)
    db.commit()
    db.refresh(attr)

    return ConceptAttributeEntry(
        concept_id=concept_id,
        attribute_concept_id=attr_concept.id,
        attribute_concept_name=attr_concept.canonical_name,
        attribute_kind=attr.attribute_kind,
        invariance=attr.invariance,
        consistency_score=attr.consistency_score,
        notes=attr.notes,
    )


@router.patch(
    "/concepts/{concept_id}/attributes/{attribute_concept_id}",
    response_model=ConceptAttributeEntry,
)
async def taxonomy_update_attribute(
    concept_id: int,
    attribute_concept_id: int,
    payload: ConceptAttributeUpdateRequest,
    db: Session = Depends(get_db),
):
    """Update an existing attribute entry."""
    attr = (
        db.query(ConceptAttributeProfile)
        .filter(
            ConceptAttributeProfile.concept_id == concept_id,
            ConceptAttributeProfile.attribute_concept_id == attribute_concept_id,
        )
        .first()
    )
    if not attr:
        raise HTTPException(status_code=404, detail="Attribute not found")

    updates = payload.model_dump(exclude_unset=True)
    for field, value in updates.items():
        setattr(attr, field, value)

    db.commit()
    db.refresh(attr)

    return ConceptAttributeEntry(
        concept_id=concept_id,
        attribute_concept_id=attribute_concept_id,
        attribute_concept_name=attr.attribute_concept.canonical_name,
        attribute_kind=attr.attribute_kind,
        invariance=attr.invariance,
        consistency_score=attr.consistency_score,
        notes=attr.notes,
    )


@router.delete("/concepts/{concept_id}/attributes/{attribute_concept_id}")
async def taxonomy_delete_attribute(
    concept_id: int,
    attribute_concept_id: int,
    db: Session = Depends(get_db),
):
    """Remove an attribute from a concept."""
    attr = (
        db.query(ConceptAttributeProfile)
        .filter(
            ConceptAttributeProfile.concept_id == concept_id,
            ConceptAttributeProfile.attribute_concept_id == attribute_concept_id,
        )
        .first()
    )
    if not attr:
        raise HTTPException(status_code=404, detail="Attribute not found")

    db.delete(attr)
    db.commit()
    return {"status": "deleted", "concept_id": concept_id, "attribute_concept_id": attribute_concept_id}
