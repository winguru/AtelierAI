# main.py
import os
import csv
import json
import re
import tempfile
import time
from pathlib import Path
from datetime import datetime
from fastapi import FastAPI, Depends, HTTPException, File, UploadFile, Form
from typing import List, Optional, Literal
from contextlib import asynccontextmanager
from urllib.parse import urlparse

import requests
from PIL import Image
from sqlalchemy import text, func, or_
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, joinedload
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from atelierai.config import (
    IMAGE_LIBRARY_PATH,
    CURRENT_SCHEMA_VERSION,
    DATABASE_URL,
    ALLOW_SCHEMA_RESET,
)

# Use absolute imports for consistency with our project structure
from database import (
    SessionLocal,
    Base,
    engine,
    get_db,
)
from models import (
    ImageModel,
    CollectionModel,
    ImageCollectionMembership,
    ImageTag,
    Concept,
    ConceptAlias,
    AuthorityTerm,
    ImageConceptObservation,
    TagAuthority,
    DatasetImage,
    AnalysisData,
    Tool,
    License,
    Artist,
    SchemaVersion,
)  # Import the specific classes we need

from image_collection import ImageCollection
from image_data import ImageData
from image_processor import ImageProcessor, is_exiftool_available
from civitai_enrichment import (
    is_civitai_image_url,
    extract_civitai_image_id,
    fetch_civitai_image_data,
)
from atelierai.civitai.civitai_api import CivitaiAPI
from atelierai.civitai.civitai_image import CivitaiImage
from atelierai.civitai.civitai import CivitaiPrivateScraper
from atelierai.utils import PngRepacker


class ScanRequest(BaseModel):
    folder_path: str


class ImageUpdateRequest(BaseModel):
    source_url: Optional[str] = None
    artist_name: Optional[str] = None
    artist_profile: Optional[str] = None


class CivitaiImportRequest(BaseModel):
    import_type: Literal["collection", "image"]
    value: str
    limit: Optional[int] = None


class CollectionCreateRequest(BaseModel):
    name: str


class CollectionRenameRequest(BaseModel):
    name: str


class TaxonomyAliasCreateRequest(BaseModel):
    alias: str
    alias_type: str = "synonym"
    is_preferred: bool = False
    authority_name: Optional[str] = None
    external_tag_id: Optional[str] = None


class TaxonomyMergeRequest(BaseModel):
    source_concept_id: int
    target_concept_id: int
    create_source_alias: bool = True
    deactivate_source: bool = True
    dry_run: bool = False


class TaxonomyParentUpdateRequest(BaseModel):
    parent_concept_id: Optional[int] = None
    dry_run: bool = False


class TaxonomyConceptCreateRequest(BaseModel):
    canonical_name: str
    parent_concept_id: Optional[int] = None
    description: Optional[str] = None


class TaxonomyConceptUpdateRequest(BaseModel):
    canonical_name: Optional[str] = None
    description: Optional[str] = None


class TaxonomyBootstrapImportRequest(BaseModel):
    authority_name: str = "user"
    format: str = "json"  # json or csv
    raw_text: str
    create_missing_concepts: bool = True
    dry_run: bool = True


class TaxonomyTagAssociationRequest(BaseModel):
    authority_term_id: int
    concept_id: int


class TaxonomyTagDetailsUpdateRequest(BaseModel):
    description: Optional[str] = None
    aliases: Optional[list[str]] = None
    implies: Optional[list[str]] = None
    examples: Optional[list[str]] = None


_CIVITAI_COLLECTION_PATH_RE = re.compile(r"^/collections/(?P<collection_id>\d+)(?:/.*)?$")


def _active_image_filter():
    return (ImageModel.image_status.is_(None)) | (ImageModel.image_status == "active")


def _commit_with_lock_retry(db: Session, context: str = "database write") -> None:
    """Commit with short retries for transient SQLite lock contention."""
    max_attempts = 6
    for attempt in range(1, max_attempts + 1):
        try:
            db.commit()
            return
        except OperationalError as e:
            locked_error = "database is locked" in str(e).lower() or "sqlite_busy" in str(e).lower()
            if not locked_error or attempt >= max_attempts:
                db.rollback()
                raise HTTPException(status_code=503, detail=f"{context} failed due to database lock: {e}")
            time.sleep(0.05 * attempt)
        except Exception:
            db.rollback()
            raise


def _ensure_image_lifecycle_columns() -> None:
    """Backfill lifecycle columns for existing sqlite databases."""
    with engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(images)")).fetchall()
        }

        if "image_status" not in existing:
            connection.execute(
                text("ALTER TABLE images ADD COLUMN image_status VARCHAR DEFAULT 'active' NOT NULL")
            )
        if "status_reason" not in existing:
            connection.execute(text("ALTER TABLE images ADD COLUMN status_reason VARCHAR"))
        if "replaced_by_image_id" not in existing:
            connection.execute(text("ALTER TABLE images ADD COLUMN replaced_by_image_id INTEGER"))

        connection.execute(
            text("UPDATE images SET image_status = 'active' WHERE image_status IS NULL OR image_status = ''")
        )


def _parse_civitai_image_id(value: str) -> int:
    cleaned = (value or "").strip()
    if cleaned.isdigit():
        return int(cleaned)

    image_id = extract_civitai_image_id(cleaned)
    if image_id is not None:
        return image_id

    raise HTTPException(
        status_code=400,
        detail="Invalid CivitAI image input. Provide an image URL or numeric image ID.",
    )


def _parse_civitai_collection_id(value: str) -> int:
    cleaned = (value or "").strip()
    if cleaned.isdigit():
        return int(cleaned)

    parsed = urlparse(cleaned)
    hostname = (parsed.hostname or "").lower()
    if hostname not in {"civitai.com", "www.civitai.com"}:
        raise HTTPException(
            status_code=400,
            detail="Invalid CivitAI collection URL host.",
        )

    match = _CIVITAI_COLLECTION_PATH_RE.match(parsed.path or "")
    if not match:
        raise HTTPException(
            status_code=400,
            detail="Invalid CivitAI collection URL path.",
        )

    try:
        return int(match.group("collection_id"))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail="Could not parse CivitAI collection ID.",
        )


def _normalize_taxonomy_text(value: str) -> str:
    normalized = (value or "").strip().replace("_", " ").lower()
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def _duplicate_key(value: str) -> str:
    lowered = _normalize_taxonomy_text(value)
    return re.sub(r"[^a-z0-9]+", "", lowered)


def _slugify_concept_name(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", _normalize_taxonomy_text(value)).strip("-")
    return slug or "concept"


def _ensure_unique_concept_slug(db: Session, base_slug: str) -> str:
    slug = base_slug
    idx = 2
    while db.query(Concept.id).filter(Concept.slug == slug).first() is not None:
        slug = f"{base_slug}-{idx}"
        idx += 1
    return slug


def _get_or_create_authority(db: Session, authority_name: str) -> TagAuthority:
    normalized = (authority_name or "user").strip().lower() or "user"
    authority = db.query(TagAuthority).filter(func.lower(TagAuthority.name) == normalized).first()
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
        "user": {
            "description": "User-curated local tags and concepts.",
            "is_external": False,
            "base_url": None,
        },
    }
    config = defaults.get(
        normalized,
        {
            "description": f"Imported taxonomy authority: {normalized}",
            "is_external": False,
            "base_url": None,
        },
    )
    authority = TagAuthority(name=normalized, **config)
    db.add(authority)
    db.flush()
    return authority


def _get_or_create_concept(db: Session, canonical_name: str) -> Concept:
    normalized_name = _normalize_taxonomy_text(canonical_name)
    concept = db.query(Concept).filter(Concept.canonical_name == normalized_name).first()
    if concept is not None:
        return concept

    slug = _ensure_unique_concept_slug(db, _slugify_concept_name(normalized_name))
    concept = Concept(
        canonical_name=normalized_name,
        slug=slug,
        status="active",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(concept)
    db.flush()
    return concept


def _ensure_alias_for_concept(
    db: Session,
    concept_id: int,
    alias_text: str,
    alias_type: str = "synonym",
    authority_id: Optional[int] = None,
    external_tag_id: Optional[str] = None,
) -> bool:
    normalized_alias = _normalize_taxonomy_text(alias_text)
    if not normalized_alias:
        return False
    existing = (
        db.query(ConceptAlias)
        .filter(
            ConceptAlias.concept_id == concept_id,
            ConceptAlias.normalized_alias == normalized_alias,
        )
        .first()
    )
    if existing is not None:
        return False

    db.add(
        ConceptAlias(
            concept_id=concept_id,
            alias=alias_text,
            normalized_alias=normalized_alias,
            alias_type=alias_type,
            is_preferred=alias_type == "canonical",
            authority_id=authority_id,
            external_tag_id=external_tag_id,
        )
    )
    db.flush()
    return True


def _parse_bootstrap_terms(format_name: str, raw_text: str) -> list[dict]:
    fmt = (format_name or "json").strip().lower()
    if fmt not in {"json", "csv"}:
        raise HTTPException(status_code=400, detail="format must be 'json' or 'csv'")

    if fmt == "json":
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}")

        if isinstance(data, dict) and isinstance(data.get("terms"), list):
            data = data["terms"]
        if not isinstance(data, list):
            raise HTTPException(status_code=400, detail="JSON must be a list or an object with a 'terms' list")

        rows: list[dict] = []
        for item in data:
            if isinstance(item, str):
                rows.append({"name": item})
            elif isinstance(item, dict):
                rows.append(item)
        return rows

    # CSV mode
    lines = [line for line in (raw_text or "").splitlines() if line.strip()]
    if not lines:
        return []

    reader = csv.DictReader(lines)
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV must include a header row")
    rows = []
    for row in reader:
        rows.append(row)
    return rows


def _execute_taxonomy_bootstrap_import(
    db: Session,
    *,
    authority_name: str,
    rows: list[dict],
    create_missing_concepts: bool,
    dry_run: bool,
) -> dict:
    authority = _get_or_create_authority(db, authority_name)

    stats = {
        "rows_received": len(rows),
        "rows_processed": 0,
        "concepts_created": 0,
        "concepts_reused": 0,
        "aliases_created": 0,
        "authority_terms_created": 0,
        "authority_terms_updated": 0,
        "errors": [],
    }

    for idx, row in enumerate(rows, start=1):
        try:
            with db.begin_nested():
                raw_name = str((row or {}).get("name") or (row or {}).get("external_name") or "").strip()
                if not raw_name:
                    continue

                normalized_name = _normalize_taxonomy_text(raw_name)
                external_tag_id = str((row or {}).get("external_tag_id") or "").strip()
                if not external_tag_id:
                    external_tag_id = f"name:{normalized_name}"

                mapped_concept_name = str((row or {}).get("concept_name") or "").strip()
                concept_name = mapped_concept_name or normalized_name

                concept = db.query(Concept).filter(Concept.canonical_name == _normalize_taxonomy_text(concept_name)).first()
                if concept is None:
                    if not create_missing_concepts:
                        stats["errors"].append(f"row {idx}: concept '{concept_name}' not found")
                        continue
                    concept = _get_or_create_concept(db, concept_name)
                    stats["concepts_created"] += 1
                else:
                    stats["concepts_reused"] += 1

                if _ensure_alias_for_concept(
                    db,
                    concept_id=concept.id,
                    alias_text=raw_name,
                    alias_type="imported",
                    authority_id=authority.id,
                    external_tag_id=external_tag_id,
                ):
                    stats["aliases_created"] += 1

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
                        concept_id=concept.id,
                        created_at=datetime.utcnow(),
                        updated_at=datetime.utcnow(),
                        last_seen_at=datetime.utcnow(),
                    )
                    db.add(term)
                    db.flush()
                    stats["authority_terms_created"] += 1
                else:
                    changed = False
                    if term.external_tag_id != external_tag_id:
                        term.external_tag_id = external_tag_id
                        changed = True
                    if term.external_name != raw_name:
                        term.external_name = raw_name
                        changed = True
                    if term.normalized_external_name != normalized_name:
                        term.normalized_external_name = normalized_name
                        changed = True
                    if term.concept_id != concept.id:
                        term.concept_id = concept.id
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


def _is_descendant(db: Session, ancestor_id: int, candidate_descendant_id: int) -> bool:
    current = db.query(Concept).filter(Concept.id == candidate_descendant_id).first()
    seen: set[int] = set()
    while current is not None and current.parent_concept_id is not None:
        if current.id in seen:
            break
        seen.add(current.id)
        if current.parent_concept_id == ancestor_id:
            return True
        current = db.query(Concept).filter(Concept.id == current.parent_concept_id).first()
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
    return mapping.get(normalized, authority_name.title() if authority_name else "Unknown")


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


def _guess_suffix(mime_type: Optional[str]) -> str:
    mime = (mime_type or "").lower()
    if "mp4" in mime:
        return ".mp4"
    if "png" in mime:
        return ".png"
    if "webp" in mime:
        return ".webp"
    if "gif" in mime:
        return ".gif"
    if "jpeg" in mime or "jpg" in mime:
        return ".jpg"
    return ".jpg"


def _download_civitai_image(image_url: str, image_id: int, mime_type: Optional[str]) -> Path:
    response = requests.get(image_url, timeout=45)
    response.raise_for_status()

    suffix = _guess_suffix(mime_type)
    with tempfile.NamedTemporaryFile(
        mode="wb",
        prefix=f"temp_civitai_{image_id}_",
        suffix=suffix,
        dir=IMAGE_LIBRARY_PATH,
        delete=False,
    ) as temp_file:
        temp_file.write(response.content)
        return Path(temp_file.name)


def _build_civitai_original_filename(
    image_id: int,
    preferred_name: Optional[str],
    image_url: str,
    mime_type: Optional[str],
) -> str:
    suffix = _guess_suffix(mime_type)

    candidates: list[str] = []
    if isinstance(preferred_name, str) and preferred_name.strip():
        candidates.append(preferred_name.strip())

    url_path_name = Path(urlparse(image_url).path).name
    if url_path_name:
        candidates.append(url_path_name)

    for candidate in candidates:
        safe_name = Path(candidate).name
        if not safe_name:
            continue
        if not Path(safe_name).suffix:
            safe_name = f"{safe_name}{suffix}"
        return safe_name

    return f"civitai_{image_id}{suffix}"


def _resolve_civitai_image_target(api: CivitaiAPI, image_id: int) -> dict:
    basic_info = api.fetch_basic_info(image_id)
    generation_data = api.fetch_generation_data(image_id)

    if not basic_info and not generation_data:
        raise HTTPException(
            status_code=404,
            detail=f"Could not fetch CivitAI data for image {image_id}.",
        )

    image = CivitaiImage.from_single_image(
        basic_info=basic_info or {"id": image_id},
        generation_data=generation_data or {},
        api=api,
    )
    image_data = image.to_dict(include_full_url=True)

    image_url = image_data.get("url")
    if not image_url:
        raise HTTPException(
            status_code=502,
            detail=f"CivitAI image {image_id} did not include a downloadable URL.",
        )

    mime_type = (basic_info or {}).get("mimeType") if isinstance(basic_info, dict) else None
    preferred_name = (basic_info or {}).get("name") if isinstance(basic_info, dict) else None
    original_filename = _build_civitai_original_filename(
        image_id=image_id,
        preferred_name=preferred_name,
        image_url=image_url,
        mime_type=mime_type,
    )

    basic_user = basic_info.get("user", {}) if isinstance(basic_info, dict) else {}
    author_name = image_data.get("author")
    if not author_name and isinstance(basic_user, dict):
        author_name = basic_user.get("username")

    return {
        "image_id": image_id,
        "image_url": image_url,
        "mime_type": mime_type,
        "original_filename": original_filename,
        "artist_name": author_name,
        "source_url": f"https://civitai.com/images/{image_id}",
    }


def _import_single_civitai_image(api: CivitaiAPI, db: Session, image_id: int) -> dict:
    source_url = f"https://civitai.com/images/{image_id}"
    recovered_existing = False

    # Fast path: if this exact CivitAI source URL is already in library,
    # skip download and only attempt metadata repair.
    existing_by_source = _find_existing_image_by_source_url(db, source_url)
    if existing_by_source is not None:
        existing_status = (getattr(existing_by_source, "image_status", None) or "active").lower()
        if existing_status == "tombstoned":
            return {
                "image_id": image_id,
                "image_db_id": existing_by_source.id,
                "images_added": 0,
                "images_skipped": 1,
                "images_recovered": 0,
                "json_files_created": 0,
                "metadata_backfilled": False,
                "skip_reason": "tombstoned_source_url",
                "existing_image_id": existing_by_source.id,
                "existing_file_hash": existing_by_source.file_hash,
                "existing_file_path": existing_by_source.file_path,
                "existing_source_url": existing_by_source.source_url,
                "error": None,
            }

        if existing_status == "deleted":
            existing_by_source.image_status = "active"
            existing_by_source.status_reason = None
            existing_by_source.replaced_by_image_id = None
            db.flush()
            return {
                "image_id": image_id,
                "image_db_id": existing_by_source.id,
                "images_added": 1,
                "images_skipped": 0,
                "images_recovered": 0,
                "json_files_created": 0,
                "metadata_backfilled": False,
                "skip_reason": None,
                "existing_image_id": existing_by_source.id,
                "existing_file_hash": existing_by_source.file_hash,
                "existing_file_path": existing_by_source.file_path,
                "existing_source_url": existing_by_source.source_url,
                "error": None,
            }

        existing_path = Path(IMAGE_LIBRARY_PATH) / str(existing_by_source.file_path)
        if _is_local_media_usable(existing_path, existing_by_source.mimetype):
            metadata_backfilled = _ensure_civitai_metadata_for_existing_image(
                db=db,
                image=existing_by_source,
                source_url=source_url,
            )
            return {
                "image_id": image_id,
                "image_db_id": existing_by_source.id,
                "images_added": 0,
                "images_skipped": 1,
                "images_recovered": 0,
                "json_files_created": 0,
                "metadata_backfilled": metadata_backfilled,
                "skip_reason": "existing_source_url",
                "existing_image_id": existing_by_source.id,
                "existing_file_hash": existing_by_source.file_hash,
                "existing_file_path": existing_by_source.file_path,
                "existing_source_url": existing_by_source.source_url,
                "error": None,
            }

            # Existing source-url record is stale/corrupt: remove and re-download.
            _remove_local_image_record(db, existing_by_source)
            recovered_existing = True

    temp_path = None
    try:
        target = _resolve_civitai_image_target(api, image_id)
        temp_path = _download_civitai_image(
            image_url=target["image_url"],
            image_id=image_id,
            mime_type=target["mime_type"],
        )

        ingest_result = ImageCollection(db).ingest_uploaded_file(
            uploaded_file_path=temp_path,
            original_filename=target["original_filename"],
            artist_name=target["artist_name"],
            source_url=target["source_url"],
            license_id=None,
        )
        return {
            "image_id": image_id,
            "image_db_id": ingest_result.get("image_id"),
            "images_added": int(ingest_result.get("images_added", 0)),
            "images_skipped": int(ingest_result.get("images_skipped", 0)),
            "images_recovered": 1 if recovered_existing else 0,
            "json_files_created": int(ingest_result.get("json_files_created", 0)),
            "metadata_backfilled": False,
            "skip_reason": ingest_result.get("skip_reason"),
            "existing_image_id": ingest_result.get("existing_image_id"),
            "existing_file_hash": ingest_result.get("existing_file_hash"),
            "existing_file_path": ingest_result.get("existing_file_path"),
            "existing_source_url": ingest_result.get("existing_source_url"),
            "error": None,
        }
    except HTTPException as e:
        return {
            "image_id": image_id,
            "image_db_id": None,
            "images_added": 0,
            "images_skipped": 0,
            "images_recovered": 0,
            "json_files_created": 0,
            "metadata_backfilled": False,
            "skip_reason": None,
            "existing_image_id": None,
            "existing_file_hash": None,
            "existing_file_path": None,
            "existing_source_url": None,
            "error": e.detail,
        }
    except Exception as e:
        return {
            "image_id": image_id,
            "image_db_id": None,
            "images_added": 0,
            "images_skipped": 0,
            "images_recovered": 0,
            "json_files_created": 0,
            "metadata_backfilled": False,
            "skip_reason": None,
            "existing_image_id": None,
            "existing_file_hash": None,
            "existing_file_path": None,
            "existing_source_url": None,
            "error": str(e),
        }
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)


def _get_runtime_warnings() -> list[str]:
    warnings: list[str] = []
    if not is_exiftool_available():
        warnings.append(
            "exiftool is not installed or not on PATH. Video metadata extraction is limited; imports still continue."
        )
    return warnings


def _find_existing_image_by_source_url(db: Session, source_url: str) -> Optional[ImageModel]:
    """Find existing image by DB source_url, then by sidecar source_url fallback."""
    direct_matches = (
        db.query(ImageModel)
        .filter(ImageModel.source_url == source_url)
        .order_by(ImageModel.id.desc())
        .all()
    )
    if direct_matches:
        # Priority: active > tombstoned > deleted
        for status in ("active", "tombstoned", "deleted"):
            for candidate in direct_matches:
                candidate_status = (candidate.image_status or "active").lower()
                if candidate_status == status:
                    return candidate

    # Back-compat fallback: older rows may have source_url only in sidecar JSON.
    candidates = (
        db.query(ImageModel)
        .filter((ImageModel.source_url.is_(None)) | (ImageModel.source_url == ""))
        .order_by(ImageModel.id.desc())
        .all()
    )
    fallback_matches: list[ImageModel] = []
    for candidate in candidates:
        image_path = Path(IMAGE_LIBRARY_PATH) / str(candidate.file_path)
        sidecar_path = image_path.with_suffix(".json")
        if not sidecar_path.exists():
            continue

        try:
            with open(sidecar_path, "r", encoding="utf-8") as f:
                sidecar_data = json.load(f)
            if isinstance(sidecar_data, dict) and sidecar_data.get("source_url") == source_url:
                fallback_matches.append(candidate)
        except (OSError, json.JSONDecodeError):
            continue

    if fallback_matches:
        for status in ("active", "tombstoned", "deleted"):
            for candidate in fallback_matches:
                candidate_status = (candidate.image_status or "active").lower()
                if candidate_status == status:
                    return candidate

    return None


def _is_local_media_usable(image_path: Path, mimetype: Optional[str]) -> bool:
    """Best-effort validation for an existing local media file."""
    if not image_path.exists() or not image_path.is_file():
        return False

    try:
        if image_path.stat().st_size <= 0:
            return False
    except OSError:
        return False

    mime = (mimetype or "").lower()
    suffix = image_path.suffix.lower()

    # For videos, keep validation lightweight; existence + non-zero size is enough.
    if mime.startswith("video/") or suffix in {".mp4", ".webm", ".mov", ".mkv"}:
        return True

    # For images, verify decode integrity to catch corrupt payloads.
    try:
        with Image.open(image_path) as img:
            img.verify()
        return True
    except Exception:
        return False


def _remove_local_image_record(db: Session, image: ImageModel) -> None:
    """Remove a broken local image row and associated files."""
    image_path = Path(IMAGE_LIBRARY_PATH) / str(image.file_path)
    sidecar_path = image_path.with_suffix(".json")

    try:
        if image_path.exists():
            image_path.unlink()
    except OSError:
        pass

    try:
        if sidecar_path.exists():
            sidecar_path.unlink()
    except OSError:
        pass

    db.delete(image)
    db.flush()


def _ensure_civitai_metadata_for_existing_image(
    db: Session,
    image: ImageModel,
    source_url: str,
) -> bool:
    """Backfill missing CivitAI metadata for an existing local image record."""
    image_path = Path(IMAGE_LIBRARY_PATH) / str(image.file_path)
    if not image_path.exists():
        return False

    sidecar_path = image_path.with_suffix(".json")
    sidecar_data: dict = {}
    if sidecar_path.exists():
        try:
            with open(sidecar_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                sidecar_data = loaded
        except (OSError, json.JSONDecodeError):
            sidecar_data = {}

    db_json = image.json_metadata if isinstance(image.json_metadata, dict) else {}
    sidecar_has_civitai = isinstance(sidecar_data.get("civitai"), dict)
    db_has_civitai = isinstance(db_json.get("civitai"), dict)
    if sidecar_has_civitai and db_has_civitai:
        return False

    civitai_data = fetch_civitai_image_data(source_url)
    if not civitai_data:
        return False

    merged_json = dict(db_json)
    merged_json["civitai"] = civitai_data

    (
        db.query(ImageModel)
        .filter(ImageModel.id == image.id)
        .update(
            {
                ImageModel.json_metadata: merged_json,
                ImageModel.source_url: source_url,
                ImageModel.source_site: "civitai",
            },
            synchronize_session=False,
        )
    )
    db.flush()
    db.refresh(image)

    processor = ImageProcessor(str(image_path), db, IMAGE_LIBRARY_PATH)
    processor.save_json_metadata(
        image_path,
        image,
        additional_data={"civitai": civitai_data},
    )
    return True


def _normalize_collection_name(name: str) -> str:
    normalized = (name or "").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="Collection name is required.")
    return normalized


def _serialize_collection(collection: CollectionModel) -> dict:
    return {
        "id": collection.id,
        "name": collection.name,
        "source": collection.source,
        "civitai_collection_id": collection.civitai_collection_id,
    }


def _fetch_civitai_collection_name(api: CivitaiAPI, civitai_collection_id: int) -> str:
    fallback = f"CivitAI Collection {civitai_collection_id}"
    try:
        response = api._make_request(
            endpoint="collection.getById",
            payload_data={"id": int(civitai_collection_id), "authed": True},
        )
        if not isinstance(response, dict):
            return fallback

        if isinstance(response.get("collection"), dict):
            candidate = response["collection"].get("name")
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()

        candidate = response.get("name")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
        return fallback
    except Exception:
        return fallback


def _get_or_create_collection(
    db: Session,
    name: str,
    source: str = "user",
    civitai_collection_id: Optional[int] = None,
) -> CollectionModel:
    normalized_name = _normalize_collection_name(name)
    existing = db.query(CollectionModel).filter(CollectionModel.name == normalized_name).first()
    if existing:
        if civitai_collection_id is not None and existing.civitai_collection_id is None:
            existing.civitai_collection_id = civitai_collection_id
        if source == "civitai" and existing.source == "user":
            existing.source = "civitai"
        db.flush()
        return existing

    created = CollectionModel(
        name=normalized_name,
        source=source,
        civitai_collection_id=civitai_collection_id,
    )
    db.add(created)
    db.flush()
    return created


def _ensure_image_in_collection(db: Session, image_id: int, collection_id: int) -> None:
    existing = (
        db.query(ImageCollectionMembership)
        .filter(
            ImageCollectionMembership.image_id == image_id,
            ImageCollectionMembership.collection_id == collection_id,
        )
        .first()
    )
    if existing is not None:
        return

    db.add(ImageCollectionMembership(image_id=image_id, collection_id=collection_id))
    db.flush()


def create_initial_data():
    """
    Populates the database with initial tools and licenses if they don't already exist.
    """
    db = SessionLocal()
    try:
        # --- Create Initial Tools ---
        initial_tools = [
            {
                "name": "WD14 Tagger",
                "description": "A fine-tuned CLIP model for generating Danbooru-style tags.",
                "version": "v1.4",
            },
            {
                "name": "BLIP",
                "description": "A multimodal model for generating natural language image captions.",
                "version": "v1",
            },
            {
                "name": "GPT-4-Vision",
                "description": "OpenAI's multimodal model for advanced image analysis and description.",
                "version": "gpt-4-vision-preview",
            },
            {
                "name": "Custom Caption",
                "description": "A user-curated, manually entered description.",
                "version": "user",
            },
        ]
        for tool_data in initial_tools:
            existing_tool = (
                db.query(Tool).filter(Tool.name == tool_data["name"]).first()
            )
            if not existing_tool:
                db_tool = Tool(**tool_data)
                db.add(db_tool)
                print(f"  - Created tool: {db_tool.name}")

        # --- Create Initial Licenses ---
        initial_licenses = [
            {
                "name": "Creative Commons Attribution 4.0 International",
                "short_name": "CC BY 4.0",
                "url": "https://creativecommons.org/licenses/by/4.0/",
                "allows_commercial_use": True,
                "requires_attribution": True,
            },
            {
                "name": "Creative Commons Attribution-NonCommercial 4.0 International",
                "short_name": "CC BY-NC 4.0",
                "url": "https://creativecommons.org/licenses/by-nc/4.0/",
                "allows_commercial_use": False,
                "requires_attribution": True,
            },
            {
                "name": "Public Domain Dedication (CC0)",
                "short_name": "CC0 1.0",
                "url": "https://creativecommons.org/publicdomain/zero/1.0/",
                "allows_commercial_use": True,
                "requires_attribution": False,
            },
            {
                "name": "All Rights Reserved",
                "short_name": "ARR",
                "url": "",
                "allows_commercial_use": False,
                "requires_attribution": False,
            },
        ]
        for license_data in initial_licenses:
            existing_license = (
                db.query(License)
                .filter(License.short_name == license_data["short_name"])
                .first()
            )
            if not existing_license:
                db_license = License(**license_data)
                db.add(db_license)
                print(f"  - Created license: {db_license.short_name}")

        # --- Create Tag Authorities ---
        initial_authorities = [
            {
                "name": "civitai",
                "description": "CivitAI native tag authority and IDs.",
                "is_external": True,
                "base_url": "https://civitai.com",
            },
            {
                "name": "danbooru",
                "description": "Danbooru tag authority and IDs.",
                "is_external": True,
                "base_url": "https://danbooru.donmai.us",
            },
            {
                "name": "user",
                "description": "User-curated local tags and concepts.",
                "is_external": False,
                "base_url": None,
            },
            {
                "name": "ai_agent",
                "description": "Local or remote AI-generated concept observations.",
                "is_external": False,
                "base_url": None,
            },
        ]
        for authority_data in initial_authorities:
            existing_authority = (
                db.query(TagAuthority)
                .filter(TagAuthority.name == authority_data["name"])
                .first()
            )
            if not existing_authority:
                db_authority = TagAuthority(**authority_data)
                db.add(db_authority)
                print(f"  - Created tag authority: {db_authority.name}")

        db.commit()
        print("Initial data population complete.")
    except Exception as e:
        print(f"An error occurred during initial data creation: {e}")
        db.rollback()
    finally:
        db.close()


# Define the lifespan context manager
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Starting AtelierAI API...")

    # --- NEW SCHEMA VERSIONING LOGIC ---
    # Check if the database file exists at all (for sqlite only).
    db_file_path = None
    db_exists = True
    if DATABASE_URL.startswith("sqlite:///"):
        db_file_path = os.path.abspath(
            os.path.expanduser(DATABASE_URL.replace("sqlite:///", "", 1))
        )
        db_exists = os.path.exists(db_file_path)

    if db_exists:
        # Check the version
        try:
            with engine.connect() as connection:
                version_result = connection.execute(
                    SchemaVersion.__table__.select()
                ).scalar_one_or_none()
                if version_result != CURRENT_SCHEMA_VERSION:
                    print(
                        f"⚠️ Schema version mismatch. Found {version_result}, expected {CURRENT_SCHEMA_VERSION}."
                    )
                    if db_file_path and os.path.exists(db_file_path):
                        if ALLOW_SCHEMA_RESET:
                            print("   Recreating database (ALLOW_SCHEMA_RESET=true)...")
                            os.remove(db_file_path)
                            db_exists = False
                        else:
                            raise RuntimeError(
                                "Schema mismatch detected and automatic reset is disabled. "
                                "Set ALLOW_SCHEMA_RESET=true in .env for development-only "
                                "auto-rebuild, or migrate/update the database manually."
                            )
                    else:
                        raise RuntimeError(
                            "Schema mismatch detected, but automatic reset is only supported "
                            "for sqlite file databases. Please migrate/update the database manually."
                        )
                else:
                    print("✅ Database schema is up to date.")
        except Exception as e:
            print(f"⚠️ Could not check schema version (table might not exist): {e}")
            if db_file_path and os.path.exists(db_file_path):
                if ALLOW_SCHEMA_RESET:
                    print("   Recreating database to be safe (ALLOW_SCHEMA_RESET=true)...")
                    os.remove(db_file_path)
                    db_exists = False
                else:
                    raise RuntimeError(
                        "Could not verify schema version and automatic reset is disabled. "
                        "Set ALLOW_SCHEMA_RESET=true in .env for development-only auto-rebuild, "
                        "or inspect/fix the database manually."
                    ) from e
            else:
                raise RuntimeError(
                    "Could not verify schema version for a non-sqlite database. "
                    "Automatic reset is unavailable; inspect/fix the database manually."
                ) from e

    # Create tables and initial data if the DB doesn't exist
    if not db_exists:
        print("Creating new database and initial data...")
        Base.metadata.create_all(bind=engine, checkfirst=True)

        # Create schema version record once.
        with SessionLocal() as db:
            existing_version = (
                db.query(SchemaVersion)
                .filter(SchemaVersion.version_num == CURRENT_SCHEMA_VERSION)
                .first()
            )
            if not existing_version:
                db.add(SchemaVersion(version_num=CURRENT_SCHEMA_VERSION))
                db.commit()

        create_initial_data()
        print("Database setup complete.")
    else:
        # If DB exists and is up-to-date, just run the data creation check
        create_initial_data()

    # Ensure any newly added tables exist for existing databases.
    Base.metadata.create_all(bind=engine, checkfirst=True)
    _ensure_image_lifecycle_columns()

    print("AtelierAI API is ready to go!")

    yield

    print("Shutting down AtelierAI API...")


# Pass the lifespan manager to the FastAPI app
app = FastAPI(title="AtelierAI API", version="0.1.0", lifespan=lifespan)

# Mount the static files directory
# This will serve files from the 'frontend' directory under the '/frontend/' URL path
app.mount("/frontend", StaticFiles(directory="frontend"), name="frontend")
# Expose processed images so the gallery can render thumbnails/full previews.
app.mount("/image_library", StaticFiles(directory=IMAGE_LIBRARY_PATH), name="image_library")


# Define a root endpoint to serve the main index.html file
@app.get("/")
async def read_index():
    return FileResponse("frontend/index.html")


@app.get("/tree")
async def read_tree_prototype():
    return FileResponse("frontend/tree.html")


@app.get("/images/", response_model=list[dict])
def read_images(
    skip: int = 0,
    limit: int = 10,
    sort_by: Literal["first_added", "last_added"] = "first_added",
    db: Session = Depends(get_db),
):
    """
    Returns a list of images with their associated artist and license info.
    Uses ImageData class to encapsulate and display image metadata.
    """
    # Use joinedload to efficiently fetch the related objects
    images_query = db.query(ImageModel).options(
        joinedload(ImageModel.artist),
        joinedload(ImageModel.license),
        joinedload(ImageModel.collections),
    ).filter(_active_image_filter())
    if sort_by == "last_added":
        images_query = images_query.order_by(ImageModel.id.desc())
    else:
        images_query = images_query.order_by(ImageModel.id.asc())

    images = images_query.offset(skip).limit(limit).all()

    # Build DB payload and merge raw sidecar JSON (source of authority) when present.
    response_payload: list[dict] = []
    for image in images:
        db_dict = ImageData.from_db_record(image).to_dict()

        sidecar_path = (Path(IMAGE_LIBRARY_PATH) / str(image.file_path)).with_suffix(
            ".json"
        )

        sidecar_dict: dict = {}
        if sidecar_path.exists():
            try:
                with open(sidecar_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    sidecar_dict = loaded
            except (OSError, json.JSONDecodeError):
                sidecar_dict = {}

        # Sidecar values override DB where present.
        merged = {**db_dict, **sidecar_dict}
        merged["collection_names"] = [c.name for c in image.collections]
        merged["collection_ids"] = [c.id for c in image.collections]
        response_payload.append(merged)

    return response_payload


@app.get("/images/state", response_model=dict)
def read_images_state(db: Session = Depends(get_db)):
    """Return lightweight image-library state for polling-based UI refresh logic."""
    total_count = db.query(ImageModel).filter(_active_image_filter()).count()
    latest_row = (
        db.query(ImageModel.id)
        .filter(_active_image_filter())
        .order_by(ImageModel.id.desc())
        .first()
    )
    latest_id = int(latest_row[0]) if latest_row else 0
    return {
        "count": total_count,
        "latest_id": latest_id,
    }


@app.patch("/images/{file_hash}", response_model=dict)
def update_image(file_hash: str, payload: ImageUpdateRequest, db: Session = Depends(get_db)):
    """Update editable image metadata fields and persist to sidecar JSON."""
    image = db.query(ImageModel).filter(ImageModel.file_hash == file_hash).first()
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found")

    update_values: dict = {}
    sidecar_additional_data: dict = {}

    # Expandable field-update pattern for future editable metadata cards.
    if payload.source_url is not None:
        normalized_source = payload.source_url.strip() or None
        update_values[ImageModel.source_url] = normalized_source
        if normalized_source and is_civitai_image_url(normalized_source):
            update_values[ImageModel.source_site] = "civitai"
        elif normalized_source is None:
            update_values[ImageModel.source_site] = None

    if payload.artist_name is not None:
        normalized_artist = payload.artist_name.strip()
        if normalized_artist:
            artist_obj = db.query(Artist).filter(Artist.name == normalized_artist).first()
            if artist_obj is None:
                artist_obj = Artist(name=normalized_artist)
                db.add(artist_obj)
                db.flush()
            update_values[ImageModel.artist_id] = artist_obj.id
        else:
            update_values[ImageModel.artist_id] = None

    if payload.artist_profile is not None:
        normalized_artist_profile = payload.artist_profile.strip() or None
        sidecar_additional_data["artist_profile"] = normalized_artist_profile

    if update_values:
        (
            db.query(ImageModel)
            .filter(ImageModel.id == image.id)
            .update(update_values, synchronize_session=False)
        )

    image_path = Path(IMAGE_LIBRARY_PATH) / str(image.file_path)
    if not image_path.exists():
        raise HTTPException(
            status_code=500,
            detail=f"Image file does not exist on disk: {image.file_path}",
        )

    try:
        db.flush()
        db.refresh(image)
        processor = ImageProcessor(str(image_path), db, IMAGE_LIBRARY_PATH)
        processor.save_json_metadata(
            image_path,
            image,
            additional_data=sidecar_additional_data if sidecar_additional_data else None,
        )
        db.commit()
        image = (
            db.query(ImageModel)
            .options(joinedload(ImageModel.artist))
            .filter(ImageModel.file_hash == file_hash)
            .first()
        )
        if image is None:
            raise HTTPException(status_code=404, detail="Image not found after update")

        sidecar_artist_profile = None
        sidecar_path = (Path(IMAGE_LIBRARY_PATH) / str(image.file_path)).with_suffix(".json")
        if sidecar_path.exists():
            try:
                with open(sidecar_path, "r", encoding="utf-8") as f:
                    sidecar_data = json.load(f)
                if isinstance(sidecar_data, dict):
                    sidecar_artist_profile = sidecar_data.get("artist_profile")
            except (OSError, json.JSONDecodeError):
                sidecar_artist_profile = None
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to update image metadata: {e}")

    return {
        "message": "Image metadata updated",
        "file_hash": image.file_hash,
        "source_url": image.source_url,
        "source_site": image.source_site,
        "artist_id": image.artist_id,
        "artist_name": image.artist.name if image.artist is not None else None,
        "artist_profile": sidecar_artist_profile,
    }


@app.post("/images/{file_hash}/repair_png", response_model=dict)
def repair_png_image(file_hash: str, db: Session = Depends(get_db)):
    """Repack a PNG image and ingest it as a new library item when hash changes."""
    image = (
        db.query(ImageModel)
        .options(joinedload(ImageModel.collections), joinedload(ImageModel.artist))
        .filter(ImageModel.file_hash == file_hash)
        .first()
    )
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found")

    image_path = Path(IMAGE_LIBRARY_PATH) / str(image.file_path)
    if not image_path.exists():
        raise HTTPException(status_code=404, detail="Image file is missing on disk")

    is_png = (image.mimetype or "").lower() == "image/png" or image_path.suffix.lower() == ".png"
    if not is_png:
        raise HTTPException(status_code=400, detail="Repair is currently available for PNG images only")

    raw_bytes = image_path.read_bytes()
    repacker = PngRepacker(copy_exif=True, copy_text=True, keep_idat_separate=False)
    try:
        repacked = repacker.repack_bytes(raw_bytes)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PNG repair failed: {e}")

    if repacked.output_bytes == raw_bytes:
        return {
            "message": "PNG is already normalized; no byte-level changes produced.",
            "original_file_hash": image.file_hash,
            "repaired_file_hash": image.file_hash,
            "repaired_image_id": image.id,
            "created_new_image": False,
            "images_recovered": 0,
            "parsed_chunks": repacked.parsed_chunks,
            "bad_crc_count": repacked.bad_crc_count,
            "exif_tags": repacked.exif_tag_count,
            "text_chunks": repacked.copied_text_chunks,
        }

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            prefix=f"temp_repair_{image.file_hash}_",
            suffix=".png",
            dir=IMAGE_LIBRARY_PATH,
            delete=False,
        ) as temp_file:
            temp_file.write(repacked.output_bytes)
            temp_path = Path(temp_file.name)

        original_filename = image.file_name or image_path.name
        if original_filename.lower().endswith(".png"):
            original_filename = f"{original_filename[:-4]}.repacked.png"
        else:
            original_filename = f"{original_filename}.repacked.png"

        ingest_result = ImageCollection(db).ingest_uploaded_file(
            uploaded_file_path=temp_path,
            original_filename=original_filename,
            artist_name=image.artist.name if image.artist is not None else None,
            source_url=image.source_url,
            license_id=image.license_id,
        )

        repaired_image_id = ingest_result.get("image_id") or ingest_result.get("existing_image_id")
        repaired_image = None
        if isinstance(repaired_image_id, int):
            repaired_image = db.query(ImageModel).filter(ImageModel.id == repaired_image_id).first()

        if repaired_image is not None and repaired_image.id != image.id:
            for collection in image.collections:
                _ensure_image_in_collection(db, repaired_image.id, collection.id)

            image.image_status = "tombstoned"
            image.status_reason = "replaced_by_png_repair"
            image.replaced_by_image_id = repaired_image.id

        db.commit()

        return {
            "message": "PNG repair completed.",
            "original_file_hash": image.file_hash,
            "repaired_file_hash": repaired_image.file_hash if repaired_image is not None else image.file_hash,
            "repaired_image_id": repaired_image.id if repaired_image is not None else image.id,
            "created_new_image": bool(ingest_result.get("images_added", 0)),
            "images_recovered": int(ingest_result.get("images_added", 0)),
            "skip_reason": ingest_result.get("skip_reason"),
            "parsed_chunks": repacked.parsed_chunks,
            "bad_crc_count": repacked.bad_crc_count,
            "exif_tags": repacked.exif_tag_count,
            "text_chunks": repacked.copied_text_chunks,
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to ingest repaired PNG: {e}")
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink(missing_ok=True)


@app.delete("/images/{file_hash}/file", response_model=dict)
def delete_image_file(file_hash: str, db: Session = Depends(get_db)):
    """Soft-delete image record while preserving file and sidecar on disk."""
    max_attempts = 4
    for attempt in range(1, max_attempts + 1):
        image = db.query(ImageModel).filter(ImageModel.file_hash == file_hash).first()
        if image is None:
            raise HTTPException(status_code=404, detail="Image not found")

        try:
            image.image_status = "deleted"
            image.status_reason = "user_deleted"
            image.replaced_by_image_id = None
            db.commit()
            break
        except OperationalError as e:
            db.rollback()
            # Retry transient SQLITE_BUSY / database locked errors.
            locked_error = "database is locked" in str(e).lower() or "sqlite_busy" in str(e).lower()
            if not locked_error or attempt >= max_attempts:
                raise HTTPException(status_code=503, detail=f"Failed to delete image due to database lock: {e}")
            time.sleep(0.1 * attempt)
        except Exception as e:
            db.rollback()
            raise HTTPException(status_code=500, detail=f"Failed to delete image: {e}")

    return {
        "message": "Image marked deleted.",
        "deleted_file_hash": file_hash,
        "image_status": image.image_status,
        "status_reason": image.status_reason,
    }


@app.get("/utilities/image_status_counts", response_model=dict)
def get_image_status_counts(db: Session = Depends(get_db)):
    active = db.query(ImageModel).filter(_active_image_filter()).count()
    deleted = db.query(ImageModel).filter(ImageModel.image_status == "deleted").count()
    tombstoned = db.query(ImageModel).filter(ImageModel.image_status == "tombstoned").count()
    return {
        "active": active,
        "deleted": deleted,
        "tombstoned": tombstoned,
    }


@app.get("/utilities/inactive_images", response_model=List[dict])
def get_inactive_images(
    status: Literal["all", "deleted", "tombstoned"] = "all",
    limit: int = 200,
    db: Session = Depends(get_db),
):
    capped_limit = max(1, min(int(limit), 1000))

    query = db.query(ImageModel).order_by(ImageModel.id.desc())
    if status == "deleted":
        query = query.filter(ImageModel.image_status == "deleted")
    elif status == "tombstoned":
        query = query.filter(ImageModel.image_status == "tombstoned")
    else:
        query = query.filter(
            (ImageModel.image_status == "deleted")
            | (ImageModel.image_status == "tombstoned")
        )

    rows = query.limit(capped_limit).all()
    return [
        {
            "id": row.id,
            "file_hash": row.file_hash,
            "file_name": row.file_name,
            "file_path": row.file_path,
            "image_status": row.image_status or "active",
            "status_reason": row.status_reason,
            "replaced_by_image_id": row.replaced_by_image_id,
            "source_url": row.source_url,
            "date_modified": (
                row.date_modified.isoformat() if row.date_modified is not None else None
            ),
        }
        for row in rows
    ]


@app.post("/utilities/images/{file_hash}/restore", response_model=dict)
def restore_image_record(file_hash: str, db: Session = Depends(get_db)):
    image = db.query(ImageModel).filter(ImageModel.file_hash == file_hash).first()
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found")

    previous_status = image.image_status or "active"
    if previous_status == "active":
        return {
            "message": "Image is already active.",
            "file_hash": file_hash,
            "image_status": "active",
        }

    image.image_status = "active"
    image.status_reason = None
    image.replaced_by_image_id = None
    db.commit()
    return {
        "message": "Image restored to active.",
        "file_hash": file_hash,
        "previous_status": previous_status,
        "image_status": "active",
    }


@app.post("/utilities/purge_deleted_files", response_model=dict)
def purge_deleted_files(db: Session = Depends(get_db)):
    """Permanently remove deleted records and their on-disk files/sidecars."""
    deleted_images = db.query(ImageModel).filter(ImageModel.image_status == "deleted").all()

    purged = 0
    file_errors: list[str] = []
    for image in deleted_images:
        image_path = Path(IMAGE_LIBRARY_PATH) / str(image.file_path)
        sidecar_path = image_path.with_suffix(".json")

        try:
            if image_path.exists():
                image_path.unlink()
            if sidecar_path.exists():
                sidecar_path.unlink()
        except OSError as e:
            file_errors.append(f"{image.file_hash}: {e}")
            continue

        db.query(ImageCollectionMembership).filter(
            ImageCollectionMembership.image_id == image.id
        ).delete(synchronize_session=False)
        db.query(ImageTag).filter(ImageTag.image_id == image.id).delete(synchronize_session=False)
        db.query(DatasetImage).filter(DatasetImage.image_id == image.id).delete(synchronize_session=False)
        db.query(AnalysisData).filter(AnalysisData.image_id == image.id).delete(synchronize_session=False)

        db.delete(image)
        purged += 1

    db.commit()
    return {
        "message": "Deleted-image purge complete.",
        "purged_records": purged,
        "file_errors": file_errors,
    }


# Also, update the /artists/ endpoint to return the new artist objects
@app.get("/artists/", response_model=List[dict])
def get_artists(db: Session = Depends(get_db)):
    """Returns a list of all artists."""
    artists = db.query(Artist).all()
    return [
        {"id": artist.id, "name": artist.name, "nickname": artist.nickname}
        for artist in artists
    ]


@app.get("/collections/", response_model=List[dict])
def get_collections(db: Session = Depends(get_db)):
    collections = db.query(CollectionModel).order_by(CollectionModel.name.asc()).all()
    return [_serialize_collection(c) for c in collections]


@app.post("/collections/", response_model=dict)
def create_collection(payload: CollectionCreateRequest, db: Session = Depends(get_db)):
    normalized_name = _normalize_collection_name(payload.name)
    existing = db.query(CollectionModel).filter(CollectionModel.name == normalized_name).first()
    if existing is not None:
        raise HTTPException(status_code=409, detail="Collection with this name already exists.")

    created = CollectionModel(name=normalized_name, source="user")
    db.add(created)
    db.commit()
    db.refresh(created)
    return _serialize_collection(created)


@app.patch("/collections/{collection_id}", response_model=dict)
def rename_collection(collection_id: int, payload: CollectionRenameRequest, db: Session = Depends(get_db)):
    collection = db.query(CollectionModel).filter(CollectionModel.id == collection_id).first()
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection not found.")

    normalized_name = _normalize_collection_name(payload.name)
    duplicate = (
        db.query(CollectionModel)
        .filter(CollectionModel.name == normalized_name, CollectionModel.id != collection_id)
        .first()
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="Collection with this name already exists.")

    collection.name = normalized_name
    db.commit()
    db.refresh(collection)
    return _serialize_collection(collection)


@app.delete("/collections/{collection_id}", response_model=dict)
def delete_collection(collection_id: int, db: Session = Depends(get_db)):
    collection = db.query(CollectionModel).filter(CollectionModel.id == collection_id).first()
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection not found.")

    db.query(ImageCollectionMembership).filter(ImageCollectionMembership.collection_id == collection_id).delete(synchronize_session=False)
    db.delete(collection)
    db.commit()
    return {"message": "Collection deleted.", "collection_id": collection_id}


@app.get("/images/{file_hash}/collections", response_model=List[dict])
def get_image_collections(file_hash: str, db: Session = Depends(get_db)):
    image = (
        db.query(ImageModel)
        .options(joinedload(ImageModel.collections))
        .filter(ImageModel.file_hash == file_hash)
        .first()
    )
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found")
    return [_serialize_collection(c) for c in image.collections]


@app.post("/images/{file_hash}/collections/{collection_id}", response_model=dict)
def add_image_to_collection(file_hash: str, collection_id: int, db: Session = Depends(get_db)):
    image = db.query(ImageModel).filter(ImageModel.file_hash == file_hash).first()
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found")

    collection = db.query(CollectionModel).filter(CollectionModel.id == collection_id).first()
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection not found")

    _ensure_image_in_collection(db, image.id, collection_id)
    db.commit()
    return {
        "message": "Image added to collection.",
        "file_hash": file_hash,
        "collection": _serialize_collection(collection),
    }


@app.delete("/images/{file_hash}/collections/{collection_id}", response_model=dict)
def remove_image_from_collection(file_hash: str, collection_id: int, db: Session = Depends(get_db)):
    image = db.query(ImageModel).filter(ImageModel.file_hash == file_hash).first()
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found")

    membership = (
        db.query(ImageCollectionMembership)
        .filter(
            ImageCollectionMembership.image_id == image.id,
            ImageCollectionMembership.collection_id == collection_id,
        )
        .first()
    )
    if membership is None:
        raise HTTPException(status_code=404, detail="Image is not in that collection")

    db.delete(membership)
    db.commit()
    return {
        "message": "Image removed from collection.",
        "file_hash": file_hash,
        "collection_id": collection_id,
    }


@app.get("/licenses/", response_model=List[dict])
def get_licenses(db: Session = Depends(get_db)):
    """Returns a list of all available licenses."""
    licenses = db.query(License).all()
    return [
        {"id": license.id, "name": license.name, "short_name": license.short_name}
        for license in licenses
    ]


@app.post("/scan_library/")
def scan_library(db: Session = Depends(get_db)):
    """
    Scans the library, imports new files, and removes duplicates/orphaned records.
    """
    try:
        collection = ImageCollection(db)
        result = collection.scan()
        return result
    except FileNotFoundError as e:
        raise HTTPException(status_code=500, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"An unexpected error occurred: {e}"
        )


@app.post("/upload_images/")
async def upload_images(
    # This will be a list of uploaded files
    files: List[UploadFile] = File(...),
    # These are the optional batch metadata fields
    artist_name: Optional[str] = Form(None),
    source_url: Optional[str] = Form(None),
    license_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    """
    Uploads one or more images, saves them to the library, and adds them to the database.
    This version is robust against filesystem/database mismatches.
    """

    images_added = 0
    images_skipped = 0
    json_files_created = 0
    errors = []

    for file in files:
        temp_path = None
        try:
            # 1. Save uploaded content to a temporary file
            contents = await file.read()
            temp_path = os.path.join(IMAGE_LIBRARY_PATH, f"temp_{file.filename}")
            with open(temp_path, "wb") as f:
                f.write(contents)

            # 2. Ingest via ImageCollection to share the same processing steps as scan.
            collection = ImageCollection(db)
            ingest_result = collection.ingest_uploaded_file(
                uploaded_file_path=Path(temp_path),
                original_filename=file.filename or Path(temp_path).name,
                artist_name=artist_name,
                source_url=source_url,
                license_id=license_id,
            )

            images_added += int(ingest_result.get("images_added", 0))
            images_skipped += int(ingest_result.get("images_skipped", 0))
            json_files_created += int(ingest_result.get("json_files_created", 0))
            _commit_with_lock_retry(db, context=f"Upload commit for {file.filename}")

        except ValueError as e:
            errors.append(str(e))
        except Exception as e:
            errors.append(f"Could not process {file.filename}: {e}")
        finally:
            # Clean up the temporary file
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)

    runtime_warnings = _get_runtime_warnings()
    return {
        "message": "Upload complete.",
        "images_added": images_added,
        "images_skipped": images_skipped,
        "json_files_created": json_files_created,
        "errors": errors,
        "warnings": runtime_warnings,
    }


@app.post("/import_civitai/")
def import_civitai_images(payload: CivitaiImportRequest, db: Session = Depends(get_db)):
    """Import CivitAI images by image URL/ID or collection URL/ID."""
    value = (payload.value or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="Import value is required.")

    if payload.limit is not None and payload.limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be greater than 0.")

    try:
        api = CivitaiAPI.get_instance()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"CivitAI API initialization failed: {e}")

    results: list[dict] = []

    if payload.import_type == "image":
        image_id = _parse_civitai_image_id(value)
        result = _import_single_civitai_image(api, db, image_id)
        if not result.get("error"):
            try:
                _commit_with_lock_retry(db, context=f"Import commit for image {image_id}")
            except HTTPException as e:
                result["images_added"] = 0
                result["images_skipped"] = 0
                result["images_recovered"] = 0
                result["json_files_created"] = 0
                result["image_db_id"] = None
                result["error"] = str(e.detail)
        results.append(result)
    else:
        collection_id = _parse_civitai_collection_id(value)
        civitai_collection_name = _fetch_civitai_collection_name(api, collection_id)
        local_collection = _get_or_create_collection(
            db,
            name=civitai_collection_name,
            source="civitai",
            civitai_collection_id=collection_id,
        )
        _commit_with_lock_retry(db, context=f"Collection setup commit for {collection_id}")

        scraper = CivitaiPrivateScraper(auto_authenticate=True)
        collection_items = scraper.fetch_collection_items(
            collection_id=collection_id,
            limit=payload.limit,
        )
        if not collection_items:
            raise HTTPException(
                status_code=404,
                detail=f"No images found for CivitAI collection {collection_id}.",
            )

        seen_ids: set[int] = set()
        image_ids: list[int] = []
        for item in collection_items:
            raw_id = item.get("id") if isinstance(item, dict) else None
            try:
                image_id = int(raw_id)
            except (TypeError, ValueError):
                continue

            if image_id in seen_ids:
                continue

            seen_ids.add(image_id)
            image_ids.append(image_id)

        if payload.limit is not None:
            image_ids = image_ids[: payload.limit]

        for image_id in image_ids:
            result = _import_single_civitai_image(api, db, image_id)
            if not result.get("error"):
                image_db_id = result.get("image_db_id")
                if isinstance(image_db_id, int):
                    _ensure_image_in_collection(db, image_db_id, local_collection.id)
                try:
                    _commit_with_lock_retry(db, context=f"Import commit for image {image_id}")
                except HTTPException as e:
                    result["images_added"] = 0
                    result["images_skipped"] = 0
                    result["images_recovered"] = 0
                    result["json_files_created"] = 0
                    result["image_db_id"] = None
                    result["error"] = str(e.detail)
            results.append(result)

    images_added = sum(int(r.get("images_added", 0)) for r in results)
    images_skipped = sum(int(r.get("images_skipped", 0)) for r in results)
    images_recovered = sum(int(r.get("images_recovered", 0)) for r in results)
    json_files_created = sum(int(r.get("json_files_created", 0)) for r in results)
    errors = [
        f"Image {r.get('image_id')}: {r['error']}"
        for r in results
        if r.get("error")
    ]

    runtime_warnings = _get_runtime_warnings()

    return {
        "message": "CivitAI import complete.",
        "import_type": payload.import_type,
        "local_collection": (
            _serialize_collection(local_collection)
            if payload.import_type == "collection"
            else None
        ),
        "requested": len(results),
        "images_added": images_added,
        "images_skipped": images_skipped,
        "images_recovered": images_recovered,
        "json_files_created": json_files_created,
        "errors": errors,
        "warnings": runtime_warnings,
        "results": results,
    }


@app.get("/taxonomy/review/summary", response_model=dict)
def taxonomy_review_summary(db: Session = Depends(get_db)):
    concepts_total = db.query(Concept).count()
    concepts_active = db.query(Concept).filter(Concept.status == "active").count()
    concepts_merged = db.query(Concept).filter(Concept.status == "merged").count()
    aliases_total = db.query(ConceptAlias).count()
    terms_total = db.query(AuthorityTerm).count()
    unresolved_terms = db.query(AuthorityTerm).filter(AuthorityTerm.concept_id.is_(None)).count()
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


@app.get("/taxonomy/review/unresolved_terms", response_model=list[dict])
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
        query = query.filter(func.lower(TagAuthority.name) == authority.strip().lower())

    rows = query.limit(capped_limit).all()
    return [
        {
            "authority_term_id": term.id,
            "authority": auth.name,
            "external_tag_id": term.external_tag_id,
            "external_name": term.external_name,
            "normalized_external_name": term.normalized_external_name,
            "last_seen_at": term.last_seen_at.isoformat() if term.last_seen_at else None,
        }
        for term, auth in rows
    ]


@app.get("/taxonomy/review/potential_duplicates", response_model=list[dict])
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
        duplicates.append(
            {
                "duplicate_key": key,
                "count": len(members),
                "concepts": [
                    {
                        "id": c.id,
                        "canonical_name": c.canonical_name,
                        "status": c.status,
                    }
                    for c in members
                ],
            }
        )

    duplicates.sort(key=lambda row: (-row["count"], row["duplicate_key"]))
    return duplicates[:capped_limit]


@app.get("/taxonomy/concepts", response_model=list[dict])
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
        alias_count = db.query(ConceptAlias).filter(ConceptAlias.concept_id == concept.id).count()
        term_count = db.query(AuthorityTerm).filter(AuthorityTerm.concept_id == concept.id).count()
        observation_count = db.query(ImageConceptObservation).filter(ImageConceptObservation.concept_id == concept.id).count()
        source_labels = source_map.get(int(concept.id), [])
        response.append(
            {
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
            }
        )

    return response


@app.patch("/taxonomy/concepts/{concept_id}", response_model=dict)
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
            raise HTTPException(status_code=409, detail="Another concept already uses that canonical_name")
        concept.canonical_name = normalized_name

    if payload.description is not None:
        concept.description = payload.description.strip() or None

    concept.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(concept)

    return {
        "message": "Concept updated.",
        "concept": {
            "id": concept.id,
            "canonical_name": concept.canonical_name,
            "description": concept.description,
            "status": concept.status,
            "parent_concept_id": concept.parent_concept_id,
        },
    }


@app.post("/taxonomy/concepts/{concept_id}/aliases", response_model=dict)
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
        authority = db.query(TagAuthority).filter(
            func.lower(TagAuthority.name) == payload.authority_name.strip().lower()
        ).first()
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


@app.post("/taxonomy/review/merge_concepts", response_model=dict)
def taxonomy_merge_concepts(payload: TaxonomyMergeRequest, db: Session = Depends(get_db)):
    if payload.source_concept_id == payload.target_concept_id:
        raise HTTPException(status_code=400, detail="source_concept_id and target_concept_id must differ")

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
    observations = db.query(ImageConceptObservation).filter(ImageConceptObservation.concept_id == source.id).all()
    source_aliases = db.query(ConceptAlias).filter(ConceptAlias.concept_id == source.id).all()

    if payload.dry_run:
        target_aliases = (
            db.query(ConceptAlias)
            .filter(ConceptAlias.concept_id == target.id)
            .all()
        )
        target_alias_set = {
            (_normalize_taxonomy_text(a.normalized_alias or a.alias or ""))
            for a in target_aliases
        }

        mergeable_aliases = 0
        duplicate_aliases = 0
        for alias in source_aliases:
            normalized_alias = _normalize_taxonomy_text(alias.normalized_alias or alias.alias or "")
            if normalized_alias in target_alias_set:
                duplicate_aliases += 1
            else:
                mergeable_aliases += 1

        source_name_alias_conflict = _normalize_taxonomy_text(source.canonical_name) in target_alias_set
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
            "source_status_after": "merged" if payload.deactivate_source else source.status,
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
        normalized_alias = alias.normalized_alias or _normalize_taxonomy_text(alias.alias)
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
            db.add(
                ConceptAlias(
                    concept_id=target.id,
                    alias=source.canonical_name,
                    normalized_alias=normalized_source_name,
                    alias_type="merged_from",
                    is_preferred=False,
                )
            )
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


@app.post("/taxonomy/bootstrap/import", response_model=dict)
def taxonomy_bootstrap_import(payload: TaxonomyBootstrapImportRequest, db: Session = Depends(get_db)):
    rows = _parse_bootstrap_terms(payload.format, payload.raw_text)

    return _execute_taxonomy_bootstrap_import(
        db,
        authority_name=payload.authority_name,
        rows=rows,
        create_missing_concepts=payload.create_missing_concepts,
        dry_run=payload.dry_run,
    )


@app.post("/taxonomy/bootstrap/import_file", response_model=dict)
async def taxonomy_bootstrap_import_file(
    authority_name: str = Form("user"),
    format: str = Form("json"),
    create_missing_concepts: bool = Form(True),
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
        create_missing_concepts=create_missing_concepts,
        dry_run=dry_run,
    )
    result["source_file"] = file.filename
    return result


@app.post("/taxonomy/concepts", response_model=dict)
def taxonomy_create_concept(payload: TaxonomyConceptCreateRequest, db: Session = Depends(get_db)):
    canonical_name = _normalize_taxonomy_text(payload.canonical_name)
    if not canonical_name:
        raise HTTPException(status_code=400, detail="canonical_name is required")

    existing = db.query(Concept).filter(Concept.canonical_name == canonical_name).first()
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
        parent = db.query(Concept).filter(Concept.id == payload.parent_concept_id).first()
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
    _ensure_alias_for_concept(db, concept_id=concept.id, alias_text=canonical_name, alias_type="canonical")
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


@app.post("/taxonomy/concepts/{concept_id}/parent", response_model=dict)
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
            raise HTTPException(status_code=400, detail="Parent assignment would create a cycle")

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


@app.delete("/taxonomy/concepts/{concept_id}", response_model=dict)
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
            db.query(Concept.id)
            .filter(Concept.parent_concept_id == current)
            .all()
        )
        to_visit.extend(int(row.id) for row in children)

    db.query(AuthorityTerm).filter(AuthorityTerm.concept_id.in_(branch_ids)).update(
        {AuthorityTerm.concept_id: None, AuthorityTerm.updated_at: datetime.utcnow()},
        synchronize_session=False,
    )
    db.query(ImageConceptObservation).filter(ImageConceptObservation.concept_id.in_(branch_ids)).delete(
        synchronize_session=False
    )
    db.query(ConceptAlias).filter(ConceptAlias.concept_id.in_(branch_ids)).delete(synchronize_session=False)
    db.query(Concept).filter(Concept.id.in_(branch_ids)).delete(synchronize_session=False)
    db.commit()

    return {
        "message": "Concept branch deleted.",
        "deleted_concept_ids": sorted(branch_ids),
    }


@app.get("/taxonomy/tree", response_model=list[dict])
def taxonomy_tree(status: str = "active", db: Session = Depends(get_db)):
    query = db.query(Concept)
    if status != "all":
        query = query.filter(Concept.status == status)
    concepts = query.order_by(Concept.canonical_name.asc()).all()
    source_map = _concept_source_map(db, [int(c.id) for c in concepts])

    by_parent: dict[Optional[int], list[dict]] = {}
    for concept in concepts:
        by_parent.setdefault(concept.parent_concept_id, []).append(
            {
                "id": concept.id,
                "canonical_name": concept.canonical_name,
                "description": concept.description,
                "status": concept.status,
                "parent_concept_id": concept.parent_concept_id,
                "source_labels": source_map.get(int(concept.id), []),
                "display_prefix": _concept_display_prefix(source_map.get(int(concept.id), [])),
            }
        )

    def build_node(node: dict) -> dict:
        children = by_parent.get(node["id"], [])
        return {
            **node,
            "children": [build_node(child) for child in children],
        }

    roots = by_parent.get(None, [])
    return [build_node(root) for root in roots]


@app.get("/taxonomy/tree/state", response_model=dict)
def taxonomy_tree_state(db: Session = Depends(get_db)):
    concepts = (
        db.query(Concept)
        .filter(Concept.status == "active")
        .order_by(Concept.id.asc())
        .all()
    )
    concept_ids = [int(c.id) for c in concepts]

    alias_data_by_concept: dict[int, dict[str, list[str]]] = {
        cid: {"aliases": [], "implies": []}
        for cid in concept_ids
    }
    if concept_ids:
        aliases = (
            db.query(ConceptAlias)
            .filter(ConceptAlias.concept_id.in_(concept_ids))
            .order_by(ConceptAlias.id.asc())
            .all()
        )
        for alias in aliases:
            if not alias.alias:
                continue
            concept_id = int(alias.concept_id)
            bucket = alias_data_by_concept.setdefault(concept_id, {"aliases": [], "implies": []})
            alias_kind = str(alias.alias_type or "synonym").strip().lower()

            if alias_kind == "canonical":
                continue
            if alias_kind == "implies":
                bucket["implies"].append(alias.alias)
            else:
                bucket["aliases"].append(alias.alias)

    term_rows = (
        db.query(AuthorityTerm, TagAuthority, Concept)
        .join(TagAuthority, TagAuthority.id == AuthorityTerm.authority_id)
        .outerjoin(Concept, Concept.id == AuthorityTerm.concept_id)
        .order_by(TagAuthority.name.asc(), AuthorityTerm.external_name.asc())
        .all()
    )

    tags: list[dict] = []
    normalized_term_names: set[str] = set()
    referenced_concept_ids: set[int] = set()
    for term, authority, concept in term_rows:
        normalized_term_name = _normalize_taxonomy_text(term.external_name or "")
        if normalized_term_name:
            normalized_term_names.add(normalized_term_name)

        source_name = str(authority.name or "user").strip().lower()
        if source_name not in {"civitai", "danbooru", "prompt", "user"}:
            source_name = "user"

        if concept is not None:
            referenced_concept_ids.add(int(concept.id))

        concept_alias_data = alias_data_by_concept.get(int(concept.id), {"aliases": [], "implies": []}) if concept else {"aliases": [], "implies": []}
        metadata = term.metadata_json if isinstance(term.metadata_json, dict) else {}
        examples = metadata.get("examples") if isinstance(metadata, dict) else []
        if not isinstance(examples, list):
            examples = []
        tags.append(
            {
                "id": f"term:{term.id}",
                "authority_term_id": int(term.id),
                "name": term.external_name,
                "source": source_name,
                "scope": "image",
                "description": concept.description if concept else "",
                "aliases": concept_alias_data.get("aliases", []),
                "implies": concept_alias_data.get("implies", []),
                "examples": [str(item) for item in examples if str(item).strip()],
                "concept_id": int(concept.id) if concept else None,
            }
        )

    child_parent_ids: set[int] = {
        int(c.parent_concept_id)
        for c in concepts
        if c.parent_concept_id is not None
    }

    filtered_concepts: list[Concept] = []
    for concept in concepts:
        concept_id = int(concept.id)
        alias_data = alias_data_by_concept.get(concept_id, {"aliases": [], "implies": []})
        has_metadata = bool((concept.description or "").strip()) or bool(alias_data.get("aliases")) or bool(alias_data.get("implies"))
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

    return {
        "concepts": [
            {
                "id": int(c.id),
                "canonical_name": c.canonical_name,
                "parent_concept_id": int(c.parent_concept_id) if c.parent_concept_id is not None else None,
            }
            for c in filtered_concepts
        ],
        "tags": tags,
    }


@app.post("/taxonomy/tree/associate", response_model=dict)
def taxonomy_tree_associate_tag(payload: TaxonomyTagAssociationRequest, db: Session = Depends(get_db)):
    term = db.query(AuthorityTerm).filter(AuthorityTerm.id == payload.authority_term_id).first()
    if term is None:
        raise HTTPException(status_code=404, detail="Authority term not found")

    concept = db.query(Concept).filter(Concept.id == payload.concept_id).first()
    if concept is None:
        raise HTTPException(status_code=404, detail="Concept not found")

    term.concept_id = int(concept.id)
    term.updated_at = datetime.utcnow()
    db.commit()

    return {
        "message": "Tag associated to concept.",
        "authority_term_id": int(term.id),
        "concept_id": int(concept.id),
    }


@app.delete("/taxonomy/tree/associate/{authority_term_id}", response_model=dict)
def taxonomy_tree_disassociate_tag(authority_term_id: int, db: Session = Depends(get_db)):
    term = db.query(AuthorityTerm).filter(AuthorityTerm.id == authority_term_id).first()
    if term is None:
        raise HTTPException(status_code=404, detail="Authority term not found")

    term.concept_id = None
    term.updated_at = datetime.utcnow()
    db.commit()

    return {
        "message": "Tag disassociated from concept.",
        "authority_term_id": int(term.id),
    }


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


def _get_term_concept(db: Session, term: AuthorityTerm) -> Concept | None:
    if term.concept_id is None:
        return None
    return db.query(Concept).filter(Concept.id == term.concept_id).first()


@app.get("/taxonomy/tree/tag/{authority_term_id}/details", response_model=dict)
def taxonomy_tree_tag_details(authority_term_id: int, db: Session = Depends(get_db)):
    term = db.query(AuthorityTerm).filter(AuthorityTerm.id == authority_term_id).first()
    if term is None:
        raise HTTPException(status_code=404, detail="Authority term not found")

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
    examples = [str(item) for item in raw_examples] if isinstance(raw_examples, list) else []

    return {
        "authority_term_id": int(term.id),
        "description": description,
        "aliases": _normalize_str_list(aliases),
        "implies": _normalize_str_list(implies),
        "examples": _normalize_str_list(examples),
    }


@app.patch("/taxonomy/tree/tag/{authority_term_id}/details", response_model=dict)
def taxonomy_tree_update_tag_details(
    authority_term_id: int,
    payload: TaxonomyTagDetailsUpdateRequest,
    db: Session = Depends(get_db),
):
    term = db.query(AuthorityTerm).filter(AuthorityTerm.id == authority_term_id).first()
    if term is None:
        raise HTTPException(status_code=404, detail="Authority term not found")

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
            db.add(
                ConceptAlias(
                    concept_id=concept.id,
                    alias=alias,
                    normalized_alias=alias,
                    alias_type="synonym",
                    is_preferred=False,
                )
            )

    if concept is not None and payload.implies is not None:
        db.query(ConceptAlias).filter(
            ConceptAlias.concept_id == concept.id,
            ConceptAlias.alias_type == "implies",
        ).delete(synchronize_session=False)

        canonical = _normalize_taxonomy_text(concept.canonical_name)
        for implied in _normalize_str_list(payload.implies):
            if implied == canonical:
                continue
            db.add(
                ConceptAlias(
                    concept_id=concept.id,
                    alias=implied,
                    normalized_alias=implied,
                    alias_type="implies",
                    is_preferred=False,
                )
            )

    if payload.examples is not None:
        metadata = term.metadata_json if isinstance(term.metadata_json, dict) else {}
        metadata = dict(metadata)
        metadata["examples"] = _normalize_str_list(payload.examples)
        term.metadata_json = metadata
        term.updated_at = datetime.utcnow()

    db.commit()

    return {
        "message": "Tag details updated.",
        "authority_term_id": int(term.id),
        "concept_id": int(term.concept_id) if term.concept_id is not None else None,
    }
