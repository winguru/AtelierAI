# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/backend-startup.md
# 📄 docs: app/docs/memories/image-api.md
# 📄 docs: app/docs/memories/taxonomy-import.md
# 📄 docs: app/docs/memories/parity-workbench.md
# 📄 docs: app/docs/memories/civitai-integration.md
# ──────────────────────────────────────────────────────────────────────────────
# pyright: reportArgumentType=false, reportAssignmentType=false, reportAttributeAccessIssue=false, reportOperatorIssue=false, reportOptionalOperand=false, reportPossiblyUnboundVariable=false
# main.py
import argparse
import base64
import binascii
import glob
import hashlib
import io
import logging
import os
import json
import re
import shutil
import tempfile
import time
import threading
import urllib.parse as _urlparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from fastapi import (
    FastAPI,
    Depends,
    HTTPException,
    File,
    UploadFile,
    Form,
    Query,
    Response,
    Request,
    status,
    Body,
)
from typing import Any, Callable, Generator, List, Optional, Literal, cast
from contextlib import asynccontextmanager
from urllib.parse import quote, urlencode, urlparse
from uuid import uuid4

import requests
from PIL import Image
from sqlalchemy import text, func, or_, event
import sqlalchemy as sa
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, joinedload
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

import atelierai.config as app_config

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
    CollectionCivitaiMapping,
    ImageCollectionMembership,
    ImageVariantGroupMembership,
    VariantGroup,
    ImageTag,
    Concept,
    ConceptAlias,
    AuthorityTerm,
    ImageConceptObservation,
    ObservationCertainty,
    ObservationSource,
    TagAuthority,
    DatasetImage,
    AnalysisData,
    License,
    Artist,
    GenerationTemplate,
    GenerationMatchAttempt,
    SchemaVersion,
    SyncSession,
)  # Import the specific classes we need

from image_collection import ImageCollection
from image_data import ImageData
from image_processor import (
    ImageProcessor,
    ensure_video_poster,
    ensure_video_thumbnail,
    get_video_poster_path,
    get_video_thumbnail_media_type,
    get_video_thumbnail_path,
    get_video_thumbnail_variant,
    is_exiftool_available,
    is_ffmpeg_available,
    sanitize_display_filename,
)
from civitai_enrichment import (
    is_civitai_image_url,
    extract_civitai_image_id,
    fetch_civitai_image_data,
)
from atelierai.civitai.civitai_api import CivitaiAPI
from atelierai.civitai.civitai_image import CivitaiImage
from atelierai.civitai.civitai import CivitaiPrivateScraper
from atelierai.civitai.http_client import CivitaiRequestError
from atelierai.task_manager import BackgroundTaskManager, TaskContext
from atelierai.utils import PngRepacker, build_prompt_tag_payload
from utils.url_helpers import build_civitai_url as _build_civitai_url
from services.gallery_filter_service import (
    apply_gallery_filter,
    parse_gallery_filter,
)
from services.gallery_query import GalleryQuery
from services.gallery_tag_service import GalleryTagService
from services.image_query_service import ImageQueryService
from services.metadata_extraction import extract_civitai_nsfw_level
from services.model_reference_service import ModelReferenceService
from services.taxonomy_service import TaxonomyService
from services.db_migrations import (
    _backfill_civitai_base_model_ids as _backfill_civitai_base_model_ids,
    _backfill_civitai_users as _backfill_civitai_users,
    _backfill_user_tags_from_sidecars as _backfill_user_tags_from_sidecars,
    _backfill_user_tags_to_observations as _backfill_user_tags_to_observations,
    _ensure_base_model_id_column as _ensure_base_model_id_column,
    _ensure_blurhash_column as _ensure_blurhash_column,
    _ensure_civitai_creator_id_column as _ensure_civitai_creator_id_column,
    _ensure_civitai_deleted_at_column as _ensure_civitai_deleted_at_column,
    _ensure_civitai_hash_column as _ensure_civitai_hash_column,
    _ensure_civitai_image_id_column as _ensure_civitai_image_id_column,
    _ensure_civitai_post_id_column as _ensure_civitai_post_id_column,
    _ensure_civitai_cdn_url_column as _ensure_civitai_cdn_url_column,
    _ensure_civitai_post_title_index_columns as _ensure_civitai_post_title_index_columns,
    _ensure_civitai_user_columns as _ensure_civitai_user_columns,
    _ensure_civitai_uuid_column as _ensure_civitai_uuid_column,
    _ensure_collection_sync_columns as _ensure_collection_sync_columns,
    ensure_collection_civitai_mappings_table as _ensure_collection_civitai_mappings_table,
    _ensure_concept_prototype_columns as _ensure_concept_prototype_columns,
    _ensure_expected_file_size_column as _ensure_expected_file_size_column,
    _ensure_file_hash_nonunique as _ensure_file_hash_nonunique,
    _ensure_image_lifecycle_columns as _ensure_image_lifecycle_columns,
    _ensure_image_variant_columns as _ensure_image_variant_columns,
    _ensure_is_corrupt_column as _ensure_is_corrupt_column,
    _ensure_observation_authority_term_unique_index as _ensure_observation_authority_term_unique_index,
    _ensure_observation_unique_constraint as _ensure_observation_unique_constraint,
    _ensure_original_file_name_column as _ensure_original_file_name_column,
    _ensure_promoted_metadata_columns as _ensure_promoted_metadata_columns,
    _ensure_user_negative_tags_column as _ensure_user_negative_tags_column,
    _ensure_user_nsfw_columns as _ensure_user_nsfw_columns,
    _ensure_user_tags_column as _ensure_user_tags_column,
    _seed_civitai_base_models as _seed_civitai_base_models,
    create_initial_data as create_initial_data,
)
from services import a1111_parser_service as _a1111_svc
# populate_initial_data moved to services/db_migrations.py
from schemas import (
    ImageUpdateRequest,
    CivitaiImportRequest,
    CivitaiCollectionSyncRequest,
    CivitaiNsfwBackfillRequest,
    CivitaiCookieRequest,
    CollectionCreateRequest,
    CollectionRenameRequest,
    CollectionBulkMembershipRequest,
    TaxonomyAliasCreateRequest,
    TaxonomyMergeRequest,
    TaxonomyParentUpdateRequest,
    TaxonomyConceptCreateRequest,
    TaxonomyPurgeRootsRequest,
    TaxonomyConceptUpdateRequest,
    TaxonomyBootstrapImportRequest,
    TaxonomyTagAssociationRequest,
    TaxonomyTagDetailsUpdateRequest,
    TaxonomyTagMaintUpdateRequest,
    TaxonomyTagMaintBulkDeleteRequest,
    TaxonomyTagMaintPurgeRequest,
    GenerationTemplateImportRequest,
    GenerationTemplateUpdateRequest,
    GenerationTemplateResolveRequest,
    A1111BridgeAnalyzeRequest,
    A1111BridgeSaveRequest,
    ComfyGenerateCompareRequest,
    ParityCandidateAuditRequest,
    CivitaiSearchRequest,
    SyncLabAnalyzeRequest,
    SyncSessionCreateRequest,
    SyncSessionStepUpdateRequest,
    VariantGroupCreateRequest,
    VariantGroupUpdateRequest,
    VariantGroupAddMembersRequest,
)

IMAGE_LIBRARY_PATH = str(getattr(app_config, "IMAGE_LIBRARY_PATH", "image_library"))
IMAGE_RESOURCES_PATH = str(
    getattr(app_config, "IMAGE_RESOURCES_PATH", "image_resources")
)
CURRENT_SCHEMA_VERSION = str(getattr(app_config, "CURRENT_SCHEMA_VERSION", "1.0"))
DATABASE_URL = str(getattr(app_config, "DATABASE_URL", "sqlite:///image_db.sqlite"))
ALLOW_SCHEMA_RESET = bool(getattr(app_config, "ALLOW_SCHEMA_RESET", False))
ATELIER_COMFYUI_BASE_URL = str(
    getattr(app_config, "ATELIER_COMFYUI_BASE_URL", "")
).strip()
ATELIER_COMFY_MATCH_THRESHOLD = float(
    getattr(app_config, "ATELIER_COMFY_MATCH_THRESHOLD", 0.95)
)


def _reconstruct_source_url(url: Optional[str]) -> Optional[str]:
    """Reconstruct a full CivitAI URL from a possibly-relative source_url.

    During the transition to relative-path storage, DB rows may contain
    either full URLs (legacy) or relative paths (new).  This helper
    normalises both to full URLs for API responses.
    """
    return _build_civitai_url(
        url, getattr(app_config, "CIVITAI_WEB_BASE_URL", "https://civitai.red")
    )

try:
    import imagehash  # pyright: ignore[reportMissingImports]
except Exception:  # pragma: no cover - runtime dependency guard
    imagehash = None

try:
    import blurhash  # pyright: ignore[reportMissingImports]
except Exception:  # pragma: no cover - runtime dependency guard
    blurhash = None


_CIVITAI_COLLECTION_PATH_RE = re.compile(
    r"^/collections/(?P<collection_id>\d+)(?:/.*)?$"
)
_CIVITAI_POST_PATH_RE = re.compile(
    r"^/posts/(?P<post_id>\d+)(?:/.*)?$"
)
_CIVITAI_IMPORT_NETWORK_CONCURRENCY = 3
_CIVITAI_SOURCE_VARIANT_DIRNAME = "civitai_source_variants"
_CIVITAI_COLLECTION_HEAD_PROBE_SIZE = 50
_CIVITAI_COLLECTION_FULL_VERIFY_MAX_AGE_SECONDS = 24 * 60 * 60
_VIDEO_FILE_SUFFIXES = {".mp4", ".webm", ".mov", ".mkv"}
# A1111 regex / constant singletons – canonical definitions live in a1111_parser_service.
# Local aliases keep call-sites unchanged during incremental migration.
_A1111_NEGATIVE_PROMPT_RE = _a1111_svc._A1111_NEGATIVE_PROMPT_RE
_A1111_KV_SPLIT_RE = _a1111_svc._A1111_KV_SPLIT_RE
_A1111_SIZE_RE = _a1111_svc._A1111_SIZE_RE
_A1111_LORA_TAG_RE = _a1111_svc._A1111_LORA_TAG_RE
_A1111_RP_DIRECTIVE_RE = _a1111_svc.A1111_RP_DIRECTIVE_RE
_COMFY_RP_EMULATION_SUPPORTED = _a1111_svc.COMFY_RP_EMULATION_SUPPORTED
_A1111_SAMPLER_TO_COMFY_ALIASES = _a1111_svc._A1111_SAMPLER_TO_COMFY_ALIASES


@dataclass
class _PreparedCivitaiImport:
    image_id: int
    image_url: str
    mime_type: Optional[str]
    declared_file_size: Optional[int]
    preview_image_url: Optional[str]
    original_filename: str
    artist_name: Optional[str]
    source_url: str
    temp_path: Path
    civitai_uuid: Optional[str] = None
    civitai_hash: Optional[str] = None
    raw_basic_info: Optional[dict[str, Any]] = None
    raw_generation_data: Optional[dict[str, Any]] = None
    raw_infinite: Optional[dict[str, Any]] = None
    api_response_paths: dict[str, str] = field(
        default_factory=dict
    )  # Pre-saved response file paths
    effective_image_url: Optional[str] = None
    mismatch_static_temp_path: Optional[Path] = None
    mismatch_source_url: Optional[str] = None
    mismatch_mime_type: Optional[str] = None
    mismatch_file_hash: Optional[str] = None
    author_id: Optional[int] = None
    author_deleted: bool = False
    author_original_name: Optional[str] = None
    civitai_post_id: Optional[int] = None
    civitai_post_title: Optional[str] = None
    civitai_post_index: Optional[int] = None
    raw_tag_records: Optional[list[dict[str, Any]]] = None


@dataclass
class _CivitaiDownloadResult:
    temp_path: Path
    selected_url: str
    selected_category: str
    selected_mime_type: Optional[str]
    mismatch_static_temp_path: Optional[Path] = None
    mismatch_source_url: Optional[str] = None
    mismatch_mime_type: Optional[str] = None
    mismatch_file_hash: Optional[str] = None


@dataclass(frozen=True)
class _RetryFailedItem:
    image_id: int
    civitai_collection_id: Optional[int] = None


@dataclass(frozen=True)
class _CivitaiCollectionProbe:
    image_ids: list[int]
    fingerprint: str
    has_more: bool
    # Pre-fetched first-page items for reuse by the import pipeline.
    # Avoids re-fetching page 1 when the probe already has it.
    first_page_items: Optional[list[dict[str, Any]]] = None
    first_page_cursor: Optional[str] = None


class _CivitaiImageUnavailableError(RuntimeError):
    def __init__(
        self,
        *,
        image_id: int,
        endpoint: str,
        reason: str,
        status_code: Optional[int] = None,
        source_url: Optional[str] = None,
        diagnostics: Optional[dict[str, Any]] = None,
    ):
        super().__init__(reason)
        self.image_id = image_id
        self.endpoint = endpoint
        self.reason = reason
        self.status_code = status_code
        self.source_url = source_url or _build_civitai_image_source_url(image_id)
        self.diagnostics = diagnostics or {}


task_manager = BackgroundTaskManager(max_workers=4)
image_query_service = ImageQueryService(image_library_path=IMAGE_LIBRARY_PATH)
taxonomy_service = TaxonomyService()
gallery_tag_service = GalleryTagService()
model_reference_service = ModelReferenceService()


def _make_gallery_query(db: Session) -> GalleryQuery:
    """Factory: build a GalleryQuery wired to this app's callables."""
    return GalleryQuery(
        db=db,
        query_service=image_query_service,
        image_library_path=IMAGE_LIBRARY_PATH,
        image_resources_path=IMAGE_RESOURCES_PATH,
        active_image_filter=_active_image_filter,
        apply_image_list_filters=_apply_image_list_filters,
        build_display_items_for_image=_build_display_items_for_image,
        merge_duplicate_grouped_items=_merge_duplicate_grouped_items,
        read_nsfw_ratings_for_image=_read_nsfw_ratings_for_image,
        get_video_poster_path=get_video_poster_path,
        get_video_thumbnail_path=get_video_thumbnail_path,
        image_data_from_db=ImageData.from_db_record,
    )


@dataclass
class _SearchCacheEntry:
    value: Any
    expires_at_monotonic: float
    version: int


_SEARCH_CACHE_TTL_SECONDS = max(
    1.0, float(os.getenv("ATELIER_SEARCH_CACHE_TTL_SECONDS", "30"))
)
_SEARCH_CACHE_MAX_ITEMS = max(
    16, int(os.getenv("ATELIER_SEARCH_CACHE_MAX_ITEMS", "512"))
)
_JSON_CACHE_SCHEMA_VERSION = int(os.getenv("ATELIER_JSON_CACHE_SCHEMA_VERSION", "2"))
_FILTER_OPTIONS_CACHE_TTL_SECONDS = max(
    1.0, float(os.getenv("ATELIER_FILTER_OPTIONS_CACHE_TTL_SECONDS", "120"))
)
_search_cache_lock = threading.RLock()
_search_cache_version = 0
_gallery_cache_version = 0  # Only bumped on image-affecting changes
_search_cache: dict[str, _SearchCacheEntry] = {}


def _normalize_cache_list(values: Optional[list[str]]) -> list[str]:
    normalized: set[str] = set()
    for value in values or []:
        text_value = str(value or "").strip().lower()
        if text_value:
            normalized.add(text_value)
    return sorted(normalized)


def _build_search_cache_key(kind: str, *, payload: dict[str, Any]) -> str:
    canonical_payload = {key: value for key, value in payload.items()}
    canonical_payload["kind"] = kind
    return json.dumps(
        canonical_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )


def _invalidate_search_cache(reason: str = "unspecified") -> None:
    global _search_cache_version, _gallery_cache_version
    with _search_cache_lock:
        _search_cache_version += 1
        _search_cache.clear()
    # Image-affecting changes also bump gallery version for ETag invalidation
    _image_affecting_reasons = {
        "db_commit",
        "image_update",
        "image_delete",
        "image_add",
        "collection_change",
        "scan_complete",
        "metadata_update",
        "variant_update",
        "nsfw_update",
        "tags_update",
    }
    if reason in _image_affecting_reasons:
        with _search_cache_lock:
            _gallery_cache_version += 1


def _search_cache_get(key: str) -> Optional[Any]:
    now = time.monotonic()
    with _search_cache_lock:
        entry = _search_cache.get(key)
        if entry is None:
            return None
        if entry.version != _search_cache_version or entry.expires_at_monotonic <= now:
            _search_cache.pop(key, None)
            return None
        return entry.value


def _search_cache_put(
    key: str, value: Any, *, ttl_seconds: Optional[float] = None
) -> None:
    now = time.monotonic()
    effective_ttl = (
        _SEARCH_CACHE_TTL_SECONDS
        if ttl_seconds is None
        else max(1.0, float(ttl_seconds))
    )
    with _search_cache_lock:
        if len(_search_cache) >= _SEARCH_CACHE_MAX_ITEMS:
            stale_keys = [
                cache_key
                for cache_key, entry in _search_cache.items()
                if entry.version != _search_cache_version
                or entry.expires_at_monotonic <= now
            ]
            for cache_key in stale_keys:
                _search_cache.pop(cache_key, None)
            if len(_search_cache) >= _SEARCH_CACHE_MAX_ITEMS:
                oldest_key = min(
                    _search_cache,
                    key=lambda cache_key: _search_cache[cache_key].expires_at_monotonic,
                )
                _search_cache.pop(oldest_key, None)

        _search_cache[key] = _SearchCacheEntry(
            value=value,
            expires_at_monotonic=now + effective_ttl,
            version=_search_cache_version,
        )


@event.listens_for(Session, "after_commit")
def _on_any_session_commit(_session: Session) -> None:
    _invalidate_search_cache("db_commit")


# ---------------------------------------------------------------------------
# Search suggestions (autocomplete) — shared implementation
# ---------------------------------------------------------------------------


def search_suggest_impl(
    body: "SuggestRequest",  # noqa: F821
    db: Session,
) -> dict:
    """Return autocomplete suggestions scoped to the filtered image set.

    Uses the constrained-IDs cache populated by ``POST /api/query``.
    On cache miss the filter is resolved fresh via ``GalleryQuery``.
    """
    from services.query_model import SuggestRequest  # noqa: PLC0415

    assert isinstance(body, SuggestRequest)

    needle = body.q.strip().lower()
    if not needle:
        return {"collections": [], "artists": [], "tags": []}

    limit = body.limit

    # ── Short-lived suggest-level cache ────────────────────────────────
    suggest_cache_key = _build_search_cache_key(
        "suggest",
        payload={
            "q": needle,
            "limit": limit,
            "search": (body.search or "").strip().lower(),
            "filter": body.filter.model_dump(),
        },
    )
    cached_suggest = _search_cache_get(suggest_cache_key)
    if isinstance(cached_suggest, dict):
        return cached_suggest

    like_pattern = f"%{needle}%"

    # ── Resolve constrained IDs (cache-aware) ──────────────────────────
    from services.query_model import filter_cache_key  # noqa: PLC0415
    from utils.cache import (  # noqa: PLC0415
        _build_search_cache_key as _util_build_key,
        _search_cache_get as _util_cache_get,
    )

    # Try the shared constrained-IDs cache first.
    fkey = filter_cache_key(body.filter, body.search)
    cids_cache_key = _util_build_key(
        "constrained_ids", payload={"filter_key": fkey},
    )
    cached_cids = _util_cache_get(cids_cache_key)
    constrained_ids: Optional[set[int]] = None
    if cached_cids is not None:
        if isinstance(cached_cids, frozenset):
            constrained_ids = set(cached_cids)
        elif cached_cids != "unfiltered":
            constrained_ids = cached_cids
        # "unfiltered" → constrained_ids stays None
    else:
        # Cache miss — resolve via GalleryQuery (which also caches).
        gq = _make_gallery_query(db)
        constrained_ids = gq._resolve_filter(body.filter, body.search)

    # ── Build the final image-ID constraint ────────────────────────────
    if constrained_ids is not None:
        if not constrained_ids:
            return {"collections": [], "artists": [], "tags": []}

    # ── Collections with counts ────────────────────────────────────────
    if constrained_ids is not None:
        collection_q = (
            db.query(
                CollectionModel.name,
                func.count(func.distinct(ImageModel.id)),
            )
            .join(
                ImageCollectionMembership,
                ImageCollectionMembership.collection_id == CollectionModel.id,
            )
            .join(ImageModel, ImageModel.id == ImageCollectionMembership.image_id)
            .filter(
                ImageModel.id.in_(constrained_ids),
                func.lower(CollectionModel.name).like(like_pattern),
            )
        )
    else:
        collection_q = (
            db.query(
                CollectionModel.name,
                func.count(func.distinct(ImageModel.id)),
            )
            .join(
                ImageCollectionMembership,
                ImageCollectionMembership.collection_id == CollectionModel.id,
            )
            .join(ImageModel, ImageModel.id == ImageCollectionMembership.image_id)
            .filter(
                _active_image_filter(),
                func.lower(CollectionModel.name).like(like_pattern),
            )
        )
    collection_rows = collection_q.group_by(CollectionModel.name).order_by(
        func.count(func.distinct(ImageModel.id)).desc(), CollectionModel.name,
    ).limit(limit).all()

    # ── Artists with counts ────────────────────────────────────────────
    if constrained_ids is not None:
        artist_q = (
            db.query(
                Artist.name,
                func.count(func.distinct(ImageModel.id)),
            )
            .join(ImageModel, ImageModel.artist_id == Artist.id)
            .filter(
                ImageModel.id.in_(constrained_ids),
                func.lower(Artist.name).like(like_pattern),
            )
        )
    else:
        artist_q = (
            db.query(
                Artist.name,
                func.count(func.distinct(ImageModel.id)),
            )
            .join(ImageModel, ImageModel.artist_id == Artist.id)
            .filter(
                _active_image_filter(),
                func.lower(Artist.name).like(like_pattern),
            )
        )
    artist_rows = artist_q.group_by(Artist.name).order_by(
        func.count(func.distinct(ImageModel.id)).desc(), Artist.name,
    ).limit(limit).all()

    # ── Tags with counts (UNION ALL across relational sources) ─────────
    # Two strategies depending on whether we have constrained IDs:
    #   • Constrained: use json_each() + JOIN (small ID set limits work)
    #   • Unconstrained: use correlated subqueries driven from tag tables
    #     so SQLite scans the small tag tables first (37 matches) then
    #     probes the observation index per tag, instead of scanning all
    #     445 K observations.  ~4–5× faster for the unconstrained path.
    if constrained_ids is not None:
        ids_json = json.dumps(sorted(constrained_ids))
        id_filter_sql = "image_id IN (SELECT value FROM json_each(:ids_json))"
        bind_params: dict[str, Any] = {"ids_json": ids_json, "pattern": like_pattern}

        union_sql = text(f"""
            SELECT tag_name, COUNT(DISTINCT image_id) AS cnt FROM (
                SELECT tags.name AS tag_name, it.image_id
                FROM tags
                JOIN image_tags it ON it.tag_id = tags.id
                WHERE {id_filter_sql}
                  AND LOWER(tags.name) LIKE :pattern
                UNION ALL
                SELECT concepts.canonical_name AS tag_name, ico.image_id
                FROM concepts
                JOIN image_concept_observations ico ON ico.concept_id = concepts.id
                WHERE {id_filter_sql}
                  AND LOWER(concepts.canonical_name) LIKE :pattern
                UNION ALL
                SELECT concept_aliases.normalized_alias AS tag_name, ico.image_id
                FROM concept_aliases
                JOIN concepts c ON c.id = concept_aliases.concept_id
                JOIN image_concept_observations ico ON ico.concept_id = c.id
                WHERE {id_filter_sql}
                  AND LOWER(concept_aliases.normalized_alias) LIKE :pattern
                UNION ALL
                SELECT authority_terms.external_name AS tag_name, ico.image_id
                FROM authority_terms
                JOIN image_concept_observations ico
                  ON ico.authority_term_id = authority_terms.id
                WHERE {id_filter_sql}
                  AND LOWER(authority_terms.external_name) LIKE :pattern
            ) GROUP BY tag_name
        """)
        raw_rows = db.execute(union_sql, bind_params).fetchall()
    else:
        # Unconstrained path — correlated subqueries drive from tag tables.
        # Uses indexed normalized columns (all lowercase) so LIKE can use
        # the index and avoids the LOWER() call per row.
        bind_params: dict[str, Any] = {"pattern": like_pattern}

        union_sql = text("""
            SELECT tag_name, cnt FROM (
                SELECT tags.name AS tag_name,
                       (SELECT COUNT(DISTINCT it.image_id) FROM image_tags it WHERE it.tag_id = tags.id) AS cnt
                FROM tags
                WHERE LOWER(tags.name) LIKE :pattern
                UNION ALL
                SELECT concepts.canonical_name AS tag_name,
                       (SELECT COUNT(DISTINCT ico.image_id) FROM image_concept_observations ico WHERE ico.concept_id = concepts.id) AS cnt
                FROM concepts
                WHERE concepts.canonical_name LIKE :pattern
                UNION ALL
                SELECT concept_aliases.normalized_alias AS tag_name,
                       (SELECT COUNT(DISTINCT ico.image_id) FROM image_concept_observations ico WHERE ico.concept_id = concept_aliases.concept_id) AS cnt
                FROM concept_aliases
                WHERE concept_aliases.normalized_alias LIKE :pattern
                UNION ALL
                SELECT authority_terms.external_name AS tag_name,
                       (SELECT COUNT(DISTINCT ico.image_id) FROM image_concept_observations ico WHERE ico.authority_term_id = authority_terms.id) AS cnt
                FROM authority_terms
                WHERE authority_terms.normalized_external_name LIKE :pattern
            ) WHERE cnt > 0
        """)
        raw_rows = db.execute(union_sql, bind_params).fetchall()

    # Merge: keep max count per canonical (lowered) name.
    tag_counts: dict[str, dict[str, Any]] = {}
    for name, cnt in raw_rows:
        key = name.lower()
        if key not in tag_counts or cnt > tag_counts[key]["count"]:
            tag_counts[key] = {"name": name, "count": int(cnt)}

    sorted_tags = sorted(
        tag_counts.values(),
        key=lambda t: (-t["count"], t["name"].lower()),
    )[:limit]

    result = {
        "collections": [
            {"name": name, "count": int(cnt)} for name, cnt in collection_rows
        ],
        "artists": [{"name": name, "count": int(cnt)} for name, cnt in artist_rows],
        "tags": sorted_tags,
    }
    _search_cache_put(suggest_cache_key, result, ttl_seconds=15.0)
    return result


class _SuppressAccessPathFilter(logging.Filter):
    def __init__(self, blocked_method: str, blocked_prefixes: list[str]):
        super().__init__()
        self._blocked_method = blocked_method.upper()
        self._blocked_prefixes = tuple(
            prefix if prefix.endswith("/") else f"{prefix}/"
            for prefix in blocked_prefixes
        )
        self._exact_prefixes = tuple(prefix.rstrip("/") for prefix in blocked_prefixes)

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args if isinstance(record.args, tuple) else ()
        if len(args) >= 3:
            method = str(args[1] or "").upper()
            path = str(args[2] or "")
            # Strip query string so /api/images/state?t=123 matches /api/images/state
            path_without_query = path.split("?", 1)[0]
            if method == self._blocked_method:
                if path_without_query in self._exact_prefixes:
                    return False
                if any(path_without_query.startswith(prefix) for prefix in self._blocked_prefixes):
                    return False
        return True


def _configure_uvicorn_access_logging(*, suppress_status_get_logs: bool) -> None:
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.filters = [
        existing_filter
        for existing_filter in access_logger.filters
        if not isinstance(existing_filter, _SuppressAccessPathFilter)
    ]
    if suppress_status_get_logs:
        access_logger.addFilter(_SuppressAccessPathFilter("GET", ["/api/tasks"]))
        access_logger.addFilter(_SuppressAccessPathFilter("GET", ["/api/images/state"]))
        access_logger.addFilter(_SuppressAccessPathFilter("GET", ["/api/civitai/auth/rate-limit-status"]))


def _build_media_cache_headers(path: Path) -> dict[str, str]:
    stat_info = path.stat()
    etag = f'W/"{int(stat_info.st_mtime_ns):x}-{int(stat_info.st_size):x}"'
    last_modified = datetime.fromtimestamp(stat_info.st_mtime, tz=timezone.utc)
    return {
        "ETag": etag,
        "Last-Modified": last_modified.strftime("%a, %d %b %Y %H:%M:%S GMT"),
        "Cache-Control": "public, max-age=300",
    }


def _if_none_match_contains(if_none_match_header: str, etag: str) -> bool:
    candidate = str(if_none_match_header or "").strip()
    if not candidate:
        return False
    if candidate == "*":
        return True

    etag_plain = etag.replace("W/", "", 1)
    parts = [part.strip() for part in candidate.split(",") if part.strip()]
    for part in parts:
        if part == etag or part == etag_plain:
            return True
        if part.replace("W/", "", 1) == etag_plain:
            return True
    return False


def _should_return_not_modified(
    request: Request, path: Path, headers: dict[str, str]
) -> bool:
    if_none_match = request.headers.get("if-none-match")
    if if_none_match and _if_none_match_contains(
        if_none_match, headers.get("ETag", "")
    ):
        return True

    if_modified_since = request.headers.get("if-modified-since")
    if not if_modified_since:
        return False

    try:
        modified_since_dt = parsedate_to_datetime(if_modified_since)
    except (TypeError, ValueError):
        return False
    if modified_since_dt.tzinfo is None:
        modified_since_dt = modified_since_dt.replace(tzinfo=timezone.utc)

    file_modified_dt = datetime.fromtimestamp(
        path.stat().st_mtime, tz=timezone.utc
    ).replace(microsecond=0)
    return file_modified_dt <= modified_since_dt.astimezone(timezone.utc)


def _current_search_cache_version() -> int:
    with _search_cache_lock:
        return int(_search_cache_version)


def _current_gallery_cache_version() -> int:
    with _search_cache_lock:
        return int(_gallery_cache_version)


def _build_json_cache_headers(
    cache_key: str, *, max_age_seconds: int = 15, gallery: bool = False
) -> dict[str, str]:
    version = (
        _current_gallery_cache_version() if gallery else _current_search_cache_version()
    )
    digest = hashlib.sha1(
        f"{cache_key}|v={version}|schema={_JSON_CACHE_SCHEMA_VERSION}".encode("utf-8")
    ).hexdigest()
    etag = f'W/"{digest}"'
    return {
        "ETag": etag,
        "Cache-Control": f"public, max-age={max(0, int(max_age_seconds))}, must-revalidate",
    }


def _should_return_json_not_modified(request: Request, headers: dict[str, str]) -> bool:
    if_none_match = request.headers.get("if-none-match")
    if not if_none_match:
        return False
    return _if_none_match_contains(if_none_match, headers.get("ETag", ""))


def _normalize_query_values(values: Optional[list[str]]) -> list[str]:
    return image_query_service.normalize_query_values(values)


def _read_generation_software_for_image(image: ImageModel) -> Optional[str]:
    return image_query_service.read_generation_software_for_image(image)


def _filter_image_ids_by_generation_software(
    images_query,
    generation_softwares: Optional[list[str]],
) -> Optional[list[int]]:
    return image_query_service.filter_image_ids_by_generation_software(
        images_query,
        generation_softwares,
    )


def _read_nsfw_ratings_for_image(image: ImageModel) -> list[str]:
    return image_query_service.read_nsfw_ratings_for_image(image)


def _filter_image_ids_by_nsfw_ratings(
    images_query,
    nsfw_ratings: Optional[list[str]],
) -> Optional[list[int]]:
    return image_query_service.filter_image_ids_by_nsfw_ratings(
        images_query,
        nsfw_ratings,
    )


def _filter_image_ids_by_nsfw_safety_classes(
    images_query,
    nsfw_safety_classes: Optional[list[str]],
) -> Optional[list[int]]:
    return image_query_service.filter_image_ids_by_nsfw_safety_classes(
        images_query,
        nsfw_safety_classes,
    )


def _filter_image_ids_by_a1111_features(
    images_query,
    *,
    a1111_hires: Optional[list[str]] = None,
    a1111_regional_prompter: Optional[list[str]] = None,
    a1111_adetailer: Optional[list[str]] = None,
) -> Optional[list[int]]:
    return image_query_service.filter_image_ids_by_a1111_features(
        images_query,
        a1111_hires=a1111_hires,
        a1111_regional_prompter=a1111_regional_prompter,
        a1111_adetailer=a1111_adetailer,
    )


def _filter_image_ids_by_tag_names(
    images_query,
    *,
    include_tags: Optional[list[str]] = None,
    exclude_tags: Optional[list[str]] = None,
) -> Optional[list[int]]:
    return image_query_service.filter_image_ids_by_tag_names(
        images_query,
        include_tags=include_tags,
        exclude_tags=exclude_tags,
    )


# ---------------------------------------------------------------------------
# Missing-data filter
# ---------------------------------------------------------------------------
# Maps normalised condition labels (what the frontend sends) to a list of
# SQLAlchemy binary expressions that, when ALL true, mean the image is
# *missing* the requested data.  Each entry returns a list of expressions so
# we can express compound checks (e.g. "no prompt" needs both exif and
# civitai prompt fields to be empty).
_MISSING_DATA_CONDITION_MAP: dict[str, list] = {}


def _build_missing_data_condition(key: str) -> list:
    """Return SQLAlchemy filter expressions for a *missing-data* condition.

    The condition ``key`` uses the normalised ``"no <field>"`` form that the
    frontend sends.  Each expression should evaluate to ``True`` when the
    corresponding piece of data is absent.
    """
    Im = ImageModel

    if key == "no artist":
        return [
            sa.or_(
                Im.artist_id.is_(None),
                Im.artist_id == 0,
            )
        ]
    if key == "no source url":
        return [
            sa.or_(
                Im.source_url.is_(None),
                Im.source_url == "",
            )
        ]
    if key == "no generation info":
        return [
            sa.or_(
                Im.generation_software.is_(None),
                Im.generation_software == "",
            )
        ]
    if key == "no prompt":
        return [
            Im.has_generation_prompt == False,  # noqa: E712
        ]
    if key == "no a1111 metadata":
        return [
            Im.has_a1111_metadata == False,  # noqa: E712
        ]
    if key == "no a1111 hires upscale":
        return [
            Im.a1111_hires == False,  # noqa: E712
        ]
    if key == "no a1111 regional prompter":
        return [
            Im.a1111_regional_prompter == False,  # noqa: E712
        ]
    if key == "no a1111 adetailer":
        return [
            Im.a1111_adetailer == False,  # noqa: E712
        ]
    if key == "no comfyui metadata":
        return [
            Im.has_comfyui_metadata == False,  # noqa: E712
        ]
    if key == "no nsfw rating":
        return [
            sa.and_(
                Im.user_nsfw_rating.is_(None),
                Im.civitai_nsfw_level.is_(None),
            )
        ]
    if key == "no safety class":
        return [
            Im.user_nsfw_safety_class.is_(None),
        ]
    if key == "no exif data":
        return [
            sa.or_(
                Im.exif_data.is_(None),
                Im.exif_data == "{}",
                Im.exif_data == "{}\n",
            )
        ]
    if key == "no civitai meta":
        # Only applies when source_site is civitai; we encode that as
        # "(source_site != civitai) OR json_metadata is empty/{}"
        return [
            sa.or_(
                sa.func.lower(Im.source_site) != "civitai",
                Im.json_metadata.is_(None),
                Im.json_metadata == "{}",
                Im.json_metadata == "{}\n",
            )
        ]
    if key == "no tags":
        # No user-tag observations exist for this image (user authority only).
        return [
            ~Im.id.in_(
                sa.select(ImageConceptObservation.image_id).where(
                    sa.and_(
                        ImageConceptObservation.image_id == Im.id,
                        ImageConceptObservation.authority_id == (
                            sa.select(TagAuthority.id).where(
                                TagAuthority.name == "user"
                            )
                        ),
                    )
                )
            )
        ]
    # Unknown condition – no filter.
    return []


def _normalize_missing_data_key(raw: str) -> str:
    """Normalise a missing-data label to the canonical ``"no <field>"`` form."""
    stripped = (raw or "").strip().lower()
    if not stripped:
        return ""
    if not stripped.startswith("no "):
        stripped = f"no {stripped}"
    return stripped


def _filter_image_ids_by_missing_data(
    images_query,
    missing_data: Optional[list[str]],
) -> Optional[list[int]]:
    """Return image IDs that match ALL missing-data conditions, or ``None``
    when no conditions are specified (i.e. skip the filter entirely)."""
    if not missing_data:
        return None

    conditions = []
    for raw_entry in missing_data:
        key = _normalize_missing_data_key(raw_entry)
        if not key:
            continue
        exprs = _build_missing_data_condition(key)
        for expr in exprs:
            conditions.append(expr)

    if not conditions:
        return None

    # AND all conditions together
    combined = sa.and_(*conditions) if len(conditions) > 1 else conditions[0]
    rows = images_query.with_entities(ImageModel.id).filter(combined).all()
    return [row[0] for row in rows]


# ---------------------------------------------------------------------------
# Missing-source (tag source) filter
# ---------------------------------------------------------------------------
# The frontend source names and how they map to backend data:
#   civitai  → image_tags (imported from CivitAI) + civitai_data tags
#   danbooru → json_metadata.danbooru_tags / exif_data.danbooru_tags
#   prompt   → json_metadata.prompt_tags   / exif_data.prompt
#   user     → user_tags column


def _build_missing_source_condition(source: str) -> list:
    """Return SQLAlchemy filter expressions for a *missing-source* condition.

    An image is considered to be *missing* tags from *source* when it has
    zero tags from that source.
    """
    Im = ImageModel
    source_lower = (source or "").strip().lower()

    if source_lower == "civitai":
        # Civitai tags come via image_concept_observations linked to
        # authority_terms under the 'civitai' authority.
        return [
            ~Im.id.in_(
                sa.select(ImageConceptObservation.image_id).where(
                    sa.and_(
                        ImageConceptObservation.image_id == Im.id,
                        ImageConceptObservation.authority_id == (
                            sa.select(TagAuthority.id).where(
                                TagAuthority.name == "civitai"
                            )
                        ),
                    )
                )
            )
        ]
    if source_lower == "danbooru":
        # Danbooru tags are embedded in json_metadata or exif_data JSON.
        # We do a LIKE check for "danbooru" key presence in either column.
        return [
            sa.and_(
                sa.not_(Im.json_metadata.cast(sa.String).like("%danbooru%")),
                sa.not_(Im.exif_data.cast(sa.String).like("%danbooru%")),
            )
        ]
    if source_lower == "prompt":
        # Prompt tags in exif_data or json_metadata (prompt_tags / prompt keys).
        # has_generation_prompt already captures this.
        return [
            Im.has_generation_prompt == False,  # noqa: E712
        ]
    if source_lower == "user":
        # User tags are stored as observations under the 'user' authority.
        return [
            ~Im.id.in_(
                sa.select(ImageConceptObservation.image_id).where(
                    sa.and_(
                        ImageConceptObservation.image_id == Im.id,
                        ImageConceptObservation.authority_id == (
                            sa.select(TagAuthority.id).where(
                                TagAuthority.name == "user"
                            )
                        ),
                    )
                )
            )
        ]
    return []


def _filter_image_ids_by_missing_source(
    images_query,
    missing_source: Optional[list[str]],
) -> Optional[list[int]]:
    """Return image IDs that have zero tags from ALL specified sources.

    Returns ``None`` when no sources are specified (skip the filter).
    """
    if not missing_source:
        return None

    conditions = []
    for source in missing_source:
        exprs = _build_missing_source_condition(source)
        for expr in exprs:
            conditions.append(expr)

    if not conditions:
        return None

    combined = sa.and_(*conditions) if len(conditions) > 1 else conditions[0]
    rows = images_query.with_entities(ImageModel.id).filter(combined).all()
    return [row[0] for row in rows]


def _apply_image_list_filters(
    images_query,
    *,
    search: Optional[str] = None,
    source_sites: Optional[list[str]] = None,
    mimetypes: Optional[list[str]] = None,
    artist_names: Optional[list[str]] = None,
    collection_names: Optional[list[str]] = None,
    exclude_artist_names: Optional[list[str]] = None,
    exclude_collection_names: Optional[list[str]] = None,
    nsfw_ratings: Optional[list[str]] = None,
):
    return image_query_service.apply_image_list_filters(
        images_query,
        search=search,
        source_sites=source_sites,
        mimetypes=mimetypes,
        artist_names=artist_names,
        collection_names=collection_names,
        exclude_artist_names=exclude_artist_names,
        exclude_collection_names=exclude_collection_names,
    )


def _read_env_flag(name: str, default: bool = False) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    normalized = str(raw_value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the AtelierAI FastAPI server.")
    parser.add_argument("--host", default="0.0.0.0", help="Host interface to bind.")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind.")
    parser.add_argument(
        "--reload", action="store_true", help="Enable Uvicorn autoreload."
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["critical", "error", "warning", "info", "debug", "trace"],
        help="Uvicorn log level.",
    )
    parser.add_argument(
        "--no-access-log",
        action="store_true",
        default=_read_env_flag("ATELIER_DISABLE_ACCESS_LOG", False),
        help="Disable all HTTP access logs.",
    )
    parser.add_argument(
        "--suppress-status-get-logs",
        action="store_true",
        default=_read_env_flag("ATELIER_SUPPRESS_STATUS_GET_LOGS", False),
        help="Suppress noisy GET polling access logs such as /tasks while keeping other access logs enabled.",
    )
    return parser


def _main() -> None:
    parser = _build_cli_parser()
    args = parser.parse_args()
    _configure_uvicorn_access_logging(
        suppress_status_get_logs=args.suppress_status_get_logs
    )
    uvicorn.run(
        "backend.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
        access_log=not args.no_access_log,
    )


def _active_image_filter():
    return (ImageModel.image_status.is_(None)) | (ImageModel.image_status == "active")


def _sync_user_tag_observations(
    db: Session,
    *,
    image_id: int,
    user_tags: list[str] | None,
    user_negative_tags: list[str] | None,
    touched_fields: set,
) -> None:
    """Upsert authority_terms + image_concept_observations for user tags.

    Called from the PATCH /images/{file_hash} endpoint whenever user_tags or
    user_negative_tags are part of the update payload.  Ensures the relational
    observation rows stay in sync with the JSON column values so that tag
    filtering and counting work without json_each().
    """
    from models import (
        AuthorityTerm,
        ImageConceptObservation,
        ObservationCertainty,
        ObservationSource,
        TagAuthority,
    )

    # Only run when at least one of the tag fields was touched.
    if (
        ImageModel.user_tags not in touched_fields
        and ImageModel.user_negative_tags not in touched_fields
    ):
        return

    user_authority = (
        db.query(TagAuthority).filter(TagAuthority.name == "user").first()
    )
    if user_authority is None:
        return
    user_authority_id = user_authority.id

    now = datetime.now()

    # Build desired state: {(normalized_name, is_present)}
    desired: dict[tuple[str, bool], str] = {}  # (normalized, is_present) → display_name
    if isinstance(user_tags, list):
        for tag in user_tags:
            name = str(tag or "").strip()
            if name:
                desired[(name.lower(), True)] = name
    if isinstance(user_negative_tags, list):
        for tag in user_negative_tags:
            name = str(tag or "").strip()
            if name:
                desired[(name.lower(), False)] = name

    # Collect existing authority_terms for the user authority.
    existing_terms: dict[str, int] = {}
    for term in (
        db.query(AuthorityTerm.id, AuthorityTerm.normalized_external_name)
        .filter(AuthorityTerm.authority_id == user_authority_id)
        .all()
    ):
        existing_terms[term.normalized_external_name] = term.id

    # Collect existing observations for this image under user authority.
    existing_obs: dict[tuple[int, bool], int] = {}  # (term_id, is_present) → obs_id
    for row in db.query(
        ImageConceptObservation.id,
        ImageConceptObservation.authority_term_id,
        ImageConceptObservation.is_present,
    ).filter(
        ImageConceptObservation.image_id == image_id,
        ImageConceptObservation.authority_id == user_authority_id,
        ImageConceptObservation.authority_term_id.isnot(None),
    ).all():
        existing_obs[(row.authority_term_id, row.is_present)] = row.id

    # Determine which desired term_ids we need.
    desired_term_ids: set[int] = set()
    for (normalized, _is_present), _display_name in desired.items():
        term_id = existing_terms.get(normalized)
        if term_id is None:
            term = AuthorityTerm(
                authority_id=user_authority_id,
                external_tag_id=None,
                external_name=_display_name,
                normalized_external_name=normalized,
                created_at=now,
                updated_at=now,
            )
            db.add(term)
            db.flush()
            term_id = term.id
            existing_terms[normalized] = term_id
        desired_term_ids.add(term_id)

    # Create observations for desired (term_id, is_present) pairs that don't exist.
    for (normalized, is_present), _display_name in desired.items():
        term_id = existing_terms[normalized]
        if (term_id, is_present) not in existing_obs:
            obs = ImageConceptObservation(
                image_id=image_id,
                concept_id=None,
                authority_id=user_authority_id,
                authority_term_id=term_id,
                source_type=ObservationSource.IMPORT,
                certainty_label=ObservationCertainty.LIKELY,
                is_present=is_present,
                is_curated=False,
                confidence=None,
                created_at=now,
                updated_at=now,
            )
            db.add(obs)

    # Remove stale observations: observations whose (term_id, is_present) is
    # no longer in the desired set.
    stale_obs_ids: set[int] = set()
    for (term_id, is_present), obs_id in existing_obs.items():
        normalized = None
        for (n, ip), _ in desired.items():
            if existing_terms.get(n) == term_id and ip == is_present:
                normalized = n
                break
        if (term_id, is_present) not in {
            (existing_terms.get(n), ip)
            for (n, ip) in desired
        }:
            stale_obs_ids.add(obs_id)

    if stale_obs_ids:
        db.query(ImageConceptObservation).filter(
            ImageConceptObservation.id.in_(stale_obs_ids)
        ).delete(synchronize_session=False)


def _commit_with_lock_retry(db: Session, context: str = "database write") -> None:
    """Commit with short retries for transient SQLite lock contention."""
    max_attempts = 6
    for attempt in range(1, max_attempts + 1):
        try:
            db.commit()
            return
        except OperationalError as e:
            locked_error = (
                "database is locked" in str(e).lower()
                or "sqlite_busy" in str(e).lower()
            )
            if not locked_error or attempt >= max_attempts:
                db.rollback()
                raise HTTPException(
                    status_code=503,
                    detail=f"{context} failed due to database lock: {e}",
                )
            time.sleep(0.05 * attempt)
        except Exception:
            db.rollback()
            raise


# ── Migration functions extracted to services/db_migrations.py ──────────────




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
    base_domain = getattr(app_config, "CIVITAI_BASE_DOMAIN", "civitai.red")
    valid_hosts = {"civitai.com", "www.civitai.com", base_domain, f"www.{base_domain}"}
    if hostname not in valid_hosts:
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


def _parse_civitai_post_id(value: str) -> int:
    cleaned = (value or "").strip()
    if cleaned.isdigit():
        return int(cleaned)

    parsed = urlparse(cleaned)
    hostname = (parsed.hostname or "").lower()
    base_domain = getattr(app_config, "CIVITAI_BASE_DOMAIN", "civitai.red")
    valid_hosts = {"civitai.com", "www.civitai.com", base_domain, f"www.{base_domain}"}
    if hostname not in valid_hosts:
        raise HTTPException(
            status_code=400,
            detail="Invalid CivitAI post URL host.",
        )

    match = _CIVITAI_POST_PATH_RE.match(parsed.path or "")
    if not match:
        raise HTTPException(
            status_code=400,
            detail="Invalid CivitAI post URL path.",
        )

    try:
        return int(match.group("post_id"))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=400,
            detail="Could not parse CivitAI post ID.",
        )


def _detect_civitai_url_type(value: str) -> tuple[str, int]:
    """Detect whether a CivitAI URL/ID is an image, collection, or post and return (type, id).

    Returns: tuple of ("image", "collection", or "post", numeric_id)
    Raises HTTPException if the URL/ID doesn't match any known pattern.
    """
    cleaned = (value or "").strip()

    # Try numeric ID first - if it's just a number, we can't auto-detect, so error
    if cleaned.isdigit():
        raise HTTPException(
            status_code=400,
            detail="Ambiguous numeric ID. Please provide a full CivitAI URL so we can detect if it's an image, collection, or post.",
        )

    # Try to extract image ID
    try:
        image_id = extract_civitai_image_id(cleaned)
        if image_id is not None:
            return ("image", image_id)
    except Exception:
        pass

    # Try to extract collection or post ID from URL
    try:
        parsed = urlparse(cleaned)
        hostname = (parsed.hostname or "").lower()
        base_domain = getattr(app_config, "CIVITAI_BASE_DOMAIN", "civitai.red")
        valid_hosts = {
            "civitai.com",
            "www.civitai.com",
            base_domain,
            f"www.{base_domain}",
        }
        if hostname in valid_hosts:
            path = parsed.path or ""
            match = _CIVITAI_COLLECTION_PATH_RE.match(path)
            if match:
                collection_id = int(match.group("collection_id"))
                return ("collection", collection_id)
            match = _CIVITAI_POST_PATH_RE.match(path)
            if match:
                post_id = int(match.group("post_id"))
                return ("post", post_id)
    except Exception:
        pass

    # If neither pattern matched
    raise HTTPException(
        status_code=400,
        detail="Invalid CivitAI URL. Please provide a valid CivitAI image, collection, or post URL.",
    )


def _normalize_taxonomy_text(value: str) -> str:
    return taxonomy_service.normalize_text(value)


def _normalize_gallery_tag_text(value: str) -> str:
    return gallery_tag_service.normalize_text(value)


def _add_gallery_tag_name(bucket: set[str], value: Any) -> None:
    gallery_tag_service.add_tag_name(bucket, value)


def _add_gallery_tag_collection(bucket: set[str], value: Any) -> None:
    gallery_tag_service.add_tag_collection(bucket, value)


def _extract_image_scope_tag_names(payload: dict[str, Any]) -> dict[str, set[str]]:
    return gallery_tag_service.extract_image_scope_tag_names(
        payload,
        normalize_taxonomy_text=_normalize_taxonomy_text,
    )


def _load_image_sidecar_payload(image: ImageModel) -> dict[str, Any]:
    return gallery_tag_service.load_image_sidecar_payload(
        image_library_path=IMAGE_LIBRARY_PATH,
        file_path=str(image.file_path),
    )


def _gallery_tag_names_by_source(db: Session) -> dict[str, list[str]]:
    """DEPRECATED: Use _gallery_tag_names_by_source_from_observations instead.

    Reads tag names from json_metadata JSON columns. Replaced by observation-based
    counting that queries authority_terms + image_concept_observations tables.
    Kept for reference only; no active callers.
    """
    by_source: dict[str, set[str]] = {
        "civitai": set(),
        "danbooru": set(),
        "prompt": set(),
        "user": set(),
    }

    images = db.query(ImageModel).filter(_active_image_filter()).all()
    for image in images:
        merged_payload = {
            **ImageData.from_db_record(image).to_dict(),
            **_load_image_sidecar_payload(image),
        }
        extracted = _extract_image_scope_tag_names(merged_payload)
        for source, names in extracted.items():
            by_source[source].update(names)

    return {source: sorted(names) for source, names in by_source.items()}


def _gallery_tag_names_by_source_db_only(db: Session) -> dict[str, list[str]]:
    """DEPRECATED: Use _gallery_tag_names_by_source_from_observations instead.

    Reads tag names from json_metadata JSON columns. Replaced by observation-based
    counting that queries authority_terms + image_concept_observations tables.
    Kept for reference only; no active callers.
    """
    """Fast path for filter option hydration using DB metadata only.

    This intentionally avoids sidecar reads to keep startup filters responsive.
    Reads user_tags from the dedicated DB column so user-assigned tags are included.
    """
    by_source: dict[str, set[str]] = {
        "civitai": set(),
        "danbooru": set(),
        "prompt": set(),
        "user": set(),
    }

    rows = (
        db.query(ImageModel.json_metadata, ImageModel.exif_data, ImageModel.user_tags)
        .filter(_active_image_filter())
        .all()
    )
    for json_metadata, exif_data, user_tags_col in rows:
        payload: dict[str, Any] = {}
        if isinstance(json_metadata, dict):
            payload.update(json_metadata)
        if isinstance(exif_data, dict):
            payload["exif_data"] = exif_data
        if isinstance(user_tags_col, list):
            payload["user_tags"] = user_tags_col
        extracted = _extract_image_scope_tag_names(payload)
        for source, names in extracted.items():
            by_source[source].update(names)

    return {source: sorted(names) for source, names in by_source.items()}


def _gallery_tag_usage_counts_by_source(db: Session) -> dict[str, dict[str, int]]:
    """DEPRECATED: Use _gallery_tag_usage_counts_by_source_from_observations instead.

    Reads usage counts from json_metadata JSON columns + sidecar files.
    Replaced by observation-based counting.
    Kept for reference only; no active callers.
    """
    by_source: dict[str, dict[str, int]] = {
        "civitai": {},
        "danbooru": {},
        "prompt": {},
        "user": {},
    }

    images = db.query(ImageModel).filter(_active_image_filter()).all()
    for image in images:
        merged_payload = {
            **ImageData.from_db_record(image).to_dict(),
            **_load_image_sidecar_payload(image),
        }
        extracted = _extract_image_scope_tag_names(merged_payload)
        for source, names in extracted.items():
            bucket = by_source.setdefault(source, {})
            for name in names:
                normalized_name = _normalize_gallery_tag_text(name)
                if not normalized_name:
                    continue
                bucket[normalized_name] = int(bucket.get(normalized_name, 0)) + 1

    return {
        source: {name: counts[name] for name in sorted(counts)}
        for source, counts in by_source.items()
    }


def _gallery_tag_usage_counts_by_source_db_only(
    db: Session,
) -> dict[str, dict[str, int]]:
    """DEPRECATED: Use _gallery_tag_usage_counts_by_source_from_observations instead.

    Reads usage counts from json_metadata JSON columns. Replaced by observation-based
    counting that queries authority_terms + image_concept_observations tables.
    Kept for reference only; no active callers.
    """
    """Fast path for tag usage counts using DB metadata only, avoiding sidecar reads.

    This provides approximate counts from json_metadata fields for responsive tree loads.
    Reads user_tags from the dedicated DB column so user-assigned tags are counted.
    """
    by_source: dict[str, dict[str, int]] = {
        "civitai": {},
        "danbooru": {},
        "prompt": {},
        "user": {},
    }

    rows = (
        db.query(ImageModel.json_metadata, ImageModel.exif_data, ImageModel.user_tags)
        .filter(_active_image_filter())
        .all()
    )
    for json_metadata, exif_data, user_tags_col in rows:
        payload: dict[str, Any] = {}
        if isinstance(json_metadata, dict):
            payload.update(json_metadata)
        if isinstance(exif_data, dict):
            payload["exif_data"] = exif_data
        if isinstance(user_tags_col, list):
            payload["user_tags"] = user_tags_col
        extracted = _extract_image_scope_tag_names(payload)
        for source, names in extracted.items():
            bucket = by_source.setdefault(source, {})
            for name in names:
                normalized_name = _normalize_gallery_tag_text(name)
                if not normalized_name:
                    continue
                bucket[normalized_name] = int(bucket.get(normalized_name, 0)) + 1

    return {
        source: {name: counts[name] for name in sorted(counts)}
        for source, counts in by_source.items()
    }


def _gallery_tag_names_by_source_from_observations(db: Session) -> dict[str, list[str]]:
    """Gallery tag names sourced from DB observations instead of json_metadata.

    Queries authority_terms joined with image_concept_observations to produce
    the same ``{source: sorted([tag_name, ...])}`` shape as the legacy
    ``_gallery_tag_names_by_source_db_only`` — but entirely from relational
    tables with no JSON parsing.

    Note: user tags are kept in sync with observations via
    ``_sync_user_tag_observations``; any user_tags not yet back-filled into
    authority_terms will not appear here until that sync runs.
    """
    by_source: dict[str, set[str]] = {
        "civitai": set(),
        "danbooru": set(),
        "prompt": set(),
        "user": set(),
    }

    # --- Core path: authority_terms + observations ---
    obs_rows = (
        db.query(
            TagAuthority.name,
            AuthorityTerm.external_name,
        )
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
    """Gallery tag usage counts sourced from DB observations.

    Returns ``{source: {tag_name: count}}`` where *count* is the number of
    distinct active images observed with that tag from the given authority.

    Mirrors the shape of ``_gallery_tag_usage_counts_by_source_db_only`` but
    computes counts via SQL ``GROUP BY`` on pre-computed observation rows
    instead of Python-side JSON parsing.
    """
    by_source: dict[str, dict[str, int]] = {
        "civitai": {},
        "danbooru": {},
        "prompt": {},
        "user": {},
    }

    # --- Core path: authority_terms + observations ---
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


def _duplicate_key(value: str) -> str:
    return taxonomy_service.duplicate_key(value)


def _slugify_concept_name(value: str) -> str:
    return taxonomy_service.slugify_concept_name(value)


def _ensure_unique_concept_slug(db: Session, base_slug: str) -> str:
    return taxonomy_service.ensure_unique_concept_slug(db, base_slug)


def _get_or_create_authority(db: Session, authority_name: str) -> TagAuthority:
    return taxonomy_service.get_or_create_authority(db, authority_name)


def _get_or_create_concept(db: Session, canonical_name: str) -> Concept:
    return taxonomy_service.get_or_create_concept(db, canonical_name)


def _ensure_alias_for_concept(
    db: Session,
    concept_id: int,
    alias_text: str,
    alias_type: str = "synonym",
    authority_id: Optional[int] = None,
    external_tag_id: Optional[int] = None,
) -> bool:
    return taxonomy_service.ensure_alias_for_concept(
        db,
        concept_id,
        alias_text,
        alias_type,
        authority_id,
        external_tag_id,
    )


def _parse_bootstrap_terms(format_name: str, raw_text: str) -> list[dict]:
    return taxonomy_service.parse_bootstrap_terms(format_name, raw_text)


def _execute_taxonomy_bootstrap_import(
    db: Session,
    *,
    authority_name: str,
    rows: list[dict],
    dry_run: bool,
) -> dict:
    authority = _get_or_create_authority(db, authority_name)

    stats = {
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

                mapped_concept_name = str((row or {}).get("concept_name") or "").strip()
                concept_name = mapped_concept_name or normalized_name

                concept = (
                    db.query(Concept)
                    .filter(
                        Concept.canonical_name == _normalize_taxonomy_text(concept_name)
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
                    if getattr(term, "concept_id", None) != (concept_id or 0):
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


def _sha256_file(file_path: Path) -> str:
    digest = hashlib.sha256()
    with open(file_path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _detect_downloaded_media(file_path: Path) -> tuple[str, Optional[str]]:
    """Return (category, mime_type) inferred from file bytes and extension."""
    try:
        with open(file_path, "rb") as handle:
            header = handle.read(64)
    except OSError:
        return "unknown", None

    if header.startswith(b"\xff\xd8"):
        return "image", "image/jpeg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image", "image/png"
    if header.startswith(b"GIF8"):
        return "image", "image/gif"
    if len(header) >= 12 and header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image", "image/webp"
    if len(header) >= 12 and header[4:8] == b"ftyp":
        brand = header[8:12]
        if brand == b"qt  ":
            return "video", "video/quicktime"
        return "video", "video/mp4"
    if header.startswith(b"\x1a\x45\xdf\xa3"):
        return "video", "video/webm"

    suffix = file_path.suffix.lower()
    if suffix in {".mp4", ".m4v", ".mov", ".mkv", ".webm"}:
        return "video", None
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
        return "image", None
    return "unknown", None


def _build_civitai_video_candidate_urls(target: dict[str, Any]) -> list[str]:
    """Build ordered candidate URLs for declared CivitAI video downloads."""
    urls: list[str] = []

    primary_url = str(target.get("image_url") or "").strip()
    if primary_url:
        urls.append(primary_url)

    url_hash = target.get("civitai_url_hash")
    file_name = str(target.get("original_filename") or "").strip()
    mime_type = target.get("mime_type")

    original_url = _build_civitai_media_url(
        url_hash,
        file_name,
        mime_type,
        use_video_transcode=False,
    )
    if original_url:
        urls.append(original_url)

    civitai_uuid = str(
        target.get("civitai_uuid") or ""
    ).strip() or _extract_civitai_uuid_from_url_hash(url_hash)
    if civitai_uuid:
        cdn_alt = getattr(
            app_config, "CIVITAI_CDN_ALT_BASE_URL", "https://image-b2.civitai.com"
        )
        urls.append(f"{cdn_alt}/file/civitai-media-cache/{civitai_uuid}/original")

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in urls:
        normalized = candidate.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _build_civitai_image_candidate_urls(target: dict[str, Any]) -> list[str]:
    """Build ordered candidate URLs for declared CivitAI image downloads."""
    urls: list[str] = []

    primary_url = str(target.get("image_url") or "").strip()
    if primary_url:
        urls.append(primary_url)

    raw_image_url = str(target.get("raw_image_url") or "").strip()
    if raw_image_url:
        urls.append(raw_image_url)

    url_hash = target.get("civitai_url_hash")
    file_name = str(target.get("original_filename") or "").strip()
    mime_type = target.get("mime_type")

    original_url = _build_civitai_media_url(
        url_hash,
        file_name,
        mime_type,
        use_video_transcode=False,
    )
    if original_url:
        urls.append(original_url)

    civitai_uuid = str(
        target.get("civitai_uuid") or ""
    ).strip() or _extract_civitai_uuid_from_url_hash(url_hash)

    # Some CivitAI image records return a broken "original=true/..." route
    # (404 "File with such name does not exist") while transformed UUID-based
    # routes remain valid. Add width-based fallbacks for those cases.
    if civitai_uuid:
        cdn_base = getattr(
            app_config,
            "CIVITAI_CDN_BASE_URL",
            "https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA",
        )
        suffix = _guess_suffix(mime_type)
        uuid_filename = f"{civitai_uuid}{suffix}"

        raw_basic = target.get("raw_basic_info")
        raw_width = None
        raw_height = None
        if isinstance(raw_basic, dict):
            try:
                raw_width = int(raw_basic.get("width")) if raw_basic.get("width") else None
            except (TypeError, ValueError):
                raw_width = None
            try:
                raw_height = int(raw_basic.get("height")) if raw_basic.get("height") else None
            except (TypeError, ValueError):
                raw_height = None

        width_candidates: list[int] = [2048]
        if isinstance(raw_width, int) and raw_width > 0:
            width_candidates.append(raw_width)
        if isinstance(raw_height, int) and raw_height > 0:
            width_candidates.append(raw_height)
        width_candidates.extend([1600, 1536, 1200, 1024, 800, 768, 450])

        seen_widths: set[int] = set()
        for width in width_candidates:
            if not isinstance(width, int) or width <= 0 or width in seen_widths:
                continue
            seen_widths.add(width)
            urls.append(f"{cdn_base}/{civitai_uuid}/width={width}/{uuid_filename}")

    if civitai_uuid:
        cdn_alt = getattr(
            app_config, "CIVITAI_CDN_ALT_BASE_URL", "https://image-b2.civitai.com"
        )
        urls.append(f"{cdn_alt}/file/civitai-media-cache/{civitai_uuid}/original")

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in urls:
        normalized = candidate.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _download_civitai_image_with_validation(
    *,
    image_id: int,
    target: dict[str, Any],
) -> _CivitaiDownloadResult:
    declared_mime = _normalize_mime_type(target.get("mime_type"))
    declared_video = declared_mime.startswith("video/")
    declared_file_size = target.get("declared_file_size")

    candidate_urls = (
        _build_civitai_video_candidate_urls(target)
        if declared_video
        else _build_civitai_image_candidate_urls(target)
    )
    candidate_urls = [url for url in candidate_urls if url]
    if not candidate_urls:
        raise HTTPException(
            status_code=502,
            detail=f"CivitAI image {image_id} did not include a downloadable URL.",
        )

    mismatch_temp_path: Optional[Path] = None
    mismatch_source_url: Optional[str] = None
    mismatch_mime_type: Optional[str] = None
    mismatch_file_hash: Optional[str] = None
    last_download_error: Optional[CivitaiRequestError] = None

    for image_url in candidate_urls:
        try:
            temp_path = _download_civitai_image(
                image_url=image_url,
                image_id=image_id,
                mime_type=target.get("mime_type"),
                declared_file_size=declared_file_size,
            )
        except CivitaiRequestError as exc:
            last_download_error = exc
            # Some CivitAI assets keep page visibility but rotate/cull
            # one or more direct file URLs. Keep trying fallbacks on 404.
            if exc.status_code == 404:
                continue
            raise

        media_category, media_mime = _detect_downloaded_media(temp_path)

        if declared_video and media_category != "video":
            if media_category == "image" and mismatch_temp_path is None:
                mismatch_temp_path = temp_path
                mismatch_source_url = image_url
                mismatch_mime_type = media_mime
                mismatch_file_hash = _sha256_file(temp_path)
            else:
                _cleanup_temp_file(temp_path)
            continue

        return _CivitaiDownloadResult(
            temp_path=temp_path,
            selected_url=image_url,
            selected_category=media_category,
            selected_mime_type=media_mime,
            mismatch_static_temp_path=mismatch_temp_path,
            mismatch_source_url=mismatch_source_url,
            mismatch_mime_type=mismatch_mime_type,
            mismatch_file_hash=mismatch_file_hash,
        )

    if mismatch_temp_path is not None:
        _cleanup_temp_file(mismatch_temp_path)

    if last_download_error is not None:
        raise HTTPException(
            status_code=502,
            detail=(
                f"Could not download CivitAI image {image_id} from any candidate URL. "
                f"Last error: {last_download_error}"
            ),
        )

    raise HTTPException(
        status_code=502,
        detail=(
            f"CivitAI image {image_id} declares video media, but all candidate download URLs returned non-video content. "
            "Ingestion aborted to avoid storing a static image as the primary asset."
        ),
    )


def _download_civitai_image(
    image_url: str,
    image_id: int,
    mime_type: Optional[str],
    declared_file_size: Optional[int] = None,
) -> Path:
    suffix = _guess_suffix(mime_type)
    client = CivitaiAPI.get_instance().http_client
    normalized_mime = _normalize_mime_type(mime_type)

    expected_size_bytes: Optional[int] = None
    if not normalized_mime.startswith("video/") and declared_file_size is not None:
        try:
            parsed_size = int(declared_file_size)
        except (TypeError, ValueError):
            parsed_size = 0
        if parsed_size > 0:
            expected_size_bytes = parsed_size

    return client.download_to_temp(
        image_url,
        output_dir=IMAGE_LIBRARY_PATH,
        prefix=f"temp_civitai_{image_id}_",
        suffix=suffix,
        expected_size_bytes=expected_size_bytes,
    )


def _build_civitai_media_url(
    url_hash: Optional[str],
    safe_name: str,
    mime_type: Optional[str],
    *,
    use_video_transcode: bool,
) -> Optional[str]:
    clean_hash = str(url_hash or "").strip()
    if not clean_hash:
        return None
    transform_segment = "original=true"
    if use_video_transcode and str(mime_type or "").lower().startswith("video/"):
        transform_segment = "transcode=true,original=true"
    return (
        f"{getattr(app_config, 'CIVITAI_CDN_BASE_URL', 'https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA')}"
        f"/{clean_hash}/{transform_segment}/{safe_name}"
    )


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
        safe_name = sanitize_display_filename(candidate, fallback_ext=suffix)
        if not safe_name:
            continue
        return safe_name

    return f"civitai_{image_id}{suffix}"


def _extract_civitai_uuid_from_url_hash(url_hash: Optional[str]) -> Optional[str]:
    """Extract UUID from CivitAI CDN URL hash (typically: <uuid>/<params>/<filename>)."""
    if not url_hash:
        return None
    text_hash = str(url_hash).strip()
    if not text_hash:
        return None
    # CivitAI URL hash format: https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA/{uuid}/{params}/{filename}
    # We want just the uuid portion
    parts = text_hash.split("/")
    if parts:
        candidate = parts[0].strip()
        if candidate and len(candidate) > 8:  # UUID-like length
            return candidate
    return text_hash if len(text_hash) > 8 else None


def _normalize_mime_type(mime_type: Optional[str]) -> str:
    return str(mime_type or "").split(";", 1)[0].strip().lower()


def _url_looks_like_video(url: Optional[str]) -> bool:
    text = str(url or "").strip()
    if not text:
        return False
    try:
        suffix = Path(urlparse(text).path).suffix.lower()
    except Exception:
        return False
    return suffix in _VIDEO_FILE_SUFFIXES


def _get_civitai_source_variant_path(
    image_id: int, actual_path: Path, actual_mime_type: Optional[str]
) -> Path:
    variant_root = Path(IMAGE_RESOURCES_PATH) / _CIVITAI_SOURCE_VARIANT_DIRNAME
    variant_root.mkdir(parents=True, exist_ok=True)
    suffix = _guess_suffix(actual_mime_type) or actual_path.suffix.lower()
    if not suffix:
        suffix = ".bin"

    try:
        variant_hash = _sha256_file(actual_path)
    except Exception:
        variant_hash = ""

    base_name = variant_hash or str(image_id)
    return variant_root / f"{base_name}{suffix}"


def _preserve_civitai_source_variant(
    db: Session,
    *,
    prepared: _PreparedCivitaiImport,
    image_db_id: int,
) -> None:
    declared_mime_type = _normalize_mime_type(prepared.mime_type)
    # Some CivitAI payloads provide a video URL but an image MIME declaration.
    # Preserve static variants whenever the source appears video-like.
    declared_video_like = declared_mime_type.startswith(
        "video/"
    ) or _url_looks_like_video(prepared.image_url)
    if not declared_video_like:
        return

    image = db.query(ImageModel).filter(ImageModel.id == image_db_id).first()
    if image is None:
        return

    actual_mime_type = _normalize_mime_type(getattr(image, "mimetype", None))
    actual_path = Path(IMAGE_LIBRARY_PATH) / str(image.file_path)
    if not actual_path.exists():
        return

    variant_metadata: Optional[dict[str, Any]] = None
    variant_path: Optional[Path] = None

    if actual_mime_type.startswith("image/"):
        variant_path = _get_civitai_source_variant_path(
            prepared.image_id, actual_path, actual_mime_type
        )
        if (
            not variant_path.exists()
            or actual_path.stat().st_mtime_ns > variant_path.stat().st_mtime_ns
        ):
            shutil.copy2(actual_path, variant_path)
        variant_file_hash = _sha256_file(variant_path)
        variant_original_file_name = _build_civitai_original_filename(
            prepared.image_id,
            None,
            str(
                prepared.effective_image_url
                or prepared.image_url
                or prepared.source_url
                or ""
            ),
            actual_mime_type or prepared.mime_type,
        )
        if Path(variant_original_file_name).suffix.lower() in _VIDEO_FILE_SUFFIXES:
            variant_original_file_name = (
                f"{prepared.image_id}{_guess_suffix(actual_mime_type)}"
            )
        variant_metadata = {
            "image_id": prepared.image_id,
            "image_db_id": image_db_id,
            "declared_mimetype": prepared.mime_type,
            "actual_mimetype": image.mimetype,
            "declared_filename": prepared.original_filename,
            "original_variant_file_name": variant_original_file_name,
            "library_file_path": str(image.file_path),
            "library_file_hash": image.file_hash,
            "variant_file_path": str(
                variant_path.relative_to(Path(IMAGE_RESOURCES_PATH))
            ),
            "variant_file_hash": variant_file_hash,
            "image_url": prepared.image_url,
            "source_url": prepared.source_url,
            "declared_file_size": prepared.declared_file_size,
            "actual_file_size": actual_path.stat().st_size,
            "reason": (
                "civitai_declared_video_but_served_image"
                if declared_mime_type.startswith("video/")
                else "civitai_video_url_but_declared_non_video"
            ),
            "saved_at": datetime.utcnow().isoformat() + "Z",
        }

    elif actual_mime_type.startswith("video/") and prepared.preview_image_url:
        client = CivitaiAPI.get_instance().http_client
        response = client.request("GET", prepared.preview_image_url, stream=True)
        try:
            preview_mime_type = _normalize_mime_type(
                response.headers.get("Content-Type")
            )
            if preview_mime_type.startswith("image/"):
                preview_extension = _guess_suffix(preview_mime_type)
                variant_root = (
                    Path(IMAGE_RESOURCES_PATH) / _CIVITAI_SOURCE_VARIANT_DIRNAME
                )
                variant_root.mkdir(parents=True, exist_ok=True)
                temp_preview_path = (
                    variant_root
                    / f"temp_preview_{prepared.image_id}{preview_extension}"
                )
                with open(temp_preview_path, "wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 256):
                        if chunk:
                            handle.write(chunk)

                _, detected_preview_mime = _detect_downloaded_media(temp_preview_path)
                resolved_preview_mime = _normalize_mime_type(
                    detected_preview_mime or preview_mime_type
                )
                resolved_preview_extension = _guess_suffix(resolved_preview_mime)
                if (
                    resolved_preview_extension
                    and resolved_preview_extension != temp_preview_path.suffix.lower()
                ):
                    temp_with_resolved_extension = temp_preview_path.with_suffix(
                        resolved_preview_extension
                    )
                    temp_preview_path.rename(temp_with_resolved_extension)
                    temp_preview_path = temp_with_resolved_extension

                variant_file_hash = _sha256_file(temp_preview_path)
                variant_path = (
                    variant_root
                    / f"{variant_file_hash}{temp_preview_path.suffix.lower()}"
                )
                if variant_path.exists():
                    temp_preview_path.unlink(missing_ok=True)
                else:
                    temp_preview_path.rename(variant_path)

                variant_original_file_name = _build_civitai_original_filename(
                    prepared.image_id,
                    None,
                    str(
                        prepared.preview_image_url
                        or prepared.image_url
                        or prepared.source_url
                        or ""
                    ),
                    resolved_preview_mime,
                )
                if (
                    Path(variant_original_file_name).suffix.lower()
                    in _VIDEO_FILE_SUFFIXES
                ):
                    variant_original_file_name = (
                        f"{prepared.image_id}{_guess_suffix(resolved_preview_mime)}"
                    )
                variant_metadata = {
                    "image_id": prepared.image_id,
                    "image_db_id": image_db_id,
                    "declared_mimetype": prepared.mime_type,
                    "actual_mimetype": resolved_preview_mime,
                    "declared_filename": prepared.original_filename,
                    "original_variant_file_name": variant_original_file_name,
                    "library_file_path": str(image.file_path),
                    "library_file_hash": image.file_hash,
                    "variant_file_path": str(
                        variant_path.relative_to(Path(IMAGE_RESOURCES_PATH))
                    ),
                    "variant_file_hash": variant_file_hash,
                    "civitai_uuid": prepared.civitai_uuid,
                    "image_url": prepared.image_url,
                    "preview_image_url": prepared.preview_image_url,
                    "source_url": prepared.source_url,
                    "declared_file_size": prepared.declared_file_size,
                    "actual_file_size": variant_path.stat().st_size,
                    "preview_file_size": variant_path.stat().st_size,
                    "reason": "civitai_video_preview_variant",
                    "saved_at": datetime.utcnow().isoformat() + "Z",
                }
        finally:
            response.close()

    if variant_metadata is None or variant_path is None:
        return

    metadata_path = variant_path.with_suffix(f"{variant_path.suffix}.json")
    with open(metadata_path, "w", encoding="utf-8") as handle:
        json.dump(variant_metadata, handle, indent=2)

    merged_json = (
        dict(image.json_metadata) if isinstance(image.json_metadata, dict) else {}
    )
    merged_json["civitai_source_variant"] = variant_metadata
    image.json_metadata = merged_json

    processor = ImageProcessor(str(actual_path), db, IMAGE_LIBRARY_PATH)
    processor.save_json_metadata(
        actual_path,
        image,
        additional_data={"civitai_source_variant": variant_metadata},
    )


def _find_existing_by_file_hash(db: Session, file_hash: str) -> list[ImageModel]:
    """Return all ImageModel records matching a given file_hash (may be >1 after dropping unique)."""
    return db.query(ImageModel).filter(ImageModel.file_hash == file_hash).all()


def _resolve_duplicate_file_path(
    file_hash: str, civitai_image_id: int, suffix: str
) -> str:
    """Build a unique file_path for a duplicate asset in the image library.

    Format: ``{hash_trunc}_civitai_{id}{ext}`` — guaranteed unique per CivitAI ID.
    """
    hash_prefix = file_hash[:16]
    return f"{hash_prefix}_civitai_{civitai_image_id}{suffix}"


def _ingest_civitai_duplicate_asset(
    db: Session,
    *,
    prepared: _PreparedCivitaiImport,
    existing_records: list[ImageModel],
    attach_collection_id: Optional[int] = None,
) -> dict:
    """Create an independent ImageModel for a CivitAI asset whose SHA256 matches an existing image.

    The duplicate file is stored in the image library with a unique filename
    (``{hash_prefix}_civitai_{id}.{ext}``) so it participates in normal serving,
    thumbnailing, and metadata workflows.  Both records share ``file_hash`` but
    have unique ``file_path`` values.
    """
    suffix = (
        _guess_suffix(prepared.mime_type) or prepared.temp_path.suffix.lower() or ".jpg"
    )
    file_hash = existing_records[0].file_hash

    # Build a unique filename in the image library
    relative_file_path = _resolve_duplicate_file_path(
        file_hash, prepared.image_id, suffix
    )
    absolute_target = Path(IMAGE_LIBRARY_PATH) / relative_file_path

    # Copy the downloaded temp file to the library
    shutil.copy2(prepared.temp_path, absolute_target)

    try:
        # Verify the hash matches (sanity check)
        actual_hash = _sha256_file(absolute_target)
        if actual_hash != file_hash:
            print(
                f"WARNING: Duplicate asset hash mismatch for CivitAI {prepared.image_id}: "
                f"expected {file_hash}, got {actual_hash}"
            )
            raise ValueError(
                f"Hash mismatch for duplicate CivitAI image {prepared.image_id}"
            )

        # Determine display filename
        original_filename = (
            prepared.original_filename or f"civitai_{prepared.image_id}{suffix}"
        )
        display_name = (
            sanitize_display_filename(
                original_filename,
                fallback_ext=suffix,
            )
            or original_filename
        )

        # Build civitai metadata
        civitai_meta: dict[str, Any] = {}
        if prepared.civitai_uuid:
            civitai_meta["uuid"] = prepared.civitai_uuid
        if prepared.civitai_hash:
            civitai_meta["hash"] = prepared.civitai_hash
        if prepared.api_response_paths:
            civitai_meta.update(prepared.api_response_paths)

        # Mark this as a duplicate asset
        civitai_meta["is_civitai_duplicate_asset"] = True
        civitai_meta["duplicate_of_file_hash"] = file_hash
        civitai_meta["duplicate_of_image_db_id"] = existing_records[0].id
        civitai_meta["original_civitai_image_id"] = prepared.image_id

        json_metadata: dict[str, Any] = {"civitai": civitai_meta}

        # Detect image dimensions
        stat = absolute_target.stat()
        width, height = None, None
        mimetype = prepared.mime_type
        try:
            from PIL import Image as PILImage

            with PILImage.open(absolute_target) as img:
                width, height = img.size
        except Exception:
            pass

        # Create the ImageModel record
        new_image = ImageModel(
            file_path=relative_file_path,
            file_name=display_name,
            original_file_name=original_filename,
            file_hash=actual_hash,
            file_size=stat.st_size,
            width=width,
            height=height,
            mimetype=mimetype,
            date_created=datetime.fromtimestamp(stat.st_ctime),
            date_modified=datetime.fromtimestamp(stat.st_mtime),
            artist_id=None,
            source_url=prepared.source_url,
            source_site="civitai",
            civitai_image_id=prepared.image_id,
            civitai_post_id=prepared.civitai_post_id,
            civitai_post_title=prepared.civitai_post_title,
            civitai_post_index=prepared.civitai_post_index,
            json_metadata=json_metadata,
        )

        if prepared.artist_name or prepared.author_id:
            if prepared.author_id is not None:
                artist_obj = ImageProcessor.find_or_update_civitai_artist(
                    db,
                    username=prepared.artist_name or "[unknown]",
                    civitai_user_id=prepared.author_id,
                    is_deleted=prepared.author_deleted,
                    original_name=prepared.author_original_name,
                )
            else:
                artist_obj = ImageProcessor.find_or_create_artist(
                    db, prepared.artist_name
                )
            new_image.artist_id = artist_obj.id

        db.add(new_image)
        db.flush()

        # Attach to collection if specified
        if attach_collection_id is not None:
            _ensure_image_in_collection(db, new_image.id, attach_collection_id)

        # Save sidecar JSON
        json_sidecar_path = absolute_target.with_suffix(f"{absolute_target.suffix}.json")
        with open(json_sidecar_path, "w", encoding="utf-8") as handle:
            json.dump(json_metadata, handle, indent=2)
    except Exception:
        # Avoid orphaned duplicate files if DB persistence fails after copy.
        absolute_target.unlink(missing_ok=True)
        absolute_target.with_suffix(f"{absolute_target.suffix}.json").unlink(missing_ok=True)
        raise

    print(
        f"Created duplicate asset record: CivitAI {prepared.image_id} -> DB #{new_image.id} "
        f"({relative_file_path}, hash={actual_hash[:12]}...)"
    )

    return {
        "image_id": prepared.image_id,
        "image_db_id": new_image.id,
        "images_added": 1,
        "images_skipped": 0,
        "images_recovered": 0,
        "json_files_created": 1,
        "metadata_backfilled": False,
        "skip_reason": None,
        "is_duplicate_asset": True,
        "duplicate_of_image_db_id": existing_records[0].id,
    }


def _persist_mismatch_static_variant(
    db: Session,
    *,
    prepared: _PreparedCivitaiImport,
    image_db_id: int,
) -> None:
    mismatch_path = prepared.mismatch_static_temp_path
    if mismatch_path is None or not mismatch_path.exists():
        return

    image = db.query(ImageModel).filter(ImageModel.id == image_db_id).first()
    if image is None:
        return

    category, detected_mime = _detect_downloaded_media(mismatch_path)
    if category != "image":
        return

    variant_path = _get_civitai_source_variant_path(
        prepared.image_id, mismatch_path, detected_mime
    )
    if (
        not variant_path.exists()
        or mismatch_path.stat().st_mtime_ns > variant_path.stat().st_mtime_ns
    ):
        shutil.copy2(mismatch_path, variant_path)

    variant_file_hash = _sha256_file(variant_path)
    variant_original_file_name = _build_civitai_original_filename(
        prepared.image_id,
        None,
        str(
            prepared.mismatch_source_url
            or prepared.image_url
            or prepared.source_url
            or ""
        ),
        detected_mime,
    )
    if Path(variant_original_file_name).suffix.lower() in _VIDEO_FILE_SUFFIXES:
        variant_original_file_name = (
            f"{prepared.image_id}{_guess_suffix(detected_mime)}"
        )
    variant_metadata = {
        "image_id": prepared.image_id,
        "image_db_id": image_db_id,
        "declared_mimetype": prepared.mime_type,
        "actual_mimetype": detected_mime or "image/unknown",
        "declared_filename": prepared.original_filename,
        "original_variant_file_name": variant_original_file_name,
        "library_file_path": str(image.file_path),
        "library_file_hash": image.file_hash,
        "variant_file_path": str(variant_path.relative_to(Path(IMAGE_RESOURCES_PATH))),
        "variant_file_hash": variant_file_hash,
        "image_url": prepared.image_url,
        "selected_video_url": prepared.effective_image_url,
        "mismatch_source_url": prepared.mismatch_source_url,
        "source_url": prepared.source_url,
        "declared_file_size": prepared.declared_file_size,
        "actual_file_size": variant_path.stat().st_size,
        "reason": "civitai_video_url_served_static_fallback",
        "saved_at": datetime.utcnow().isoformat() + "Z",
    }

    metadata_path = variant_path.with_suffix(f"{variant_path.suffix}.json")
    with open(metadata_path, "w", encoding="utf-8") as handle:
        json.dump(variant_metadata, handle, indent=2)

    merged_json = (
        dict(image.json_metadata) if isinstance(image.json_metadata, dict) else {}
    )
    merged_json["civitai_source_variant_static"] = variant_metadata
    image.json_metadata = merged_json

    actual_path = Path(IMAGE_LIBRARY_PATH) / str(image.file_path)
    if actual_path.exists():
        processor = ImageProcessor(str(actual_path), db, IMAGE_LIBRARY_PATH)
        processor.save_json_metadata(
            actual_path,
            image,
            additional_data={"civitai_source_variant_static": variant_metadata},
        )


def _save_civitai_api_responses(
    civitai_uuid: Optional[str],
    raw_basic_info: Optional[dict] = None,
    raw_generation_data: Optional[dict] = None,
    raw_infinite: Optional[dict] = None,
) -> dict[str, str]:
    """Return path references for CivitAI API responses.

    File archiving is now handled exclusively by CivitaiAPI._archive_metadata_response.
    This function only builds the path dict for metadata records.
    """
    saved_paths: dict[str, str] = {}

    if not civitai_uuid:
        return saved_paths

    try:
        if isinstance(raw_basic_info, dict):
            saved_paths["raw_basic_info_path"] = (
                f"civitai_api_responses/civitai_image_get_{civitai_uuid}.json"
            )

        if isinstance(raw_generation_data, dict):
            saved_paths["raw_generation_data_path"] = (
                f"civitai_api_responses/civitai_image_getGenerationData_{civitai_uuid}.json"
            )

        if isinstance(raw_infinite, dict):
            saved_paths["raw_infinite_path"] = (
                f"civitai_api_responses/civitai_image_getInfinite_{civitai_uuid}.json"
            )
    except Exception as exc:
        print(
            f"[ERROR] Failed to build CivitAI API response paths for UUID {civitai_uuid}: {exc}"
        )

    return saved_paths


def _archive_civitai_collection_items(items: list[dict[str, Any]]) -> None:
    """No-op: file archiving is now handled exclusively by CivitaiAPI._archive_metadata_response.

    Kept as a stub to preserve call sites without breaking imports during collection scraping.
    """


def _resolve_civitai_image_target(
    api: CivitaiAPI,
    image_id: int,
    *,
    strict: bool = False,
    listing_item: Optional[dict[str, Any]] = None,
    pre_fetched_generation_data: Optional[dict[str, Any]] = None,
) -> dict:
    # When a collection listing item is available (from image.getInfinite),
    # use it as basic_info instead of making a separate image.get API call.
    # The listing item contains all the fields we extract: mimeType, name,
    # url, hash, metadata.size, user.*, postId, etc.
    if isinstance(listing_item, dict) and listing_item.get("id") == image_id:
        basic_info = listing_item
    else:
        try:
            basic_info = api.fetch_basic_info_cached(image_id, strict=strict)
        except CivitaiRequestError as exc:
            if exc.status_code == 404:
                raise _CivitaiImageUnavailableError(
                    image_id=image_id,
                    endpoint="image.get",
                    reason=str(exc),
                    status_code=exc.status_code,
                ) from exc
            raise

    if pre_fetched_generation_data is not None:
        generation_data = pre_fetched_generation_data
    else:
        try:
            generation_data = api.fetch_generation_data_cached(image_id, strict=strict)
        except CivitaiRequestError as exc:
            if exc.status_code == 404:
                raise _CivitaiImageUnavailableError(
                    image_id=image_id,
                    endpoint="image.getGenerationData",
                    reason=str(exc),
                    status_code=exc.status_code,
                ) from exc
            raise

    if not basic_info and not generation_data:
        raise _CivitaiImageUnavailableError(
            image_id=image_id,
            endpoint="image.get,image.getGenerationData",
            reason=f"Could not fetch CivitAI data for image {image_id}.",
            status_code=404,
        )

    # NOTE: api=None prevents merge_basic_info from making an uncached
    # tag.getVotableTags call.  Tags are fetched separately (and cached) in
    # _prepare_civitai_download via fetch_image_tag_records_cached().
    image = CivitaiImage.from_single_image(
        basic_info=basic_info or {"id": image_id},
        generation_data=generation_data or {},
        api=None,
    )
    image_data = image.to_dict(include_full_url=True)

    mime_type = (
        (basic_info or {}).get("mimeType") if isinstance(basic_info, dict) else None
    )
    declared_file_size = None
    if isinstance(basic_info, dict):
        metadata = basic_info.get("metadata")
        if isinstance(metadata, dict):
            raw_size = metadata.get("size")
            try:
                declared_file_size = int(raw_size) if raw_size is not None else None
            except (TypeError, ValueError):
                declared_file_size = None
    preferred_name = (
        (basic_info or {}).get("name") if isinstance(basic_info, dict) else None
    )
    original_filename = _build_civitai_original_filename(
        image_id=image_id,
        preferred_name=preferred_name,
        image_url=image_data.get("url") or "",
        mime_type=mime_type,
    )
    url_hash = (basic_info or {}).get("url") if isinstance(basic_info, dict) else None
    perceptual_hash = (
        (basic_info or {}).get("hash") if isinstance(basic_info, dict) else None
    )
    if not isinstance(perceptual_hash, str) or not perceptual_hash.strip():
        if isinstance(basic_info, dict):
            metadata = basic_info.get("metadata")
            if isinstance(metadata, dict):
                raw_hash = metadata.get("hash")
                if isinstance(raw_hash, str) and raw_hash.strip():
                    perceptual_hash = raw_hash.strip()
                else:
                    perceptual_hash = None
            else:
                perceptual_hash = None
    else:
        perceptual_hash = perceptual_hash.strip()
    civitai_uuid = _extract_civitai_uuid_from_url_hash(url_hash)

    # Save API responses immediately upon successful fetch, before any download/import attempts
    # This ensures we capture metadata even if the image later fails to download
    api_response_paths = _save_civitai_api_responses(
        civitai_uuid=civitai_uuid,
        raw_basic_info=basic_info,
        raw_generation_data=generation_data,
    )

    image_url = _build_civitai_media_url(
        url_hash,
        original_filename,
        mime_type,
        use_video_transcode=True,
    ) or image_data.get("url")
    if not image_url:
        raise HTTPException(
            status_code=502,
            detail=f"CivitAI image {image_id} did not include a downloadable URL.",
        )
    preview_image_url = (
        _build_civitai_media_url(
            url_hash,
            original_filename,
            mime_type,
            use_video_transcode=False,
        )
        if _normalize_mime_type(mime_type).startswith("video/")
        else None
    )

    basic_user = basic_info.get("user", {}) if isinstance(basic_info, dict) else {}
    author_name = image_data.get("author")
    if not author_name and isinstance(basic_user, dict):
        author_name = basic_user.get("username")

    author_id = basic_user.get("id") if isinstance(basic_user, dict) else None
    if author_id is not None:
        try:
            author_id = int(author_id)
        except (TypeError, ValueError):
            author_id = None

    deleted_at = basic_user.get("deletedAt") if isinstance(basic_user, dict) else None
    is_deleted = deleted_at is not None
    author_original_name = None
    if is_deleted and author_name and author_name != "[deleted]":
        author_original_name = author_name

    # For deleted accounts with no username, build a synthetic name from the
    # CivitAI user ID so the artist record is identifiable and searchable.
    if is_deleted and not author_name and author_id is not None:
        author_name = f"[deleted:{author_id}]"

    civitai_post_id = None
    if isinstance(basic_info, dict):
        raw_post_id = basic_info.get("postId")
        if raw_post_id is not None:
            try:
                civitai_post_id = int(raw_post_id)
            except (TypeError, ValueError):
                civitai_post_id = None

    return {
        "image_id": image_id,
        "image_url": image_url,
        "raw_image_url": image_data.get("url"),
        "mime_type": mime_type,
        "declared_file_size": declared_file_size,
        "preview_image_url": preview_image_url,
        "original_filename": original_filename,
        "artist_name": author_name,
        "author_id": author_id,
        "author_deleted": is_deleted,
        "author_original_name": author_original_name,
        "source_url": f"{getattr(app_config, 'CIVITAI_WEB_BASE_URL', 'https://civitai.red')}/images/{image_id}",
        "civitai_url_hash": url_hash,
        "civitai_uuid": civitai_uuid,
        "civitai_hash": perceptual_hash,
        "civitai_post_id": civitai_post_id,
        "civitai_post_title": None,  # populated from collection pipeline, not API
        "civitai_post_index": None,  # populated from collection pipeline, not API
        "raw_basic_info": basic_info,
        "raw_generation_data": generation_data,
        "api_response_paths": api_response_paths,
    }


def _isoformat_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _dict_payload(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _list_payload(value: Any) -> list:
    return value if isinstance(value, list) else []


def _first_meaningful_civitai_value(*values: Any, allow_zero: bool = False) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            cleaned = value.strip()
            if not cleaned or cleaned.lower() == "unknown":
                continue
            return cleaned
        if isinstance(value, (int, float)) and not allow_zero and value == 0:
            continue
        return value
    return None


def _normalize_civitai_platform_name(
    generation_data: dict, image_data: dict, basic_info: dict, meta: dict
) -> str:
    process_value = (
        str(
            _first_meaningful_civitai_value(
                generation_data.get("process"), image_data.get("process")
            )
            or ""
        )
        .strip()
        .lower()
    )
    engine_value = _first_meaningful_civitai_value(
        image_data.get("engine"), meta.get("engine")
    )
    tools = _list_payload(generation_data.get("tools"))
    first_tool = tools[0] if tools and isinstance(tools[0], dict) else {}
    tool_name = _first_meaningful_civitai_value(first_tool.get("name"))

    if isinstance(meta.get("comfy"), dict) or process_value in {"comfy", "comfyui"}:
        return "ComfyUI"
    if tool_name:
        return str(tool_name)
    if generation_data.get("onSite"):
        return "CivitAI Generator"
    if engine_value:
        return str(engine_value)
    if str(basic_info.get("type") or "").strip().lower() == "video":
        return "CivitAI"
    return "CivitAI"


def _normalize_civitai_method_family(
    generation_data: dict, image_data: dict, meta: dict
) -> Optional[str]:
    process_value = (
        str(
            _first_meaningful_civitai_value(
                generation_data.get("process"), image_data.get("process")
            )
            or ""
        )
        .strip()
        .lower()
    )
    workflow_value = _first_meaningful_civitai_value(
        meta.get("workflow"), image_data.get("workflow")
    )
    techniques = _list_payload(generation_data.get("techniques"))
    first_technique = (
        techniques[0] if techniques and isinstance(techniques[0], dict) else {}
    )
    technique_name = _first_meaningful_civitai_value(first_technique.get("name"))

    if isinstance(meta.get("comfy"), dict) or process_value in {"comfy", "comfyui"}:
        return "comfyui_workflow"
    if process_value:
        return process_value
    if workflow_value:
        return str(workflow_value).strip().lower()
    if technique_name:
        return str(technique_name).strip().lower()
    return None


def _resolve_civitai_workflow_payload(meta: dict, image_data: dict) -> Any:
    comfy_payload = meta.get("comfy") if isinstance(meta.get("comfy"), dict) else None
    if comfy_payload:
        workflow_graph = comfy_payload.get("workflow")
        if workflow_graph is not None:
            return workflow_graph
        return comfy_payload
    return _first_meaningful_civitai_value(
        meta.get("workflow"), image_data.get("workflow"), image_data.get("process")
    )


def _resolve_civitai_generation_dimensions(
    meta: dict, image_data: dict, generation_data: dict, basic_info: dict
) -> tuple[Optional[int], Optional[int], Optional[int], Optional[int]]:
    generation_width = _first_meaningful_civitai_value(
        meta.get("width"),
        generation_data.get("width"),
        image_data.get("width"),
    )
    generation_height = _first_meaningful_civitai_value(
        meta.get("height"),
        generation_data.get("height"),
        image_data.get("height"),
    )

    basic_metadata = _dict_payload(basic_info.get("metadata"))
    output_width = _first_meaningful_civitai_value(
        basic_info.get("width"),
        basic_metadata.get("width"),
        generation_width,
    )
    output_height = _first_meaningful_civitai_value(
        basic_info.get("height"),
        basic_metadata.get("height"),
        generation_height,
    )
    return generation_width, generation_height, output_width, output_height


def _coerce_optional_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        try:
            return int(float(cleaned))
        except ValueError:
            return None
    return None


def _coerce_optional_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        try:
            return float(cleaned)
        except ValueError:
            return None
    return None


def _round_preview_float(value: Any, places: int = 2) -> Any:
    normalized = _coerce_optional_float(value)
    if normalized is None:
        return value
    rounded = round(normalized, places)
    if float(rounded).is_integer():
        return int(rounded)
    return rounded


def _build_generation_resource_preview(
    resource_type: str,
    display_name: Optional[str] = None,
    *,
    version_name: Optional[str] = None,
    base_model_name: Optional[str] = None,
    strength_model: Any = None,
    strength_clip: Any = None,
    strength_text_encoder: Any = None,
    civitai_model_id: Any = None,
    civitai_model_version_id: Any = None,
    source_identifier: Optional[str] = None,
    resource_role: Optional[str] = None,
    is_primary: Optional[bool] = None,
    raw_resource_json: Any = None,
) -> dict:
    normalized_type = str(resource_type or "other").strip().lower() or "other"
    normalized_name = (
        display_name.lower()
        if isinstance(display_name, str) and display_name.strip()
        else None
    )
    resolved_model_id = _coerce_optional_int(civitai_model_id)
    resolved_model_version_id = _coerce_optional_int(civitai_model_version_id)
    resolved_identifier = (
        str(source_identifier or resolved_model_id or "").strip() or None
    )
    resolved_is_primary = (
        normalized_type == "checkpoint" if is_primary is None else bool(is_primary)
    )
    resolved_role = resource_role or ("primary" if resolved_is_primary else "reference")
    return {
        "id": None,
        "process_id": None,
        "stage_id": None,
        "resource_role": resolved_role,
        "resource_type": normalized_type,
        "display_name": display_name,
        "normalized_name": normalized_name,
        "version_name": version_name,
        "base_model_name": base_model_name,
        "strength_model": _round_preview_float(strength_model),
        "strength_clip": _round_preview_float(strength_clip),
        "strength_text_encoder": _round_preview_float(strength_text_encoder),
        "civitai_model_id": resolved_model_id,
        "civitai_model_version_id": resolved_model_version_id,
        "source_identifier": resolved_identifier,
        "is_primary": resolved_is_primary,
        "raw_resource_json": raw_resource_json,
        "created_at": None,
        "updated_at": None,
    }


def _looks_like_comfy_prompt_graph(payload: Any) -> bool:
    if not isinstance(payload, dict) or not payload:
        return False
    for node in payload.values():
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs")
        if isinstance(inputs, dict) and (node.get("class_type") or node.get("type")):
            return True
    return False


def _extract_comfy_prompt_graph(workflow_payload: Any, meta: dict) -> dict:
    comfy_payload = meta.get("comfy") if isinstance(meta.get("comfy"), dict) else None
    candidate_payloads = []
    if comfy_payload:
        candidate_payloads.extend(
            [comfy_payload.get("prompt"), comfy_payload.get("workflow"), comfy_payload]
        )
    candidate_payloads.extend(
        [
            workflow_payload,
            (
                workflow_payload.get("prompt")
                if isinstance(workflow_payload, dict)
                else None
            ),
            (
                workflow_payload.get("workflow")
                if isinstance(workflow_payload, dict)
                else None
            ),
        ]
    )
    for candidate in candidate_payloads:
        if _looks_like_comfy_prompt_graph(candidate):
            return candidate
    return {}


def _comfy_node_id_from_reference(value: Any) -> Optional[str]:
    if isinstance(value, (list, tuple)) and value:
        node_id = value[0]
        if isinstance(node_id, (str, int)):
            return str(node_id)
    return None


def _get_comfy_node(graph: dict, node_id: Optional[str]) -> dict:
    if not node_id:
        return {}
    node = graph.get(str(node_id))
    return node if isinstance(node, dict) else {}


def _get_comfy_node_inputs(node: dict) -> dict:
    inputs = node.get("inputs")
    return inputs if isinstance(inputs, dict) else {}


def _get_comfy_node_class(node: dict) -> str:
    return str(node.get("class_type") or node.get("type") or "").strip()


def _get_comfy_node_title(node: dict) -> Optional[str]:
    raw_meta = node.get("_meta")
    meta = raw_meta if isinstance(raw_meta, dict) else {}
    return str(meta.get("title") or node.get("title") or "").strip() or None


def _collect_comfy_upstream_node_ids(
    graph: dict, start_node_id: str, input_names: Optional[set[str]] = None
) -> set[str]:
    visited: set[str] = set()

    def walk_value(value: Any) -> None:
        if isinstance(value, (list, tuple)):
            node_id = _comfy_node_id_from_reference(value)
            if node_id:
                walk_node(node_id)
            return
        if isinstance(value, dict):
            for nested_value in value.values():
                walk_value(nested_value)
            return
        if isinstance(value, list):
            for nested_value in value:
                walk_value(nested_value)

    def walk_node(node_id: str) -> None:
        normalized_id = str(node_id)
        if normalized_id in visited:
            return
        visited.add(normalized_id)
        node = _get_comfy_node(graph, normalized_id)
        if not node:
            return
        for input_name, input_value in _get_comfy_node_inputs(node).items():
            if input_names is not None and input_name not in input_names:
                continue
            walk_value(input_value)

    walk_node(str(start_node_id))
    visited.discard(str(start_node_id))
    return visited


def _count_upstream_comfy_sampler_nodes(
    graph: dict, node_id: str, memo: dict[str, int]
) -> int:
    normalized_id = str(node_id)
    if normalized_id in memo:
        return memo[normalized_id]
    total = 0
    for upstream_id in _collect_comfy_upstream_node_ids(graph, normalized_id):
        upstream_node = _get_comfy_node(graph, upstream_id)
        if _is_comfy_generation_stage_node_class(
            _get_comfy_node_class(upstream_node).lower()
        ):
            total += 1 + _count_upstream_comfy_sampler_nodes(graph, upstream_id, memo)
    memo[normalized_id] = total
    return total


def _is_comfy_generation_stage_node_class(node_class_lower: str) -> bool:
    return "ksampler" in node_class_lower or node_class_lower == "ultimatesdupscale"


def _unwrap_workflow_resource_identifier(value: Any) -> Any:
    if isinstance(value, dict):
        for key in (
            "content",
            "name",
            "model_name",
            "ckpt_name",
            "vae_name",
            "lora_name",
        ):
            candidate = value.get(key)
            if candidate not in {None, ""}:
                return candidate
    return value


def _parse_civitai_air_identifier(value: Any) -> dict:
    text = str(value or "").strip()
    if not text:
        return {}
    match = re.match(
        r"^urn:air:(?P<base_model>[^:]+):(?P<resource_kind>[^:]+):civitai:(?P<model_id>\d+)(?:@(?P<version_id>\d+))?$",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return {}
    resource_kind = str(match.group("resource_kind") or "other").strip().lower()
    if resource_kind == "upscaler":
        normalized_type = "upscaler"
    elif resource_kind == "embedding":
        normalized_type = "textualinversion"
    else:
        normalized_type = resource_kind
    return {
        "resource_type": normalized_type,
        "base_model_name": str(match.group("base_model") or "").strip() or None,
        "civitai_model_id": _coerce_optional_int(match.group("model_id")),
        "civitai_model_version_id": _coerce_optional_int(match.group("version_id")),
        "source_identifier": text,
    }


def _normalize_workflow_resource_name(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    air_details = _parse_civitai_air_identifier(text)
    if air_details:
        return None
    parsed = urlparse(text)
    if parsed.scheme and parsed.path:
        text = parsed.path
    text = text.replace("\\", "/")
    leaf = text.rsplit("/", 1)[-1]
    if "." in leaf:
        leaf = leaf.rsplit(".", 1)[0]
    return leaf.strip() or None


def _resource_preview_dedupe_key(resource: dict) -> tuple:
    version_id = resource.get("civitai_model_version_id")
    if version_id is not None:
        return ("version", int(version_id))
    model_id = resource.get("civitai_model_id")
    if model_id is not None:
        return ("model", resource.get("resource_type"), int(model_id))
    source_identifier = str(resource.get("source_identifier") or "").strip().lower()
    if source_identifier:
        return ("source", source_identifier)
    normalized_name = str(resource.get("normalized_name") or "").strip().lower()
    if normalized_name:
        return ("name", resource.get("resource_type"), normalized_name)
    return ("fallback", resource.get("resource_type"), id(resource))


def _merge_resource_preview(existing: dict, incoming: dict) -> dict:
    merged = dict(existing)

    def is_empty_value(value: Any) -> bool:
        return value is None or value == "" or value == {}

    for field_name in (
        "resource_role",
        "resource_type",
        "display_name",
        "normalized_name",
        "version_name",
        "base_model_name",
        "strength_model",
        "strength_clip",
        "strength_text_encoder",
        "civitai_model_id",
        "civitai_model_version_id",
        "source_identifier",
        "raw_resource_json",
    ):
        if is_empty_value(merged.get(field_name)) and not is_empty_value(
            incoming.get(field_name)
        ):
            merged[field_name] = incoming.get(field_name)
    merged["is_primary"] = bool(merged.get("is_primary") or incoming.get("is_primary"))
    if merged.get("is_primary"):
        merged["resource_role"] = "primary"
    return merged


def _dedupe_resource_previews(resources: list[dict]) -> list[dict]:
    deduped: dict[tuple, dict] = {}
    order: list[tuple] = []
    for resource in resources:
        if not isinstance(resource, dict):
            continue
        key = _resource_preview_dedupe_key(resource)
        if key in deduped:
            deduped[key] = _merge_resource_preview(deduped[key], resource)
            continue
        deduped[key] = resource
        order.append(key)
    return [deduped[key] for key in order]


def _build_workflow_resource_preview(
    resource_type: str,
    source_identifier: Any,
    raw_resource_json: dict,
    *,
    display_name: Optional[str] = None,
    strength_model: Any = None,
    strength_clip: Any = None,
    strength_text_encoder: Any = None,
    resource_role: Optional[str] = None,
) -> dict:
    resolved_identifier = _unwrap_workflow_resource_identifier(source_identifier)
    air_details = _parse_civitai_air_identifier(resolved_identifier)
    return _build_generation_resource_preview(
        air_details.get("resource_type") or resource_type,
        display_name or _normalize_workflow_resource_name(resolved_identifier),
        base_model_name=air_details.get("base_model_name"),
        civitai_model_id=air_details.get("civitai_model_id"),
        civitai_model_version_id=air_details.get("civitai_model_version_id"),
        source_identifier=air_details.get("source_identifier")
        or (
            str(resolved_identifier).strip()
            if resolved_identifier is not None
            else None
        ),
        strength_model=strength_model,
        strength_clip=strength_clip,
        strength_text_encoder=strength_text_encoder,
        resource_role=resource_role,
        is_primary=(air_details.get("resource_type") or resource_type) == "checkpoint",
        raw_resource_json=raw_resource_json,
    )


def _extract_embedding_tokens_from_prompt(prompt_text: str) -> list[str]:
    if not prompt_text:
        return []
    matches = re.findall(r"embedding:([^,\s]+)", prompt_text, flags=re.IGNORECASE)
    seen: set[str] = set()
    tokens: list[str] = []
    for match in matches:
        token = str(match).strip()
        if not token or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def _collect_comfy_prompt_texts(graph: dict, reference: Any) -> list[dict]:
    prompt_nodes: list[dict] = []
    visited: set[str] = set()

    def walk_ref(value: Any) -> None:
        node_id = _comfy_node_id_from_reference(value)
        if node_id:
            walk_node(node_id)

    def walk_node(node_id: str) -> None:
        normalized_id = str(node_id)
        if normalized_id in visited:
            return
        visited.add(normalized_id)
        node = _get_comfy_node(graph, normalized_id)
        if not node:
            return
        node_class = _get_comfy_node_class(node)
        inputs = _get_comfy_node_inputs(node)
        if (
            "cliptextencode" in node_class.lower()
            and str(inputs.get("text") or "").strip()
        ):
            prompt_nodes.append(
                {
                    "node_id": normalized_id,
                    "node_class": node_class,
                    "node_title": _get_comfy_node_title(node),
                    "text": str(inputs.get("text") or "").strip(),
                }
            )
        for input_value in inputs.values():
            if isinstance(input_value, (list, tuple)):
                walk_ref(input_value)

    walk_ref(reference)
    return prompt_nodes


def _collect_comfy_image_sources(graph: dict, sampler_node_id: str) -> list[dict]:
    sources: list[dict] = []
    seen_urls: set[str] = set()
    for upstream_id in _collect_comfy_upstream_node_ids(graph, sampler_node_id):
        node = _get_comfy_node(graph, upstream_id)
        if _get_comfy_node_class(node).lower() != "loadimage":
            continue
        inputs = _get_comfy_node_inputs(node)
        source_url = str(inputs.get("image") or "").strip() or None
        if not source_url or source_url in seen_urls:
            continue
        seen_urls.add(source_url)
        sources.append(
            {
                "id": None,
                "process_id": None,
                "stage_id": None,
                "asset_role": "input_image",
                "source_image_id": None,
                "source_url": source_url,
                "encoded_payload_ref": str(upstream_id),
                "mime_type": None,
                "width": None,
                "height": None,
                "metadata_json": {
                    "node_id": str(upstream_id),
                    "node_title": _get_comfy_node_title(node),
                    "upload": inputs.get("upload"),
                },
                "created_at": None,
            }
        )
    return sources


def _resolve_comfy_image_dimensions(
    graph: dict,
    reference: Any,
    fallback_width: Optional[int],
    fallback_height: Optional[int],
    visited: Optional[set[str]] = None,
) -> tuple[Optional[int], Optional[int]]:
    node_id = _comfy_node_id_from_reference(reference)
    if not node_id:
        return fallback_width, fallback_height
    if visited is None:
        visited = set()
    if node_id in visited:
        return fallback_width, fallback_height
    visited.add(node_id)
    node = _get_comfy_node(graph, node_id)
    if not node:
        return fallback_width, fallback_height
    node_class = _get_comfy_node_class(node).lower()
    inputs = _get_comfy_node_inputs(node)
    if node_class == "imagescale":
        return _coerce_optional_int(inputs.get("width")), _coerce_optional_int(
            inputs.get("height")
        )
    if node_class == "loadimage":
        return fallback_width, fallback_height
    if node_class == "imageupscalewithmodel":
        return _resolve_comfy_image_dimensions(
            graph, inputs.get("image"), fallback_width, fallback_height, visited
        )
    if node_class == "vaedecode":
        return _resolve_comfy_latent_dimensions(
            graph, inputs.get("samples"), fallback_width, fallback_height, visited
        )
    return fallback_width, fallback_height


def _resolve_comfy_latent_dimensions(
    graph: dict,
    reference: Any,
    fallback_width: Optional[int],
    fallback_height: Optional[int],
    visited: Optional[set[str]] = None,
) -> tuple[Optional[int], Optional[int]]:
    node_id = _comfy_node_id_from_reference(reference)
    if not node_id:
        return fallback_width, fallback_height
    if visited is None:
        visited = set()
    if node_id in visited:
        return fallback_width, fallback_height
    visited.add(node_id)
    node = _get_comfy_node(graph, node_id)
    if not node:
        return fallback_width, fallback_height
    node_class = _get_comfy_node_class(node).lower()
    inputs = _get_comfy_node_inputs(node)
    if node_class in {
        "emptylatentimage",
        "emptysd3latentimage",
        "emptyhunyuanlatentvideo",
    }:
        return _coerce_optional_int(inputs.get("width")), _coerce_optional_int(
            inputs.get("height")
        )
    if node_class == "latentupscale":
        return _coerce_optional_int(inputs.get("width")), _coerce_optional_int(
            inputs.get("height")
        )
    if node_class == "latentupscaleby":
        source_width, source_height = _resolve_comfy_latent_dimensions(
            graph, inputs.get("samples"), fallback_width, fallback_height, visited
        )
        scale_by = _coerce_optional_float(inputs.get("scale_by"))
        if source_width is not None and source_height is not None and scale_by:
            return int(round(source_width * scale_by)), int(
                round(source_height * scale_by)
            )
        return source_width, source_height
    if "ksampler" in node_class:
        return _resolve_comfy_latent_dimensions(
            graph, inputs.get("latent_image"), fallback_width, fallback_height, visited
        )
    if node_class == "vaeencode":
        return _resolve_comfy_image_dimensions(
            graph, inputs.get("pixels"), fallback_width, fallback_height, visited
        )
    return fallback_width, fallback_height


def _collect_comfy_model_resources(
    graph: dict,
    reference: Any,
    inherited_weight: Any = None,
    visited: Optional[set[str]] = None,
) -> list[dict]:
    node_id = _comfy_node_id_from_reference(reference)
    if not node_id:
        return []
    if visited is None:
        visited = set()
    normalized_id = str(node_id)
    if normalized_id in visited:
        return []
    visited.add(normalized_id)

    node = _get_comfy_node(graph, normalized_id)
    if not node:
        return []

    node_class = _get_comfy_node_class(node)
    node_class_lower = node_class.lower()
    inputs = _get_comfy_node_inputs(node)
    title = _get_comfy_node_title(node)

    if (
        node_class_lower.startswith("checkpointloader")
        or "checkpointloader" in node_class_lower
    ):
        return [
            _build_workflow_resource_preview(
                "checkpoint",
                inputs.get("ckpt_name") or inputs.get("model_name"),
                {
                    "node_id": normalized_id,
                    "node_class": node_class,
                    "node_title": title,
                    "inputs": inputs,
                    "source": "model_graph",
                },
                strength_model=inherited_weight,
                resource_role="primary",
            )
        ]

    if node_class_lower == "av_checkpointmerge":
        model1_weight = _first_meaningful_civitai_value(
            inputs.get("model1_weight"), inputs.get("weight1"), allow_zero=True
        )
        model2_weight = _first_meaningful_civitai_value(
            inputs.get("model2_weight"), inputs.get("weight2"), allow_zero=True
        )
        merged_resources = []
        merged_resources.extend(
            _collect_comfy_model_resources(
                graph, inputs.get("model1"), model1_weight, visited
            )
        )
        merged_resources.extend(
            _collect_comfy_model_resources(
                graph, inputs.get("model2"), model2_weight, visited
            )
        )
        return merged_resources

    if node_class_lower in {"cr apply lora stack", "reroute"}:
        for input_name in ("model", "input", "", "source"):
            if input_name in inputs:
                nested_resources = _collect_comfy_model_resources(
                    graph, inputs.get(input_name), inherited_weight, visited
                )
                if nested_resources:
                    return nested_resources
        for input_value in inputs.values():
            nested_resources = _collect_comfy_model_resources(
                graph, input_value, inherited_weight, visited
            )
            if nested_resources:
                return nested_resources
        return []

    return []


def _collect_comfy_stage_resources(
    graph: dict, sampler_node_id: str, prompt_nodes: list[dict]
) -> list[dict]:
    resource_nodes: list[dict] = []
    sampler_node = _get_comfy_node(graph, sampler_node_id)
    sampler_inputs = _get_comfy_node_inputs(sampler_node)
    resource_nodes.extend(
        _collect_comfy_model_resources(graph, sampler_inputs.get("model"))
    )
    upstream_ids = _collect_comfy_upstream_node_ids(graph, sampler_node_id)
    for upstream_id in upstream_ids:
        node = _get_comfy_node(graph, upstream_id)
        node_class = _get_comfy_node_class(node)
        node_class_lower = node_class.lower()
        inputs = _get_comfy_node_inputs(node)
        title = _get_comfy_node_title(node)
        if (
            node_class_lower.startswith("checkpointloader")
            or "checkpointloader" in node_class_lower
        ):
            resource_nodes.append(
                _build_workflow_resource_preview(
                    "checkpoint",
                    inputs.get("ckpt_name") or inputs.get("model_name"),
                    {
                        "node_id": upstream_id,
                        "node_class": node_class,
                        "node_title": title,
                        "inputs": inputs,
                    },
                )
            )
        elif node_class_lower == "loraloader":
            resource_nodes.append(
                _build_workflow_resource_preview(
                    "lora",
                    inputs.get("lora_name"),
                    {
                        "node_id": upstream_id,
                        "node_class": node_class,
                        "node_title": title,
                        "inputs": inputs,
                    },
                    strength_model=inputs.get("strength_model"),
                    strength_clip=inputs.get("strength_clip"),
                )
            )
        elif node_class_lower == "vaeloader":
            resource_nodes.append(
                _build_workflow_resource_preview(
                    "vae",
                    inputs.get("vae_name"),
                    {
                        "node_id": upstream_id,
                        "node_class": node_class,
                        "node_title": title,
                        "inputs": inputs,
                    },
                )
            )
        elif node_class_lower == "upscalemodelloader":
            resource_nodes.append(
                _build_workflow_resource_preview(
                    "upscaler",
                    inputs.get("model_name"),
                    {
                        "node_id": upstream_id,
                        "node_class": node_class,
                        "node_title": title,
                        "inputs": inputs,
                    },
                )
            )
        elif node_class_lower == "cr lora stack":
            for index in range(1, 9):
                if str(inputs.get(f"switch_{index}") or "Off").strip().lower() == "off":
                    continue
                lora_name = inputs.get(f"lora_name_{index}")
                if not lora_name:
                    continue
                resource_nodes.append(
                    _build_workflow_resource_preview(
                        "lora",
                        lora_name,
                        {
                            "node_id": upstream_id,
                            "node_class": node_class,
                            "node_title": title,
                            "inputs": inputs,
                            "stack_index": index,
                        },
                        strength_model=inputs.get(f"model_weight_{index}"),
                        strength_clip=inputs.get(f"clip_weight_{index}"),
                    )
                )

    for prompt_node in prompt_nodes:
        for embedding_token in _extract_embedding_tokens_from_prompt(
            prompt_node.get("text") or ""
        ):
            resource_nodes.append(
                _build_workflow_resource_preview(
                    "textualinversion",
                    embedding_token,
                    {
                        "node_id": prompt_node.get("node_id"),
                        "node_class": prompt_node.get("node_class"),
                        "node_title": prompt_node.get("node_title"),
                        "source": "prompt_embedding",
                    },
                )
            )

    return _dedupe_resource_previews(resource_nodes)


def _build_comfy_workflow_stages(
    graph: dict,
    generation_data: dict,
    generation_width: Optional[int],
    generation_height: Optional[int],
    output_width: Optional[int],
    output_height: Optional[int],
) -> list[dict]:
    sampler_ids: list[str] = []
    for node_id, node in graph.items():
        if not isinstance(node, dict):
            continue
        if _is_comfy_generation_stage_node_class(_get_comfy_node_class(node).lower()):
            sampler_ids.append(str(node_id))
    if not sampler_ids:
        return []

    sampler_depths: dict[str, int] = {}
    sampler_ids.sort(
        key=lambda item: (
            _count_upstream_comfy_sampler_nodes(graph, item, sampler_depths),
            (
                _coerce_optional_int(item)
                if _coerce_optional_int(item) is not None
                else 10**9
            ),
            str(item),
        )
    )

    stages: list[dict] = []
    for stage_index, sampler_id in enumerate(sampler_ids):
        sampler_node = _get_comfy_node(graph, sampler_id)
        sampler_class_lower = _get_comfy_node_class(sampler_node).lower()
        sampler_inputs = _get_comfy_node_inputs(sampler_node)
        prompt_nodes_positive = _collect_comfy_prompt_texts(
            graph, sampler_inputs.get("positive")
        )
        prompt_nodes_negative = _collect_comfy_prompt_texts(
            graph, sampler_inputs.get("negative")
        )
        prompt_nodes_all = prompt_nodes_positive + prompt_nodes_negative
        source_assets = _collect_comfy_image_sources(graph, sampler_id)
        if sampler_class_lower == "ultimatesdupscale":
            source_width, source_height = _resolve_comfy_image_dimensions(
                graph,
                sampler_inputs.get("image"),
                output_width,
                output_height,
            )
            upscale_by = _coerce_optional_float(sampler_inputs.get("upscale_by"))
            if source_width is not None and source_height is not None and upscale_by:
                stage_width = int(round(source_width * upscale_by))
                stage_height = int(round(source_height * upscale_by))
            else:
                stage_width, stage_height = output_width, output_height
        else:
            stage_width, stage_height = _resolve_comfy_latent_dimensions(
                graph,
                sampler_inputs.get("latent_image"),
                generation_width,
                generation_height,
            )
        denoise_strength = _first_meaningful_civitai_value(
            sampler_inputs.get("denoise"),
            sampler_inputs.get("denoise_strength"),
            allow_zero=True,
        )
        upstream_ids = _collect_comfy_upstream_node_ids(graph, sampler_id)
        upstream_classes = {
            _get_comfy_node_class(_get_comfy_node(graph, upstream_id)).lower()
            for upstream_id in upstream_ids
        }
        has_loaded_input = bool(source_assets)
        has_latent_upscale = any(
            node_class.startswith("latentupscale") for node_class in upstream_classes
        )
        if sampler_class_lower == "ultimatesdupscale":
            stage_role = "upscale"
            stage_method_family = "img2img_upscale"
        elif stage_index == 0:
            stage_role = "base"
            if has_loaded_input:
                stage_method_family = "img2img"
            else:
                stage_method_family = "txt2img"
        else:
            if has_latent_upscale:
                stage_role = "upscale"
                stage_method_family = "img2img-hires"
            elif denoise_strength is not None and float(denoise_strength) < 1:
                stage_role = "refine"
                stage_method_family = "img2img_refine"
            else:
                stage_role = "refine"
                stage_method_family = "img2img"

        prompts: list[dict] = []
        for prompt_role, prompt_nodes in (
            ("positive", prompt_nodes_positive),
            ("negative", prompt_nodes_negative),
        ):
            seen_prompt_texts: set[str] = set()
            for prompt_node in prompt_nodes:
                prompt_text = str(prompt_node.get("text") or "").strip()
                if not prompt_text or prompt_text in seen_prompt_texts:
                    continue
                seen_prompt_texts.add(prompt_text)
                prompts.append(
                    {
                        "id": None,
                        "process_id": None,
                        "stage_id": None,
                        "prompt_role": prompt_role,
                        "prompt_text": prompt_text,
                        "source_type": "civitai_api",
                        "token_count": None,
                        **_build_parsed_prompt_fields(
                            prompt_text,
                            prompt_role=prompt_role,
                            source_type="civitai_api",
                            source_label=f"comfy_prompt_graph:{prompt_node.get('node_id')}",
                        ),
                        "raw_prompt_json": {
                            "node_id": prompt_node.get("node_id"),
                            "node_class": prompt_node.get("node_class"),
                            "node_title": prompt_node.get("node_title"),
                            "source": "comfy_prompt_graph",
                        },
                        "created_at": None,
                    }
                )

        stage_resources = _collect_comfy_stage_resources(
            graph, sampler_id, prompt_nodes_all
        )
        output_dimensions = None
        if stage_index == len(sampler_ids) - 1 and output_width and output_height:
            output_dimensions = {
                "output_width": output_width,
                "output_height": output_height,
            }

        stages.append(
            {
                "id": None,
                "process_id": None,
                "stage_id": None,
                "stage_index": stage_index,
                "stage_role": stage_role,
                "stage_label": _get_comfy_node_title(sampler_node),
                "method_family": stage_method_family,
                "method_variant": _get_comfy_node_class(sampler_node) or None,
                "input_image_id": None,
                "input_asset_ref": (
                    source_assets[0]["encoded_payload_ref"] if source_assets else None
                ),
                "width": stage_width,
                "height": stage_height,
                "base_width": generation_width if stage_index == 0 else stage_width,
                "base_height": generation_height if stage_index == 0 else stage_height,
                "sampler_name": str(sampler_inputs.get("sampler_name") or "").strip()
                or None,
                "scheduler_name": str(sampler_inputs.get("scheduler") or "").strip()
                or None,
                "steps": _first_meaningful_civitai_value(sampler_inputs.get("steps")),
                "cfg_scale": _round_preview_float(
                    _first_meaningful_civitai_value(
                        sampler_inputs.get("cfg"), sampler_inputs.get("cfg_scale")
                    )
                ),
                "seed": (
                    None
                    if sampler_inputs.get("seed") in {None, ""}
                    else str(sampler_inputs.get("seed"))
                ),
                "clip_skip": sampler_inputs.get("clip_skip"),
                "strength": _round_preview_float(sampler_inputs.get("strength")),
                "denoise_strength": _round_preview_float(denoise_strength),
                "guidance_notes": None,
                "compatibility_json": {
                    "node_id": sampler_id,
                    "node_class": _get_comfy_node_class(sampler_node),
                    "node_title": _get_comfy_node_title(sampler_node),
                    "upstream_sampler_count": _count_upstream_comfy_sampler_nodes(
                        graph, sampler_id, sampler_depths
                    ),
                    **(output_dimensions or {}),
                },
                "raw_stage_json": {
                    "sampler_node_id": sampler_id,
                    "sampler_node": sampler_node,
                },
                "created_at": None,
                "updated_at": None,
                "prompts": prompts,
                "resources": stage_resources,
                "source_assets": source_assets,
                "field_values": [],
                "provenance_records": [],
            }
        )
    return stages


def _read_image_sidecar_payload(image: ImageModel) -> dict:
    sidecar_path = (Path(IMAGE_LIBRARY_PATH) / str(image.file_path)).with_suffix(
        ".json"
    )
    if not sidecar_path.exists():
        return {}
    try:
        with open(sidecar_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _assert_imagehash_available() -> None:
    if imagehash is None:
        raise HTTPException(
            status_code=503,
            detail="Perceptual hashing requires the ImageHash package. Install dependencies and retry.",
        )


def _get_perceptual_hash_algorithms() -> dict[str, Callable[..., Any]]:
    ih: Any = imagehash
    if ih is None:
        _assert_imagehash_available()
        raise HTTPException(
            status_code=503,
            detail="Perceptual hashing requires the ImageHash package. Install dependencies and retry.",
        )
    average_hash_builder = getattr(ih, "average_hash", None) or getattr(
        ih, "ahash", None
    )
    if average_hash_builder is None:
        raise HTTPException(
            status_code=500,
            detail="ImageHash installation is missing average hash support.",
        )
    return {
        "phash": lambda img, hash_size: ih.phash(img, hash_size=hash_size),
        "dhash": lambda img, hash_size: ih.dhash(img, hash_size=hash_size),
        "ahash": lambda img, hash_size: average_hash_builder(img, hash_size=hash_size),
        "whash": lambda img, hash_size: ih.whash(img, hash_size=hash_size),
    }


def _normalize_media_url_path(file_path: str) -> str:
    normalized = str(file_path or "").replace("\\", "/").lstrip("/")
    return quote(normalized)


def _coerce_json_object(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text_value = value.strip()
        if not text_value:
            return {}
        try:
            parsed = json.loads(text_value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _read_civitai_payload_for_image(image: ImageModel) -> dict:
    sidecar_payload = _read_image_sidecar_payload(image)
    json_metadata_payload = _coerce_json_object(image.json_metadata)
    candidates = [
        sidecar_payload.get("civitai"),
        getattr(image, "civitai_data", None),
        json_metadata_payload.get("civitai"),
        sidecar_payload,
        json_metadata_payload,
    ]
    for candidate in candidates:
        parsed = _coerce_json_object(candidate)
        if parsed:
            return parsed
    return {}


def _iter_hash_strings(payload: Any, *, path: str = "civitai"):
    if isinstance(payload, dict):
        for key, value in payload.items():
            key_text = str(key)
            next_path = f"{path}.{key_text}" if path else key_text
            if isinstance(value, str) and "hash" in key_text.lower():
                raw_value = value.strip()
                if raw_value:
                    yield {
                        "path": next_path,
                        "value": raw_value,
                    }
            yield from _iter_hash_strings(value, path=next_path)
    elif isinstance(payload, list):
        for index, item in enumerate(payload):
            yield from _iter_hash_strings(item, path=f"{path}[{index}]")


def _decode_civitai_hash_candidate(value: str) -> Optional[dict[str, Any]]:
    text_value = str(value or "").strip()
    if not text_value:
        return None

    if re.fullmatch(r"[0-9a-fA-F]+", text_value):
        try:
            decoded_int = int(text_value, 16)
            return {
                "encoding": "hex",
                "bit_length": len(text_value) * 4,
                "value_int": decoded_int,
            }
        except ValueError:
            return None

    clean_candidate = re.sub(r"[^A-Za-z0-9_\-+/]", "", text_value)
    if not clean_candidate:
        return None

    for encoding, decoder in (
        ("base64", base64.b64decode),
        ("base64url", base64.urlsafe_b64decode),
    ):
        padded = clean_candidate + ("=" * ((4 - (len(clean_candidate) % 4)) % 4))
        try:
            decoded_bytes = decoder(padded.encode("ascii"))
        except Exception:
            continue
        if not decoded_bytes:
            continue
        return {
            "encoding": encoding,
            "bit_length": len(decoded_bytes) * 8,
            "value_int": int.from_bytes(decoded_bytes, byteorder="big", signed=False),
        }

    return None


def _hash_object_to_bits(hash_obj: Any) -> tuple[int, int]:
    bits_str = "".join("1" if bool(bit) else "0" for bit in hash_obj.hash.flatten())
    return int(bits_str, 2), len(bits_str)


def _compute_bit_distance(
    local_int: int, local_bits: int, candidate_int: int, candidate_bits: int
) -> tuple[int, int, bool]:
    compare_bits = min(local_bits, candidate_bits)
    if compare_bits <= 0:
        return 0, 0, False
    mask = (1 << compare_bits) - 1
    distance = ((local_int & mask) ^ (candidate_int & mask)).bit_count()
    truncated = local_bits != candidate_bits
    return distance, compare_bits, truncated


def _hash_hex_to_encodings(hex_str: str) -> dict[str, str]:
    """Return base16/base32/base64/uuencode representations of hash bytes."""
    try:
        raw = bytes.fromhex(hex_str)
    except ValueError:
        return {}
    b16 = base64.b16encode(raw).decode("ascii")
    b32 = base64.b32encode(raw).decode("ascii")
    b64 = base64.b64encode(raw).decode("ascii")
    uu_lines: list[str] = []
    for i in range(0, len(raw), 45):
        uu_lines.append(binascii.b2a_uu(raw[i : i + 45]).decode("ascii").rstrip("\n"))
    return {
        "base16": b16,
        "base32": b32,
        "base64": b64,
        "uuencode": "\n".join(uu_lines),
    }


def _extract_primary_civitai_image_hash(
    image: ImageModel, civitai_payload: dict[str, Any]
) -> Optional[str]:
    db_hash = str(getattr(image, "civitai_hash", "") or "").strip()
    if db_hash:
        return db_hash

    direct = civitai_payload.get("hash")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()

    metadata = civitai_payload.get("metadata")
    if isinstance(metadata, dict):
        metadata_hash = metadata.get("hash")
        if isinstance(metadata_hash, str) and metadata_hash.strip():
            return metadata_hash.strip()

    nested_image = civitai_payload.get("image")
    if isinstance(nested_image, dict):
        nested_hash = nested_image.get("hash")
        if isinstance(nested_hash, str) and nested_hash.strip():
            return nested_hash.strip()
        nested_meta = nested_image.get("metadata")
        if isinstance(nested_meta, dict):
            nested_meta_hash = nested_meta.get("hash")
            if isinstance(nested_meta_hash, str) and nested_meta_hash.strip():
                return nested_meta_hash.strip()

    return None


def _suggest_blurhash_component_pairs(
    target_hash: Optional[str],
) -> list[tuple[int, int]]:
    default_pairs: list[tuple[int, int]] = [(4, 3), (4, 4), (7, 7), (8, 8), (9, 9)]
    pairs: list[tuple[int, int]] = []
    target_text = str(target_hash or "").strip()
    if target_text:
        target_length = len(target_text)
        if target_length > 4 and (target_length - 4) % 2 == 0:
            product = (target_length - 4) // 2
            for x_components in range(1, 10):
                if product % x_components != 0:
                    continue
                y_components = product // x_components
                if 1 <= y_components <= 9:
                    pairs.append((x_components, y_components))
    pairs.extend(default_pairs)

    deduped: list[tuple[int, int]] = []
    seen: set[tuple[int, int]] = set()
    for pair in pairs:
        if pair in seen:
            continue
        seen.add(pair)
        deduped.append(pair)
    return deduped


def _encode_blurhash_with_fallbacks(
    image_rgb: Image.Image,
    pixel_rows: list[list[tuple[int, int, int]]],
    x_components: int,
    y_components: int,
) -> Optional[str]:
    bh: Any = blurhash
    if bh is None:
        return None

    encode_fn = getattr(bh, "encode", None)
    if not callable(encode_fn):
        return None

    attempts = (
        lambda: encode_fn(
            image_rgb, x_components=x_components, y_components=y_components
        ),
        lambda: encode_fn(
            image_rgb, components_x=x_components, components_y=y_components
        ),
        lambda: encode_fn(
            pixel_rows, x_components=x_components, y_components=y_components
        ),
        lambda: encode_fn(
            pixel_rows, components_x=x_components, components_y=y_components
        ),
    )

    for attempt in attempts:
        try:
            encoded = attempt()
        except Exception:
            continue
        if isinstance(encoded, str) and encoded.strip():
            return encoded.strip()
    return None


def _decode_blurhash_pixels(
    blurhash_value: Optional[str], *, width: int = 32, height: int = 32
) -> Optional[list[list[list[int]]]]:
    text_value = str(blurhash_value or "").strip()
    bh: Any = blurhash
    if not text_value or bh is None:
        return None

    decode_fn = getattr(bh, "decode", None)
    if not callable(decode_fn):
        return None

    try:
        decoded = decode_fn(text_value, width, height)
    except Exception:
        return None

    if not isinstance(decoded, list) or not decoded:
        return None
    return decoded


def _blurhash_preview_data_url(
    blurhash_value: Optional[str], *, width: int = 32, height: int = 32
) -> Optional[str]:
    decoded = _decode_blurhash_pixels(blurhash_value, width=width, height=height)
    if not decoded:
        return None

    image = Image.new("RGB", (width, height))
    flat_pixels: list[tuple[int, int, int]] = []
    for row in decoded:
        if not isinstance(row, list):
            return None
        for pixel in row:
            if not isinstance(pixel, list) or len(pixel) < 3:
                return None
            try:
                flat_pixels.append((int(pixel[0]), int(pixel[1]), int(pixel[2])))
            except (TypeError, ValueError):
                return None

    if len(flat_pixels) != width * height:
        return None

    image.putdata(flat_pixels)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded_png = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded_png}"


def _blurhash_preview_distance(
    candidate_hash: Optional[str],
    reference_hash: Optional[str],
    *,
    width: int = 32,
    height: int = 32,
) -> Optional[dict[str, Any]]:
    candidate_pixels = _decode_blurhash_pixels(
        candidate_hash, width=width, height=height
    )
    reference_pixels = _decode_blurhash_pixels(
        reference_hash, width=width, height=height
    )
    if not candidate_pixels or not reference_pixels:
        return None

    total_abs = 0.0
    total_sq = 0.0
    sample_count = 0
    for row_index in range(min(len(candidate_pixels), len(reference_pixels))):
        candidate_row = candidate_pixels[row_index]
        reference_row = reference_pixels[row_index]
        for col_index in range(min(len(candidate_row), len(reference_row))):
            candidate_pixel = candidate_row[col_index]
            reference_pixel = reference_row[col_index]
            for channel_index in range(3):
                delta = float(candidate_pixel[channel_index]) - float(
                    reference_pixel[channel_index]
                )
                total_abs += abs(delta)
                total_sq += delta * delta
                sample_count += 1

    if sample_count <= 0:
        return None

    mean_abs_error = total_abs / sample_count
    rmse = (total_sq / sample_count) ** 0.5
    return {
        "mean_absolute_error": round(mean_abs_error, 4),
        "rmse": round(rmse, 4),
        "normalized_similarity": round(max(0.0, 1.0 - (mean_abs_error / 255.0)), 6),
        "preview_width": width,
        "preview_height": height,
    }


def _build_blurhash_report(
    image_path: Path, *, civitai_hash: Optional[str]
) -> dict[str, Any]:
    civitai_hash_text = str(civitai_hash or "").strip()
    max_dimension = 128
    max_runtime_seconds = 8.0
    preview_width = 32
    preview_height = 32
    report: dict[str, Any] = {
        "available": blurhash is not None,
        "target_civitai_hash": civitai_hash_text or None,
        "target_length": len(civitai_hash_text) if civitai_hash_text else 0,
        "candidates": {},
        "exact_match": False,
        "matching_component_pairs": [],
        "max_dimension": max_dimension,
        "max_runtime_seconds": max_runtime_seconds,
        "preview_size": {"width": preview_width, "height": preview_height},
    }

    if blurhash is None:
        report["reason"] = (
            "BlurHash package is not installed. Install blurhash to enable this comparison."
        )
        return report

    target_preview = _blurhash_preview_data_url(
        civitai_hash_text, width=preview_width, height=preview_height
    )
    report["target_preview"] = {
        "hash": civitai_hash_text or None,
        "image_data_url": target_preview,
        "decodable": bool(target_preview),
    }

    try:
        with Image.open(image_path) as handle:
            normalized_image = handle.convert("RGB")
            original_size = normalized_image.size
            resized_image = normalized_image.copy()
            resized_image.thumbnail(
                (max_dimension, max_dimension), Image.Resampling.LANCZOS
            )
            resized_size = resized_image.size
            width, height = resized_image.size
            flat_pixels = list(resized_image.getdata())
            pixel_rows = [
                [
                    tuple(pixel)
                    for pixel in flat_pixels[
                        row_index * width : (row_index + 1) * width
                    ]
                ]
                for row_index in range(height)
            ]
            report["analysis_size"] = {
                "width": resized_size[0],
                "height": resized_size[1],
            }
            report["source_size"] = {
                "width": original_size[0],
                "height": original_size[1],
            }
            report["was_resized"] = original_size != resized_size
            run_started = time.monotonic()
            for x_components, y_components in _suggest_blurhash_component_pairs(
                civitai_hash_text
            ):
                if time.monotonic() - run_started > max_runtime_seconds:
                    report["truncated"] = True
                    break
                encoded = _encode_blurhash_with_fallbacks(
                    resized_image,
                    pixel_rows,
                    x_components,
                    y_components,
                )
                key = f"{x_components}x{y_components}"
                if not encoded:
                    report["candidates"][key] = {
                        "value": None,
                        "length": 0,
                        "matches_civitai_hash": False,
                    }
                    continue

                is_match = bool(civitai_hash_text and encoded == civitai_hash_text)
                string_distance = None
                if civitai_hash_text:
                    compare_length = min(len(encoded), len(civitai_hash_text))
                    string_distance = sum(
                        1
                        for index in range(compare_length)
                        if encoded[index] != civitai_hash_text[index]
                    ) + abs(len(encoded) - len(civitai_hash_text))

                preview_data_url = _blurhash_preview_data_url(
                    encoded,
                    width=preview_width,
                    height=preview_height,
                )
                preview_distance = _blurhash_preview_distance(
                    encoded,
                    civitai_hash_text,
                    width=preview_width,
                    height=preview_height,
                )
                report["candidates"][key] = {
                    "value": encoded,
                    "length": len(encoded),
                    "matches_civitai_hash": is_match,
                    "string_distance": string_distance,
                    "preview": {
                        "image_data_url": preview_data_url,
                        "decodable": bool(preview_data_url),
                    },
                    "preview_distance": preview_distance,
                }
                if is_match:
                    report["matching_component_pairs"].append(key)
    except Exception as exc:
        report["error"] = str(exc)
        return report

    report["exact_match"] = bool(report["matching_component_pairs"])
    best_candidate_key = None
    best_candidate_score = None
    for key, candidate in report["candidates"].items():
        if not isinstance(candidate, dict):
            continue
        preview_distance = candidate.get("preview_distance")
        if not isinstance(preview_distance, dict):
            continue
        score = preview_distance.get("mean_absolute_error")
        if not isinstance(score, (int, float)):
            continue
        if best_candidate_score is None or score < best_candidate_score:
            best_candidate_score = float(score)
            best_candidate_key = key
    report["best_candidate_key"] = best_candidate_key
    report["best_candidate"] = (
        report["candidates"].get(best_candidate_key) if best_candidate_key else None
    )
    return report


def _build_perceptual_hash_suite(
    image_path: Path, hash_size: int = 8
) -> dict[str, dict[str, Any]]:
    algorithms = _get_perceptual_hash_algorithms()
    clamped_hash_size = max(4, min(int(hash_size), 32))
    with Image.open(image_path) as handle:
        normalized_image = handle.convert("RGB")
        payload: dict[str, dict[str, Any]] = {}
        for algorithm, builder in algorithms.items():
            hash_obj = builder(normalized_image, clamped_hash_size)
            hash_int, bit_length = _hash_object_to_bits(hash_obj)
            hex_str = str(hash_obj)
            payload[algorithm] = {
                "hex": hex_str,
                "hash_size": clamped_hash_size,
                "bit_length": bit_length,
                "value_int": hash_int,
                **_hash_hex_to_encodings(hex_str),
            }
        return payload


def _extract_local_blurhash_4x4(image: ImageModel) -> Optional[str]:
    db_json = _coerce_json_object(getattr(image, "json_metadata", None))
    db_blurhash = (
        db_json.get("blurhash") if isinstance(db_json.get("blurhash"), dict) else None
    )
    if isinstance(db_blurhash, dict):
        db_value = str(db_blurhash.get("4x4") or "").strip()
        if db_value:
            return db_value

    sidecar_payload = _read_image_sidecar_payload(image)
    sidecar_blurhash = (
        sidecar_payload.get("blurhash")
        if isinstance(sidecar_payload.get("blurhash"), dict)
        else None
    )
    if isinstance(sidecar_blurhash, dict):
        sidecar_value = str(sidecar_blurhash.get("4x4") or "").strip()
        if sidecar_value:
            return sidecar_value

    return None


def _compute_blurhash_4x4(
    image_path: Path, *, max_dimension: int = 128
) -> Optional[str]:
    if blurhash is None:
        return None

    with Image.open(image_path) as handle:
        normalized = handle.convert("RGB")
        resized = normalized.copy()
        resized.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
        width, height = resized.size
        if width <= 0 or height <= 0:
            return None

        flat_pixels = list(resized.getdata())
        pixel_rows = [
            [
                tuple(pixel)
                for pixel in flat_pixels[row_index * width : (row_index + 1) * width]
            ]
            for row_index in range(height)
        ]
        return _encode_blurhash_with_fallbacks(
            resized,
            pixel_rows,
            4,
            4,
        )


def _resolve_local_blurhash_4x4(
    image: ImageModel, image_path: Path
) -> tuple[Optional[str], str]:
    metadata_hash = _extract_local_blurhash_4x4(image)
    if metadata_hash:
        return metadata_hash, "metadata"
    try:
        computed_hash = _compute_blurhash_4x4(image_path)
    except OSError:
        return None, "unavailable"
    if computed_hash:
        return computed_hash, "computed"
    return None, "unavailable"


def _resolve_image_library_path(image: ImageModel) -> Path:
    return Path(IMAGE_LIBRARY_PATH) / str(image.file_path)


def _get_image_or_404(db: Session, file_hash: str) -> ImageModel:
    image = (
        db.query(ImageModel)
        .filter(ImageModel.file_hash == file_hash)
        .filter(_active_image_filter())
        .first()
    )
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found.")
    return image


def _build_civitai_hash_comparison_payload(
    local_hashes: dict[str, dict[str, Any]], civitai_payload: dict
) -> dict:
    candidates = []
    seen_values: set[str] = set()
    for item in _iter_hash_strings(civitai_payload):
        value_text = str(item.get("value") or "").strip()
        if not value_text or value_text in seen_values:
            continue
        seen_values.add(value_text)
        candidates.append(
            {
                "path": str(item.get("path") or "civitai"),
                "value": value_text,
            }
        )
        if len(candidates) >= 32:
            break

    comparisons: list[dict[str, Any]] = []
    best_match: Optional[dict[str, Any]] = None
    for candidate in candidates:
        decoded = _decode_civitai_hash_candidate(candidate["value"])
        candidate_row: dict[str, Any] = {
            "path": candidate["path"],
            "value": candidate["value"],
            "decoded": decoded,
            "matches": [],
        }
        if decoded is None:
            comparisons.append(candidate_row)
            continue

        candidate_int = int(decoded["value_int"])
        candidate_bits = int(decoded["bit_length"])
        for algorithm, local_entry in local_hashes.items():
            distance, compared_bits, truncated = _compute_bit_distance(
                int(local_entry["value_int"]),
                int(local_entry["bit_length"]),
                candidate_int,
                candidate_bits,
            )
            match = {
                "algorithm": algorithm,
                "distance": distance,
                "compared_bits": compared_bits,
                "truncated": truncated,
            }
            candidate_row["matches"].append(match)
            if best_match is None or distance < int(best_match["distance"]):
                best_match = {
                    "algorithm": algorithm,
                    "distance": distance,
                    "compared_bits": compared_bits,
                    "truncated": truncated,
                    "path": candidate["path"],
                    "value": candidate["value"],
                    "decoded_encoding": decoded.get("encoding"),
                }

        comparisons.append(candidate_row)

    return {
        "candidate_count": len(candidates),
        "candidates": comparisons,
        "best_match": best_match,
    }


def _build_parsed_prompt_fields(
    prompt_text: str,
    *,
    prompt_role: str,
    source_type: Optional[str],
    source_label: Optional[str],
) -> dict[str, Any]:
    analysis = build_prompt_tag_payload(
        prompt_text,
        prompt_role=prompt_role,
        source_type=source_type,
        source_label=source_label,
    )
    return {
        "prompt_style": analysis.get("prompt_style"),
        "parsed_concepts_json": analysis.get("concepts"),
        "parsed_phrases_json": analysis.get("phrases"),
    }


def _serialize_generation_prompt(prompt) -> dict:
    return {
        "id": prompt.id,
        "process_id": prompt.process_id,
        "stage_id": prompt.stage_id,
        "prompt_role": prompt.prompt_role,
        "prompt_text": prompt.prompt_text,
        "prompt_style": prompt.prompt_style,
        "source_type": prompt.source_type,
        "token_count": prompt.token_count,
        "parsed_concepts_json": prompt.parsed_concepts_json,
        "parsed_phrases_json": prompt.parsed_phrases_json,
        "raw_prompt_json": prompt.raw_prompt_json,
        "created_at": _isoformat_or_none(prompt.created_at),
    }


def _serialize_generation_resource(resource) -> dict:
    return {
        "id": resource.id,
        "process_id": resource.process_id,
        "stage_id": resource.stage_id,
        "resource_role": resource.resource_role,
        "resource_type": resource.resource_type,
        "display_name": resource.display_name,
        "normalized_name": resource.normalized_name,
        "version_name": resource.version_name,
        "base_model_name": resource.base_model_name,
        "strength_model": resource.strength_model,
        "strength_clip": resource.strength_clip,
        "strength_text_encoder": resource.strength_text_encoder,
        "civitai_model_id": resource.civitai_model_id,
        "civitai_model_version_id": resource.civitai_model_version_id,
        "source_identifier": resource.source_identifier,
        "is_primary": resource.is_primary,
        "raw_resource_json": resource.raw_resource_json,
        "created_at": _isoformat_or_none(resource.created_at),
        "updated_at": _isoformat_or_none(resource.updated_at),
    }


def _serialize_generation_source_asset(asset) -> dict:
    return {
        "id": asset.id,
        "process_id": asset.process_id,
        "stage_id": asset.stage_id,
        "asset_role": asset.asset_role,
        "source_image_id": asset.source_image_id,
        "source_url": _reconstruct_source_url(asset.source_url),
        "encoded_payload_ref": asset.encoded_payload_ref,
        "mime_type": asset.mime_type,
        "width": asset.width,
        "height": asset.height,
        "metadata_json": asset.metadata_json,
        "created_at": _isoformat_or_none(asset.created_at),
    }


def _serialize_generation_field_value(field_value) -> dict:
    return {
        "id": field_value.id,
        "process_id": field_value.process_id,
        "stage_id": field_value.stage_id,
        "field_name": field_value.field_name,
        "field_value_text": field_value.field_value_text,
        "field_value_number": field_value.field_value_number,
        "field_value_json": field_value.field_value_json,
        "value_type": field_value.value_type,
        "source_type": field_value.source_type,
        "is_preferred": field_value.is_preferred,
        "created_at": _isoformat_or_none(field_value.created_at),
    }


def _serialize_generation_provenance(record) -> dict:
    return {
        "id": record.id,
        "process_id": record.process_id,
        "stage_id": record.stage_id,
        "scope_type": record.scope_type,
        "scope_id": record.scope_id,
        "source_type": record.source_type,
        "source_label": record.source_label,
        "confidence_label": record.confidence_label,
        "is_preferred": record.is_preferred,
        "raw_fragment_json": record.raw_fragment_json,
        "notes": record.notes,
        "created_at": _isoformat_or_none(record.created_at),
    }


def _serialize_generation_stage(stage) -> dict:
    return {
        "id": stage.id,
        "process_id": stage.process_id,
        "stage_index": stage.stage_index,
        "stage_role": stage.stage_role,
        "stage_label": stage.stage_label,
        "method_family": stage.method_family,
        "method_variant": stage.method_variant,
        "input_image_id": stage.input_image_id,
        "input_asset_ref": stage.input_asset_ref,
        "width": stage.width,
        "height": stage.height,
        "base_width": stage.base_width,
        "base_height": stage.base_height,
        "sampler_name": stage.sampler_name,
        "scheduler_name": stage.scheduler_name,
        "steps": stage.steps,
        "cfg_scale": stage.cfg_scale,
        "seed": stage.seed,
        "clip_skip": stage.clip_skip,
        "strength": stage.strength,
        "denoise_strength": stage.denoise_strength,
        "guidance_notes": stage.guidance_notes,
        "compatibility_json": stage.compatibility_json,
        "raw_stage_json": stage.raw_stage_json,
        "created_at": _isoformat_or_none(stage.created_at),
        "updated_at": _isoformat_or_none(stage.updated_at),
        "prompts": [_serialize_generation_prompt(item) for item in stage.prompts],
        "resources": [_serialize_generation_resource(item) for item in stage.resources],
        "source_assets": [
            _serialize_generation_source_asset(item) for item in stage.source_assets
        ],
        "field_values": [
            _serialize_generation_field_value(item) for item in stage.field_values
        ],
        "provenance_records": [
            _serialize_generation_provenance(item) for item in stage.provenance_records
        ],
    }


def _serialize_generation_process(process) -> dict:
    return {
        "id": process.id,
        "image_id": process.image_id,
        "source_type": process.source_type,
        "source_label": process.source_label,
        "is_preferred": process.is_preferred,
        "is_user_supplied": process.is_user_supplied,
        "platform_name": process.platform_name,
        "platform_version": process.platform_version,
        "method_family": process.method_family,
        "method_variant": process.method_variant,
        "stage_count": process.stage_count,
        "has_embedded_sources": process.has_embedded_sources,
        "has_refiners": process.has_refiners,
        "has_video_generation": process.has_video_generation,
        "raw_payload_json": process.raw_payload_json,
        "workflow_json": process.workflow_json,
        "compatibility_json": process.compatibility_json,
        "created_at": _isoformat_or_none(process.created_at),
        "updated_at": _isoformat_or_none(process.updated_at),
        "stages": [_serialize_generation_stage(item) for item in process.stages],
        "prompts": [
            _serialize_generation_prompt(item)
            for item in process.prompts
            if item.stage_id is None
        ],
        "resources": [
            _serialize_generation_resource(item)
            for item in process.resources
            if item.stage_id is None
        ],
        "source_assets": [
            _serialize_generation_source_asset(item)
            for item in process.source_assets
            if item.stage_id is None
        ],
        "field_values": [
            _serialize_generation_field_value(item)
            for item in process.field_values
            if item.stage_id is None
        ],
        "provenance_records": [
            _serialize_generation_provenance(item)
            for item in process.provenance_records
            if item.stage_id is None
        ],
    }


def _summarize_validation(warnings: list[str], errors: list[str]) -> dict:
    status = "ok"
    if errors:
        status = "error"
    elif warnings:
        status = "warning"
    return {
        "status": status,
        "warnings": warnings,
        "errors": errors,
        "warning_count": len(warnings),
        "error_count": len(errors),
    }


def _normalize_civitai_resource_preview(resource: dict) -> dict:
    resource_type = (
        str(resource.get("modelType") or resource.get("type") or "other")
        .strip()
        .lower()
        or "other"
    )
    display_name = (
        str(resource.get("modelName") or resource.get("name") or "").strip() or None
    )
    version_name = str(resource.get("versionName") or "").strip() or None
    base_model_name = str(resource.get("baseModel") or "").strip() or None
    model_id = _first_meaningful_civitai_value(
        resource.get("modelId"), resource.get("id")
    )
    return _build_generation_resource_preview(
        resource_type,
        display_name,
        version_name=version_name,
        base_model_name=base_model_name,
        strength_model=resource.get("strength"),
        strength_clip=resource.get("clipWeight"),
        strength_text_encoder=resource.get("textEncoderWeight"),
        civitai_model_id=model_id,
        civitai_model_version_id=resource.get("modelVersionId"),
        source_identifier=str(model_id or "").strip() or None,
        is_primary=resource_type == "checkpoint",
        raw_resource_json=resource,
    )


def _build_civitai_image_data_resource_previews(image_data: dict) -> list[dict]:
    resources: list[dict] = []

    models = image_data.get("models") if isinstance(image_data, dict) else []
    if not isinstance(models, list):
        models = []
    for item in models:
        if not isinstance(item, dict):
            continue
        model_id = _first_meaningful_civitai_value(item.get("modelId"), item.get("id"))
        resources.append(
            _build_generation_resource_preview(
                "checkpoint",
                str(item.get("name") or item.get("modelName") or "").strip() or None,
                version_name=str(
                    item.get("version") or item.get("versionName") or ""
                ).strip()
                or None,
                base_model_name=str(item.get("baseModel") or "").strip() or None,
                civitai_model_id=model_id,
                civitai_model_version_id=item.get("modelVersionId"),
                source_identifier=str(model_id or "").strip() or None,
                is_primary=True,
                raw_resource_json=item,
            )
        )

    primary_model_name = (
        str(image_data.get("model") or "").strip()
        if isinstance(image_data, dict)
        else ""
    )
    if primary_model_name:
        primary_model_id = _first_meaningful_civitai_value(
            image_data.get("modelId"),
            image_data.get("model_id"),
        )
        resources.append(
            _build_generation_resource_preview(
                "checkpoint",
                primary_model_name,
                version_name=str(
                    image_data.get("modelVersion")
                    or image_data.get("model_version")
                    or ""
                ).strip()
                or None,
                base_model_name=str(image_data.get("baseModel") or "").strip() or None,
                civitai_model_id=primary_model_id,
                civitai_model_version_id=image_data.get("modelVersionId")
                or image_data.get("model_version_id"),
                source_identifier=str(primary_model_id or "").strip() or None,
                is_primary=True,
                raw_resource_json={
                    "model": image_data.get("model"),
                    "modelVersion": image_data.get("modelVersion")
                    or image_data.get("model_version"),
                },
            )
        )

    loras = image_data.get("loras") if isinstance(image_data, dict) else []
    if not isinstance(loras, list):
        loras = []
    for item in loras:
        if not isinstance(item, dict):
            continue
        model_id = _first_meaningful_civitai_value(item.get("modelId"), item.get("id"))
        resources.append(
            _build_generation_resource_preview(
                "lora",
                str(item.get("name") or item.get("modelName") or "").strip() or None,
                version_name=str(
                    item.get("version") or item.get("versionName") or ""
                ).strip()
                or None,
                base_model_name=str(item.get("baseModel") or "").strip() or None,
                strength_model=item.get("strength"),
                strength_clip=item.get("clipWeight"),
                strength_text_encoder=item.get("textEncoderWeight"),
                civitai_model_id=model_id,
                civitai_model_version_id=item.get("modelVersionId"),
                source_identifier=str(model_id or "").strip() or None,
                is_primary=False,
                raw_resource_json=item,
            )
        )

    return _dedupe_resource_previews(resources)


def _build_civitai_normalized_preview(
    image_id: int,
    basic_info: dict,
    generation_data: dict,
    image_data: dict,
) -> dict:
    meta = generation_data.get("meta") if isinstance(generation_data, dict) else {}
    if not isinstance(meta, dict):
        meta = {}
    raw_resources = (
        generation_data.get("resources") if isinstance(generation_data, dict) else []
    )
    if not isinstance(raw_resources, list):
        raw_resources = []
    method_family = _normalize_civitai_method_family(generation_data, image_data, meta)
    platform_name = _normalize_civitai_platform_name(
        generation_data, image_data, basic_info, meta
    )
    workflow_value = _resolve_civitai_workflow_payload(meta, image_data)
    comfy_prompt_graph = _extract_comfy_prompt_graph(workflow_value, meta)
    generation_width, generation_height, output_width, output_height = (
        _resolve_civitai_generation_dimensions(
            meta,
            image_data,
            generation_data,
            basic_info,
        )
    )

    prompts: list[dict] = []
    positive_prompt = str(image_data.get("prompt") or "").strip()
    negative_prompt = str(image_data.get("negative_prompt") or "").strip()
    if positive_prompt:
        prompts.append(
            {
                "id": None,
                "process_id": None,
                "stage_id": None,
                "prompt_role": "positive",
                "prompt_text": positive_prompt,
                "source_type": "civitai_api",
                "token_count": None,
                **_build_parsed_prompt_fields(
                    positive_prompt,
                    prompt_role="positive",
                    source_type="civitai_api",
                    source_label="meta.prompt",
                ),
                "raw_prompt_json": {"source": "meta.prompt"},
                "created_at": None,
            }
        )
    if negative_prompt:
        prompts.append(
            {
                "id": None,
                "process_id": None,
                "stage_id": None,
                "prompt_role": "negative",
                "prompt_text": negative_prompt,
                "source_type": "civitai_api",
                "token_count": None,
                **_build_parsed_prompt_fields(
                    negative_prompt,
                    prompt_role="negative",
                    source_type="civitai_api",
                    source_label="meta.negativePrompt",
                ),
                "raw_prompt_json": {"source": "meta.negativePrompt"},
                "created_at": None,
            }
        )

    raw_stage_resources = [
        _normalize_civitai_resource_preview(item)
        for item in raw_resources
        if isinstance(item, dict)
    ]
    if not raw_stage_resources:
        raw_stage_resources = _build_civitai_image_data_resource_previews(image_data)
    compatibility_json = (
        {"draft": image_data.get("draft")}
        if image_data.get("draft") is not None
        else None
    )
    has_generation_payload = bool(
        meta
        or raw_resources
        or raw_stage_resources
        or positive_prompt
        or negative_prompt
    )
    normalized_seed = _first_meaningful_civitai_value(
        meta.get("seed"), image_data.get("seed")
    )
    stages = (
        _build_comfy_workflow_stages(
            comfy_prompt_graph,
            generation_data,
            generation_width,
            generation_height,
            output_width,
            output_height,
        )
        if comfy_prompt_graph
        else []
    )

    if stages:
        stages = [dict(stage) for stage in stages]
        stages[0]["resources"] = _dedupe_resource_previews(
            raw_stage_resources + _list_payload(stages[0].get("resources"))
        )
        if not stages[0].get("prompts"):
            stages[0]["prompts"] = prompts
        for stage in stages:
            stage.setdefault("field_values", [])
            stage.setdefault("provenance_records", [])
            stage.setdefault("source_assets", [])
    else:
        stages = [
            {
                "id": None,
                "process_id": None,
                "stage_index": 0,
                "stage_role": "base",
                "stage_label": None,
                "method_family": method_family,
                "method_variant": None,
                "input_image_id": None,
                "input_asset_ref": None,
                "width": generation_width,
                "height": generation_height,
                "base_width": generation_width,
                "base_height": generation_height,
                "sampler_name": str(image_data.get("sampler") or "").strip() or None,
                "scheduler_name": str(meta.get("scheduler") or "").strip() or None,
                "steps": _first_meaningful_civitai_value(
                    meta.get("steps"), image_data.get("steps")
                ),
                "cfg_scale": _round_preview_float(
                    _first_meaningful_civitai_value(
                        meta.get("cfgScale"), image_data.get("cfg_scale")
                    )
                ),
                "seed": None if normalized_seed is None else str(normalized_seed),
                "clip_skip": (
                    meta.get("clipSkip")
                    if meta.get("clipSkip") is not None
                    else image_data.get("clip_skip")
                ),
                "strength": _round_preview_float(meta.get("strength")),
                "denoise_strength": _round_preview_float(
                    _first_meaningful_civitai_value(
                        meta.get("denoise"),
                        meta.get("denoiseStrength"),
                        allow_zero=True,
                    )
                ),
                "guidance_notes": None,
                "compatibility_json": {
                    **(compatibility_json or {}),
                    **(
                        {"output_width": output_width, "output_height": output_height}
                        if output_width and output_height
                        else {}
                    ),
                }
                or None,
                "raw_stage_json": generation_data,
                "created_at": None,
                "updated_at": None,
                "prompts": prompts,
                "resources": raw_stage_resources,
                "source_assets": [],
                "field_values": [],
                "provenance_records": [],
            }
        ]

    process_method_family = method_family
    if stages and not method_family:
        process_method_family = stages[0].get("method_family")
    stage_source_assets = any(
        _list_payload(stage.get("source_assets")) for stage in stages
    )

    return {
        "processes": [
            {
                "id": None,
                "image_id": image_id,
                "source_type": "civitai_api",
                "source_label": "prototype_fetch",
                "is_preferred": True,
                "is_user_supplied": False,
                "platform_name": platform_name,
                "platform_version": None,
                "method_family": process_method_family,
                "method_variant": None,
                "stage_count": len(stages) if has_generation_payload else 0,
                "has_embedded_sources": stage_source_assets,
                "has_refiners": len(stages) > 1
                or any(
                    item.get("resource_type") == "refiner"
                    for stage in stages
                    for item in _list_payload(stage.get("resources"))
                ),
                "has_video_generation": str(basic_info.get("mimeType") or "")
                .lower()
                .startswith("video/"),
                "raw_payload_json": {
                    "basic_info": basic_info,
                    "generation_data": generation_data,
                },
                "workflow_json": workflow_value,
                "compatibility_json": {
                    **(compatibility_json or {}),
                    **({"workflow_stage_count": len(stages)} if stages else {}),
                }
                or None,
                "created_at": None,
                "updated_at": None,
                "stages": stages,
                "prompts": [],
                "resources": [],
                "source_assets": [],
                "field_values": [],
                "provenance_records": [
                    {
                        "id": None,
                        "process_id": None,
                        "stage_id": None,
                        "scope_type": "process",
                        "scope_id": None,
                        "source_type": "civitai_api",
                        "source_label": "image.get + image.getGenerationData",
                        "confidence_label": "direct",
                        "is_preferred": True,
                        "raw_fragment_json": {
                            "basic_info_present": bool(basic_info),
                            "generation_data_present": bool(generation_data),
                        },
                        "notes": None,
                        "created_at": None,
                    }
                ],
            }
        ]
    }


def _build_local_fallback_normalized_preview(
    image: ImageModel,
    merged_payload: dict,
    exif_payload: dict,
) -> list[dict]:
    def _pick_value(*candidates: Any) -> Any:
        for candidate in candidates:
            if candidate not in (None, ""):
                return candidate
        return None

    def _read_exif(*keys: str) -> Any:
        if not isinstance(exif_payload, dict):
            return None
        lowered = {
            str(key).strip().lower(): value for key, value in exif_payload.items()
        }
        for key in keys:
            value = lowered.get(str(key).strip().lower())
            if value not in (None, ""):
                return value
        return None

    image_data = _dict_payload(merged_payload.get("civitai_data"))
    if not image_data:
        image_data = _dict_payload(
            _dict_payload(merged_payload.get("civitai")).get("image")
        )
    image_data = dict(image_data)

    image_data.setdefault(
        "prompt",
        _pick_value(_read_exif("Prompt", "prompt"), merged_payload.get("prompt")),
    )
    image_data.setdefault(
        "negative_prompt",
        _pick_value(
            _read_exif("Negative prompt", "NegativePrompt", "negative prompt"),
            merged_payload.get("negative_prompt"),
        ),
    )
    image_data.setdefault("sampler", _read_exif("Sampler", "sampler"))
    image_data.setdefault(
        "steps", _pick_value(_read_exif("Steps", "steps"), merged_payload.get("steps"))
    )
    image_data.setdefault(
        "cfg_scale",
        _pick_value(
            _read_exif("CFG scale", "cfg scale", "cfg"), merged_payload.get("cfg_scale")
        ),
    )
    image_data.setdefault(
        "seed", _pick_value(_read_exif("Seed", "seed"), merged_payload.get("seed"))
    )
    image_data.setdefault(
        "clip_skip",
        _pick_value(
            _read_exif("Clip skip", "clip skip"), merged_payload.get("clip_skip")
        ),
    )
    image_data.setdefault(
        "model", _pick_value(_read_exif("Model", "model"), merged_payload.get("model"))
    )
    image_data.setdefault(
        "model_version",
        _pick_value(
            _read_exif("Model version", "model version"),
            merged_payload.get("model_version"),
            merged_payload.get("modelVersion"),
        ),
    )
    image_data.setdefault(
        "baseModel",
        _pick_value(
            _read_exif("Base model", "base model"), merged_payload.get("base_model")
        ),
    )

    generation_data = _dict_payload(merged_payload.get("raw_generation_data"))
    generation_data = dict(generation_data)
    meta = _dict_payload(generation_data.get("meta"))
    meta = dict(meta)
    if image_data.get("prompt") and not meta.get("prompt"):
        meta["prompt"] = image_data.get("prompt")
    if image_data.get("negative_prompt") and not meta.get("negativePrompt"):
        meta["negativePrompt"] = image_data.get("negative_prompt")
    if image_data.get("steps") is not None and meta.get("steps") in (None, ""):
        meta["steps"] = image_data.get("steps")
    if image_data.get("cfg_scale") is not None and meta.get("cfgScale") in (None, ""):
        meta["cfgScale"] = image_data.get("cfg_scale")
    if image_data.get("seed") is not None and meta.get("seed") in (None, ""):
        meta["seed"] = image_data.get("seed")
    if image_data.get("sampler") and not meta.get("sampler"):
        meta["sampler"] = image_data.get("sampler")
    generation_data["meta"] = meta

    if not isinstance(generation_data.get("resources"), list):
        generation_data["resources"] = []

    basic_info = _dict_payload(merged_payload.get("raw_basic_info"))
    if not basic_info:
        basic_info = {
            "id": image.id,
            "mimeType": image.mimetype,
            "width": image.width,
            "height": image.height,
            "url": image.source_url,
        }

    normalized = _build_civitai_normalized_preview(
        image.id,
        basic_info,
        generation_data,
        image_data,
    )
    processes = _list_payload(
        normalized.get("processes") if isinstance(normalized, dict) else []
    )

    for process in processes:
        if not isinstance(process, dict):
            continue
        process["source_type"] = "local_metadata"
        process["source_label"] = "prototype_local_fallback"
        provenance_records = _list_payload(process.get("provenance_records"))
        for record in provenance_records:
            if isinstance(record, dict):
                record["source_type"] = "local_metadata"
                record["source_label"] = "sidecar/json_metadata/exif"

    return processes


def _build_civitai_validation_payload(
    basic_info: dict, generation_data: dict, normalized: dict
) -> dict:
    warnings: list[str] = []
    errors: list[str] = []
    meta = generation_data.get("meta") if isinstance(generation_data, dict) else {}
    if not isinstance(meta, dict):
        meta = {}
    processes = _list_payload(
        normalized.get("processes") if isinstance(normalized, dict) else []
    )
    first_process = processes[0] if processes else {}
    stages = _list_payload(first_process.get("stages"))
    first_stage = stages[0] if stages else {}
    compatibility_json = {}
    for stage in reversed(stages):
        stage_compatibility = _dict_payload(stage.get("compatibility_json"))
        if stage_compatibility.get("output_width") and stage_compatibility.get(
            "output_height"
        ):
            compatibility_json = stage_compatibility
            break
    if not basic_info:
        errors.append("The image.get endpoint returned no payload.")
    if not generation_data:
        warnings.append("The image.getGenerationData endpoint returned no payload.")
    if not str(meta.get("prompt") or "").strip():
        warnings.append(
            "Positive prompt is missing from the fetched generation metadata."
        )
    if meta.get("seed") in {None, ""} and not first_stage.get("seed"):
        warnings.append("Seed is missing from the fetched generation metadata.")
    has_generation_dimensions = bool(
        first_stage.get("width") and first_stage.get("height")
    )
    has_output_dimensions = bool(
        compatibility_json.get("output_width")
        and compatibility_json.get("output_height")
    )
    if not has_generation_dimensions and not has_output_dimensions:
        warnings.append(
            "Width and height are incomplete in the fetched generation metadata."
        )
    raw_resources = (
        generation_data.get("resources") if isinstance(generation_data, dict) else []
    )
    if not isinstance(raw_resources, list) or not raw_resources:
        warnings.append(
            "No generation resources were returned by CivitAI for this item."
        )
    if not processes:
        errors.append("The prototype could not build a normalized generation preview.")
    return _summarize_validation(warnings, errors)


def _build_local_generation_validation_payload(
    image: ImageModel,
    sidecar_payload: dict,
    merged_payload: dict,
    serialized_processes: list[dict],
    *,
    used_fallback_preview: bool,
) -> dict:
    warnings: list[str] = []
    errors: list[str] = []
    db_json = _dict_payload(image.json_metadata)
    exif_payload = _dict_payload(image.exif_data)
    source_url = str(image.source_url or "").strip()
    source_variant = sidecar_payload.get("civitai_source_variant") or db_json.get(
        "civitai_source_variant"
    )

    if not serialized_processes and not used_fallback_preview:
        warnings.append(
            "No normalized generation process records have been stored for this image yet."
        )
    if used_fallback_preview:
        warnings.append(
            "No persisted normalized generation process records exist; showing a metadata-derived preview."
        )
    if not sidecar_payload and not db_json and not exif_payload:
        errors.append(
            "No sidecar JSON, json_metadata, or EXIF payload is available for this image."
        )
    if (
        source_url
        and ("civitai.com/images/" in source_url or "civitai.red/images/" in source_url)
        and not isinstance(
            sidecar_payload.get("civitai") or db_json.get("civitai"), dict
        )
    ):
        warnings.append(
            "Image has a CivitAI source URL but no cached CivitAI metadata payload is stored locally."
        )
    if not str(_read_generation_software_for_image(image) or "").strip():
        warnings.append(
            "No generation_software summary is currently available for this image."
        )
    if isinstance(source_variant, dict):
        variant_reason = str(
            source_variant.get("reason") or "source_variant_present"
        ).strip()
        warnings.append(
            f"CivitAI source variant metadata is present: {variant_reason}."
        )
    if not isinstance(merged_payload.get("civitai"), dict) and not serialized_processes:
        warnings.append(
            "Prototype view has no API-derived generation block to compare against persisted records."
        )

    return _summarize_validation(warnings, errors)


def _build_local_generation_overview(
    image: ImageModel,
    sidecar_payload: dict,
    serialized_processes: list[dict],
) -> dict:
    stage_count = sum(
        len(process.get("stages", [])) for process in serialized_processes
    )
    return {
        "image_db_id": image.id,
        "file_hash": image.file_hash,
        "file_name": image.file_name,
        "file_path": image.file_path,
        "mimetype": image.mimetype,
        "source_site": image.source_site,
        "source_url": _reconstruct_source_url(image.source_url),
        "generation_software": _read_generation_software_for_image(image),
        "has_sidecar": bool(sidecar_payload),
        "has_exif_data": bool(_dict_payload(image.exif_data)),
        "has_json_metadata": bool(_dict_payload(image.json_metadata)),
        "process_count": len(serialized_processes),
        "stage_count": stage_count,
        "image_status": image.image_status,
    }


def _build_civitai_generation_overview(
    image_id: int, prepared: dict, normalized: dict
) -> dict:
    processes = _list_payload(
        normalized.get("processes") if isinstance(normalized, dict) else []
    )
    first_process = processes[0] if processes else {}
    stages = _list_payload(first_process.get("stages"))
    first_stage = stages[0] if stages else {}
    compatibility_json = {}
    for stage in reversed(stages):
        stage_compatibility = _dict_payload(stage.get("compatibility_json"))
        if stage_compatibility.get("output_width") and stage_compatibility.get(
            "output_height"
        ):
            compatibility_json = stage_compatibility
            break
    stage_count = 0
    for process in processes:
        stages = process.get("stages")
        if isinstance(stages, list):
            stage_count += len(stages)
    return {
        "image_id": image_id,
        "source_url": _reconstruct_source_url(prepared.get("source_url")),
        "image_url": prepared.get("image_url"),
        "mime_type": prepared.get("mime_type"),
        "original_filename": prepared.get("original_filename"),
        "artist_name": prepared.get("artist_name"),
        "process_count": len(processes),
        "stage_count": stage_count,
        "platform_name": first_process.get("platform_name"),
        "method_family": first_process.get("method_family"),
        "sampler_name": first_stage.get("sampler_name"),
        "output_dimensions": (
            f"{compatibility_json.get('output_width')}x{compatibility_json.get('output_height')}"
            if compatibility_json.get("output_width")
            and compatibility_json.get("output_height")
            else None
        ),
        "dimensions": (
            f"{first_stage.get('width')}x{first_stage.get('height')}"
            if first_stage.get("width") and first_stage.get("height")
            else None
        ),
    }


def _build_generation_prototype_civitai_payload(image_id: int) -> dict:
    api = CivitaiAPI.get_instance()
    try:
        prepared = _resolve_civitai_image_target(api, image_id)
    except _CivitaiImageUnavailableError as exc:
        validation = _summarize_validation(
            warnings=[
                f"CivitAI could not resolve image {image_id} via {exc.endpoint}."
            ],
            errors=[exc.reason],
        )
        return {
            "ok": False,
            "mode": "civitai",
            "target": {
                "image_id": image_id,
                "source_url": _reconstruct_source_url(exc.source_url),
            },
            "overview": {
                "image_id": image_id,
                "source_url": _reconstruct_source_url(exc.source_url),
            },
            "raw": {
                "basic_info": None,
                "generation_data": None,
            },
            "normalized": {
                "processes": [],
            },
            "validation": validation,
            "error": {
                "type": "civitai_image_unavailable",
                "endpoint": exc.endpoint,
                "status_code": exc.status_code,
                "reason": exc.reason,
                "source_url": _reconstruct_source_url(exc.source_url),
            },
        }

    basic_info = api.fetch_basic_info_cached(image_id, strict=False) or {}
    generation_data = api.fetch_generation_data_cached(image_id, strict=False) or {}
    image_data = CivitaiImage.from_single_image(
        basic_info=basic_info or {"id": image_id},
        generation_data=generation_data,
        api=api,
    ).to_dict(include_full_url=True)
    normalized = _build_civitai_normalized_preview(
        image_id, basic_info, generation_data, image_data
    )
    validation = _build_civitai_validation_payload(
        basic_info, generation_data, normalized
    )

    return {
        "ok": validation.get("status") != "error",
        "mode": "civitai",
        "target": {
            "image_id": image_id,
            "source_url": _reconstruct_source_url(prepared.get("source_url")),
        },
        "overview": _build_civitai_generation_overview(image_id, prepared, normalized),
        "raw": {
            "basic_info": basic_info,
            "generation_data": generation_data,
            "prepared_import_target": prepared,
            "image_data": image_data,
        },
        "normalized": normalized,
        "validation": validation,
        "error": None,
    }


def _build_generation_prototype_local_payload(image: ImageModel) -> dict:
    db_payload = ImageData.from_db_record(image).to_dict()
    sidecar_payload = _read_image_sidecar_payload(image)
    merged_payload = _normalize_merged_image_payload(
        image,
        db_payload=db_payload,
        merged_payload={**db_payload, **sidecar_payload},
    )
    serialized_processes = [
        _serialize_generation_process(item) for item in image.generation_processes
    ]
    used_fallback_preview = False
    if not serialized_processes:
        fallback_processes = _build_local_fallback_normalized_preview(
            image,
            merged_payload,
            _dict_payload(image.exif_data),
        )
        if fallback_processes:
            serialized_processes = fallback_processes
            used_fallback_preview = True
    validation = _build_local_generation_validation_payload(
        image,
        sidecar_payload,
        merged_payload,
        serialized_processes,
        used_fallback_preview=used_fallback_preview,
    )
    return {
        "ok": validation.get("status") != "error",
        "mode": "local",
        "target": {
            "file_hash": image.file_hash,
            "image_db_id": image.id,
            "source_url": _reconstruct_source_url(image.source_url),
        },
        "overview": _build_local_generation_overview(
            image, sidecar_payload, serialized_processes
        ),
        "raw": {
            "db": db_payload,
            "merged": merged_payload,
            "sidecar": sidecar_payload,
            "json_metadata": image.json_metadata,
            "exif_data": image.exif_data,
            "fallback_normalized_preview_used": used_fallback_preview,
        },
        "normalized": {
            "processes": serialized_processes,
        },
        "validation": validation,
        "error": None,
    }


def _parse_json_container(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if not (
        (text.startswith("{") and text.endswith("}"))
        or (text.startswith("[") and text.endswith("]"))
    ):
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _collect_nested_comfy_candidates(value: Any, *, max_depth: int = 3) -> list[Any]:
    candidates: list[Any] = []
    visited: set[int] = set()

    interesting_keys = (
        "workflow",
        "Workflow",
        "prompt",
        "Prompt",
        "comfy",
        "Comfy",
        "comfyui",
        "ComfyUI",
        "ui",
        "graph",
    )

    def walk(node: Any, depth: int) -> None:
        if depth < 0:
            return
        node_id = id(node)
        if node_id in visited:
            return
        visited.add(node_id)

        parsed = _parse_json_container(node)
        if parsed is not None and parsed is not node:
            candidates.append(parsed)
            walk(parsed, depth - 1)
            return

        if isinstance(node, dict):
            candidates.append(node)
            for key in interesting_keys:
                if key in node:
                    candidates.append(node.get(key))
                    walk(node.get(key), depth - 1)
            for nested in node.values():
                if isinstance(nested, (dict, list, str)):
                    walk(nested, depth - 1)
            return

        if isinstance(node, list):
            candidates.append(node)
            for nested in node:
                if isinstance(nested, (dict, list, str)):
                    walk(nested, depth - 1)

    walk(value, max_depth)
    return [item for item in candidates if item is not None]


def _looks_like_comfy_workflow_ui(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    nodes = payload.get("nodes")
    if not isinstance(nodes, list):
        return False
    if "links" in payload and isinstance(payload.get("links"), list):
        return True
    if payload.get("last_node_id") is not None:
        return True
    if payload.get("version") is not None:
        return True
    return False


def _normalize_comfy_workflow_ui_graph(
    payload: Any,
) -> tuple[Optional[dict], list[str]]:
    warnings: list[str] = []
    if not isinstance(payload, dict):
        return None, warnings
    nodes = payload.get("nodes")
    if not isinstance(nodes, list):
        return None, warnings

    # Detach from source structures so we can safely enforce Comfy workspace defaults.
    try:
        normalized: dict[str, Any] = json.loads(json.dumps(payload))
    except (TypeError, ValueError):
        normalized = dict(payload)

    normalized_nodes = normalized.get("nodes")
    if not isinstance(normalized_nodes, list):
        normalized_nodes = []
    for index, node in enumerate(normalized_nodes):
        if not isinstance(node, dict):
            continue
        node.setdefault("flags", {})
        node.setdefault("mode", 0)
        node.setdefault("order", index)
        node.setdefault("properties", {})

    normalized["nodes"] = normalized_nodes
    normalized.setdefault("id", "00000000-0000-0000-0000-000000000000")
    normalized.setdefault("revision", 0)
    normalized.setdefault("groups", [])
    normalized.setdefault("config", {})
    normalized.setdefault("extra", {})
    normalized.setdefault("version", 0.4)

    links = normalized.get("links")
    if not isinstance(links, list):
        normalized["links"] = []
        links = normalized["links"]

    if normalized.get("last_node_id") is None:
        numeric_node_ids = [
            int(node.get("id"))
            for node in normalized_nodes
            if isinstance(node, dict)
            and _coerce_optional_int(node.get("id")) is not None
        ]
        if numeric_node_ids:
            normalized["last_node_id"] = max(numeric_node_ids)
            warnings.append(
                "Comfy workflow missing last_node_id; inferred from node ids."
            )

    if normalized.get("last_link_id") is None:
        numeric_link_ids: list[int] = []
        for link in links:
            if isinstance(link, (list, tuple)) and link:
                first = _coerce_optional_int(link[0])
                if first is not None:
                    numeric_link_ids.append(first)
        if numeric_link_ids:
            normalized["last_link_id"] = max(numeric_link_ids)
            warnings.append(
                "Comfy workflow missing last_link_id; inferred from link ids."
            )

    return normalized, warnings


def _extract_comfy_workflow_ui_graph(
    workflow_payload: Any, meta: dict
) -> Optional[dict]:
    comfy_payload = meta.get("comfy") if isinstance(meta.get("comfy"), dict) else None
    candidates: list[Any] = []
    if comfy_payload:
        candidates.extend(
            [
                comfy_payload.get("workflow"),
                comfy_payload.get("ui"),
                comfy_payload,
            ]
        )
    candidates.extend(
        [
            workflow_payload,
            (
                workflow_payload.get("workflow")
                if isinstance(workflow_payload, dict)
                else None
            ),
            workflow_payload.get("ui") if isinstance(workflow_payload, dict) else None,
        ]
    )
    candidates.extend(_collect_nested_comfy_candidates(workflow_payload, max_depth=3))
    candidates.extend(_collect_nested_comfy_candidates(meta, max_depth=3))

    for candidate in candidates:
        parsed = _parse_json_container(candidate)
        candidate_value = parsed if parsed is not None else candidate
        if _looks_like_comfy_workflow_ui(candidate_value):
            return candidate_value
    return None


def _extract_comfy_graphs_from_generation_payload(
    generation_payload: dict,
) -> tuple[Optional[dict], Optional[dict], list[str]]:
    warnings: list[str] = []
    mode = str(generation_payload.get("mode") or "inspection").strip().lower()

    if mode == "civitai":
        raw = _dict_payload(generation_payload.get("raw"))
        generation_data = _dict_payload(raw.get("generation_data"))
        meta = _dict_payload(generation_data.get("meta"))
        image_data = _dict_payload(raw.get("image_data"))
        workflow_payload = _resolve_civitai_workflow_payload(meta, image_data)
        prompt_graph = _extract_comfy_prompt_graph(workflow_payload, meta)
        workflow_ui = _extract_comfy_workflow_ui_graph(workflow_payload, meta)
        return (prompt_graph or None, workflow_ui, warnings)

    processes = _list_payload(
        _dict_payload(generation_payload.get("normalized")).get("processes")
    )
    raw = _dict_payload(generation_payload.get("raw"))
    merged = _dict_payload(raw.get("merged"))
    civitai_merged = _dict_payload(merged.get("civitai"))

    candidates: list[Any] = []
    for process in processes:
        if not isinstance(process, dict):
            continue
        candidates.append(process.get("workflow_json"))
        raw_payload = _dict_payload(process.get("raw_payload_json"))
        generation_data = _dict_payload(raw_payload.get("generation_data"))
        meta = _dict_payload(generation_data.get("meta"))
        candidates.extend(
            [
                raw_payload.get("workflow"),
                raw_payload.get("prompt"),
                generation_data.get("workflow"),
                meta.get("workflow"),
                meta.get("comfy"),
            ]
        )

    raw_db = _dict_payload(raw.get("db"))
    raw_sidecar = _dict_payload(raw.get("sidecar"))
    raw_json_metadata = _dict_payload(raw.get("json_metadata"))
    raw_exif = _dict_payload(raw.get("exif_data"))

    candidates.extend(
        [
            merged.get("workflow"),
            merged.get("prompt"),
            merged.get("Prompt"),
            merged.get("UserComment"),
            merged.get("comfy"),
            civitai_merged.get("workflow"),
            _dict_payload(civitai_merged.get("meta")).get("comfy"),
            raw_db,
            raw_sidecar,
            raw_json_metadata,
            raw_exif,
        ]
    )
    candidates.extend(_collect_nested_comfy_candidates(merged, max_depth=4))
    candidates.extend(_collect_nested_comfy_candidates(raw_db, max_depth=4))
    candidates.extend(_collect_nested_comfy_candidates(raw_sidecar, max_depth=4))
    candidates.extend(_collect_nested_comfy_candidates(raw_json_metadata, max_depth=4))
    candidates.extend(_collect_nested_comfy_candidates(raw_exif, max_depth=4))

    prompt_graph: Optional[dict] = None
    workflow_ui: Optional[dict] = None
    for candidate in candidates:
        parsed = _parse_json_container(candidate)
        candidate_value = parsed if parsed is not None else candidate
        if prompt_graph is None:
            resolved_prompt = _extract_comfy_prompt_graph(candidate_value, {})
            if resolved_prompt:
                prompt_graph = resolved_prompt
        if workflow_ui is None:
            resolved_ui = _extract_comfy_workflow_ui_graph(candidate_value, {})
            if resolved_ui:
                workflow_ui = resolved_ui
        if prompt_graph is not None and workflow_ui is not None:
            break

    return (prompt_graph, workflow_ui, warnings)


def _build_fallback_comfy_prompt_graph_from_generation_payload(
    generation_payload: dict,
    *,
    local_catalog: Optional[dict] = None,
) -> tuple[Optional[dict], list[str]]:
    warnings: list[str] = []
    normalized = _dict_payload(generation_payload.get("normalized"))
    processes = _list_payload(normalized.get("processes"))
    if not processes or not isinstance(processes[0], dict):
        return None, [
            "No normalized process rows were available to synthesize a Comfy prompt graph."
        ]

    process = processes[0]
    stages = _list_payload(process.get("stages"))
    stage = stages[0] if stages and isinstance(stages[0], dict) else {}

    resources = _list_payload(stage.get("resources")) + _list_payload(
        process.get("resources")
    )
    resources = [item for item in resources if isinstance(item, dict)]
    checkpoint = next(
        (
            item
            for item in resources
            if str(item.get("resource_type") or "").strip().lower() == "checkpoint"
        ),
        None,
    )
    loras = [
        item
        for item in resources
        if str(item.get("resource_type") or "").strip().lower() == "lora"
    ]
    vae_resource = next(
        (
            item
            for item in resources
            if str(item.get("resource_type") or "").strip().lower() == "vae"
        ),
        None,
    )

    stage_prompts = _list_payload(stage.get("prompts")) + _list_payload(
        process.get("prompts")
    )
    positive_prompt = ""
    negative_prompt = ""
    for prompt in stage_prompts:
        if not isinstance(prompt, dict):
            continue
        role = str(prompt.get("prompt_role") or "").strip().lower()
        text_value = str(prompt.get("prompt_text") or "").strip()
        if role == "positive" and text_value and not positive_prompt:
            positive_prompt = text_value
        elif role == "negative" and text_value and not negative_prompt:
            negative_prompt = text_value

    prompt_lora_names: list[str] = []
    if positive_prompt:
        for match in re.finditer(
            r"<lora:([^:>]+)(?::[^>]+)?>", positive_prompt, flags=re.IGNORECASE
        ):
            candidate = str(match.group(1) or "").strip()
            if not candidate:
                continue
            if "." not in candidate:
                candidate = f"{candidate}.safetensors"
            prompt_lora_names.append(candidate)

    sanitized_positive_prompt, removed_rp_directives = (
        _sanitize_a1111_positive_prompt_for_comfy(positive_prompt)
    )
    if removed_rp_directives:
        warnings.append(
            "Removed A1111 Regional Prompter directives from fallback Comfy positive prompt because RP emulation is not enabled: "
            + ", ".join(removed_rp_directives)
        )
    positive_prompt = sanitized_positive_prompt

    raw_payload = _dict_payload(generation_payload.get("raw"))
    merged_payload = _dict_payload(raw_payload.get("merged"))
    exif_candidates = [
        _dict_payload(raw_payload.get("exif_data_fresh")),
        _dict_payload(raw_payload.get("exif_data")),
        _dict_payload(merged_payload.get("exif_data_fresh")),
        _dict_payload(merged_payload.get("exif_data")),
        _dict_payload(_dict_payload(raw_payload.get("sidecar")).get("exif_data")),
        _dict_payload(_dict_payload(raw_payload.get("db")).get("exif_data")),
    ]

    def first_exif_value(*keys: str) -> Any:
        for exif_candidate in exif_candidates:
            if not isinstance(exif_candidate, dict):
                continue
            lowered = {str(k).strip().lower(): v for k, v in exif_candidate.items()}
            for key in keys:
                value = lowered.get(str(key).strip().lower())
                if value not in (None, ""):
                    return value
        return None

    width = _coerce_optional_int(stage.get("width"))
    height = _coerce_optional_int(stage.get("height"))
    if width is None:
        width = _coerce_optional_int(first_exif_value("Width", "ImageWidth"))
    if height is None:
        height = _coerce_optional_int(first_exif_value("Height", "ImageHeight"))
    size_value = str(first_exif_value("Size") or "").strip()
    if size_value and (width is None or height is None):
        size_match = re.match(r"^\s*(\d+)\s*[xX]\s*(\d+)\s*$", size_value)
        if size_match:
            width = width or int(size_match.group(1))
            height = height or int(size_match.group(2))
    width = width or 1024
    height = height or 1024

    seed_value = _coerce_optional_int(stage.get("seed")) or 0
    steps_value = _coerce_optional_int(stage.get("steps")) or 24
    cfg_value = _coerce_optional_float(stage.get("cfg_scale")) or 7.0

    def normalize_sampler_name(value: Any) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return "euler"
        sampler_map = {
            "euler a": "euler",
            "euler_ancestral": "euler",
            "euler ancestral": "euler",
            "dpm++ 2m": "dpmpp_2m",
            "dpm++ 2m karras": "dpmpp_2m",
            "dpmpp 2m": "dpmpp_2m",
            "dpmpp 2m karras": "dpmpp_2m",
        }
        return sampler_map.get(text, text)

    def normalize_scheduler_name(value: Any) -> str:
        text = str(value or "").strip().lower()
        if not text:
            return "normal"
        scheduler_map = {
            "automatic": "normal",
            "auto": "normal",
            "karras": "karras",
            "normal": "normal",
            "simple": "simple",
            "sgm_uniform": "sgm_uniform",
            "ddim": "ddim_uniform",
            "ddim uniform": "ddim_uniform",
            "beta": "beta",
            "beta57": "beta57",
            "exponential": "exponential",
            "linear_quadratic": "linear_quadratic",
            "kl_optimal": "kl_optimal",
            "bong_tangent": "bong_tangent",
        }
        return scheduler_map.get(text, "normal")

    def looks_like_model_filename(value: Any) -> bool:
        text = str(value or "").strip()
        if not text:
            return False
        lowered = text.lower()
        if re.fullmatch(r"\d+", lowered):
            return False
        if any(
            token in lowered
            for token in (".safetensors", ".ckpt", ".pt", ".pth", "/", "\\")
        ):
            return True
        return False

    sampler_name = normalize_sampler_name(
        stage.get("sampler_name") or first_exif_value("Sampler")
    )
    scheduler_name = normalize_scheduler_name(
        stage.get("scheduler_name") or first_exif_value("Scheduler", "Schedule type")
    )

    catalog_entries = [
        item
        for item in _list_payload(_dict_payload(local_catalog).get("entries"))
        if isinstance(item, dict)
    ]

    def _catalog_source_identifier(entry: dict) -> Optional[str]:
        source_identifier = str(entry.get("source_identifier") or "").strip()
        if not source_identifier:
            return None
        return source_identifier.replace("\\", "/")

    def basename_only(value: Any) -> str:
        text = str(value or "").strip().replace("\\", "/")
        if not text:
            return ""
        return text.rsplit("/", 1)[-1].strip()

    def resolve_catalog_identifier(
        *,
        resource_type: str,
        version_id: Optional[int],
        model_id: Optional[int],
        name_candidates: list[Any],
    ) -> Optional[str]:
        if not catalog_entries:
            return None

        type_key = str(resource_type or "").strip().lower()
        normalized_candidates = {
            str(model_reference_service.normalize_name_key(candidate) or "")
            .strip()
            .lower()
            for candidate in name_candidates
            if str(candidate or "").strip()
        }
        normalized_candidates = {item for item in normalized_candidates if item}

        compact_candidates = {
            re.sub(r"[^a-z0-9]+", "", candidate.lower())
            for candidate in normalized_candidates
            if candidate
        }
        compact_candidates = {item for item in compact_candidates if item}

        compatible_entries = [
            entry
            for entry in catalog_entries
            if model_reference_service._types_match(
                type_key, str(entry.get("resource_type") or "other")
            )
        ]

        if version_id is not None:
            for entry in compatible_entries:
                if (
                    _coerce_optional_int(entry.get("civitai_model_version_id"))
                    == version_id
                ):
                    resolved = _catalog_source_identifier(entry)
                    if resolved:
                        return resolved

        if model_id is not None:
            for entry in compatible_entries:
                if _coerce_optional_int(entry.get("civitai_model_id")) == model_id:
                    resolved = _catalog_source_identifier(entry)
                    if resolved:
                        return resolved

        if normalized_candidates:
            for entry in compatible_entries:
                entry_name = str(entry.get("normalized_name") or "").strip().lower()
                if entry_name and entry_name in normalized_candidates:
                    resolved = _catalog_source_identifier(entry)
                    if resolved:
                        return resolved

        if compact_candidates:
            for entry in compatible_entries:
                entry_candidates = {
                    str(entry.get("normalized_name") or "").strip().lower(),
                    str(
                        model_reference_service.normalize_name_key(
                            entry.get("display_name")
                        )
                        or ""
                    )
                    .strip()
                    .lower(),
                    str(
                        model_reference_service.normalize_name_key(
                            entry.get("source_identifier")
                        )
                        or ""
                    )
                    .strip()
                    .lower(),
                }
                entry_candidates = {item for item in entry_candidates if item}
                entry_compact_candidates = {
                    re.sub(r"[^a-z0-9]+", "", item.lower())
                    for item in entry_candidates
                    if item
                }
                entry_compact_candidates = {
                    item for item in entry_compact_candidates if item
                }
                if compact_candidates & entry_compact_candidates:
                    resolved = _catalog_source_identifier(entry)
                    if resolved:
                        return resolved
        return None

    def resolve_identifier(resource: Optional[dict], fallback: str) -> str:
        if not isinstance(resource, dict):
            return fallback
        # Prefer concrete filenames/paths over opaque numeric identifiers.
        preferred_candidates = [
            resource.get("source_identifier"),
            resource.get("display_name"),
            resource.get("normalized_name"),
            resource.get("version_name"),
        ]
        for candidate in preferred_candidates:
            if looks_like_model_filename(candidate):
                return str(candidate).strip()
        for key in (
            "display_name",
            "normalized_name",
            "version_name",
            "source_identifier",
        ):
            value = str(resource.get(key) or "").strip()
            if value and not re.fullmatch(r"\d+", value):
                return value if "." in value else f"{value}.safetensors"
        return fallback

    checkpoint_name = resolve_identifier(checkpoint, "MISSING_CHECKPOINT.safetensors")
    exif_model = str(first_exif_value("Model") or "").strip()
    if exif_model:
        checkpoint_name = (
            exif_model if "." in exif_model else f"{exif_model}.safetensors"
        )

    checkpoint_catalog_identifier = resolve_catalog_identifier(
        resource_type="checkpoint",
        version_id=_coerce_optional_int(
            _dict_payload(checkpoint).get("civitai_model_version_id")
        ),
        model_id=_coerce_optional_int(
            _dict_payload(checkpoint).get("civitai_model_id")
        ),
        name_candidates=[
            checkpoint_name,
            exif_model,
            _dict_payload(checkpoint).get("display_name"),
            _dict_payload(checkpoint).get("version_name"),
            _dict_payload(checkpoint).get("source_identifier"),
        ],
    )
    if checkpoint_catalog_identifier:
        checkpoint_name = checkpoint_catalog_identifier
    else:
        # Fallback stays filename-only so users can resolve manually in ComfyUI.
        checkpoint_name = basename_only(checkpoint_name) or checkpoint_name

    if re.fullmatch(r"\d+", checkpoint_name):
        checkpoint_name = f"{checkpoint_name}.safetensors"
    if not checkpoint:
        warnings.append(
            "No checkpoint reference was extracted; using placeholder checkpoint in fallback prompt graph."
        )

    node_counter = 1
    nodes: dict[str, Any] = {}

    checkpoint_node_id = str(node_counter)
    nodes[checkpoint_node_id] = {
        "class_type": "CheckpointLoaderSimple",
        "inputs": {
            "ckpt_name": checkpoint_name,
        },
    }
    node_counter += 1

    current_model_ref = [checkpoint_node_id, 0]
    current_clip_ref = [checkpoint_node_id, 1]
    for index, lora in enumerate(loras):
        lora_node_id = str(node_counter)
        node_counter += 1
        lora_name = resolve_identifier(lora, "MISSING_LORA.safetensors")
        if index < len(prompt_lora_names):
            lora_name = prompt_lora_names[index]

        lora_catalog_identifier = resolve_catalog_identifier(
            resource_type="lora",
            version_id=_coerce_optional_int(
                _dict_payload(lora).get("civitai_model_version_id")
            ),
            model_id=_coerce_optional_int(_dict_payload(lora).get("civitai_model_id")),
            name_candidates=[
                lora_name,
                _dict_payload(lora).get("display_name"),
                _dict_payload(lora).get("version_name"),
                _dict_payload(lora).get("source_identifier"),
                prompt_lora_names[index] if index < len(prompt_lora_names) else None,
            ],
        )
        if lora_catalog_identifier:
            lora_name = lora_catalog_identifier
        else:
            # Fallback stays filename-only so users can resolve manually in ComfyUI.
            lora_name = basename_only(lora_name) or lora_name

        if re.fullmatch(r"\d+", str(lora_name)):
            lora_name = f"{lora_name}.safetensors"
        strength_model = _coerce_optional_float(lora.get("strength_model"))
        strength_clip = _coerce_optional_float(lora.get("strength_clip"))
        nodes[lora_node_id] = {
            "class_type": "LoraLoader",
            "inputs": {
                "lora_name": lora_name,
                "strength_model": strength_model if strength_model is not None else 1.0,
                "strength_clip": strength_clip if strength_clip is not None else 1.0,
                "model": current_model_ref,
                "clip": current_clip_ref,
            },
        }
        current_model_ref = [lora_node_id, 0]
        current_clip_ref = [lora_node_id, 1]

    positive_node_id = str(node_counter)
    nodes[positive_node_id] = {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "text": positive_prompt,
            "clip": current_clip_ref,
        },
    }
    node_counter += 1

    negative_node_id = str(node_counter)
    nodes[negative_node_id] = {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "text": negative_prompt,
            "clip": current_clip_ref,
        },
    }
    node_counter += 1

    latent_node_id = str(node_counter)
    nodes[latent_node_id] = {
        "class_type": "EmptyLatentImage",
        "inputs": {
            "width": width,
            "height": height,
            "batch_size": 1,
        },
    }
    node_counter += 1

    sampler_node_id = str(node_counter)
    nodes[sampler_node_id] = {
        "class_type": "KSampler",
        "inputs": {
            "seed": seed_value,
            "steps": steps_value,
            "cfg": cfg_value,
            "sampler_name": sampler_name,
            "scheduler": scheduler_name,
            "denoise": 1.0,
            "model": current_model_ref,
            "positive": [positive_node_id, 0],
            "negative": [negative_node_id, 0],
            "latent_image": [latent_node_id, 0],
        },
    }
    node_counter += 1

    vae_ref = [checkpoint_node_id, 2]
    if vae_resource:
        vae_node_id = str(node_counter)
        node_counter += 1
        vae_name = resolve_identifier(vae_resource, "MISSING_VAE.safetensors")
        nodes[vae_node_id] = {
            "class_type": "VAELoader",
            "inputs": {
                "vae_name": vae_name,
            },
        }
        vae_ref = [vae_node_id, 0]

    decode_node_id = str(node_counter)
    nodes[decode_node_id] = {
        "class_type": "VAEDecode",
        "inputs": {
            "samples": [sampler_node_id, 0],
            "vae": vae_ref,
        },
    }
    node_counter += 1

    save_node_id = str(node_counter)
    nodes[save_node_id] = {
        "class_type": "SaveImage",
        "inputs": {
            "filename_prefix": "AtelierAI",
            "images": [decode_node_id, 0],
        },
    }

    warnings.append(
        "Comfy prompt graph was synthesized from normalized generation fields because no embedded prompt graph was found."
    )
    return nodes, warnings


def _build_fallback_comfy_workflow_ui_from_prompt_graph(
    prompt_graph: dict,
) -> tuple[Optional[dict], list[str]]:
    warnings: list[str] = []
    if not isinstance(prompt_graph, dict) or not prompt_graph:
        return None, warnings

    normalized_node_ids: list[int] = []
    for raw_node_id in prompt_graph.keys():
        coerced = _coerce_optional_int(raw_node_id)
        if coerced is not None:
            normalized_node_ids.append(coerced)
    if not normalized_node_ids:
        return None, [
            "Prompt graph node ids were not numeric; could not synthesize Comfy workflow UI graph."
        ]

    sorted_node_ids = sorted(set(normalized_node_ids))
    node_id_to_col: dict[int, int] = {
        node_id: index for index, node_id in enumerate(sorted_node_ids)
    }

    links: list[list[Any]] = []
    next_link_id = 1
    outgoing_by_node_slot: dict[tuple[int, int], list[int]] = {}

    workflow_nodes: list[dict[str, Any]] = []
    for order_index, node_id in enumerate(sorted_node_ids):
        node_payload = _dict_payload(
            prompt_graph.get(str(node_id)) or prompt_graph.get(node_id)
        )
        class_type = (
            str(
                node_payload.get("class_type")
                or node_payload.get("type")
                or "UnknownNode"
            ).strip()
            or "UnknownNode"
        )
        raw_inputs = _dict_payload(node_payload.get("inputs"))

        input_ports: list[dict[str, Any]] = []
        literal_inputs: dict[str, Any] = {}

        for input_name, input_value in raw_inputs.items():
            input_name_text = str(input_name)
            if isinstance(input_value, (list, tuple)) and len(input_value) >= 2:
                source_node_id = _coerce_optional_int(input_value[0])
                source_slot = _coerce_optional_int(input_value[1]) or 0
                if source_node_id is None:
                    literal_inputs[input_name_text] = input_value
                    continue

                link_id = next_link_id
                next_link_id += 1
                target_input_index = len(input_ports)
                input_ports.append(
                    {
                        "name": input_name_text,
                        "type": "*",
                        "link": link_id,
                    }
                )
                links.append(
                    [
                        link_id,
                        source_node_id,
                        source_slot,
                        node_id,
                        target_input_index,
                        "*",
                    ]
                )
                outgoing_by_node_slot.setdefault(
                    (source_node_id, source_slot), []
                ).append(link_id)
            else:
                literal_inputs[input_name_text] = input_value

        class_key = class_type.lower()
        if class_key == "checkpointloadersimple":
            widget_values = [literal_inputs.get("ckpt_name", "")]
        elif class_key == "loraloader":
            widget_values = [
                literal_inputs.get("lora_name", ""),
                (
                    _coerce_optional_float(literal_inputs.get("strength_model"))
                    if literal_inputs.get("strength_model") is not None
                    else 1.0
                ),
                (
                    _coerce_optional_float(literal_inputs.get("strength_clip"))
                    if literal_inputs.get("strength_clip") is not None
                    else 1.0
                ),
            ]
        elif class_key == "cliptextencode":
            widget_values = [literal_inputs.get("text", "")]
        elif class_key == "emptylatentimage":
            widget_values = [
                _coerce_optional_int(literal_inputs.get("width")) or 1024,
                _coerce_optional_int(literal_inputs.get("height")) or 1024,
                _coerce_optional_int(literal_inputs.get("batch_size")) or 1,
            ]
        elif class_key == "ksampler":
            sampler = str(literal_inputs.get("sampler_name") or "euler").strip().lower()
            scheduler = str(literal_inputs.get("scheduler") or "normal").strip().lower()
            widget_values = [
                _coerce_optional_int(literal_inputs.get("seed")) or 0,
                "randomize",
                _coerce_optional_int(literal_inputs.get("steps")) or 24,
                _coerce_optional_float(literal_inputs.get("cfg")) or 7.0,
                sampler,
                scheduler,
                (
                    _coerce_optional_float(literal_inputs.get("denoise"))
                    if literal_inputs.get("denoise") is not None
                    else 1.0
                ),
            ]
        elif class_key == "saveimage":
            widget_values = [literal_inputs.get("filename_prefix", "AtelierAI")]
        elif class_key == "vaeloader":
            widget_values = [literal_inputs.get("vae_name", "")]
        else:
            widget_values = list(literal_inputs.values())

        inferred_size = [270, 120]
        if class_type.lower() == "cliptextencode":
            inferred_size = [400, 200]
        elif class_type.lower() == "ksampler":
            inferred_size = [270, 262]
        elif class_type.lower() == "emptylatentimage":
            inferred_size = [270, 106]

        workflow_nodes.append(
            {
                "id": node_id,
                "type": class_type,
                "pos": [100 + (node_id_to_col[node_id] * 360), 130],
                "size": inferred_size,
                "flags": {},
                "order": order_index,
                "mode": 0,
                "inputs": input_ports,
                "outputs": [],
                "properties": {
                    "Node name for S&R": class_type,
                },
                "widgets_values": widget_values,
            }
        )

    # Fill outputs after all links are known.
    for node in workflow_nodes:
        node_id = _coerce_optional_int(node.get("id"))
        if node_id is None:
            continue
        output_ports: list[dict[str, Any]] = []
        slot_indices = sorted(
            {
                slot
                for (source_id, slot), _ in outgoing_by_node_slot.items()
                if source_id == node_id
            }
        )
        for slot_index in slot_indices:
            output_ports.append(
                {
                    "name": f"OUT_{slot_index}",
                    "type": "*",
                    "links": outgoing_by_node_slot.get((node_id, slot_index), []),
                }
            )
        node["outputs"] = output_ports

    workflow_payload = {
        "id": "00000000-0000-0000-0000-000000000000",
        "revision": 0,
        "last_node_id": max(sorted_node_ids),
        "last_link_id": next_link_id - 1,
        "nodes": workflow_nodes,
        "links": links,
        "groups": [],
        "config": {},
        "extra": {
            "source": "atelierai_prompt_graph_fallback",
        },
        "version": 0.4,
    }
    warnings.append(
        "Comfy workflow UI graph was synthesized from prompt graph because no embedded workflow UI graph was found."
    )
    return workflow_payload, warnings


def _build_comfy_reference_validation(
    generation_payload: dict, local_catalog: dict
) -> dict:
    references = model_reference_service.extract_references_from_generation_payload(
        generation_payload
    )
    matched_references = model_reference_service.apply_local_catalog_matches(
        references, local_catalog
    )

    available_count = 0
    missing_count = 0
    enriched_references: list[dict] = []
    for reference in matched_references:
        local_matches = _list_payload(reference.get("local_matches"))
        first_match = (
            local_matches[0]
            if local_matches and isinstance(local_matches[0], dict)
            else {}
        )
        local_installed = bool(reference.get("local_installed"))
        if local_installed:
            available_count += 1
        else:
            missing_count += 1
        enriched_references.append(
            {
                **reference,
                "availability": "available" if local_installed else "missing",
                "resolved_model_path": first_match.get("source_identifier"),
                "resolved_match_basis": first_match.get("match_basis"),
                "local_match_count": len(local_matches),
            }
        )

    summary = {
        **model_reference_service._summarize_references(
            enriched_references, local_catalog
        ),
        "available_reference_count": available_count,
        "missing_reference_count": missing_count,
    }
    validation = model_reference_service._build_validation(
        enriched_references, local_catalog, catalog_expected=True
    )

    return {
        "references": enriched_references,
        "summary": summary,
        "validation": validation,
        "local_catalog": {
            "configured": bool(local_catalog.get("configured")),
            "entry_count": len(_list_payload(local_catalog.get("entries"))),
            "sources": _dict_payload(local_catalog.get("sources")),
            "error": local_catalog.get("error"),
        },
    }


def _build_generation_comfy_workspace_export_payload(
    generation_payload: dict, *, local_catalog: dict
) -> dict:
    prompt_graph, workflow_ui, extraction_warnings = (
        _extract_comfy_graphs_from_generation_payload(generation_payload)
    )
    workflow_ui, workflow_normalization_warnings = _normalize_comfy_workflow_ui_graph(
        workflow_ui
    )
    extraction_warnings.extend(workflow_normalization_warnings)
    synthesized_prompt_graph = False
    if not prompt_graph:
        prompt_graph, fallback_warnings = (
            _build_fallback_comfy_prompt_graph_from_generation_payload(
                generation_payload,
                local_catalog=local_catalog,
            )
        )
        extraction_warnings.extend(fallback_warnings)
        synthesized_prompt_graph = bool(prompt_graph)

    synthesized_workflow_ui_graph = False
    if not workflow_ui and prompt_graph:
        workflow_ui, workflow_fallback_warnings = (
            _build_fallback_comfy_workflow_ui_from_prompt_graph(prompt_graph)
        )
        extraction_warnings.extend(workflow_fallback_warnings)
        workflow_ui, workflow_normalization_warnings = (
            _normalize_comfy_workflow_ui_graph(workflow_ui)
        )
        extraction_warnings.extend(workflow_normalization_warnings)
        synthesized_workflow_ui_graph = bool(workflow_ui)

    model_validation = _build_comfy_reference_validation(
        generation_payload, local_catalog
    )
    summary = _dict_payload(model_validation.get("summary"))

    warnings = list(extraction_warnings)
    errors: list[str] = []
    if not prompt_graph:
        errors.append(
            "Unable to build a Comfy prompt API graph from available generation data."
        )
    if not workflow_ui:
        warnings.append(
            "No Comfy UI workflow graph was found; export still includes prompt API graph."
        )
    if int(summary.get("missing_reference_count") or 0) > 0:
        warnings.append(
            "Some referenced models are missing from the configured LoRA Manager catalog."
        )
    if local_catalog.get("error"):
        warnings.append(str(local_catalog.get("error")))

    validation = _summarize_validation(warnings, errors)

    mode = str(generation_payload.get("mode") or "inspection")
    target = _dict_payload(generation_payload.get("target"))
    target_key = str(
        target.get("file_hash")
        or target.get("image_id")
        or target.get("image_db_id")
        or "unknown"
    )

    workspace_bundle = {
        "schema": "atelierai.comfy_workspace_bundle.v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {
            "mode": mode,
            "target": target,
        },
        "comfy_prompt_api": prompt_graph,
        "comfy_workflow_ui": workflow_ui,
        "synthesized_prompt_graph": synthesized_prompt_graph,
        "synthesized_workflow_ui_graph": synthesized_workflow_ui_graph,
    }

    return {
        "ok": validation.get("status") != "error",
        "mode": mode,
        "target": target,
        "overview": {
            "target_key": target_key,
            "prompt_graph_available": bool(prompt_graph),
            "workflow_ui_available": bool(workflow_ui),
            "synthesized_prompt_graph": synthesized_prompt_graph,
            "synthesized_workflow_ui_graph": synthesized_workflow_ui_graph,
            "reference_count": summary.get("reference_count", 0),
            "available_reference_count": summary.get("available_reference_count", 0),
            "missing_reference_count": summary.get("missing_reference_count", 0),
        },
        "workspace_bundle": workspace_bundle,
        "model_validation": model_validation,
        "validation": validation,
        "raw": {
            "source_inspection": generation_payload,
            "local_catalog_fetch": {
                "sources": _dict_payload(local_catalog.get("sources")),
                "error": local_catalog.get("error"),
                "raw_compacted": bool(local_catalog.get("raw_compacted", True)),
                "raw": _dict_payload(local_catalog.get("raw")),
            },
        },
        "error": None,
    }


_TEMPLATE_PLACEHOLDER_RE = re.compile(r"\{\{\s*([a-zA-Z0-9_\.\-\[\]]+)\s*\}\}")


def _clone_json_value(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value))
    except (TypeError, ValueError):
        return value


def _serialize_generation_template(
    template: GenerationTemplate, *, include_workflow: bool
) -> dict[str, Any]:
    workflow_json = _dict_payload(template.workflow_json)
    return {
        "id": template.id,
        "name": str(template.name or ""),
        "description": template.description,
        "mapping_count": len(_list_payload(template.mappings_json)),
        "default_token_count": len(_dict_payload(template.default_tokens_json)),
        "node_count": len(_list_payload(workflow_json.get("nodes"))),
        "created_at": _isoformat_or_none(template.created_at),
        "updated_at": _isoformat_or_none(template.updated_at),
        "mappings": _list_payload(template.mappings_json),
        "default_tokens": _dict_payload(template.default_tokens_json),
        "workflow_json": workflow_json if include_workflow else None,
    }


def _template_parse_selector_value(raw_value: str) -> Any:
    value = raw_value.strip().strip('"').strip("'")
    if re.fullmatch(r"-?\d+", value):
        try:
            return int(value)
        except ValueError:
            return value
    return value


def _tokenize_template_path(path: str) -> list[tuple[str, Any]]:
    text = str(path or "").strip()
    if not text:
        raise ValueError("Template target path is empty.")

    operations: list[tuple[str, Any]] = []
    segments = [segment for segment in text.split(".") if segment]
    if not segments:
        raise ValueError("Template target path is invalid.")

    for segment in segments:
        key_match = re.match(r"^([A-Za-z_][A-Za-z0-9_\-]*)(.*)$", segment)
        if not key_match:
            raise ValueError(f"Invalid template path segment: {segment}")
        key = key_match.group(1)
        suffix = key_match.group(2)
        operations.append(("key", key))

        while suffix:
            if not suffix.startswith("["):
                raise ValueError(f"Invalid bracket selector in segment: {segment}")
            closing_index = suffix.find("]")
            if closing_index <= 1:
                raise ValueError(f"Malformed selector in segment: {segment}")
            selector_text = suffix[1:closing_index].strip()
            suffix = suffix[closing_index + 1 :]
            if not selector_text:
                raise ValueError(f"Empty selector in segment: {segment}")

            if re.fullmatch(r"-?\d+", selector_text):
                operations.append(("index", int(selector_text)))
                continue

            if "=" in selector_text:
                selector_key, selector_value = selector_text.split("=", 1)
                selector_key = selector_key.strip()
                if not selector_key:
                    raise ValueError(f"Invalid keyed selector in segment: {segment}")
                operations.append(
                    (
                        "filter",
                        (selector_key, _template_parse_selector_value(selector_value)),
                    )
                )
                continue

            raise ValueError(
                f"Unsupported selector '{selector_text}' in segment: {segment}"
            )

    return operations


def _select_template_list_index(
    items: Any, selector: tuple[str, Any], *, path: str
) -> int:
    if not isinstance(items, list):
        raise ValueError(
            f"Path '{path}' expected a list for selector [{selector[0]}={selector[1]}]."
        )
    key, expected = selector
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        candidate = item.get(key)
        if candidate == expected or str(candidate) == str(expected):
            return index
    raise ValueError(
        f"Path '{path}' could not find selector [{key}={expected}] in target list."
    )


def _read_template_path_value(payload: Any, path: str) -> Any:
    operations = _tokenize_template_path(path)
    current = payload
    for operation, operand in operations:
        if operation == "key":
            if not isinstance(current, dict) or operand not in current:
                raise ValueError(f"Path '{path}' missing key '{operand}'.")
            current = current[operand]
            continue
        if operation == "index":
            if not isinstance(current, list) or operand < 0 or operand >= len(current):
                raise ValueError(f"Path '{path}' has invalid list index [{operand}].")
            current = current[operand]
            continue
        if operation == "filter":
            index = _select_template_list_index(current, operand, path=path)
            current = current[index]
            continue
    return current


def _write_template_path_value(payload: Any, path: str, value: Any) -> None:
    operations = _tokenize_template_path(path)
    if not operations:
        raise ValueError("Template path is empty.")

    current = payload
    for operation, operand in operations[:-1]:
        if operation == "key":
            if not isinstance(current, dict) or operand not in current:
                raise ValueError(f"Path '{path}' missing key '{operand}'.")
            current = current[operand]
            continue
        if operation == "index":
            if not isinstance(current, list) or operand < 0 or operand >= len(current):
                raise ValueError(f"Path '{path}' has invalid list index [{operand}].")
            current = current[operand]
            continue
        if operation == "filter":
            index = _select_template_list_index(current, operand, path=path)
            current = current[index]
            continue

    final_operation, final_operand = operations[-1]
    if final_operation == "key":
        if not isinstance(current, dict) or final_operand not in current:
            raise ValueError(f"Path '{path}' missing terminal key '{final_operand}'.")
        current[final_operand] = value
        return
    if final_operation == "index":
        if (
            not isinstance(current, list)
            or final_operand < 0
            or final_operand >= len(current)
        ):
            raise ValueError(
                f"Path '{path}' has invalid terminal list index [{final_operand}]."
            )
        current[final_operand] = value
        return
    if final_operation == "filter":
        index = _select_template_list_index(current, final_operand, path=path)
        current[index] = value
        return

    raise ValueError(f"Unsupported terminal operation in path '{path}'.")


def _coerce_template_value(value: Any, value_type: str, *, token: str) -> Any:
    normalized_type = str(value_type or "auto").strip().lower() or "auto"
    if normalized_type == "auto":
        return value
    if normalized_type == "string":
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=True)
        return "" if value is None else str(value)
    if normalized_type == "integer":
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Token '{token}' cannot be coerced to integer.") from exc
    if normalized_type == "number":
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Token '{token}' cannot be coerced to number.") from exc
    if normalized_type == "boolean":
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        text_value = str(value or "").strip().lower()
        if text_value in {"true", "1", "yes", "on"}:
            return True
        if text_value in {"false", "0", "no", "off"}:
            return False
        raise ValueError(f"Token '{token}' cannot be coerced to boolean.")
    if normalized_type == "json":
        if isinstance(value, (dict, list)):
            return value
        if isinstance(value, str):
            parsed = _parse_json_container(value)
            if parsed is not None:
                return parsed
        raise ValueError(f"Token '{token}' cannot be coerced to JSON object/list.")
    raise ValueError(f"Unsupported value_type '{value_type}' for token '{token}'.")


def _replace_template_placeholders_in_string(
    text: str, token_values: dict[str, Any], unresolved: set[str]
) -> Any:
    full_match = _TEMPLATE_PLACEHOLDER_RE.fullmatch(text)
    if full_match:
        token = full_match.group(1).strip()
        if token in token_values:
            return token_values[token]
        unresolved.add(token)
        return text

    def replacer(match: re.Match[str]) -> str:
        token = match.group(1).strip()
        if token not in token_values:
            unresolved.add(token)
            return match.group(0)
        replacement_value = token_values[token]
        if replacement_value is None:
            return ""
        if isinstance(replacement_value, (dict, list)):
            return json.dumps(replacement_value, ensure_ascii=True)
        return str(replacement_value)

    return _TEMPLATE_PLACEHOLDER_RE.sub(replacer, text)


def _replace_template_placeholders(
    value: Any, token_values: dict[str, Any], unresolved: set[str]
) -> Any:
    if isinstance(value, dict):
        return {
            key: _replace_template_placeholders(item, token_values, unresolved)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _replace_template_placeholders(item, token_values, unresolved)
            for item in value
        ]
    if isinstance(value, str):
        return _replace_template_placeholders_in_string(value, token_values, unresolved)
    return value


def _build_generation_template_token_values(
    generation_payload: dict, local_catalog: dict
) -> dict[str, Any]:
    token_values: dict[str, Any] = {}

    mode = str(generation_payload.get("mode") or "unknown")
    token_values["source.mode"] = mode

    target = _dict_payload(generation_payload.get("target"))
    if target.get("file_hash"):
        token_values["target.file_hash"] = str(target.get("file_hash"))
    if target.get("image_id") is not None:
        token_values["target.image_id"] = target.get("image_id")
    if target.get("source_url"):
        token_values["target.source_url"] = str(target.get("source_url"))

    normalized = _dict_payload(generation_payload.get("normalized"))
    processes = _list_payload(normalized.get("processes"))
    process = next(
        (
            item
            for item in processes
            if isinstance(item, dict) and item.get("is_preferred")
        ),
        None,
    )
    if process is None and processes:
        process = processes[0] if isinstance(processes[0], dict) else None
    process = _dict_payload(process)

    stages = _list_payload(process.get("stages"))
    stage = next(
        (
            item
            for item in stages
            if isinstance(item, dict)
            and str(item.get("stage_role") or "").lower() in {"base", "primary"}
        ),
        None,
    )
    if stage is None and stages:
        stage = stages[0] if isinstance(stages[0], dict) else None
    stage = _dict_payload(stage)

    process_prompts = _list_payload(process.get("prompts"))
    stage_prompts = _list_payload(stage.get("prompts"))
    all_prompts = [
        item for item in [*stage_prompts, *process_prompts] if isinstance(item, dict)
    ]
    for prompt_role, token_name in (
        ("positive", "prompt.positive"),
        ("negative", "prompt.negative"),
    ):
        prompt_value = next(
            (
                str(item.get("prompt_text") or "").strip()
                for item in all_prompts
                if str(item.get("prompt_role") or "").strip().lower() == prompt_role
                and str(item.get("prompt_text") or "").strip()
            ),
            "",
        )
        if prompt_value:
            token_values[token_name] = prompt_value

    if stage.get("seed") not in (None, ""):
        token_values["sampler.seed"] = stage.get("seed")
    if stage.get("steps") not in (None, ""):
        token_values["sampler.steps"] = stage.get("steps")
    if stage.get("cfg_scale") not in (None, ""):
        token_values["sampler.cfg"] = stage.get("cfg_scale")
    if stage.get("sampler_name"):
        token_values["sampler.name"] = str(stage.get("sampler_name"))
    if stage.get("scheduler_name"):
        token_values["sampler.scheduler"] = str(stage.get("scheduler_name"))
    if stage.get("denoise_strength") not in (None, ""):
        token_values["sampler.denoise"] = stage.get("denoise_strength")

    if stage.get("width") not in (None, ""):
        token_values["image.width"] = stage.get("width")
    if stage.get("height") not in (None, ""):
        token_values["image.height"] = stage.get("height")

    process_resources = _list_payload(process.get("resources"))
    stage_resources: list[dict[str, Any]] = []
    for stage_item in stages:
        if isinstance(stage_item, dict):
            stage_resources.extend(
                [
                    resource
                    for resource in _list_payload(stage_item.get("resources"))
                    if isinstance(resource, dict)
                ]
            )
    all_resources = [
        resource
        for resource in [*stage_resources, *process_resources]
        if isinstance(resource, dict)
    ]

    def pick_resource(resource_type: str) -> Optional[dict[str, Any]]:
        typed = [
            resource
            for resource in all_resources
            if str(resource.get("resource_type") or "").strip().lower() == resource_type
        ]
        if not typed:
            return None
        primary = next(
            (resource for resource in typed if bool(resource.get("is_primary"))), None
        )
        return primary or typed[0]

    checkpoint_resource = pick_resource("checkpoint")
    if checkpoint_resource:
        checkpoint_name = str(
            checkpoint_resource.get("display_name")
            or checkpoint_resource.get("version_name")
            or checkpoint_resource.get("source_identifier")
            or ""
        ).strip()
        checkpoint_path = str(
            checkpoint_resource.get("source_identifier") or ""
        ).strip()
        if checkpoint_name:
            token_values["model.checkpoint_name"] = checkpoint_name
        if checkpoint_path:
            token_values["model.checkpoint_path"] = checkpoint_path

    lora_resources = [
        resource
        for resource in all_resources
        if str(resource.get("resource_type") or "").strip().lower() == "lora"
    ]
    if lora_resources:
        lora_names = [
            str(
                resource.get("display_name")
                or resource.get("version_name")
                or resource.get("source_identifier")
                or ""
            ).strip()
            for resource in lora_resources
        ]
        lora_paths = [
            str(resource.get("source_identifier") or "").strip()
            for resource in lora_resources
        ]
        lora_model_strengths = [
            (
                resource.get("weight")
                if resource.get("weight") not in (None, "")
                else resource.get("strength")
            )
            for resource in lora_resources
        ]
        lora_clip_strengths = [
            (
                resource.get("clip_weight")
                if resource.get("clip_weight") not in (None, "")
                else resource.get("clipStrength")
            )
            for resource in lora_resources
        ]
        lora_names = [value for value in lora_names if value]
        lora_paths = [value for value in lora_paths if value]
        lora_model_strengths = [
            value for value in lora_model_strengths if value not in (None, "")
        ]
        lora_clip_strengths = [
            value for value in lora_clip_strengths if value not in (None, "")
        ]
        if lora_names:
            token_values["model.lora_names"] = lora_names
            token_values["model.lora_name"] = lora_names[0]
        if lora_paths:
            token_values["model.lora_paths"] = lora_paths
            token_values["model.lora_path"] = lora_paths[0]
        if lora_model_strengths:
            token_values["model.lora_model_strengths"] = lora_model_strengths
            token_values["model.lora_model_strength"] = lora_model_strengths[0]
        if lora_clip_strengths:
            token_values["model.lora_clip_strengths"] = lora_clip_strengths
            token_values["model.lora_clip_strength"] = lora_clip_strengths[0]

    if bool(local_catalog.get("configured")):
        references = model_reference_service.extract_references_from_generation_payload(
            generation_payload
        )
        matched = model_reference_service.apply_local_catalog_matches(
            references, local_catalog
        )
        checkpoint_match = next(
            (
                reference
                for reference in matched
                if str(reference.get("resource_type") or "").strip().lower()
                == "checkpoint"
                and _list_payload(reference.get("local_matches"))
            ),
            None,
        )
        if checkpoint_match:
            first_match = _dict_payload(
                _list_payload(checkpoint_match.get("local_matches"))[0]
            )
            match_path = str(first_match.get("source_identifier") or "").strip()
            if match_path:
                token_values["model.checkpoint_path"] = match_path

        lora_match_paths: list[str] = []
        for reference in matched:
            if str(reference.get("resource_type") or "").strip().lower() != "lora":
                continue
            local_matches = _list_payload(reference.get("local_matches"))
            first_match = _dict_payload(local_matches[0]) if local_matches else {}
            match_path = str(first_match.get("source_identifier") or "").strip()
            if match_path:
                lora_match_paths.append(match_path)
        if lora_match_paths:
            token_values["model.lora_paths"] = lora_match_paths
            token_values["model.lora_path"] = lora_match_paths[0]

    return token_values


def _build_generation_template_step_token_groups(
    generation_payload: dict,
) -> list[dict[str, Any]]:
    normalized = _dict_payload(generation_payload.get("normalized"))
    processes = _list_payload(normalized.get("processes"))
    process = next(
        (
            item
            for item in processes
            if isinstance(item, dict) and item.get("is_preferred")
        ),
        None,
    )
    if process is None and processes:
        process = processes[0] if isinstance(processes[0], dict) else None
    process = _dict_payload(process)

    stages = [
        item for item in _list_payload(process.get("stages")) if isinstance(item, dict)
    ]
    step_groups: list[dict[str, Any]] = []

    for step_index, stage in enumerate(stages):
        step_tokens: dict[str, Any] = {}
        token_prefix = f"step.{step_index}"

        stage_role = str(stage.get("stage_role") or "").strip()
        if stage_role:
            step_tokens[f"{token_prefix}.stage_role"] = stage_role

        for source_key, token_key in (
            ("seed", "sampler.seed"),
            ("steps", "sampler.steps"),
            ("cfg_scale", "sampler.cfg"),
            ("sampler_name", "sampler.name"),
            ("scheduler_name", "sampler.scheduler"),
            ("denoise_strength", "sampler.denoise"),
            ("width", "image.width"),
            ("height", "image.height"),
        ):
            value = stage.get(source_key)
            if value not in (None, ""):
                step_tokens[f"{token_prefix}.{token_key}"] = value

        stage_prompts = [
            item
            for item in _list_payload(stage.get("prompts"))
            if isinstance(item, dict)
        ]
        for prompt_role in ("positive", "negative"):
            prompt_text = next(
                (
                    str(item.get("prompt_text") or "").strip()
                    for item in stage_prompts
                    if str(item.get("prompt_role") or "").strip().lower() == prompt_role
                    and str(item.get("prompt_text") or "").strip()
                ),
                "",
            )
            if prompt_text:
                step_tokens[f"{token_prefix}.prompt.{prompt_role}"] = prompt_text

        stage_resources = [
            item
            for item in _list_payload(stage.get("resources"))
            if isinstance(item, dict)
        ]
        checkpoint_resources = [
            item
            for item in stage_resources
            if str(item.get("resource_type") or "").strip().lower() == "checkpoint"
        ]
        if checkpoint_resources:
            checkpoint = checkpoint_resources[0]
            checkpoint_name = str(
                checkpoint.get("display_name")
                or checkpoint.get("version_name")
                or checkpoint.get("source_identifier")
                or ""
            ).strip()
            checkpoint_path = str(checkpoint.get("source_identifier") or "").strip()
            if checkpoint_name:
                step_tokens[f"{token_prefix}.model.checkpoint_name"] = checkpoint_name
            if checkpoint_path:
                step_tokens[f"{token_prefix}.model.checkpoint_path"] = checkpoint_path

        lora_resources = [
            item
            for item in stage_resources
            if str(item.get("resource_type") or "").strip().lower() == "lora"
        ]
        lora_groups: list[dict[str, Any]] = []
        for lora_index, lora in enumerate(lora_resources):
            lora_prefix = f"{token_prefix}.model.lora.{lora_index}"
            lora_name = str(
                lora.get("display_name")
                or lora.get("version_name")
                or lora.get("source_identifier")
                or ""
            ).strip()
            lora_path = str(lora.get("source_identifier") or "").strip()
            lora_model_strength = (
                lora.get("weight")
                if lora.get("weight") not in (None, "")
                else lora.get("strength")
            )
            lora_clip_strength = (
                lora.get("clip_weight")
                if lora.get("clip_weight") not in (None, "")
                else lora.get("clipStrength")
            )
            if lora_name:
                step_tokens[f"{lora_prefix}.name"] = lora_name
            if lora_path:
                step_tokens[f"{lora_prefix}.path"] = lora_path
            if lora_model_strength not in (None, ""):
                step_tokens[f"{lora_prefix}.model_strength"] = lora_model_strength
                step_tokens[f"{lora_prefix}.strength"] = lora_model_strength
            if lora_clip_strength not in (None, ""):
                step_tokens[f"{lora_prefix}.clip_strength"] = lora_clip_strength

            lora_groups.append(
                {
                    "index": lora_index,
                    "name": lora_name or None,
                    "path": lora_path or None,
                    "model_strength": lora_model_strength,
                    "clip_strength": lora_clip_strength,
                }
            )

        step_groups.append(
            {
                "step_index": step_index,
                "stage_role": stage_role or None,
                "lora_count": len(lora_groups),
                "loras": lora_groups,
                "tokens": step_tokens,
            }
        )

    return step_groups


def _build_generation_template_token_preview(
    generation_payload: dict, local_catalog: dict
) -> dict[str, Any]:
    global_tokens = _build_generation_template_token_values(
        generation_payload, local_catalog
    )
    step_groups = _build_generation_template_step_token_groups(generation_payload)
    flattened_step_tokens: dict[str, Any] = {}
    for group in step_groups:
        for key, value in _dict_payload(group.get("tokens")).items():
            flattened_step_tokens[str(key)] = value

    combined_tokens = {**global_tokens, **flattened_step_tokens}
    return {
        "tokens": combined_tokens,
        "global_tokens": global_tokens,
        "step_groups": step_groups,
    }


def _resolve_generation_template_workflow(
    template: GenerationTemplate,
    generation_payload: dict,
    *,
    token_overrides: dict[str, Any],
    local_catalog: dict,
) -> dict[str, Any]:
    warnings: list[str] = []
    errors: list[str] = []

    workflow_json = _dict_payload(_clone_json_value(template.workflow_json))
    if not workflow_json:
        raise HTTPException(
            status_code=422, detail="Template has no workflow JSON payload."
        )

    template_defaults = _dict_payload(template.default_tokens_json)
    derived_tokens = _build_generation_template_token_values(
        generation_payload, local_catalog
    )
    resolved_tokens: dict[str, Any] = {
        **template_defaults,
        **derived_tokens,
        **_dict_payload(token_overrides),
    }

    mappings = [
        item for item in _list_payload(template.mappings_json) if isinstance(item, dict)
    ]
    for mapping in mappings:
        token_name = str(mapping.get("token") or "").strip()
        target_path = str(mapping.get("target_path") or "").strip()
        required = bool(mapping.get("required", True))
        value_type = str(mapping.get("value_type") or "auto").strip() or "auto"
        default_value = mapping.get("default_value")

        if not token_name or not target_path:
            warnings.append(
                "Skipped template mapping with missing token or target_path."
            )
            continue

        has_value = (
            token_name in resolved_tokens
            and resolved_tokens.get(token_name) is not None
        )
        if not has_value and default_value is not None:
            resolved_tokens[token_name] = default_value
            has_value = True

        if not has_value:
            message = f"Template token '{token_name}' has no resolved value for path '{target_path}'."
            if required:
                errors.append(message)
            else:
                warnings.append(message)
            continue

        try:
            coerced_value = _coerce_template_value(
                resolved_tokens.get(token_name), value_type, token=token_name
            )
        except ValueError as exc:
            errors.append(str(exc))
            continue

        try:
            _write_template_path_value(workflow_json, target_path, coerced_value)
        except ValueError as exc:
            errors.append(str(exc))
            continue

    unresolved_placeholders: set[str] = set()
    workflow_json = _replace_template_placeholders(
        workflow_json, resolved_tokens, unresolved_placeholders
    )
    if unresolved_placeholders:
        warnings.append(
            "Unresolved placeholder tokens remain in workflow JSON: "
            + ", ".join(sorted(unresolved_placeholders))
        )

    extracted_tokens: dict[str, Any] = {}
    for mapping in mappings:
        token_name = str(mapping.get("token") or "").strip()
        target_path = str(mapping.get("target_path") or "").strip()
        if not token_name or not target_path:
            continue
        try:
            extracted_tokens[token_name] = _read_template_path_value(
                workflow_json, target_path
            )
        except ValueError:
            continue

    validation = _summarize_validation(warnings, errors)
    return {
        "ok": validation.get("status") != "error",
        "template": _serialize_generation_template(template, include_workflow=False),
        "source": {
            "mode": generation_payload.get("mode"),
            "target": _dict_payload(generation_payload.get("target")),
        },
        "resolved_tokens": resolved_tokens,
        "applied_mapping_values": extracted_tokens,
        "resolved_workflow_json": workflow_json,
        "validation": validation,
    }


def _import_single_civitai_image(
    api: CivitaiAPI,
    db: Session,
    image_id: int,
    *,
    force_reimport_on_missing_metadata: bool = False,
) -> dict:
    source_url = f"{getattr(app_config, 'CIVITAI_WEB_BASE_URL', 'https://civitai.red')}/images/{image_id}"
    recovered_existing = False

    # Fast path: if this exact CivitAI source URL is already in library,
    # skip download and only attempt metadata repair.
    existing_by_source = _find_existing_image_by_source_url(db, source_url)
    if existing_by_source is not None:
        existing_status = (
            getattr(existing_by_source, "image_status", None) or "active"
        ).lower()
        if existing_status == "placeholder":
            return {
                "image_id": image_id,
                "image_db_id": existing_by_source.id,
                "images_added": 0,
                "images_skipped": 1,
                "images_recovered": 0,
                "json_files_created": 0,
                "metadata_backfilled": False,
                "skip_reason": "placeholder_source_url",
                "existing_image_id": existing_by_source.id,
                "existing_file_hash": existing_by_source.file_hash,
                "existing_file_path": existing_by_source.file_path,
                "existing_source_url": existing_by_source.source_url,
                "error": None,
            }
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
            if metadata_backfilled or not force_reimport_on_missing_metadata:
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

            # Metadata is still incomplete and caller requested hard recovery.
            _remove_local_image_record(db, existing_by_source)
            recovered_existing = True

    temp_path = None
    try:
        target = _resolve_civitai_image_target(api, image_id)
        download_result = _download_civitai_image_with_validation(
            image_id=image_id,
            target=target,
        )
        temp_path = download_result.temp_path

        prepared = _PreparedCivitaiImport(
            image_id=image_id,
            image_url=target["image_url"],
            mime_type=target["mime_type"],
            declared_file_size=target.get("declared_file_size"),
            preview_image_url=target.get("preview_image_url"),
            original_filename=target["original_filename"],
            artist_name=target["artist_name"],
            source_url=target["source_url"],
            temp_path=temp_path,
            civitai_uuid=target.get("civitai_uuid"),
            civitai_hash=target.get("civitai_hash"),
            raw_basic_info=target.get("raw_basic_info"),
            raw_generation_data=target.get("raw_generation_data"),
            api_response_paths=target.get("api_response_paths", {}),
            effective_image_url=download_result.selected_url,
            mismatch_static_temp_path=download_result.mismatch_static_temp_path,
            mismatch_source_url=download_result.mismatch_source_url,
            mismatch_mime_type=download_result.mismatch_mime_type,
            mismatch_file_hash=download_result.mismatch_file_hash,
            author_id=target.get("author_id"),
            author_deleted=target.get("author_deleted", False),
            author_original_name=target.get("author_original_name"),
            civitai_post_id=target.get("civitai_post_id"),
            civitai_post_title=target.get("civitai_post_title"),
            civitai_post_index=target.get("civitai_post_index"),
        )
        return _ingest_prepared_civitai_import(
            db,
            prepared=prepared,
            recovered_existing=recovered_existing,
        )
    except _CivitaiImageUnavailableError as e:
        if e.status_code == 404:
            return _build_civitai_unavailable_result(image_id, e, api=api, db=db)
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
    except HTTPException as e:
        if e.status_code == 404:
            return _build_civitai_unavailable_result(image_id, e, api=api, db=db)
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
    except CivitaiRequestError as e:
        if e.status_code == 404:
            return _build_civitai_unavailable_result(image_id, e, api=api, db=db)
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
    if not is_ffmpeg_available():
        warnings.append(
            "ffmpeg is not installed or not on PATH. Server-generated video posters are unavailable, so the gallery falls back to browser-generated static posters plus hover preview. Install ffmpeg to improve video thumbnail support; on macOS with Homebrew, run 'brew install ffmpeg'."
        )
    return warnings


def _get_media_capabilities() -> dict[str, Any]:
    ffmpeg_available = is_ffmpeg_available()
    video_thumbnail_variant = get_video_thumbnail_variant()
    return {
        "exiftool_available": is_exiftool_available(),
        "ffmpeg_available": ffmpeg_available,
        "video_poster_mode": "server" if ffmpeg_available else "browser-fallback",
        "video_thumbnail_mode": (
            f"server-animated-{video_thumbnail_variant}"
            if video_thumbnail_variant is not None
            else "unavailable"
        ),
        "video_thumbnail_format": video_thumbnail_variant,
    }


def _find_existing_image_by_source_url(
    db: Session, source_url: str
) -> Optional[ImageModel]:
    """Find existing image by civitai_image_id index, source_url, or sidecar fallback."""
    # Fast path: if source_url is a CivitAI image URL, try indexed lookup first.
    if is_civitai_image_url(str(source_url or "")):
        extracted_id = extract_civitai_image_id(source_url)
        if extracted_id is not None:
            indexed_matches = (
                db.query(ImageModel)
                .filter(ImageModel.civitai_image_id == extracted_id)
                .order_by(ImageModel.id.desc())
                .all()
            )
            if indexed_matches:
                for status in ("active", "placeholder", "tombstoned", "deleted"):
                    for candidate in indexed_matches:
                        candidate_status = (candidate.image_status or "active").lower()
                        if candidate_status == status:
                            return candidate

    direct_matches = (
        db.query(ImageModel)
        .filter(ImageModel.source_url == source_url)
        .order_by(ImageModel.id.desc())
        .all()
    )
    if direct_matches:
        # Priority: active > placeholder > tombstoned > deleted
        for status in ("active", "placeholder", "tombstoned", "deleted"):
            for candidate in direct_matches:
                candidate_status = (candidate.image_status or "active").lower()
                if candidate_status == status:
                    return candidate

    # Cross-domain fallback: images imported when CIVITAI_WEB_BASE_URL used a
    # different hostname (e.g. civitai.com vs civitai.red) won't match on exact
    # URL equality.  Match by the URL path suffix instead so both domains resolve
    # to the same record.  This mirrors the LIKE pattern used by the sync-lab
    # analyze-local endpoint.
    _parsed = urlparse(source_url)
    _path_suffix = _parsed.path  # e.g. "/images/12345"
    if _path_suffix and _path_suffix != "/":
        cross_domain_matches = (
            db.query(ImageModel)
            .filter(
                ImageModel.source_url.like(f"%{_path_suffix}"),
                ImageModel.source_url != source_url,
            )
            .order_by(ImageModel.id.desc())
            .all()
        )
        if cross_domain_matches:
            for status in ("active", "placeholder", "tombstoned", "deleted"):
                for candidate in cross_domain_matches:
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
            if (
                isinstance(sidecar_data, dict)
                and sidecar_data.get("source_url") == source_url
            ):
                fallback_matches.append(candidate)
        except (OSError, json.JSONDecodeError):
            continue

    if fallback_matches:
        for status in ("active", "placeholder", "tombstoned", "deleted"):
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
    if mime.startswith("video/") or suffix in _VIDEO_FILE_SUFFIXES:
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


def _payload_has_nsfw_level(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("nsfwLevel") is not None:
        return True
    image_payload = payload.get("image")
    if isinstance(image_payload, dict) and image_payload.get("nsfwLevel") is not None:
        return True
    meta_payload = payload.get("meta")
    if isinstance(meta_payload, dict) and meta_payload.get("nsfwLevel") is not None:
        return True
    return False


def _ensure_civitai_source_attribution_for_existing_image(
    db: Session,
    image: ImageModel,
    source_url: str,
) -> bool:
    """Ensure existing dedupe matches keep CivitAI source attribution fields."""
    image_path = Path(IMAGE_LIBRARY_PATH) / str(image.file_path)
    if not image_path.exists():
        return False

    normalized_source_url = str(source_url or "").strip()
    if not normalized_source_url or not is_civitai_image_url(normalized_source_url):
        return False

    changed = False
    current_source_url = str(image.source_url or "").strip()
    if not current_source_url:
        image.source_url = normalized_source_url
        changed = True

    current_source_site = str(image.source_site or "").strip().lower()
    if not current_source_site and is_civitai_image_url(
        str(image.source_url or "").strip()
    ):
        image.source_site = "civitai"
        changed = True

    # Backfill civitai_image_id from the source URL if not already set.
    if image.civitai_image_id is None:
        extracted_id = extract_civitai_image_id(normalized_source_url)
        if extracted_id is not None:
            image.civitai_image_id = extracted_id
            changed = True

    if not changed:
        return False

    db.flush()
    db.refresh(image)
    processor = ImageProcessor(str(image_path), db, IMAGE_LIBRARY_PATH)
    processor.save_json_metadata(image_path, image)
    return True


def _ensure_civitai_metadata_for_existing_image(
    db: Session,
    image: ImageModel,
    source_url: str,
) -> bool:
    """Backfill missing CivitAI metadata for an existing local image record."""
    # Skip images known to be deleted from CivitAI (404 on image.get).
    if image.civitai_deleted_at is not None:
        return False

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
    sidecar_civitai_payload = sidecar_data.get("civitai")
    db_civitai_payload = db_json.get("civitai")
    sidecar_has_civitai = isinstance(sidecar_civitai_payload, dict)
    db_has_civitai = isinstance(db_civitai_payload, dict)
    sidecar_has_nsfw_level = _payload_has_nsfw_level(sidecar_civitai_payload)
    db_has_nsfw_level = _payload_has_nsfw_level(db_civitai_payload)

    # Even when CivitAI metadata is already complete, sync tags into
    # the taxonomy so that authority_terms get their external_tag_id
    # filled in from sidecar data (backfills legacy terms that were
    # imported without numeric IDs).  Must commit here because the
    # fast-path caller does not commit when we return False.
    if (
        sidecar_has_civitai
        and db_has_civitai
        and sidecar_has_nsfw_level
        and db_has_nsfw_level
    ):
        sidecar_tags = (
            sidecar_civitai_payload.get("tags")
            if isinstance(sidecar_civitai_payload, dict)
            else None
        )
        if isinstance(sidecar_tags, list) and sidecar_tags:
            try:
                _upsert_civitai_authority_terms(db, {"tags": sidecar_tags})
                db.commit()
            except Exception:
                db.rollback()

        # Even on the fast path, observations may be missing.
        merged_payload = {**db_json, **sidecar_data}
        _hydrate_observations_from_payload(db, image, merged_payload)
        return False

    civitai_data = fetch_civitai_image_data(source_url)
    if not civitai_data:
        return False

    civitai_uuid = None
    raw_uuid = civitai_data.get("civitai_uuid")
    if isinstance(raw_uuid, str) and raw_uuid.strip():
        civitai_uuid = raw_uuid.strip()

    civitai_hash = None
    raw_hash = civitai_data.get("civitai_hash")
    if isinstance(raw_hash, str) and raw_hash.strip():
        civitai_hash = raw_hash.strip()

    # Extract post_id from enrichment data (CivitaiImage.to_dict() provides it)
    civitai_post_id = None
    raw_post_id = civitai_data.get("post_id")
    if raw_post_id is not None:
        try:
            civitai_post_id = int(raw_post_id)
        except (TypeError, ValueError):
            civitai_post_id = None

    # Extract post title and index from enrichment data (when available)
    civitai_post_title = civitai_data.get("post_title") or None
    civitai_post_index = None
    raw_post_index = civitai_data.get("post_index")
    if raw_post_index is not None:
        try:
            civitai_post_index = int(raw_post_index)
        except (TypeError, ValueError):
            civitai_post_index = None

    # Sync CivitAI tags into taxonomy authority_terms so numeric IDs
    # and names are tracked for concept mapping.  This is best-effort
    # and must not block the enrichment pipeline on failure.
    try:
        _upsert_civitai_authority_terms(db, civitai_data)
    except Exception as exc:
        print(f"Warning: CivitAI authority-term sync failed for {source_url}: {exc}")

    merged_json = dict(db_json)
    merged_json["civitai"] = civitai_data

    # Extract civitai_nsfw_level from the enrichment data
    # Extract civitai_image_id from source_url and include in update
    civitai_img_id = extract_civitai_image_id(source_url)

    nsfw_level = extract_civitai_nsfw_level({"civitai": civitai_data})

    update_fields = {
        ImageModel.json_metadata: merged_json,
        ImageModel.source_url: source_url,
        ImageModel.source_site: "civitai",
        ImageModel.civitai_uuid: civitai_uuid,
        ImageModel.civitai_hash: civitai_hash,
        ImageModel.civitai_nsfw_level: nsfw_level,
    }
    if civitai_img_id is not None:
        update_fields[ImageModel.civitai_image_id] = civitai_img_id
    if civitai_post_id is not None:
        update_fields[ImageModel.civitai_post_id] = civitai_post_id
    if civitai_post_title is not None:
        update_fields[ImageModel.civitai_post_title] = civitai_post_title
    if civitai_post_index is not None:
        update_fields[ImageModel.civitai_post_index] = civitai_post_index

    # Persist declared file size from CivitAI metadata for size-mismatch detection
    declared_file_size = civitai_data.get("declared_file_size")
    if declared_file_size is not None:
        try:
            declared_file_size = int(declared_file_size)
        except (TypeError, ValueError):
            declared_file_size = None
    if declared_file_size is not None:
        update_fields[ImageModel.expected_file_size] = declared_file_size

    (
        db.query(ImageModel)
        .filter(ImageModel.id == image.id)
        .update(
            update_fields,
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

    # Hydrate observations for this image from the newly enriched tags.
    # Only creates observations for authority_terms that already have a
    # concept_id — tags without concepts are left for manual curation.
    _hydrate_observations_from_payload(db, image, merged_json)

    return True


def _hydrate_observations_from_payload(
    db: Session,
    image: ImageModel,
    merged_payload: dict,
) -> None:
    """Create observations for tags in *merged_payload* that have no observation row yet.

    Creates observations for ALL authority_terms, including those without
    a ``concept_id`` (orphan tags).  Deduplication uses authority_term_id.
    """
    # NOTE: We intentionally do NOT bail out if the image already has
    # observations from another source (e.g. CivitAI).  Prompt observations
    # are added independently, and the per-observation existence check below
    # prevents duplicates.

    gallery_tag_svc = GalleryTagService()
    tax = TaxonomyService()

    tags_by_source = gallery_tag_svc.extract_image_scope_tag_names(
        merged_payload,
        normalize_taxonomy_text=tax.normalize_text,
    )
    if not any(tags_by_source.values()):
        return

    now = datetime.utcnow()
    _seen: set[tuple[int, int, int]] = set()

    try:
        for source, tag_names in tags_by_source.items():
            if not tag_names:
                continue

            authority = tax.get_or_create_authority(db, source)
            authority_id = int(authority.id)
            normalized_names = {
                tax.normalize_text(n): n for n in tag_names if n
            }
            if not normalized_names:
                continue

            # Batch-load authority_terms (including orphans without concept_id)
            terms = (
                db.query(AuthorityTerm)
                .filter(
                    AuthorityTerm.authority_id == authority_id,
                    AuthorityTerm.normalized_external_name.in_(normalized_names),
                )
                .all()
            )

            for term in terms:
                concept_id = term.concept_id  # May be None for orphans

                # Dedup by authority_term_id — one observation per term
                obs_key = (int(image.id), int(term.id), authority_id)
                if obs_key in _seen:
                    continue

                existing = (
                    db.query(ImageConceptObservation.id)
                    .filter(
                        ImageConceptObservation.image_id == image.id,
                        ImageConceptObservation.authority_term_id == term.id,
                    )
                    .first()
                )
                if existing is not None:
                    _seen.add(obs_key)
                    continue

                db.add(
                    ImageConceptObservation(
                        image_id=int(image.id),
                        concept_id=concept_id,
                        authority_id=authority_id,
                        authority_term_id=int(term.id),
                        source_type=ObservationSource.IMPORT,
                        certainty_label=ObservationCertainty.LIKELY,
                        is_present=True,
                        is_curated=False,
                        created_at=now,
                        updated_at=now,
                    )
                )
                _seen.add(obs_key)

        db.flush()
    except Exception as exc:
        db.rollback()
        print(f"Warning: observation hydration failed for image {image.id}: {exc}")


def _resolve_tag_ids_from_local_db(
    db: Session, tag_ids: list[int]
) -> tuple[list[dict[str, Any]], set[int]]:
    """Resolve CivitAI tag IDs to full tag record dicts from local AuthorityTerm rows.

    For each tag_id that has a matching AuthorityTerm under the 'civitai' authority,
    reconstruct a tag record dict suitable for ``_upsert_civitai_authority_terms``
    and ``_insert_tag_observations_for_image``.

    Returns:
        (resolved_records, unresolved_ids) — resolved tag record dicts and the set
        of tag IDs that could not be found locally.
    """
    if not tag_ids:
        return [], set()

    civitai_authority = _get_or_create_authority(db, "civitai")
    terms = (
        db.query(
            AuthorityTerm.external_tag_id,
            AuthorityTerm.external_name,
            AuthorityTerm.metadata_json,
        )
        .filter(
            AuthorityTerm.authority_id == civitai_authority.id,
            AuthorityTerm.external_tag_id.in_(tag_ids),
        )
        .all()
    )

    resolved: list[dict[str, Any]] = []
    resolved_ids: set[int] = set()
    for ext_id, name, meta_json in terms:
        if ext_id is None:
            continue
        record: dict[str, Any] = {"id": ext_id, "name": name or ""}
        if isinstance(meta_json, dict):
            # metadata_json stores camelCase keys from the API (type, nsfwLevel, etc.)
            record.update(meta_json)
        resolved.append(record)
        resolved_ids.add(ext_id)

    unresolved = set(tag_ids) - resolved_ids
    return resolved, unresolved


def _upsert_civitai_authority_terms(db: Session, civitai_data: dict) -> dict:
    """Sync CivitAI tag records into the taxonomy authority_terms table.

    For each tag in civitai_data["tags"], creates or updates an AuthorityTerm
    under the CivitAI authority.  Does NOT create new Concepts — tags that
    don't map to an existing concept are stored with concept_id=NULL so
    they can be resolved later by user curation or AI-assisted mapping.

    Returns a small stats dict for logging.
    """
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
            external_tag_id = int(raw_tag_id) if raw_tag_id not in (None, "") else None
        except (TypeError, ValueError):
            external_tag_id = None

        # Build extra metadata for the tag (type, nsfw level, etc.)
        tag_meta: dict = {}
        for meta_key in ("type", "nsfwLevel", "automated", "concrete", "score"):
            val = tag.get(meta_key)
            if val is not None:
                # Use API-camelCase keys to avoid confusion with DB columns.
                tag_meta[meta_key] = val

        # Look up by (authority_id, external_tag_id) when ID available,
        # otherwise fall back to (authority_id, normalized_external_name).
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
                concept_id=None,  # Unresolved until user/AI maps it
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


def _insert_tag_observations_for_image(
    db: Session,
    *,
    image_db_id: int,
    tag_records: list[dict[str, Any]],
) -> int:
    """Insert image_concept_observations for CivitAI tag records.

    First upserts authority_terms from the tag records, then creates
    observation rows linking the image to matched authority_terms.
    Skips terms whose authority_term row has no matching external_tag_id.

    Returns the number of observations inserted.
    """
    if not tag_records:
        return 0

    # 1. Upsert authority_terms so they exist before we reference them.
    _upsert_civitai_authority_terms(db, {"tags": tag_records})

    # 2. Collect external tag IDs from the records.
    tag_ids: list[int] = []
    for tag in tag_records:
        raw_id = tag.get("id")
        if raw_id is not None:
            try:
                tag_ids.append(int(raw_id))
            except (TypeError, ValueError):
                pass
    if not tag_ids:
        return 0

    # 3. Find authority_terms matching these tag IDs.
    civitai_authority = _get_or_create_authority(db, "civitai")
    terms = (
        db.query(AuthorityTerm.id, AuthorityTerm.concept_id)
        .filter(
            AuthorityTerm.authority_id == civitai_authority.id,
            AuthorityTerm.external_tag_id.in_(tag_ids),
        )
        .all()
    )
    if not terms:
        return 0

    now = datetime.utcnow()
    inserted = 0
    for term_id, concept_id in terms:
        # Check for existing observation to avoid duplicates.
        existing = (
            db.query(ImageConceptObservation.id)
            .filter(
                ImageConceptObservation.image_id == image_db_id,
                ImageConceptObservation.authority_id == civitai_authority.id,
                ImageConceptObservation.authority_term_id == term_id,
            )
            .first()
        )
        if existing is not None:
            continue

        db.add(
            ImageConceptObservation(
                image_id=image_db_id,
                concept_id=concept_id,
                authority_id=civitai_authority.id,
                authority_term_id=term_id,
                source_type=ObservationSource.IMPORT,
                certainty_label=ObservationCertainty.LIKELY,
                is_present=True,
                is_curated=False,
                created_at=now,
                updated_at=now,
            )
        )
        inserted += 1

    if inserted:
        db.flush()
    return inserted


def _normalize_collection_name(name: str) -> str:
    normalized = (name or "").strip()
    if not normalized:
        raise HTTPException(status_code=400, detail="Collection name is required.")
    return normalized


def _collection_name_exists(
    db: Session, name: str, exclude_collection_id: Optional[int] = None
) -> bool:
    query = db.query(CollectionModel).filter(CollectionModel.name == name)
    if exclude_collection_id is not None:
        query = query.filter(CollectionModel.id != exclude_collection_id)
    return query.first() is not None


def _ensure_civitai_mapping(
    db: Session, collection_id: int, civitai_collection_id: int
) -> None:
    """Insert a junction-table row mapping *civitai_collection_id* → *collection_id*.

    Idempotent — does nothing if the mapping already exists (either on the
    same local collection or on another).  Because
    ``CollectionCivitaiMapping.civitai_collection_id`` is UNIQUE, each CivitAI
    collection ID can only point to one local collection.
    """
    existing = (
        db.query(CollectionCivitaiMapping)
        .filter(CollectionCivitaiMapping.civitai_collection_id == civitai_collection_id)
        .first()
    )
    if existing is not None:
        # Already mapped — if it points to a different local collection we
        # don't silently move it; caller should handle that case explicitly.
        return
    db.add(
        CollectionCivitaiMapping(
            collection_id=collection_id,
            civitai_collection_id=civitai_collection_id,
        )
    )
    db.flush()


def _resolve_local_collection_by_civitai_id(
    db: Session, civitai_collection_id: int
) -> Optional[CollectionModel]:
    """Look up a local collection by CivitAI collection ID via the junction table.

    Falls back to the legacy ``CollectionModel.civitai_collection_id`` column
    for rows that have not yet been migrated.
    """
    mapping = (
        db.query(CollectionCivitaiMapping)
        .filter(CollectionCivitaiMapping.civitai_collection_id == civitai_collection_id)
        .first()
    )
    if mapping is not None:
        return (
            db.query(CollectionModel)
            .filter(CollectionModel.id == mapping.collection_id)
            .first()
        )
    # Legacy fallback
    return (
        db.query(CollectionModel)
        .filter(CollectionModel.civitai_collection_id == civitai_collection_id)
        .first()
    )


def _serialize_collection(collection: CollectionModel) -> dict:
    # Pull every CivitAI id mapped to this collection through the junction
    # table so the frontend can show all remote IDs that feed into one local
    # collection.  The legacy scalar is kept for backwards compatibility.
    from sqlalchemy import inspect  # noqa: PLC0415

    mapped_ids: list[int] = []
    session = inspect(collection).session  # type: ignore[union-attr]
    if session is not None:
        mapped_ids = [
            row.civitai_collection_id
            for row in session.query(CollectionCivitaiMapping)
            .filter(CollectionCivitaiMapping.collection_id == collection.id)
            .all()
        ]

    legacy = collection.civitai_collection_id
    if legacy is not None and legacy not in mapped_ids:
        mapped_ids.append(legacy)

    return {
        "id": collection.id,
        "name": collection.name,
        "source": collection.source,
        "civitai_collection_id": legacy,  # deprecated — use civitai_collection_ids
        "civitai_collection_ids": sorted(mapped_ids),
        "civitai_last_synced_at": _isoformat_or_none(collection.civitai_last_synced_at),
        "civitai_last_full_scan_at": _isoformat_or_none(
            collection.civitai_last_full_scan_at
        ),
        "civitai_last_full_item_count": collection.civitai_last_full_item_count,
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


def _classify_civitai_upstream_error(exc: CivitaiRequestError) -> HTTPException:
    """Map a CivitaiRequestError to a semantically correct HTTPException.

    Status code mapping:
        401 → 502 Bad Gateway (auth expired – our gateway cannot fulfill the request)
        4xx → 502 Bad Gateway (other client error from upstream)
        5xx → 503 Service Unavailable (CivitAI is down or in maintenance)
        None / unknown → 503 Service Unavailable
    """
    upstream_status = getattr(exc, "status_code", None)
    message = str(exc)

    if upstream_status == 401:
        return HTTPException(
            status_code=502,
            detail=(
                "CivitAI returned HTTP 401 Unauthorized. "
                "Your session cookie has expired. "
                "Please re-authenticate via /civitai/auth/cookie or /civitai/auth/refresh."
            ),
        )

    if upstream_status and 500 <= upstream_status < 600:
        return HTTPException(
            status_code=503,
            detail=(
                f"CivitAI is currently unavailable (HTTP {upstream_status}). "
                "The service may be under maintenance. "
                "Check https://status.civitai.com/status/public for details."
            ),
        )

    if upstream_status and 400 <= upstream_status < 500:
        return HTTPException(
            status_code=502,
            detail=f"CivitAI returned HTTP {upstream_status}: {message}",
        )

    # No status code (e.g. connection error after retries) – treat as unavailable.
    return HTTPException(
        status_code=503,
        detail=f"Could not reach CivitAI: {message}",
    )


def _fetch_civitai_user_image_collections(
    api: CivitaiAPI,
    *,
    max_age: Optional[timedelta] = None,
    force_refresh: bool = False,
) -> list[dict]:
    try:
        payload = {"authed": True}
        if force_refresh:
            response = api._make_request(
                endpoint="collection.getAllUser",
                payload_data=payload,
                strict=True,
            )
        else:
            response = api.get_cached_or_fetch(
                endpoint="collection.getAllUser",
                payload_data=payload,
                max_age=max_age,
                strict=True,
            )
    except CivitaiRequestError as exc:
        raise _classify_civitai_upstream_error(exc)
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"Could not fetch CivitAI user collections: {e}",
        )

    if not isinstance(response, list):
        raise HTTPException(
            status_code=502,
            detail="CivitAI collection listing returned an unexpected response.",
        )

    collections: list[dict] = []
    seen_ids: set[int] = set()
    for row in response:
        if not isinstance(row, dict):
            continue

        collection_type = str(row.get("type") or "").strip().lower()
        if collection_type not in ("image", "post"):
            continue

        try:
            collection_id = int(row.get("id"))
        except (TypeError, ValueError):
            continue

        if collection_id in seen_ids:
            continue

        seen_ids.add(collection_id)
        name = (
            str(row.get("name") or "").strip() or f"CivitAI Collection {collection_id}"
        )
        collections.append(
            {
                "id": collection_id,
                "name": name,
                "type": collection_type,
            }
        )

    collections.sort(key=lambda item: str(item.get("name") or "").lower())

    # ── Enrich with stored DB metadata (item counts, sync timestamps) ──
    if collections:
        civitai_ids = [c["id"] for c in collections]
        with SessionLocal() as db:
            # Join through junction table; fall back to legacy column for
            # rows not yet migrated.
            from sqlalchemy import or_ as _or_

            rows = (
                db.query(CollectionModel, CollectionCivitaiMapping.civitai_collection_id)
                .join(
                    CollectionCivitaiMapping,
                    CollectionCivitaiMapping.collection_id == CollectionModel.id,
                )
                .filter(
                    CollectionCivitaiMapping.civitai_collection_id.in_(civitai_ids)
                )
                .all()
            )
            by_civitai_id: dict[int, CollectionModel] = {
                civ_id: col for col, civ_id in rows
            }
            # Legacy fallback for unmigrated rows
            unmigrated = [
                cid for cid in civitai_ids
                if cid not in by_civitai_id
            ]
            if unmigrated:
                legacy_rows = (
                    db.query(CollectionModel)
                    .filter(
                        CollectionModel.civitai_collection_id.in_(unmigrated),
                        _or_(
                            ~CollectionModel.id.in_(
                                db.query(CollectionCivitaiMapping.collection_id)
                                .filter(
                                    CollectionCivitaiMapping.civitai_collection_id.in_(unmigrated)
                                )
                                .subquery()
                            ),
                            True,  # keep all rows; the NOT IN above is sufficient
                        ),
                    )
                    .all()
                )
                for r in legacy_rows:
                    if r.civitai_collection_id and r.civitai_collection_id not in by_civitai_id:
                        by_civitai_id[r.civitai_collection_id] = r

        for col in collections:
            db_col = by_civitai_id.get(col["id"])
            if db_col is None:
                continue
            # Prefer the most complete count available
            col["itemCount"] = (
                db_col.civitai_last_full_item_count
                or db_col.civitai_head_item_count
            )
            if db_col.civitai_last_synced_at is not None:
                col["lastSyncedAt"] = _isoformat_or_none(
                    db_col.civitai_last_synced_at
                )
            if db_col.civitai_last_full_scan_at is not None:
                col["lastFullScanAt"] = _isoformat_or_none(
                    db_col.civitai_last_full_scan_at
                )

    return collections


def _build_civitai_image_source_url(image_id: int) -> str:
    return f"{getattr(app_config, 'CIVITAI_WEB_BASE_URL', 'https://civitai.red')}/images/{image_id}"


def _build_civitai_collection_fingerprint(image_ids: list[int]) -> str:
    return ",".join(str(image_id) for image_id in image_ids)


def _probe_civitai_collection_head(
    scraper: CivitaiPrivateScraper,
    *,
    collection_id: int,
    probe_size: int = _CIVITAI_COLLECTION_HEAD_PROBE_SIZE,
) -> _CivitaiCollectionProbe:
    data, next_cursor = scraper._make_collection_request(collection_id, None, False)
    if data is None:
        raise RuntimeError(
            f"Could not fetch CivitAI collection {collection_id} head page."
        )

    page_items = scraper._find_deep_image_list(data) or []
    seen_ids: set[int] = set()
    image_ids: list[int] = []
    for item in page_items:
        raw_id = item.get("id") if isinstance(item, dict) else None
        try:
            image_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if image_id in seen_ids:
            continue
        seen_ids.add(image_id)
        image_ids.append(image_id)
        if len(image_ids) >= probe_size:
            break

    return _CivitaiCollectionProbe(
        image_ids=image_ids,
        fingerprint=_build_civitai_collection_fingerprint(image_ids),
        has_more=bool(next_cursor),
        first_page_items=page_items,
        first_page_cursor=next_cursor,
    )


def _inspect_local_civitai_collection_health(
    db: Session,
    *,
    local_collection_id: int,
) -> tuple[int, bool]:
    collection = (
        db.query(CollectionModel)
        .options(joinedload(CollectionModel.images))
        .filter(CollectionModel.id == local_collection_id)
        .first()
    )
    if collection is None:
        return 0, False

    membership_count = len(collection.images)
    for image in collection.images:
        image_path = Path(IMAGE_LIBRARY_PATH) / str(image.file_path)
        image_status = (image.image_status or "active").lower()
        # Placeholders are a known, expected state for unavailable remote
        # images.  They should NOT force a full verify on every sync pass.
        if image_status == "placeholder":
            continue
        if image_status != "active":
            return membership_count, True
        if not _is_local_media_usable(image_path, image.mimetype):
            return membership_count, True

    return membership_count, False


def _refresh_local_collection_sync_metadata(
    db: Session,
    *,
    civitai_collection_id: int,
    set_full_snapshot: bool = False,
) -> dict[str, Any]:
    """Refresh collection sync metadata from current local membership state.

    This updates ``civitai_last_synced_at`` on every call and refreshes
    ``civitai_head_item_count`` from the current local collection size.

    When ``set_full_snapshot`` is True, this also writes
    ``civitai_last_full_item_count`` + ``civitai_last_full_scan_at``.
    """
    collection = _resolve_local_collection_by_civitai_id(db, civitai_collection_id)
    if collection is None:
        return {
            "updated": False,
            "reason": "collection_not_found",
            "civitai_collection_id": civitai_collection_id,
        }

    local_count = (
        db.query(func.count(ImageCollectionMembership.image_id))
        .filter(ImageCollectionMembership.collection_id == collection.id)
        .scalar()
    )
    local_count = int(local_count or 0)
    now = datetime.utcnow()

    collection.civitai_head_item_count = local_count
    collection.civitai_last_synced_at = now

    if set_full_snapshot:
        collection.civitai_last_full_item_count = local_count
        collection.civitai_last_full_scan_at = now

    db.flush()
    return {
        "updated": True,
        "civitai_collection_id": civitai_collection_id,
        "local_collection_id": collection.id,
        "local_count": local_count,
        "set_full_snapshot": bool(set_full_snapshot),
        "last_synced_at": _isoformat_or_none(collection.civitai_last_synced_at),
        "last_full_scan_at": _isoformat_or_none(collection.civitai_last_full_scan_at),
    }


def _apply_civitai_collection_probe_state(
    collection: CollectionModel,
    *,
    probe: _CivitaiCollectionProbe,
    synced_at: datetime,
    full_item_count: Optional[int] = None,
    mark_full_scan: bool = False,
) -> None:
    collection.civitai_head_fingerprint = probe.fingerprint or None
    collection.civitai_head_item_count = len(probe.image_ids)
    collection.civitai_head_has_more = probe.has_more
    collection.civitai_last_synced_at = synced_at
    if full_item_count is not None:
        collection.civitai_last_full_item_count = full_item_count
    if mark_full_scan:
        collection.civitai_last_full_scan_at = synced_at


def _civitai_collection_requires_full_verify(
    collection: CollectionModel,
    *,
    probe: _CivitaiCollectionProbe,
    local_membership_count: int,
    local_media_incomplete: bool,
    force_full_verify: bool,
) -> tuple[bool, str]:
    if force_full_verify:
        return True, "limit_requested"
    if local_media_incomplete:
        return True, "local_media_incomplete"

    raw_last_full_item_count = getattr(collection, "civitai_last_full_item_count", None)
    last_full_item_count = (
        int(raw_last_full_item_count)
        if isinstance(raw_last_full_item_count, int)
        else None
    )
    if last_full_item_count is None:
        return True, "missing_full_snapshot"
    if local_membership_count != last_full_item_count:
        return True, "local_membership_count_mismatch"

    head_fingerprint = str(getattr(collection, "civitai_head_fingerprint", "") or "")
    if head_fingerprint != probe.fingerprint:
        return True, "remote_head_changed"

    head_has_more = bool(getattr(collection, "civitai_head_has_more", False))
    if head_has_more != probe.has_more:
        return True, "remote_page_shape_changed"

    if (
        last_full_item_count <= _CIVITAI_COLLECTION_HEAD_PROBE_SIZE
        and not probe.has_more
    ):
        return False, "head_matches_complete_collection"

    last_full_scan_at = getattr(collection, "civitai_last_full_scan_at", None)
    if not isinstance(last_full_scan_at, datetime):
        return True, "missing_full_scan_timestamp"

    age_seconds = max(0.0, (datetime.utcnow() - last_full_scan_at).total_seconds())
    if age_seconds > _CIVITAI_COLLECTION_FULL_VERIFY_MAX_AGE_SECONDS:
        return True, "periodic_full_verify_due"

    return False, "head_matches_recent_verify"


def _build_civitai_collection_skip_summary(
    *,
    collection_id: int,
    collection_name: str,
    local_collection_snapshot: dict,
    sync_state: str,
) -> dict[str, Any]:
    return {
        "civitai_collection_id": collection_id,
        "civitai_collection_name": collection_name,
        "local_collection": local_collection_snapshot,
        "requested": 0,
        "images_added": 0,
        "images_skipped": 0,
        "images_recovered": 0,
        "images_cancelled": 0,
        "json_files_created": 0,
        "memberships_removed": 0,
        "errors": [],
        "unavailable_items": [],
        "results": [],
        "sync_state": sync_state,
    }


def _build_civitai_empty_collection_sync_summary(
    *,
    collection_id: int,
    collection_name: str,
    local_collection_snapshot: dict,
    memberships_removed: int,
) -> dict[str, Any]:
    summary = _build_civitai_collection_skip_summary(
        collection_id=collection_id,
        collection_name=collection_name,
        local_collection_snapshot=local_collection_snapshot,
        sync_state="empty_verified",
    )
    summary["memberships_removed"] = memberships_removed
    return summary


def _summarize_civitai_collection_item_stub(
    collection_item: Optional[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    if not isinstance(collection_item, dict):
        return None

    item: dict[str, Any] = collection_item
    metadata: dict[str, Any] = (
        item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    )
    user: dict[str, Any] = (
        item.get("user") if isinstance(item.get("user"), dict) else {}
    )
    account: dict[str, Any] = (
        item.get("account") if isinstance(item.get("account"), dict) else {}
    )
    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "url_hash": item.get("url"),
        "mime_type": item.get("mimeType"),
        "type": item.get("type"),
        "created_at": item.get("createdAt"),
        "published_at": item.get("publishedAt"),
        "post_id": item.get("postId"),
        "user_id": item.get("userId") or user.get("id") or account.get("id"),
        "username": item.get("username")
        or user.get("username")
        or account.get("username"),
        "nsfw_level": item.get("nsfwLevel"),
        "has_meta": item.get("hasMeta"),
        "has_positive_prompt": item.get("hasPositivePrompt"),
        "on_site": item.get("onSite"),
        "ingestion": item.get("ingestion"),
        "blocked_for": item.get("blockedFor"),
        "needs_review": item.get("needsReview"),
        "tos_violation": item.get("tosViolation"),
        "declared_file_size": metadata.get("size"),
        "width": item.get("width") or metadata.get("width"),
        "height": item.get("height") or metadata.get("height"),
    }


def _probe_civitai_endpoint_status(
    api: CivitaiAPI, endpoint: str, payload_data: dict[str, Any]
) -> dict[str, Any]:
    try:
        response = api._make_request(
            endpoint=endpoint, payload_data=payload_data, strict=True
        )
        return {
            "status_code": 200,
            "available": response is not None,
            "has_payload": response is not None,
        }
    except CivitaiRequestError as exc:
        return {
            "status_code": exc.status_code,
            "available": False,
            "has_payload": False,
            "error": str(exc),
        }
    except Exception as exc:
        return {
            "status_code": None,
            "available": False,
            "has_payload": False,
            "error": str(exc),
        }


def _probe_civitai_media_status(api: CivitaiAPI, media_url: str) -> dict[str, Any]:
    response = None
    try:
        response = api.http_client.request("GET", media_url, stream=True)
        return {
            "status_code": response.status_code,
            "reachable": True,
            "content_type": response.headers.get("Content-Type"),
            "content_length": response.headers.get("Content-Length"),
            "url": media_url,
        }
    except CivitaiRequestError as exc:
        return {
            "status_code": exc.status_code,
            "reachable": False,
            "error": str(exc),
            "url": media_url,
        }
    except Exception as exc:
        return {
            "status_code": None,
            "reachable": False,
            "error": str(exc),
            "url": media_url,
        }
    finally:
        if response is not None:
            response.close()


def _classify_civitai_unavailable_diagnostics(diagnostics: dict[str, Any]) -> str:
    collection_stub: dict[str, Any] = (
        diagnostics.get("collection_stub")
        if isinstance(diagnostics.get("collection_stub"), dict)
        else {}
    )
    endpoint_status: dict[str, Any] = (
        diagnostics.get("endpoint_status")
        if isinstance(diagnostics.get("endpoint_status"), dict)
        else {}
    )
    image_get: dict[str, Any] = (
        endpoint_status.get("image.get")
        if isinstance(endpoint_status.get("image.get"), dict)
        else {}
    )
    generation_get: dict[str, Any] = (
        endpoint_status.get("image.getGenerationData")
        if isinstance(endpoint_status.get("image.getGenerationData"), dict)
        else {}
    )
    media_probe: dict[str, Any] = (
        diagnostics.get("media_probe")
        if isinstance(diagnostics.get("media_probe"), dict)
        else {}
    )
    image_get_status = image_get.get("status_code")
    generation_status = generation_get.get("status_code")
    media_status = media_probe.get("status_code")

    ingestion = str(collection_stub.get("ingestion") or "").strip().lower()
    blocked_for = str(collection_stub.get("blocked_for") or "").strip()
    if (
        blocked_for
        or ingestion == "blocked"
        or bool(collection_stub.get("tos_violation"))
    ):
        return "collection_reference_blocked_or_moderated"
    if image_get_status == 404 and generation_status == 404 and media_status == 404:
        if diagnostics.get("collection_reference_present"):
            return "collection_reference_stale_or_asset_removed"
        return "image_and_asset_missing"
    if image_get_status == 404 and generation_status == 404 and media_status == 200:
        return "metadata_missing_but_asset_present"
    if image_get_status == 404 and generation_status == 200:
        return "basic_info_missing"
    if image_get_status == 200 and generation_status == 404:
        return "generation_data_missing"
    if media_status == 403:
        return "media_access_forbidden"
    if media_status == 404:
        return "media_asset_missing"
    return "unknown_remote_unavailable"


def _diagnose_civitai_unavailable_item(
    api: Optional[CivitaiAPI],
    *,
    image_id: int,
    collection_item: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {
        "collection_reference_present": isinstance(collection_item, dict),
        "collection_stub": _summarize_civitai_collection_item_stub(collection_item),
    }
    if api is None:
        diagnostics["classification"] = _classify_civitai_unavailable_diagnostics(
            diagnostics
        )
        return diagnostics

    diagnostics["endpoint_status"] = {
        "image.get": _probe_civitai_endpoint_status(
            api, "image.get", {"id": int(image_id), "authed": True}
        ),
        "image.getGenerationData": _probe_civitai_endpoint_status(
            api,
            "image.getGenerationData",
            {"id": int(image_id), "authed": True},
        ),
    }

    if isinstance(collection_item, dict):
        preferred_name = (
            collection_item.get("name")
            if isinstance(collection_item.get("name"), str)
            else None
        )
        url_hash = collection_item.get("url")
        mime_type = (
            collection_item.get("mimeType")
            if isinstance(collection_item.get("mimeType"), str)
            else None
        )
        original_filename = _build_civitai_original_filename(
            image_id=image_id,
            preferred_name=preferred_name,
            image_url=str(url_hash or ""),
            mime_type=mime_type,
        )
        media_url = _build_civitai_media_url(
            url_hash,
            original_filename,
            mime_type,
            use_video_transcode=True,
        )
        if media_url:
            diagnostics["media_probe"] = _probe_civitai_media_status(api, media_url)

    diagnostics["classification"] = _classify_civitai_unavailable_diagnostics(
        diagnostics
    )
    return diagnostics


def _collection_context_item(
    collection_context: Optional[dict[str, Any]],
    image_id: int,
) -> Optional[dict[str, Any]]:
    item_index = (collection_context or {}).get("collection_item_index")
    if not isinstance(item_index, dict):
        return None
    candidate = item_index.get(image_id)
    return candidate if isinstance(candidate, dict) else None


def _get_or_create_collection(
    db: Session,
    name: str,
    source: str = "user",
    civitai_collection_id: Optional[int] = None,
) -> CollectionModel:
    normalized_name = _normalize_collection_name(name)

    # ── 1. Existing mapping via junction table ──────────────────────────
    if civitai_collection_id is not None:
        existing_by_remote = _resolve_local_collection_by_civitai_id(
            db, civitai_collection_id
        )
        if existing_by_remote is not None:
            # Rename if the name has changed and no *other* collection owns it
            if not _collection_name_exists(
                db,
                normalized_name,
                exclude_collection_id=existing_by_remote.id,
            ):
                existing_by_remote.name = normalized_name
            if (
                source == "civitai"
                and str(existing_by_remote.source or "") != "civitai"
            ):
                existing_by_remote.source = "civitai"
            # Keep legacy column in sync for backward compatibility
            if existing_by_remote.civitai_collection_id is None:
                existing_by_remote.civitai_collection_id = civitai_collection_id
            db.flush()
            return existing_by_remote

    # ── 2. Existing collection with the same name ──────────────────────
    #     If found, add a junction-table mapping instead of creating a
    #     suffixed duplicate.  This allows multiple CivitAI collection IDs
    #     (e.g. an image-collection and a post-collection with the same
    #     title) to share one local collection.
    existing = (
        db.query(CollectionModel)
        .filter(CollectionModel.name == normalized_name)
        .first()
    )
    if existing:
        if civitai_collection_id is not None:
            _ensure_civitai_mapping(db, existing.id, civitai_collection_id)
            # Keep legacy column if it was previously unset
            if existing.civitai_collection_id is None:
                existing.civitai_collection_id = civitai_collection_id
        if (
            source == "civitai"
            and str(existing.source or "") == "user"
        ):
            existing.source = "civitai"
        db.flush()
        return existing

    # ── 3. Create brand-new collection + mapping ────────────────────────
    created = CollectionModel(
        name=normalized_name,
        source=source,
        civitai_collection_id=civitai_collection_id,
    )
    db.add(created)
    db.flush()
    if civitai_collection_id is not None:
        _ensure_civitai_mapping(db, created.id, civitai_collection_id)
    return created


def _ensure_image_in_collection(db: Session, image_id: int, collection_id: int) -> None:
    # Resolve collection_id: the FK targets collections.id (local DB id), but
    # callers may pass the CivitAI collection id instead.  Try the local PK
    # first; if that doesn't match, look up via the junction table (and fall
    # back to the legacy column for rows that haven't been migrated yet).
    local_collection_id: Optional[int] = collection_id
    if not db.query(CollectionModel).filter(CollectionModel.id == collection_id).first():
        resolved = _resolve_local_collection_by_civitai_id(db, collection_id)
        if resolved is not None:
            local_collection_id = resolved.id
        else:
            # No local collection matches — SQLite won't enforce the FK, so
            # the membership would be silently orphaned.  Bail out with a
            # warning so the caller can create the collection first.
            logging.getLogger(__name__).warning(
                "_ensure_image_in_collection: no local collection found for "
                "id=%s (looked up by local PK, junction table, and legacy "
                "civitai_collection_id); image %s will NOT be attached to any collection",
                collection_id, image_id,
            )
            return

    existing = (
        db.query(ImageCollectionMembership)
        .filter(
            ImageCollectionMembership.image_id == image_id,
            ImageCollectionMembership.collection_id == local_collection_id,
        )
        .first()
    )
    if existing is not None:
        return

    db.add(ImageCollectionMembership(image_id=image_id, collection_id=local_collection_id))
    db.flush()


def _remove_images_not_in_collection_set(
    db: Session, collection_id: int, keep_image_ids: set[int]
) -> int:
    memberships = (
        db.query(ImageCollectionMembership)
        .filter(ImageCollectionMembership.collection_id == collection_id)
        .all()
    )
    removed = 0
    for membership in memberships:
        if int(membership.image_id) in keep_image_ids:
            continue
        db.delete(membership)
        removed += 1
    if removed:
        db.flush()
    return removed


def _task_item_key(prefix: str, image_id: int) -> str:
    return f"{prefix}:image:{image_id}"


def _parse_retry_failed_item(item_key: str) -> Optional[_RetryFailedItem]:
    parts = str(item_key or "").split(":")
    if len(parts) == 3 and parts[0] == "civitai-image-import" and parts[1] == "image":
        try:
            return _RetryFailedItem(image_id=int(parts[2]), civitai_collection_id=None)
        except ValueError:
            return None

    # Retry jobs can produce standalone keys in the form
    # retry:standalone:image:<image_id>.
    if (
        len(parts) == 4
        and parts[0] == "retry"
        and parts[1] == "standalone"
        and parts[2] == "image"
    ):
        try:
            return _RetryFailedItem(image_id=int(parts[3]), civitai_collection_id=None)
        except ValueError:
            return None

    # Defensive support for collection retry keys if emitted as
    # retry:collection:<collection_id>:image:<image_id>.
    if (
        len(parts) == 5
        and parts[0] == "retry"
        and parts[1] == "collection"
        and parts[3] == "image"
    ):
        try:
            return _RetryFailedItem(
                image_id=int(parts[4]),
                civitai_collection_id=int(parts[2]),
            )
        except ValueError:
            return None

    if len(parts) == 4 and parts[0] == "collection" and parts[2] == "image":
        try:
            return _RetryFailedItem(
                image_id=int(parts[3]),
                civitai_collection_id=int(parts[1]),
            )
        except ValueError:
            return None

    return None


def _get_retry_failed_items_from_task(
    task_payload: dict[str, Any],
) -> tuple[list[_RetryFailedItem], list[str]]:
    failed_items = task_payload.get("failed_items")
    if not isinstance(failed_items, list):
        return [], []

    parsed: list[_RetryFailedItem] = []
    skipped: list[str] = []
    seen: set[tuple[int, Optional[int]]] = set()
    for entry in failed_items:
        item_key = entry.get("item_key") if isinstance(entry, dict) else None
        parsed_item = _parse_retry_failed_item(str(item_key or ""))
        if parsed_item is None:
            if item_key:
                skipped.append(str(item_key))
            continue
        dedupe_key = (parsed_item.image_id, parsed_item.civitai_collection_id)
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        parsed.append(parsed_item)

    return parsed, skipped


def _ensure_local_civitai_collection_for_retry(
    api: CivitaiAPI,
    *,
    civitai_collection_id: int,
) -> tuple[dict, int]:
    collection_name = _fetch_civitai_collection_name(api, civitai_collection_id)
    with SessionLocal() as db:
        local_collection = _get_or_create_collection(
            db,
            name=collection_name,
            source="civitai",
            civitai_collection_id=civitai_collection_id,
        )
        _commit_with_lock_retry(
            db, context=f"Collection setup commit for retry {civitai_collection_id}"
        )
        return _serialize_collection(local_collection), int(local_collection.id)


def _build_failed_civitai_import_result(image_id: int, error: str) -> dict:
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
        "error": error,
        "cancelled": False,
    }


def _build_skipped_civitai_import_result(
    image_id: int,
    skip_reason: str,
    *,
    skip_message: Optional[str] = None,
) -> dict:
    result = _build_failed_civitai_import_result(image_id, "")
    result["images_skipped"] = 1
    result["skip_reason"] = skip_reason
    result["skip_message"] = skip_message
    result["error"] = None
    return result


def _build_cancelled_civitai_import_result(image_id: int) -> dict:
    result = _build_failed_civitai_import_result(image_id, "")
    result["cancelled"] = True
    result["error"] = None
    return result


def _build_civitai_unavailable_item_detail(
    image_id: int,
    exc: Exception,
    *,
    api: Optional[CivitaiAPI] = None,
    collection_id: Optional[int] = None,
    collection_name: Optional[str] = None,
    collection_item: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    endpoint: Optional[str] = None
    status_code: Optional[int] = None
    source_url = _build_civitai_image_source_url(image_id)
    reason = str(exc)
    diagnostics: dict[str, Any] = {}

    if isinstance(exc, _CivitaiImageUnavailableError):
        endpoint = exc.endpoint
        status_code = exc.status_code
        source_url = exc.source_url or source_url
        reason = exc.reason
        diagnostics = dict(exc.diagnostics or {})
    elif isinstance(exc, HTTPException):
        status_code = int(exc.status_code)
        reason = str(exc.detail)
    elif isinstance(exc, CivitaiRequestError):
        status_code = exc.status_code

    if not diagnostics:
        diagnostics = _diagnose_civitai_unavailable_item(
            api,
            image_id=image_id,
            collection_item=collection_item,
        )

    return {
        "image_id": image_id,
        "source_url": source_url,
        "status_code": status_code,
        "endpoint": endpoint,
        "reason": reason,
        "collection_id": collection_id,
        "collection_name": collection_name,
        "diagnostics": diagnostics,
        "classification": diagnostics.get("classification"),
    }


def _format_civitai_unavailable_skip_message(detail: dict[str, Any]) -> str:
    image_id = detail.get("image_id")
    status_code = detail.get("status_code")
    endpoint = detail.get("endpoint")
    reason = str(detail.get("reason") or "").strip()
    classification = str(detail.get("classification") or "").strip()

    qualifiers: list[str] = []
    if status_code is not None:
        qualifiers.append(f"HTTP {status_code}")
    if endpoint:
        qualifiers.append(f"endpoint {endpoint}")
    if classification:
        qualifiers.append(classification)

    if qualifiers:
        prefix = (
            f"Remote CivitAI image {image_id} is unavailable ({', '.join(qualifiers)})"
        )
    else:
        prefix = f"Remote CivitAI image {image_id} is unavailable"

    if reason:
        return f"{prefix}: {reason}"
    return prefix


def _log_civitai_unavailable_item(detail: dict[str, Any]) -> None:
    parts = [f"image_id={detail.get('image_id')}"]
    collection_id = detail.get("collection_id")
    collection_name = detail.get("collection_name")
    status_code = detail.get("status_code")
    endpoint = detail.get("endpoint")
    source_url = detail.get("source_url")
    reason = detail.get("reason")
    classification = detail.get("classification")
    diagnostics: dict[str, Any] = (
        detail.get("diagnostics") if isinstance(detail.get("diagnostics"), dict) else {}
    )
    collection_stub: dict[str, Any] = (
        diagnostics.get("collection_stub")
        if isinstance(diagnostics.get("collection_stub"), dict)
        else {}
    )
    media_probe: dict[str, Any] = (
        diagnostics.get("media_probe")
        if isinstance(diagnostics.get("media_probe"), dict)
        else {}
    )

    if collection_id is not None:
        parts.append(f"collection_id={collection_id}")
    if collection_name:
        parts.append(f"collection_name={collection_name}")
    if status_code is not None:
        parts.append(f"status={status_code}")
    if endpoint:
        parts.append(f"endpoint={endpoint}")
    if source_url:
        parts.append(f"source_url={source_url}")
    if classification:
        parts.append(f"classification={classification}")
    if collection_stub.get("blocked_for"):
        parts.append(f"blocked_for={collection_stub.get('blocked_for')}")
    if collection_stub.get("ingestion"):
        parts.append(f"ingestion={collection_stub.get('ingestion')}")
    if media_probe.get("status_code") is not None:
        parts.append(f"media_status={media_probe.get('status_code')}")
    if reason:
        parts.append(f"reason={reason}")

    logging.getLogger("atelierai.civitai.import").warning(
        "Skipping unavailable CivitAI item: %s",
        " | ".join(parts),
    )


def _build_civitai_unavailable_result(
    image_id: int,
    exc: Exception,
    *,
    api: Optional[CivitaiAPI] = None,
    db: Optional[Session] = None,
    attach_collection_id: Optional[int] = None,
    collection_id: Optional[int] = None,
    collection_name: Optional[str] = None,
    collection_item: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    detail = _build_civitai_unavailable_item_detail(
        image_id,
        exc,
        api=api,
        collection_id=collection_id,
        collection_name=collection_name,
        collection_item=collection_item,
    )
    result = _build_skipped_civitai_import_result(
        image_id,
        "remote_not_found",
        skip_message=_format_civitai_unavailable_skip_message(detail),
    )
    placeholder_image_id: Optional[int] = None
    placeholder_created = False
    if db is not None:
        source_url = str(
            detail.get("source_url") or _build_civitai_image_source_url(image_id)
        ).strip()
        existing = _find_existing_image_by_source_url(db, source_url)
        existing_status = (
            (getattr(existing, "image_status", None) or "active").lower()
            if existing is not None
            else ""
        )

        if existing is not None and existing_status not in ("placeholder",):
            # Existing real image that 404'd — mark as deleted from CivitAI.
            if existing.civitai_deleted_at is None:
                existing.civitai_deleted_at = datetime.utcnow()
                existing.date_modified = datetime.utcnow()
                _commit_with_lock_retry(
                    db,
                    context=f"CivitAI deletion timestamp for image {image_id}",
                )

        if existing is None or existing_status == "placeholder":
            placeholder_hash = hashlib.sha256(
                f"civitai-placeholder:{source_url}".encode("utf-8")
            ).hexdigest()
            placeholder_path = (
                f"placeholders/{placeholder_hash[:2]}/{placeholder_hash}.placeholder"
            )
            placeholder_reason = (
                str(
                    detail.get("classification") or "civitai_remote_unavailable"
                ).strip()
                or "civitai_remote_unavailable"
            )

            civitai_unavailable_payload = {
                "unavailable_detail": detail,
                "placeholder": {
                    "kind": "civitai_remote_unavailable",
                    "updated_at": datetime.utcnow().isoformat(),
                },
            }

            if existing is None:
                placeholder = ImageModel(
                    file_path=placeholder_path,
                    file_name=f"civitai-unavailable-{image_id}.placeholder",
                    file_hash=placeholder_hash,
                    file_size=0,
                    width=None,
                    height=None,
                    mimetype="application/x-civitai-placeholder",
                    date_created=datetime.utcnow(),
                    date_modified=datetime.utcnow(),
                    image_status="placeholder",
                    status_reason=placeholder_reason,
                    replaced_by_image_id=None,
                    source_url=source_url,
                    source_site="civitai",
                    civitai_image_id=image_id,
                    exif_data={},
                    json_metadata={"civitai": civitai_unavailable_payload},
                )
                db.add(placeholder)
                db.flush()
                placeholder_image_id = int(placeholder.id)
                placeholder_created = True
            else:
                merged_json = (
                    dict(existing.json_metadata)
                    if isinstance(existing.json_metadata, dict)
                    else {}
                )
                civitai_json = (
                    merged_json.get("civitai")
                    if isinstance(merged_json.get("civitai"), dict)
                    else {}
                )
                civitai_json = cast(dict[str, Any], civitai_json)
                civitai_json.update(civitai_unavailable_payload)
                merged_json["civitai"] = civitai_json

                existing.image_status = "placeholder"
                existing.status_reason = placeholder_reason
                existing.replaced_by_image_id = None
                existing.source_url = source_url
                existing.source_site = "civitai"
                existing.civitai_image_id = image_id
                existing.mimetype = (
                    existing.mimetype or "application/x-civitai-placeholder"
                )
                existing.json_metadata = merged_json
                existing.date_modified = datetime.utcnow()
                placeholder_image_id = int(existing.id)

            if attach_collection_id is not None and placeholder_image_id is not None:
                _ensure_image_in_collection(
                    db, placeholder_image_id, attach_collection_id
                )

            _commit_with_lock_retry(
                db,
                context=f"Unavailable placeholder commit for CivitAI image {image_id}",
            )

    result["unavailable_detail"] = detail
    result["placeholder_image_id"] = placeholder_image_id
    result["placeholder_created"] = placeholder_created
    # Expose as image_db_id so callers (e.g. _process_civitai_image_ids)
    # include the placeholder in desired_image_db_ids, preventing
    # _remove_images_not_in_collection_set from pruning its membership.
    if placeholder_image_id is not None:
        result["image_db_id"] = placeholder_image_id
    _log_civitai_unavailable_item(detail)
    return result


def _collect_civitai_unavailable_items(
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    unavailable_items: list[dict[str, Any]] = []
    for result in results:
        detail = result.get("unavailable_detail")
        if isinstance(detail, dict):
            unavailable_items.append(detail)
    return unavailable_items


def _is_civitai_remote_not_found_error(exc: Exception) -> bool:
    if isinstance(exc, _CivitaiImageUnavailableError):
        return exc.status_code == 404
    if isinstance(exc, HTTPException):
        return exc.status_code == 404
    if isinstance(exc, CivitaiRequestError):
        return exc.status_code == 404
    return False


def _apply_civitai_task_result(
    task_context: TaskContext,
    *,
    item_key: str,
    image_id: int,
    result: dict,
) -> None:
    if result.get("cancelled"):
        task_context.increment_counter("images_cancelled", 1)
        task_context.mark_item(item_key, "cancelled", "Cancelled")
        task_context.advance()
        return

    if result.get("error"):
        task_context.increment_counter("images_failed", 1)
        task_context.add_error(f"Image {image_id}: {result['error']}")
        # Categorize failure: unavailable_detail indicates missing (404/deleted),
        # everything else is a temporary failure (timeout, rate-limit, etc.).
        unavailable_detail = result.get("unavailable_detail")
        item_data = {"image_id": image_id, "error": result["error"]}
        if isinstance(unavailable_detail, dict):
            task_context.mark_missing_failure(
                item_key,
                str(result["error"]),
                item_data={**item_data, "unavailable_detail": unavailable_detail},
            )
        else:
            task_context.mark_temporary_failure(
                item_key,
                str(result["error"]),
                item_data=item_data,
            )
        task_context.mark_item(item_key, "failed", str(result["error"]))
        task_context.advance()
        return

    for key in (
        "images_added",
        "images_skipped",
        "images_recovered",
        "json_files_created",
    ):
        value = int(result.get(key, 0) or 0)
        if value:
            task_context.increment_counter(key, value)

    if result.get("metadata_backfilled"):
        task_context.increment_counter("metadata_backfilled", 1)

    if int(result.get("images_added", 0) or 0) > 0:
        task_context.mark_item(item_key, "completed", "Imported")
    elif int(result.get("images_skipped", 0) or 0) > 0:
        unavailable_detail = result.get("unavailable_detail")
        if isinstance(unavailable_detail, dict):
            task_context.add_error(
                _format_civitai_unavailable_skip_message(unavailable_detail)
            )
        task_context.mark_item(
            item_key,
            "skipped",
            str(
                result.get("skip_message")
                or result.get("skip_reason")
                or "Already present"
            ),
        )
    else:
        task_context.mark_item(item_key, "completed", "Processed")
    task_context.advance()


def _snapshot_civitai_payload_retry_metrics(api: CivitaiAPI) -> dict[str, Any]:
    try:
        snapshot = api.get_payload_retry_metrics_snapshot()
    except Exception:
        return {"total": 0, "by_status": {}, "by_endpoint": {}}

    total = int(snapshot.get("total", 0) or 0) if isinstance(snapshot, dict) else 0
    by_status_raw = snapshot.get("by_status") if isinstance(snapshot, dict) else {}
    by_endpoint_raw = snapshot.get("by_endpoint") if isinstance(snapshot, dict) else {}
    by_status = {
        str(key): int(value or 0)
        for key, value in (
            by_status_raw.items() if isinstance(by_status_raw, dict) else []
        )
        if int(value or 0) >= 0
    }
    by_endpoint = {
        str(key): int(value or 0)
        for key, value in (
            by_endpoint_raw.items() if isinstance(by_endpoint_raw, dict) else []
        )
        if int(value or 0) >= 0
    }
    return {
        "total": max(0, total),
        "by_status": by_status,
        "by_endpoint": by_endpoint,
    }


def _diff_civitai_payload_retry_metrics(
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    before_total = int(before.get("total", 0) or 0)
    after_total = int(after.get("total", 0) or 0)

    def _delta_map(before_map: Any, after_map: Any) -> dict[str, int]:
        before_dict = before_map if isinstance(before_map, dict) else {}
        after_dict = after_map if isinstance(after_map, dict) else {}
        keys = set(before_dict.keys()) | set(after_dict.keys())
        deltas: dict[str, int] = {}
        for key in keys:
            delta = int(after_dict.get(key, 0) or 0) - int(before_dict.get(key, 0) or 0)
            if delta > 0:
                deltas[str(key)] = delta
        return deltas

    return {
        "total": max(0, after_total - before_total),
        "by_status": _delta_map(before.get("by_status"), after.get("by_status")),
        "by_endpoint": _delta_map(before.get("by_endpoint"), after.get("by_endpoint")),
    }


def _record_civitai_payload_retry_metrics(
    task_context: TaskContext, metrics: dict[str, Any]
) -> None:
    total = int(metrics.get("total", 0) or 0)
    if total <= 0:
        return

    task_context.increment_counter("civitai_payload_retries", total)

    by_status_raw = metrics.get("by_status")
    by_status = by_status_raw if isinstance(by_status_raw, dict) else {}
    for status_code, count in by_status.items():
        normalized_count = int(count or 0)
        if normalized_count <= 0:
            continue
        normalized_status = str(status_code).strip() or "unknown"
        task_context.increment_counter(
            f"civitai_payload_retries_http_{normalized_status}",
            normalized_count,
        )

    by_endpoint_raw = metrics.get("by_endpoint")
    by_endpoint = by_endpoint_raw if isinstance(by_endpoint_raw, dict) else {}
    for endpoint, count in by_endpoint.items():
        normalized_count = int(count or 0)
        if normalized_count <= 0:
            continue
        normalized_endpoint = (
            re.sub(r"[^0-9a-zA-Z_]+", "_", str(endpoint or "")).strip("_").lower()
        )
        if not normalized_endpoint:
            normalized_endpoint = "unknown"
        task_context.increment_counter(
            f"civitai_payload_retries_endpoint_{normalized_endpoint}",
            normalized_count,
        )


def _build_civitai_import_summary(
    *,
    import_type: str,
    requested: int,
    results: list[dict],
    local_collection: Optional[dict] = None,
    civitai_payload_retry_metrics: Optional[dict[str, Any]] = None,
) -> dict:
    unavailable_items = _collect_civitai_unavailable_items(results)
    return {
        "message": "CivitAI import complete.",
        "import_type": import_type,
        "local_collection": local_collection,
        "requested": requested,
        "images_added": sum(int(r.get("images_added", 0) or 0) for r in results),
        "images_skipped": sum(int(r.get("images_skipped", 0) or 0) for r in results),
        "images_recovered": sum(
            int(r.get("images_recovered", 0) or 0) for r in results
        ),
        "images_cancelled": sum(1 for r in results if r.get("cancelled")),
        "json_files_created": sum(
            int(r.get("json_files_created", 0) or 0) for r in results
        ),
        "errors": [
            f"Image {r.get('image_id')}: {r['error']}"
            for r in results
            if r.get("error")
        ],
        "unavailable_items": unavailable_items,
        "warnings": _get_runtime_warnings(),
        "results": results,
        "civitai_payload_retry_metrics": civitai_payload_retry_metrics
        or {
            "total": 0,
            "by_status": {},
            "by_endpoint": {},
        },
    }


def _sync_civitai_tags_from_sidecar(db: Session, image_path: Path) -> None:
    """Read CivitAI tags from a sidecar JSON file and upsert them into
    authority_terms so legacy terms gain their numeric external_tag_id.

    This is a lightweight alternative to full metadata backfill — it only
    touches the taxonomy, not the image record itself.
    """
    sidecar_path = image_path.with_suffix(".json")
    if not sidecar_path.exists():
        return
    try:
        with open(sidecar_path, "r", encoding="utf-8") as f:
            sidecar_data = json.load(f)
        if not isinstance(sidecar_data, dict):
            return
        civitai_payload = sidecar_data.get("civitai")
        if not isinstance(civitai_payload, dict):
            return
        tags = civitai_payload.get("tags")
        if not isinstance(tags, list) or not tags:
            return
        stats = _upsert_civitai_authority_terms(db, {"tags": tags})
        if stats.get("terms_updated"):
            db.commit()
    except Exception as exc:
        logging.getLogger(__name__).debug(
            "Sidecar tag sync failed for %s: %s", image_path.name, exc
        )
        try:
            db.rollback()
        except Exception:
            pass


def _handle_existing_civitai_image(
    db: Session,
    *,
    image_id: int,
    attach_collection_id: Optional[int] = None,
    backfill_metadata: bool = True,
) -> tuple[Optional[dict], bool]:
    source_url = _build_civitai_image_source_url(image_id)
    existing_by_source = _find_existing_image_by_source_url(db, source_url)
    if existing_by_source is None:
        return None, False

    existing_status = (
        getattr(existing_by_source, "image_status", None) or "active"
    ).lower()
    if existing_status == "placeholder":
        if attach_collection_id is not None:
            _ensure_image_in_collection(db, existing_by_source.id, attach_collection_id)
        return {
            "image_id": image_id,
            "image_db_id": existing_by_source.id,
            "images_added": 0,
            "images_skipped": 1,
            "images_recovered": 0,
            "json_files_created": 0,
            "metadata_backfilled": False,
            "skip_reason": "placeholder_source_url",
            "existing_image_id": existing_by_source.id,
            "existing_file_hash": existing_by_source.file_hash,
            "existing_file_path": existing_by_source.file_path,
            "existing_source_url": existing_by_source.source_url,
            "error": None,
            "cancelled": False,
        }, False

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
            "cancelled": False,
        }, False

    if existing_status == "deleted":
        existing_by_source.image_status = "active"
        existing_by_source.status_reason = None
        existing_by_source.replaced_by_image_id = None
        if attach_collection_id is not None:
            _ensure_image_in_collection(db, existing_by_source.id, attach_collection_id)
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
            "cancelled": False,
        }, False

    existing_path = Path(IMAGE_LIBRARY_PATH) / str(existing_by_source.file_path)
    if _is_local_media_usable(existing_path, existing_by_source.mimetype):
        metadata_backfilled = False
        if backfill_metadata:
            metadata_backfilled = _ensure_civitai_metadata_for_existing_image(
                db=db,
                image=existing_by_source,
                source_url=source_url,
            )
        else:
            # Even when full metadata backfill is not requested, sync
            # tags from the sidecar into authority_terms so that legacy
            # terms gain their external_tag_id during normal syncs.
            _sync_civitai_tags_from_sidecar(db, existing_path)
        if attach_collection_id is not None:
            _ensure_image_in_collection(db, existing_by_source.id, attach_collection_id)
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
            "cancelled": False,
        }, False

    _remove_local_image_record(db, existing_by_source)
    return None, True


def _prepare_civitai_download(
    api: CivitaiAPI,
    *,
    image_id: int,
    item_key: str,
    task_context: TaskContext,
    collection_context: Optional[dict[str, Any]] = None,
) -> _PreparedCivitaiImport:
    # Extract collection listing item BEFORE resolve so we can skip image.get
    collection_item = _collection_context_item(collection_context, image_id)

    # ── Phase 4: local DB tag resolution (zero API calls) ──────────────
    raw_tag_records: list[dict[str, Any]] = []
    unresolved_tag_ids: set[int] = set()
    tags_fully_resolved_locally = False

    if isinstance(collection_item, dict):
        listing_tag_ids = collection_item.get("tagIds")
        if isinstance(listing_tag_ids, list) and listing_tag_ids:
            int_tag_ids: list[int] = []
            for raw_tid in listing_tag_ids:
                try:
                    int_tag_ids.append(int(raw_tid))
                except (TypeError, ValueError):
                    pass
            if int_tag_ids:
                try:
                    with SessionLocal() as db:
                        resolved, unresolved_tag_ids = _resolve_tag_ids_from_local_db(
                            db, int_tag_ids
                        )
                    raw_tag_records.extend(resolved)
                    if resolved and not unresolved_tag_ids:
                        tags_fully_resolved_locally = True
                except Exception:
                    # DB lookup failure — fall through to API
                    unresolved_tag_ids = set()

    # ── Phase 3: batch fetch (generation_data + tags in one HTTP call) ─
    pre_fetched_generation_data: Optional[dict[str, Any]] = None

    if not tags_fully_resolved_locally:
        # Tags need API fetch — batch generation_data + tag.getVotableTags
        # into a single HTTP call to reduce API overhead.
        task_context.mark_item(item_key, "fetching_metadata", "Fetching CivitAI metadata (batch)")
        try:
            batch_result = api.fetch_batch_for_image(image_id)
            pre_fetched_generation_data = batch_result.get("generation_data")
            batch_tag_records = batch_result.get("tag_records")
            if batch_tag_records:
                # Merge: prefer batch API records (more complete/fresh) but
                # keep locally-resolved records for tags the API might not return.
                api_ids = {t.get("id") for t in batch_tag_records if isinstance(t, dict)}
                local_only = [
                    t for t in raw_tag_records
                    if isinstance(t, dict) and t.get("id") not in api_ids
                ]
                raw_tag_records = batch_tag_records + local_only
        except Exception:
            pass  # Best-effort; fall through to individual fetch in _resolve

    # ── Resolve image target (basic_info + generation_data) ────────────
    if pre_fetched_generation_data is None:
        task_context.mark_item(item_key, "fetching_metadata", "Fetching CivitAI metadata")

    target = _resolve_civitai_image_target(
        api, image_id, strict=True, listing_item=collection_item,
        pre_fetched_generation_data=pre_fetched_generation_data,
    )
    task_context.mark_item(item_key, "downloading", "Downloading media")
    download_result = _download_civitai_image_with_validation(
        image_id=image_id,
        target=target,
    )
    temp_path = download_result.temp_path

    # Save collection metadata immediately if available (before download attempt)
    collection_paths = {}
    if collection_item and target.get("civitai_uuid"):
        collection_paths = _save_civitai_api_responses(
            civitai_uuid=target.get("civitai_uuid"),
            raw_infinite=collection_item,
        )

    return _PreparedCivitaiImport(
        image_id=image_id,
        image_url=target["image_url"],
        mime_type=target["mime_type"],
        declared_file_size=target.get("declared_file_size"),
        preview_image_url=target.get("preview_image_url"),
        original_filename=target["original_filename"],
        artist_name=target["artist_name"],
        source_url=target["source_url"],
        temp_path=temp_path,
        civitai_uuid=target.get("civitai_uuid"),
        civitai_hash=target.get("civitai_hash"),
        raw_basic_info=target.get("raw_basic_info"),
        raw_generation_data=target.get("raw_generation_data"),
        raw_infinite=collection_item if isinstance(collection_item, dict) else None,
        api_response_paths={**target.get("api_response_paths", {}), **collection_paths},
        effective_image_url=download_result.selected_url,
        mismatch_static_temp_path=download_result.mismatch_static_temp_path,
        mismatch_source_url=download_result.mismatch_source_url,
        mismatch_mime_type=download_result.mismatch_mime_type,
        mismatch_file_hash=download_result.mismatch_file_hash,
        author_id=target.get("author_id"),
        author_deleted=target.get("author_deleted", False),
        author_original_name=target.get("author_original_name"),
        civitai_post_id=target.get("civitai_post_id"),
        civitai_post_title=target.get("civitai_post_title"),
        civitai_post_index=target.get("civitai_post_index"),
        raw_tag_records=raw_tag_records if raw_tag_records else None,
    )


def _ingest_prepared_civitai_import(
    db: Session,
    *,
    prepared: _PreparedCivitaiImport,
    attach_collection_id: Optional[int] = None,
    recovered_existing: bool = False,
) -> dict:
    ingest_result = ImageCollection(db).ingest_uploaded_file(
        uploaded_file_path=prepared.temp_path,
        original_filename=prepared.original_filename,
        artist_name=prepared.artist_name,
        source_url=prepared.source_url,
        license_id=None,
    )

    resolved_image_db_id = ingest_result.get("image_id") or ingest_result.get(
        "existing_image_id"
    )
    image_db_id = (
        resolved_image_db_id if isinstance(resolved_image_db_id, int) else None
    )
    metadata_backfilled = False

    if isinstance(image_db_id, int):
        image = db.query(ImageModel).filter(ImageModel.id == image_db_id).first()
        if image is not None:
            _ensure_civitai_source_attribution_for_existing_image(
                db,
                image,
                prepared.source_url,
            )

            # Store UUID and pre-saved API response paths
            civitai_metadata_info: dict[str, Any] = {}

            if prepared.civitai_uuid:
                image.civitai_uuid = prepared.civitai_uuid
                civitai_metadata_info["uuid"] = prepared.civitai_uuid

            if prepared.civitai_hash:
                image.civitai_hash = prepared.civitai_hash
                civitai_metadata_info["hash"] = prepared.civitai_hash

            # Ensure civitai_image_id is populated from the prepared import.
            if image.civitai_image_id is None and prepared.image_id:
                image.civitai_image_id = prepared.image_id

            # Ensure civitai_post_id is populated from the prepared import.
            if image.civitai_post_id is None and prepared.civitai_post_id:
                image.civitai_post_id = prepared.civitai_post_id

            # Persist post title and index from CivitAI metadata.
            if prepared.civitai_post_title and not image.civitai_post_title:
                image.civitai_post_title = prepared.civitai_post_title
            if prepared.civitai_post_index is not None and image.civitai_post_index is None:
                image.civitai_post_index = prepared.civitai_post_index

            # Persist the actual CDN URL used for download (may differ from
            # source_url when fallback width-based routes are used).
            if prepared.effective_image_url and not image.civitai_cdn_url:
                image.civitai_cdn_url = prepared.effective_image_url

            # Persist declared file size from CivitAI metadata
            if image.expected_file_size is None and prepared.declared_file_size is not None:
                image.expected_file_size = prepared.declared_file_size

            # Add pre-saved API response file paths (includes basic_info, generation_data, and infinite)
            if prepared.api_response_paths:
                civitai_metadata_info.update(prepared.api_response_paths)

            # Update image metadata with UUID and paths
            if civitai_metadata_info:
                merged_json = (
                    dict(image.json_metadata)
                    if isinstance(image.json_metadata, dict)
                    else {}
                )
                if "civitai" not in merged_json:
                    merged_json["civitai"] = {}
                if isinstance(merged_json["civitai"], dict):
                    merged_json["civitai"].update(civitai_metadata_info)
                image.json_metadata = merged_json

                # Save sidecar JSON with UUID and metadata paths
                image_path = Path(IMAGE_LIBRARY_PATH) / str(image.file_path)
                if image_path.exists():
                    processor = ImageProcessor(str(image_path), db, IMAGE_LIBRARY_PATH)
                    processor.save_json_metadata(image_path, image)

            is_existing_hash_match = (
                str(ingest_result.get("skip_reason") or "").strip()
                == "existing_file_hash"
            )
            if is_existing_hash_match:
                effective_source_url = str(
                    image.source_url or prepared.source_url or ""
                ).strip()
                if effective_source_url and is_civitai_image_url(effective_source_url):
                    metadata_backfilled = _ensure_civitai_metadata_for_existing_image(
                        db,
                        image,
                        effective_source_url,
                    )

            # ── CivitAI tag processing ────────────────────────────────────
            # Insert authority_terms and image_concept_observations from
            # tag records fetched during the prepare/download step.
            if prepared.raw_tag_records:
                try:
                    _insert_tag_observations_for_image(
                        db,
                        image_db_id=image_db_id,
                        tag_records=prepared.raw_tag_records,
                    )
                except Exception as exc:
                    print(
                        f"Warning: CivitAI tag observation insert failed for "
                        f"image {prepared.image_id}: {exc}"
                    )

        if attach_collection_id is not None:
            _ensure_image_in_collection(db, image_db_id, attach_collection_id)
        _preserve_civitai_source_variant(
            db,
            prepared=prepared,
            image_db_id=image_db_id,
        )
        _persist_mismatch_static_variant(
            db,
            prepared=prepared,
            image_db_id=image_db_id,
        )

        # Update artist with CivitAI identity if we have author_id
        if image is not None and prepared.author_id is not None:
            if image.artist_id is not None:
                # Update existing artist with CivitAI identity
                artist_obj = db.query(Artist).filter(Artist.id == image.artist_id).first()
                if artist_obj is not None:
                    dirty = False
                    if artist_obj.civitai_user_id is None:
                        artist_obj.civitai_user_id = prepared.author_id
                        dirty = True
                    if prepared.author_deleted and artist_obj.civitai_user_deleted is not True:
                        artist_obj.civitai_user_deleted = True
                        if (
                            prepared.author_original_name
                            and artist_obj.civitai_user_original_name is None
                        ):
                            artist_obj.civitai_user_original_name = (
                                prepared.author_original_name
                            )
                        dirty = True
                    if dirty:
                        db.flush()
            else:
                # No artist yet — create one from CivitAI identity
                artist_obj = ImageProcessor.find_or_update_civitai_artist(
                    db,
                    username=prepared.artist_name or "[deleted:" + str(prepared.author_id) + "]",
                    civitai_user_id=prepared.author_id,
                    is_deleted=prepared.author_deleted,
                    original_name=prepared.author_original_name,
                )
                image.artist_id = artist_obj.id
                db.flush()

    _commit_with_lock_retry(db, context=f"Import commit for image {prepared.image_id}")
    return {
        "image_id": prepared.image_id,
        "image_db_id": image_db_id,
        "images_added": int(ingest_result.get("images_added", 0) or 0),
        "images_skipped": int(ingest_result.get("images_skipped", 0) or 0),
        "images_recovered": 1 if recovered_existing else 0,
        "json_files_created": int(ingest_result.get("json_files_created", 0) or 0),
        "metadata_backfilled": metadata_backfilled,
        "skip_reason": ingest_result.get("skip_reason"),
        "existing_image_id": ingest_result.get("existing_image_id"),
        "existing_file_hash": ingest_result.get("existing_file_hash"),
        "existing_file_path": ingest_result.get("existing_file_path"),
        "existing_source_url": ingest_result.get("existing_source_url"),
        "error": None,
        "cancelled": False,
    }


def _cleanup_temp_file(path: Optional[Path]) -> None:
    if path is None:
        return
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def _build_civitai_empty_collection_message(collection_id: int) -> str:
    return (
        f"No importable items were returned for CivitAI collection {collection_id}. "
        "The collection may be empty, unavailable, private, inaccessible to the current session, "
        "or it may not be an image collection."
    )


_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)


def _is_sha256_hex(value: Optional[str]) -> bool:
    text = str(value or "").strip()
    return bool(text and _SHA256_HEX_RE.fullmatch(text))


def _looks_like_hashed_display_name(
    file_name: Optional[str],
    *,
    file_hash: Optional[str],
    file_path: Optional[str],
) -> bool:
    candidate = sanitize_display_filename(file_name) or ""
    if not candidate:
        return False

    file_path_name = Path(str(file_path or "")).name.strip()
    candidate_stem = Path(candidate).stem.strip()
    normalized_hash = str(file_hash or "").strip().lower()

    return (
        candidate == file_path_name
        or (
            _is_sha256_hex(candidate_stem) and candidate_stem.lower() == normalized_hash
        )
        or (_is_sha256_hex(candidate) and candidate.lower() == normalized_hash)
    )


def _remove_stale_video_resources(image_path: Path) -> list[str]:
    actions: list[str] = []
    for resource_path in (
        get_video_poster_path(image_path, IMAGE_RESOURCES_PATH),
        get_video_thumbnail_path(image_path, IMAGE_RESOURCES_PATH),
    ):
        try:
            if resource_path.exists():
                resource_path.unlink()
                actions.append(f"Removed stale resource {resource_path.name}.")
        except OSError:
            continue
    return actions


def _derive_preferred_file_name(
    image: ImageModel,
    *,
    actual_extension: str,
    civitai_target: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    current_name = (
        sanitize_display_filename(image.file_name, fallback_ext=actual_extension) or ""
    )
    if civitai_target and isinstance(civitai_target.get("original_filename"), str):
        candidate = sanitize_display_filename(
            str(civitai_target["original_filename"]),
            fallback_ext=actual_extension,
        )
        if candidate:
            return candidate

    source_name = (
        sanitize_display_filename(image.source_url, fallback_ext=actual_extension) or ""
    )
    if source_name and not _looks_like_hashed_display_name(
        source_name,
        file_hash=image.file_hash,
        file_path=image.file_path,
    ):
        return source_name

    if current_name and not _looks_like_hashed_display_name(
        current_name,
        file_hash=image.file_hash,
        file_path=image.file_path,
    ):
        stem = Path(current_name).stem or current_name
        return f"{stem}{actual_extension}"

    return None


def _normalize_merged_image_payload(
    image: ImageModel,
    *,
    db_payload: dict[str, Any],
    merged_payload: dict[str, Any],
) -> dict[str, Any]:
    normalized = dict(merged_payload)
    fallback_ext = Path(str(image.file_path or "")).suffix

    sanitized_merged_file_name = sanitize_display_filename(
        normalized.get("file_name"),
        fallback_ext=fallback_ext,
    )
    if sanitized_merged_file_name:
        normalized["file_name"] = sanitized_merged_file_name

    db_file_name = sanitize_display_filename(
        db_payload.get("file_name"), fallback_ext=fallback_ext
    )
    merged_file_name = normalized.get("file_name")
    file_path = (
        normalized.get("file_path") or db_payload.get("file_path") or image.file_path
    )

    if _looks_like_hashed_display_name(
        merged_file_name,
        file_hash=image.file_hash,
        file_path=file_path,
    ) and not _looks_like_hashed_display_name(
        db_file_name,
        file_hash=image.file_hash,
        file_path=file_path,
    ):
        normalized["file_name"] = db_file_name

    # Sidecar payloads can contain null values that should not override DB truth.
    for key in ("civitai_uuid", "civitai_hash"):
        if normalized.get(key) in (None, "") and db_payload.get(key):
            normalized[key] = db_payload.get(key)

    if isinstance(normalized.get("civitai"), dict) and isinstance(
        db_payload.get("civitai"), dict
    ):
        merged_civitai = dict(normalized.get("civitai") or {})
        db_civitai = dict(db_payload.get("civitai") or {})
        for key in ("uuid", "hash"):
            if merged_civitai.get(key) in (None, "") and db_civitai.get(key):
                merged_civitai[key] = db_civitai.get(key)
        normalized["civitai"] = merged_civitai

    return normalized


def _variant_group_key_for_image(
    image: ImageModel, merged_payload: dict[str, Any]
) -> str:
    explicit_key = str(
        getattr(image, "variant_group_key", None)
        or merged_payload.get("variant_group_key")
        or ""
    ).strip()
    if explicit_key:
        return explicit_key
    # Default to file_hash so that duplicate assets (same SHA256, different
    # CivitAI IDs) are grouped together as variants.
    return str(image.file_hash or image.file_path or image.id or "").strip()


def _variant_sort_index_for_image(image: ImageModel, fallback: int) -> int:
    try:
        value = getattr(image, "variant_sort_index", None)
        if value is None:
            return int(fallback)
        return int(value)
    except (TypeError, ValueError):
        return int(fallback)


def _encode_relative_static_path(path_value: str) -> str:
    return "/".join(quote(part, safe="") for part in str(path_value or "").split("/"))


def _coerce_positive_int(value: Any) -> Optional[int]:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _get_nested_value(payload: Any, path: tuple[str, ...]) -> Any:
    current = payload
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _first_non_empty_string(payloads: list[Any], paths: list[tuple[str, ...]]) -> str:
    for payload in payloads:
        for path in paths:
            value = _get_nested_value(payload, path)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _first_positive_int(
    payloads: list[Any], paths: list[tuple[str, ...]]
) -> Optional[int]:
    for payload in payloads:
        for path in paths:
            parsed = _coerce_positive_int(_get_nested_value(payload, path))
            if parsed is not None:
                return parsed
    return None


def _extract_civitai_variant_payloads(
    merged_payload: dict[str, Any], *, include_merged: bool = True
) -> list[Any]:
    payloads: list[Any] = [merged_payload] if include_merged else []
    json_metadata = merged_payload.get("json_metadata")
    if isinstance(json_metadata, dict):
        payloads.append(json_metadata)
        civitai_meta = json_metadata.get("civitai")
        civitai_data_meta = json_metadata.get("civitai_data")
        if isinstance(civitai_meta, dict):
            payloads.append(civitai_meta)
            if isinstance(civitai_meta.get("image"), dict):
                payloads.append(civitai_meta.get("image"))
            if isinstance(civitai_meta.get("meta"), dict):
                payloads.append(civitai_meta.get("meta"))
        if isinstance(civitai_data_meta, dict):
            payloads.append(civitai_data_meta)
            if isinstance(civitai_data_meta.get("image"), dict):
                payloads.append(civitai_data_meta.get("image"))
            if isinstance(civitai_data_meta.get("meta"), dict):
                payloads.append(civitai_data_meta.get("meta"))

    for key in ("civitai", "civitai_data"):
        candidate = merged_payload.get(key)
        if isinstance(candidate, dict):
            payloads.append(candidate)
            if isinstance(candidate.get("image"), dict):
                payloads.append(candidate.get("image"))
            if isinstance(candidate.get("meta"), dict):
                payloads.append(candidate.get("meta"))
    return payloads


def _extract_civitai_media_name(
    payloads: list[Any], *, image_id: int, media_url: str, mime_type: Optional[str]
) -> str:
    preferred_name = _first_non_empty_string(
        payloads,
        [
            ("name",),
            ("file_name",),
            ("filename",),
            ("original_filename",),
            ("meta", "name"),
            ("image", "name"),
        ],
    )
    return _build_civitai_original_filename(
        image_id, preferred_name, media_url, mime_type
    )


def _extract_civitai_media_mime_type(payloads: list[Any]) -> str:
    return _normalize_mime_type(
        _first_non_empty_string(
            payloads,
            [
                ("mimeType",),
                ("mime_type",),
                ("meta", "mimeType"),
                ("image", "mimeType"),
            ],
        )
    )


def _extract_civitai_media_size(payloads: list[Any]) -> Optional[int]:
    return _first_positive_int(
        payloads,
        [
            ("size",),
            ("fileSize",),
            ("meta", "size"),
            ("metadata", "size"),
            ("image", "size"),
        ],
    )


def _extract_civitai_media_dimensions(
    payloads: list[Any], fallback_width: Any, fallback_height: Any
) -> tuple[Optional[int], Optional[int]]:
    width = _first_positive_int(
        payloads, [("width",), ("meta", "width"), ("image", "width")]
    )
    height = _first_positive_int(
        payloads, [("height",), ("meta", "height"), ("image", "height")]
    )
    if width is None:
        width = _coerce_positive_int(fallback_width)
    if height is None:
        height = _coerce_positive_int(fallback_height)
    return width, height


def _extract_civitai_media_url_from_payload(merged_payload: dict[str, Any]) -> str:
    payloads = _extract_civitai_variant_payloads(merged_payload)
    return _first_non_empty_string(
        payloads,
        [
            ("url",),
            ("media_url",),
            ("image_url",),
        ],
    )


def _extract_civitai_playable_video_url(merged_payload: dict[str, Any]) -> str:
    media_url = _extract_civitai_media_url_from_payload(merged_payload)
    if not _url_looks_like_video(media_url):
        return ""

    media_uuid = _extract_civitai_uuid_from_url_hash(media_url)
    if not media_uuid:
        return media_url

    return f"{getattr(app_config, 'CIVITAI_CDN_ALT_BASE_URL', 'https://image-b2.civitai.com')}/file/civitai-media-cache/{media_uuid}/original"


def _get_asset_category_from_mime(mime_type: Optional[str]) -> str:
    """Determine if asset should be 'video' or 'image' based on MIME type. Returns 'video', 'image', or 'unknown'."""
    if not mime_type:
        return "unknown"
    normalized = str(mime_type).split(";", 1)[0].strip().lower()
    if normalized.startswith("video/"):
        return "video"
    if normalized.startswith("image/"):
        return "image"
    return "unknown"


def _get_asset_category_from_path(file_path: Optional[str]) -> str:
    """Determine if asset should be 'video' or 'image' based on file extension."""
    if not file_path:
        return "unknown"
    suffix = Path(str(file_path)).suffix.lower()
    if suffix in {".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v", ".flv", ".wmv"}:
        return "video"
    if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff"}:
        return "image"
    return "unknown"


def _get_local_asset_category(
    local_file_path: Optional[str], db_mime_type: Optional[str]
) -> str:
    """
    Determine asset category (video/image/unknown) using MIME type first, then extension.

    Returns 'video', 'image', or 'unknown'.
    """
    category_from_mime = _get_asset_category_from_mime(db_mime_type)
    if category_from_mime != "unknown":
        return category_from_mime
    return _get_asset_category_from_path(local_file_path)


def _get_civitai_asset_category(
    civitai_url: Optional[str], civitai_mime_type: Optional[str]
) -> str:
    """
    Determine CivitAI asset category (video/image/unknown) from URL or MIME type.

    Returns 'video', 'image', or 'unknown'.
    """
    category_from_mime = _get_asset_category_from_mime(civitai_mime_type)
    if category_from_mime != "unknown":
        return category_from_mime
    return _get_asset_category_from_path(civitai_url)


def _validate_local_file_health(file_path_obj: Path) -> tuple[bool, Optional[str]]:
    """
    Validate local file exists and is not corrupted.

    Returns: (is_healthy, error_message)
    - is_healthy: True if file is valid and readable
    - error_message: None if healthy, otherwise description of issue
    """
    if not file_path_obj.exists():
        return False, f"File does not exist: {file_path_obj}"

    try:
        file_size = file_path_obj.stat().st_size
        if file_size == 0:
            return False, "File is empty (0 bytes)"
        if file_size < 100:  # All valid images/videos are > 100 bytes
            return False, "File too small to be valid media"
    except OSError as e:
        return False, f"Cannot stat file: {e}"

    # Check file magic bytes for known formats
    suffix = file_path_obj.suffix.lower()
    try:
        with open(file_path_obj, "rb") as f:
            header = f.read(12)

            if suffix == ".png":
                # PNG files start with specific magic bytes: 89 50 4E 47 0D 0A 1A 0A
                if header[:8] != b"\x89PNG\r\n\x1a\n":
                    return False, "PNG file has invalid magic bytes (corrupted)"

            elif suffix in {".jpg", ".jpeg"}:
                # JPEG files start with FFD8
                if header[:2] != b"\xff\xd8":
                    return False, "JPEG file has invalid magic bytes (corrupted)"

            elif suffix == ".webp":
                # WEBP files start with RIFF...WEBP
                if not header.startswith(b"RIFF") or header[8:12] != b"WEBP":
                    return False, "WEBP file has invalid structure (corrupted)"

            elif suffix == ".mp4":
                # MP4 is more complex - just check it's not empty
                if len(header) == 0:
                    return False, "MP4 file appears empty"

    except IOError as e:
        return False, f"Cannot read file: {e}"

    return True, None


def _build_local_image_variant(
    image: ImageModel, merged_payload: dict[str, Any], *, group_key: str
) -> dict[str, Any]:
    variant_role = (
        str(
            getattr(image, "variant_role", None)
            or merged_payload.get("variant_role")
            or "library"
        ).strip()
        or "library"
    )

    # Build a descriptive label — for CivitAI duplicates include the image ID
    # so the user can tell variants apart in the picker.
    civitai_image_id = (
        extract_civitai_image_id(str(merged_payload.get("source_url") or ""))
        or (merged_payload.get("json_metadata") or {}).get("original_civitai_image_id")
        or None
    )
    if civitai_image_id:
        variant_label = f"CivitAI #{civitai_image_id}"
    else:
        variant_label = "Library Asset"

    variant = {
        "variant_key": f"variant:local:{group_key}:{image.file_hash}:id:{image.id}",
        "variant_label": variant_label,
        "variant_role": variant_role,
        "variant_sort_index": _variant_sort_index_for_image(image, 100),
        "file_name": merged_payload.get("file_name"),
        "original_file_name": merged_payload.get("original_file_name")
        or merged_payload.get("file_name"),
        "file_hash": image.file_hash,
        "file_size": merged_payload.get("file_size"),
        "width": merged_payload.get("width"),
        "height": merged_payload.get("height"),
        "mimetype": merged_payload.get("mimetype"),
        "file_path": merged_payload.get("file_path"),
        "display_url": None,
        "poster_url": merged_payload.get("video_poster_url")
        or merged_payload.get("poster_url"),
        "video_poster_url": merged_payload.get("video_poster_url"),
        "video_thumbnail_url": merged_payload.get("video_thumbnail_url"),
        "preview_image_url": merged_payload.get("preview_image_url")
        or merged_payload.get("video_poster_url"),
        "source_url": merged_payload.get("source_url"),
        "civitai_uuid": merged_payload.get("civitai_uuid") or image.civitai_uuid,
        "civitai_hash": merged_payload.get("civitai_hash") or image.civitai_hash,
        "resource_origin": "library",
        "resource_status": "available",
        "is_remote": False,
        "is_local": True,
        "editable_file_hash": image.file_hash,
        # Per-variant tag data so the frontend can switch tags when the
        # user picks a different variant in a grouped display item.
        "civitai_tags": merged_payload.get("civitai_tags"),
        "user_tags": merged_payload.get("user_tags"),
        "user_negative_tags": merged_payload.get("user_negative_tags"),
        "prompt_tags": merged_payload.get("prompt_tags"),
        "danbooru_tags": merged_payload.get("danbooru_tags"),
    }
    return variant


def _build_civitai_video_variant(
    image: ImageModel,
    merged_payload: dict[str, Any],
    *,
    group_key: str,
    poster_url: Optional[str],
    local_asset_category: str,
) -> Optional[dict[str, Any]]:
    """
    Build a CivitAI remote video variant.

    Only creates a variant if:
    1. CivitAI payload points to a video URL
    2. Local asset is NOT already a video (to avoid duplicates)
       - If local is image and remote is video, we have a genuine multi-variant resource
       - If local is video and remote points to video, it's the same asset via different access path

    Args:
        image: ImageModel record
        merged_payload: Combined DB + sidecar metadata
        group_key: Variant grouping key
        poster_url: Optional poster/thumbnail URL from local asset
        local_asset_category: Category of local asset ('video', 'image', 'unknown')

    Returns: Dict representing variant, or None if variant should not be created
    """
    playable_url = _extract_civitai_playable_video_url(merged_payload)
    if not playable_url:
        return None

    # Skip remote video variant if local asset is already a video
    # This prevents duplicate variants of the same asset
    if local_asset_category == "video":
        return None

    payloads = _extract_civitai_variant_payloads(merged_payload, include_merged=False)
    extracted_mime_type = _extract_civitai_media_mime_type(payloads)
    guessed_mime_type = "video/mp4"
    lower_url = playable_url.lower()
    if lower_url.endswith(".webm"):
        guessed_mime_type = "video/webm"
    elif lower_url.endswith(".mov"):
        guessed_mime_type = "video/quicktime"
    mime_type = (
        extracted_mime_type
        if extracted_mime_type.startswith("video/")
        else guessed_mime_type
    )
    image_id = extract_civitai_image_id(str(image.source_url or "")) or int(image.id)
    file_name = _extract_civitai_media_name(
        payloads, image_id=image_id, media_url=playable_url, mime_type=mime_type
    )
    width, height = _extract_civitai_media_dimensions(payloads, None, None)
    file_size = _extract_civitai_media_size(payloads)
    media_uuid = _extract_civitai_uuid_from_url_hash(playable_url)
    variant_suffix = media_uuid or str(image_id)

    return {
        "variant_key": f"variant:civitai-video:{group_key}:{variant_suffix}",
        "variant_label": "Source Video",
        "variant_role": "source_video",
        "variant_sort_index": 0,
        "file_name": file_name,
        "original_file_name": file_name,
        "file_hash": None,
        "file_size": file_size,
        "width": width,
        "height": height,
        "mimetype": mime_type,
        "file_path": None,
        "display_url": playable_url,
        "poster_url": poster_url,
        "video_poster_url": poster_url,
        "video_thumbnail_url": None,
        "preview_image_url": poster_url,
        "source_url": merged_payload.get("source_url"),
        "resource_origin": "civitai",
        "resource_status": "remote",
        "is_remote": True,
        "is_local": False,
        "editable_file_hash": image.file_hash,
        "civitai_uuid": merged_payload.get("civitai_uuid")
        or image.civitai_uuid
        or media_uuid,
        "civitai_hash": merged_payload.get("civitai_hash") or image.civitai_hash,
    }


def _build_static_resource_variant(
    image: ImageModel,
    variant_metadata: dict[str, Any],
    *,
    group_key: str,
    variant_key_suffix: str,
    variant_label: str,
    variant_role: str,
    variant_sort_index: int,
) -> Optional[dict[str, Any]]:
    relative_path = str(variant_metadata.get("variant_file_path") or "").strip()
    if not relative_path:
        return None

    file_name = Path(relative_path).name
    original_file_name = (
        str(variant_metadata.get("original_variant_file_name") or "").strip() or None
    )
    if not original_file_name:
        declared_filename = str(variant_metadata.get("declared_filename") or "").strip()
        if declared_filename:
            original_file_name = Path(declared_filename).name
    if not original_file_name:
        variant_file_path = str(variant_metadata.get("variant_file_path") or "").strip()
        if variant_file_path:
            original_file_name = Path(variant_file_path).name
    resource_path = Path(IMAGE_RESOURCES_PATH) / relative_path
    resources_root = Path(IMAGE_RESOURCES_PATH)
    metadata_mime = _normalize_mime_type(
        variant_metadata.get("actual_mimetype")
        or variant_metadata.get("declared_mimetype")
    )
    metadata_size = _coerce_positive_int(variant_metadata.get("actual_file_size"))
    metadata_hash = str(variant_metadata.get("variant_file_hash") or "").strip() or None

    # Prefer hash-named files when available so stale legacy metadata paths
    # still resolve to the migrated canonical resource.
    if metadata_hash:
        normalized_hash = metadata_hash.lower()
        suffix_candidates: list[str] = []
        legacy_suffix = Path(relative_path).suffix.lower()
        if legacy_suffix:
            suffix_candidates.append(legacy_suffix)
        guessed_suffix = _guess_suffix(metadata_mime)
        if guessed_suffix and guessed_suffix not in suffix_candidates:
            suffix_candidates.append(guessed_suffix)

        for suffix in suffix_candidates:
            candidate = (
                resources_root
                / "civitai_source_variants"
                / f"{normalized_hash}{suffix}"
            )
            if candidate.exists() and candidate.is_file():
                resource_path = candidate
                relative_path = str(resource_path.relative_to(resources_root))
                file_name = resource_path.name
                break
        else:
            glob_pattern = str(
                resources_root / "civitai_source_variants" / f"{normalized_hash}.*"
            )
            for candidate_path in glob.glob(glob_pattern):
                candidate = Path(candidate_path)
                if candidate.suffix.lower() == ".json":
                    continue
                if candidate.exists() and candidate.is_file():
                    resource_path = candidate
                    relative_path = str(resource_path.relative_to(resources_root))
                    file_name = resource_path.name
                    break

    # Skip variant entirely if the referenced file does not exist on disk.
    # This avoids building display_urls that will 404 (e.g. legacy ID-named
    # variant paths for resources that were never downloaded).
    if not resource_path.exists() or not resource_path.is_file():
        return None

    mimetype = metadata_mime
    file_size = metadata_size
    file_hash = metadata_hash
    if resource_path.exists() and resource_path.is_file():
        _, detected_mime = _detect_downloaded_media(resource_path)
        if detected_mime:
            mimetype = detected_mime
        file_size = int(resource_path.stat().st_size)
        file_hash = _sha256_file(resource_path)

    if mimetype.startswith("image/"):
        current_suffix = Path(file_name).suffix.lower()
        if current_suffix in _VIDEO_FILE_SUFFIXES:
            canonical_suffix = _guess_suffix(mimetype)
            canonical_path = resource_path.with_suffix(canonical_suffix)
            if canonical_path.exists() and canonical_path.is_file():
                resource_path = canonical_path
                relative_path = str(resource_path.relative_to(resources_root))
                file_size = int(resource_path.stat().st_size)
                file_hash = _sha256_file(resource_path)
                file_name = resource_path.name
            else:
                file_name = f"{Path(file_name).stem}{canonical_suffix}"

    # Legacy records may preserve the primary video filename as the variant
    # original name even when this archived variant is static image media.
    # For image variants, normalize to an image-like original filename.
    if mimetype.startswith("image/"):
        original_suffix = Path(str(original_file_name or "")).suffix.lower()
        if original_suffix in _VIDEO_FILE_SUFFIXES:
            source_candidates = [
                variant_metadata.get("source_url"),
                variant_metadata.get("expected_source_url"),
                image.source_url,
            ]
            civitai_image_id: Optional[int] = None
            for source_candidate in source_candidates:
                candidate_id = extract_civitai_image_id(
                    str(source_candidate or "").strip()
                )
                if candidate_id is not None:
                    civitai_image_id = int(candidate_id)
                    break
            if civitai_image_id is not None:
                original_file_name = f"{civitai_image_id}{_guess_suffix(mimetype)}"
            else:
                original_file_name = f"{Path(file_name).stem}{_guess_suffix(mimetype)}"

    display_url = f"/image_resources/{_encode_relative_static_path(relative_path)}"

    return {
        "variant_key": f"variant:{variant_key_suffix}:{group_key}:{file_name}",
        "variant_label": variant_label,
        "variant_role": variant_role,
        "variant_sort_index": variant_sort_index,
        "file_name": file_name,
        "original_file_name": original_file_name or file_name,
        "file_hash": file_hash,
        "file_size": file_size,
        "width": None,
        "height": None,
        "mimetype": mimetype or None,
        "file_path": relative_path,
        "display_url": display_url,
        "poster_url": display_url,
        "video_poster_url": None,
        "video_thumbnail_url": None,
        "preview_image_url": display_url,
        "source_url": str(
            variant_metadata.get("source_url") or image.source_url or ""
        ).strip()
        or None,
        "resource_origin": "image_resources",
        "resource_status": "archived",
        "is_remote": False,
        "is_local": False,
        "editable_file_hash": image.file_hash,
        "civitai_uuid": variant_metadata.get("civitai_uuid") or image.civitai_uuid,
        "civitai_hash": image.civitai_hash,
    }


def _dedupe_image_variants(variants: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen_keys: set[str] = set()
    seen_display_urls: set[str] = set()
    seen_archived_hashes: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for variant in sorted(
        variants,
        key=lambda item: (
            int(item.get("variant_sort_index") or 0),
            str(item.get("variant_key") or ""),
        ),
    ):
        variant_key = str(variant.get("variant_key") or "").strip()
        display_url = str(variant.get("display_url") or "").strip()
        variant_hash = str(variant.get("file_hash") or "").strip().lower()
        is_archived = str(variant.get("resource_origin") or "") == "image_resources"
        if variant_key and variant_key in seen_keys:
            continue
        if display_url and display_url in seen_display_urls:
            continue
        if is_archived and variant_hash and variant_hash in seen_archived_hashes:
            continue
        if variant_key:
            seen_keys.add(variant_key)
        if display_url:
            seen_display_urls.add(display_url)
        if is_archived and variant_hash:
            seen_archived_hashes.add(variant_hash)
        deduped.append(variant)
    return deduped


def _build_image_variants(
    image: ImageModel, merged_payload: dict[str, Any]
) -> list[dict[str, Any]]:
    group_key = _variant_group_key_for_image(image, merged_payload)
    local_variant = _build_local_image_variant(
        image, merged_payload, group_key=group_key
    )
    variants: list[dict[str, Any]] = []

    # Determine local asset category to help decide which variants to create
    local_asset_category = _get_local_asset_category(
        merged_payload.get("file_path"), merged_payload.get("mimetype")
    )

    # Validate local file health (if it should exist)
    local_file_path = Path(IMAGE_LIBRARY_PATH) / str(image.file_path)
    if local_file_path.exists():
        is_healthy, health_issue = _validate_local_file_health(local_file_path)
        if not is_healthy:
            # Log issue but continue - we'll use whatever metadata we have
            pass  # Health issues are tracked at display time, not variant build time

    # Only create CivitAI remote video variant if local asset is NOT already a video
    # This prevents duplicates when downloading videos directly from CivitAI
    remote_video_variant = _build_civitai_video_variant(
        image,
        merged_payload,
        group_key=group_key,
        poster_url=local_variant.get("poster_url")
        or local_variant.get("preview_image_url"),
        local_asset_category=local_asset_category,
    )
    if remote_video_variant is not None:
        variants.append(remote_video_variant)

    variants.append(local_variant)

    json_metadata = merged_payload.get("json_metadata")
    static_variant_meta: Optional[dict[str, Any]] = None
    archived_variant_meta: Optional[dict[str, Any]] = None

    if isinstance(json_metadata, dict):
        candidate_static = json_metadata.get("civitai_source_variant_static")
        if isinstance(candidate_static, dict):
            static_variant_meta = candidate_static
        candidate_archived = json_metadata.get("civitai_source_variant")
        if isinstance(candidate_archived, dict):
            archived_variant_meta = candidate_archived

    if static_variant_meta is None and isinstance(
        merged_payload.get("civitai_source_variant_static"), dict
    ):
        static_variant_meta = merged_payload.get("civitai_source_variant_static")
    if archived_variant_meta is None and isinstance(
        merged_payload.get("civitai_source_variant"), dict
    ):
        archived_variant_meta = merged_payload.get("civitai_source_variant")

    if isinstance(static_variant_meta, dict):
        static_variant = _build_static_resource_variant(
            image,
            static_variant_meta,
            group_key=group_key,
            variant_key_suffix="static-source",
            variant_label="Archived Static Source",
            variant_role="static_source",
            variant_sort_index=200,
        )
        if static_variant is not None:
            variants.append(static_variant)

    if isinstance(archived_variant_meta, dict):
        archived_variant = _build_static_resource_variant(
            image,
            archived_variant_meta,
            group_key=group_key,
            variant_key_suffix="archived-source",
            variant_label="Archived Source Variant",
            variant_role="archived_source",
            variant_sort_index=210,
        )
        if archived_variant is not None:
            variants.append(archived_variant)

    return _dedupe_image_variants(variants)


def _merge_display_item_variant(
    base_payload: dict[str, Any], variant: dict[str, Any]
) -> dict[str, Any]:
    display_item = dict(base_payload)
    for field_name in (
        "file_name",
        "original_file_name",
        "file_hash",
        "file_size",
        "width",
        "height",
        "mimetype",
        "file_path",
        "source_url",
        "video_poster_url",
        "video_thumbnail_url",
        "preview_image_url",
        "civitai_uuid",
        "civitai_hash",
        # Per-variant tag data — overwrite base tags with the active
        # variant's tags so the frontend displays the correct set.
        "civitai_tags",
        "user_tags",
        "user_negative_tags",
        "prompt_tags",
        "danbooru_tags",
    ):
        if field_name in variant:
            display_item[field_name] = variant.get(field_name)

    display_item["display_url"] = variant.get("display_url")
    display_item["poster_url"] = variant.get("poster_url")
    display_item["variant_key"] = variant.get("variant_key")
    display_item["variant_label"] = variant.get("variant_label")
    display_item["variant_role"] = variant.get("variant_role")
    display_item["variant_sort_index"] = variant.get("variant_sort_index")
    display_item["resource_origin"] = variant.get("resource_origin")
    display_item["resource_status"] = variant.get("resource_status")
    display_item["is_remote_resource"] = bool(variant.get("is_remote"))
    display_item["is_local_resource"] = bool(variant.get("is_local"))
    display_item["editable_file_hash"] = variant.get(
        "editable_file_hash"
    ) or base_payload.get("editable_file_hash")
    return display_item


def _build_grouped_display_item(
    image: ImageModel,
    merged_payload: dict[str, Any],
    variants: list[dict[str, Any]],
    variant_group_id: Optional[int] = None,
) -> dict[str, Any]:
    group_key = _variant_group_key_for_image(image, merged_payload)
    default_variant = variants[0] if variants else {}
    base_payload = dict(merged_payload)
    base_payload["editable_file_hash"] = image.file_hash
    base_payload["group_primary_file_hash"] = image.file_hash
    base_payload["base_image_id"] = image.id
    base_payload["variant_group_key"] = group_key
    base_payload["variant_count"] = len(variants)
    base_payload["variants"] = variants
    base_payload["default_variant_key"] = default_variant.get("variant_key")
    base_payload["active_variant_key"] = default_variant.get("variant_key")
    # Use variant_group.id for unique identity when available, falling back
    # to the legacy hash-based key for backward compatibility.
    if variant_group_id is not None:
        base_payload["gallery_item_key"] = f"group:{variant_group_id}"
        base_payload["variant_group_id"] = variant_group_id
    else:
        base_payload["gallery_item_key"] = f"group:{group_key}"
    base_payload["display_mode"] = "grouped"
    base_payload["variant_index"] = 0
    return _merge_display_item_variant(base_payload, default_variant)


def _build_flat_variant_display_items(
    image: ImageModel, merged_payload: dict[str, Any], variants: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    group_key = _variant_group_key_for_image(image, merged_payload)
    flat_items: list[dict[str, Any]] = []
    for variant_index, variant in enumerate(variants):
        base_payload = dict(merged_payload)
        base_payload["editable_file_hash"] = image.file_hash
        base_payload["group_primary_file_hash"] = image.file_hash
        base_payload["base_image_id"] = image.id
        base_payload["variant_group_key"] = group_key
        base_payload["variant_count"] = len(variants)
        base_payload["variants"] = [variant]
        base_payload["default_variant_key"] = variant.get("variant_key")
        base_payload["active_variant_key"] = variant.get("variant_key")
        base_payload["gallery_item_key"] = variant.get("variant_key")
        base_payload["display_mode"] = "variant"
        base_payload["variant_index"] = variant_index
        flat_items.append(_merge_display_item_variant(base_payload, variant))
    return flat_items


def _build_display_items_for_image(
    image: ImageModel,
    merged_payload: dict[str, Any],
    *,
    group_variants: bool,
    variant_group_id: Optional[int] = None,
) -> list[dict[str, Any]]:
    variants = _build_image_variants(image, merged_payload)
    if not variants:
        return []
    if group_variants:
        return [_build_grouped_display_item(image, merged_payload, variants, variant_group_id=variant_group_id)]
    return _build_flat_variant_display_items(image, merged_payload, variants)


def _load_filtered_image_keys_unified(
    db: Session,
    *,
    query_service: ImageQueryService,
    included: list[str],
    excluded: list[str],
    hidden: list[str],
    missing: list[str],
    search: Optional[str],
    group_variants: bool = True,
) -> list[str]:
    """Unified key generation using parse_gallery_filter / apply_gallery_filter."""
    images_query = db.query(ImageModel).filter(_active_image_filter())

    if search:
        images_query = _apply_image_list_filters(images_query, search=search)

    parsed = parse_gallery_filter(included, excluded, hidden, missing)
    images_query, filter_constrained_ids = apply_gallery_filter(
        images_query, parsed, db, query_service,
    )

    constrained_ids = filter_constrained_ids

    if constrained_ids is not None:
        if constrained_ids:
            images_query = images_query.filter(ImageModel.id.in_(list(constrained_ids)))
        else:
            return []

    rows = (
        images_query.with_entities(ImageModel.id, ImageModel.file_hash, ImageModel.variant_group_key)
        .order_by(ImageModel.id.asc())
        .all()
    )

    if group_variants:
        image_ids = [row_id for row_id, _, _ in rows]
        image_to_vg: dict[int, int] = {}
        if image_ids:
            try:
                vg_rows = (
                    db.query(
                        ImageVariantGroupMembership.image_id,
                        ImageVariantGroupMembership.group_id,
                    )
                    .filter(ImageVariantGroupMembership.image_id.in_(image_ids))
                    .all()
                )
                for img_id, grp_id in vg_rows:
                    if img_id not in image_to_vg:
                        image_to_vg[img_id] = grp_id
            except Exception:
                pass

        seen: set[str] = set()
        keys: list[str] = []
        for row_id, file_hash, variant_group_key in rows:
            vg_id = image_to_vg.get(row_id)
            if vg_id is not None:
                item_key = f"group:{vg_id}"
            else:
                group_key = (variant_group_key or "").strip() or str(file_hash or "")
                item_key = f"group:{group_key}"
            if item_key not in seen:
                seen.add(item_key)
                keys.append(item_key)
        return keys
    else:
        return [str(file_hash) for _, file_hash, _ in rows if file_hash]


def _load_filtered_image_keys(
    db: Session,
    *,
    search: Optional[str],
    generation_software: Optional[list[str]],
    source_site: Optional[list[str]],
    mimetype: Optional[list[str]],
    nsfw_rating: Optional[list[str]],
    nsfw_safety: Optional[list[str]],
    artist_name: Optional[list[str]],
    collection_name: Optional[list[str]],
    exclude_artist_name: Optional[list[str]] = None,
    exclude_collection_name: Optional[list[str]] = None,
    a1111_hires: Optional[list[str]] = None,
    a1111_regional_prompter: Optional[list[str]] = None,
    a1111_adetailer: Optional[list[str]] = None,
    include_tag: Optional[list[str]] = None,
    exclude_tag: Optional[list[str]] = None,
    group_variants: bool = True,
) -> list[str]:
    """Lightweight DB-only key generation — no sidecar reads or relationship loading."""
    images_query = db.query(ImageModel).filter(_active_image_filter())
    images_query = _apply_image_list_filters(
        images_query,
        search=search,
        source_sites=source_site,
        mimetypes=mimetype,
        artist_names=artist_name,
        collection_names=collection_name,
        exclude_artist_names=exclude_artist_name,
        exclude_collection_names=exclude_collection_name,
    )

    generation_filtered_ids = _filter_image_ids_by_generation_software(
        images_query, generation_software
    )
    nsfw_filtered_ids = _filter_image_ids_by_nsfw_ratings(images_query, nsfw_rating)
    nsfw_safety_filtered_ids = _filter_image_ids_by_nsfw_safety_classes(
        images_query, nsfw_safety
    )
    a1111_filtered_ids = _filter_image_ids_by_a1111_features(
        images_query,
        a1111_hires=a1111_hires,
        a1111_regional_prompter=a1111_regional_prompter,
        a1111_adetailer=a1111_adetailer,
    )
    tag_filtered_ids = _filter_image_ids_by_tag_names(
        images_query,
        include_tags=include_tag,
        exclude_tags=exclude_tag,
    )

    constrained_ids: Optional[set[int]] = None
    for filtered_ids in (
        generation_filtered_ids,
        nsfw_filtered_ids,
        nsfw_safety_filtered_ids,
        a1111_filtered_ids,
        tag_filtered_ids,
    ):
        if filtered_ids is None:
            continue
        filtered_set = set(filtered_ids)
        constrained_ids = (
            filtered_set
            if constrained_ids is None
            else constrained_ids.intersection(filtered_set)
        )

    if constrained_ids is not None:
        if constrained_ids:
            images_query = images_query.filter(ImageModel.id.in_(list(constrained_ids)))
        else:
            return []

    rows = (
        images_query.with_entities(ImageModel.id, ImageModel.file_hash, ImageModel.variant_group_key)
        .order_by(ImageModel.id.asc())
        .all()
    )

    if group_variants:
        # Pre-load variant group memberships for all matching images.
        image_ids = [row_id for row_id, _, _ in rows]
        image_to_vg: dict[int, int] = {}
        if image_ids:
            try:
                vg_rows = (
                    db.query(
                        ImageVariantGroupMembership.image_id,
                        ImageVariantGroupMembership.group_id,
                    )
                    .filter(ImageVariantGroupMembership.image_id.in_(image_ids))
                    .all()
                )
                for img_id, grp_id in vg_rows:
                    if img_id not in image_to_vg:
                        image_to_vg[img_id] = grp_id
            except Exception:
                pass  # Fall back to legacy hash-based keys

        seen: set[str] = set()
        keys: list[str] = []
        for row_id, file_hash, variant_group_key in rows:
            vg_id = image_to_vg.get(row_id)
            if vg_id is not None:
                item_key = f"group:{vg_id}"
            else:
                group_key = (variant_group_key or "").strip() or str(file_hash or "")
                item_key = f"group:{group_key}"
            if item_key not in seen:
                seen.add(item_key)
                keys.append(item_key)
        return keys
    else:
        return [str(file_hash) for _, file_hash, _ in rows if file_hash]


def _merge_duplicate_grouped_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge grouped display items that share the same gallery_item_key.

    Duplicate CivitAI assets (same SHA256, different CivitAI IDs) each
    produce their own grouped display item with the same gallery_item_key.
    This function merges them into a single item whose variant list is the
    union of all duplicates' variants, so the user sees one tile with a
    variant picker to switch between the different CivitAI entries.
    """
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in items:
        key = item.get("gallery_item_key", "")
        if key not in merged:
            merged[key] = dict(item)
            order.append(key)
            continue

        existing = merged[key]
        # Collect all variants from both items, deduplicating by variant_key
        existing_variants: list[dict[str, Any]] = existing.get("variants") or []
        new_variants: list[dict[str, Any]] = item.get("variants") or []
        seen_vkeys: set[str] = {
            v.get("variant_key") for v in existing_variants if v.get("variant_key")
        }
        for v in new_variants:
            vk = v.get("variant_key")
            if vk and vk not in seen_vkeys:
                existing_variants.append(v)
                seen_vkeys.add(vk)

        existing["variants"] = existing_variants
        existing["variant_count"] = len(existing_variants)
        existing["default_variant_key"] = (
            existing_variants[0].get("variant_key") if existing_variants else None
        )
        existing["active_variant_key"] = (
            existing_variants[0].get("variant_key") if existing_variants else None
        )

        # Merge collection names/ids from the duplicate
        existing_cnames = set(existing.get("collection_names") or [])
        existing_cids = set(existing.get("collection_ids") or [])
        for cname in item.get("collection_names") or []:
            existing_cnames.add(cname)
        for cid in item.get("collection_ids") or []:
            existing_cids.add(cid)
        existing["collection_names"] = list(existing_cnames)
        existing["collection_ids"] = list(existing_cids)

        # Re-merge the default variant into the top-level payload
        if existing_variants:
            merged_item = _merge_display_item_variant(existing, existing_variants[0])
            # Copy the variant-merged fields back into the existing dict
            existing.update(merged_item)

    return [merged[k] for k in order]


# Multiplier applied to the requested page size when using cursor-based
# pagination.  Because grouping/merging can reduce N DB rows to fewer
# display items, we over-fetch from the DB and trim after merging so the
# caller always receives a full page.
_OVERFETCH_FACTOR = 3


def _load_display_image_items_unified(
    db: Session,
    *,
    query_service: ImageQueryService,
    included: list[str],
    excluded: list[str],
    hidden: list[str],
    missing: list[str],
    sort_by: str,
    search: Optional[str],
    variant_group_id: Optional[list[int]] = None,
    group_variants: bool = True,
    skip: int = 0,
    limit: Optional[int] = None,
    cursor: Optional[int] = None,
) -> tuple[list[dict[str, Any]], int, Optional[int]]:
    """Unified filter path using parse_gallery_filter / apply_gallery_filter.

    Returns (paginated_display_items, total_grouped_count, next_cursor)
    using the same display-item pipeline as the legacy path but with a
    single unified filter entry point.
    """
    import time as _time

    _t0 = _time.perf_counter()

    images_query = (
        db.query(ImageModel)
        .options(
            joinedload(ImageModel.artist),
            joinedload(ImageModel.license),
            joinedload(ImageModel.collections),
        )
        .filter(_active_image_filter())
    )

    # Apply text search filter (not part of the unified filter model).
    if search:
        images_query = _apply_image_list_filters(
            images_query,
            search=search,
        )

    # Parse and apply the unified gallery filter.
    parsed = parse_gallery_filter(included, excluded, hidden, missing)
    images_query, filter_constrained_ids = apply_gallery_filter(
        images_query, parsed, db, query_service,
    )

    # Combine with the parsed filter's constrained IDs.
    constrained_ids = filter_constrained_ids

    # --- variant_group_id filter ------------------------------------------
    if variant_group_id:
        try:
            vg_image_ids = [
                row[0]
                for row in db.query(ImageVariantGroupMembership.image_id)
                .filter(ImageVariantGroupMembership.group_id.in_(variant_group_id))
                .all()
            ]
            if vg_image_ids:
                vg_set = set(vg_image_ids)
                constrained_ids = (
                    vg_set
                    if constrained_ids is None
                    else constrained_ids.intersection(vg_set)
                )
            else:
                constrained_ids = set()  # no matches → empty result
        except Exception:
            pass

    # Apply constrained IDs to the query.
    if constrained_ids is not None:
        if constrained_ids:
            images_query = images_query.filter(ImageModel.id.in_(list(constrained_ids)))
        else:
            images_query = images_query.filter(text("1 = 0"))

    if sort_by == "last_added":
        images_query = images_query.order_by(ImageModel.id.desc())
    elif sort_by == "civitai_image_id":
        images_query = images_query.order_by(
            ImageModel.civitai_image_id.desc().nulls_last()
        )
    else:
        images_query = images_query.order_by(ImageModel.id.asc())

    # --- Cursor-based / over-fetch pagination ----------------------------
    use_overfetch = group_variants and limit is not None
    use_cursor = use_overfetch
    if use_overfetch:
        if cursor is not None:
            if sort_by == "last_added":
                images_query = images_query.filter(ImageModel.id < cursor)
            else:
                images_query = images_query.filter(ImageModel.id > cursor)
        db_row_limit = limit * _OVERFETCH_FACTOR
        images_query = images_query.limit(db_row_limit)
    else:
        if skip > 0:
            images_query = images_query.offset(skip)
        if limit is not None:
            images_query = images_query.limit(limit)

    images = images_query.all()

    _t1 = _time.perf_counter()
    print(f"[PERF] unified query: {_t1-_t0:.3f}s  rows={len(images)}")

    # --- Build display items (same pipeline as legacy) -------------------
    image_to_variant_group: dict[int, int] = {}
    if group_variants:
        image_ids = [img.id for img in images]
        try:
            membership_rows = (
                db.query(
                    ImageVariantGroupMembership.image_id,
                    ImageVariantGroupMembership.group_id,
                )
                .filter(ImageVariantGroupMembership.image_id.in_(image_ids))
                .all()
            )
            for img_id, grp_id in membership_rows:
                if img_id not in image_to_variant_group:
                    image_to_variant_group[img_id] = grp_id
        except Exception:
            pass

    display_items: list[dict[str, Any]] = []
    for image in images:
        db_dict = ImageData.from_db_record(image).to_dict()
        db_dict["exif_data"] = None
        db_dict["civitai_data"] = None
        db_dict["json_metadata"] = None
        merged = db_dict

        if (image.mimetype or "").lower().startswith("video/"):
            image_path = Path(IMAGE_LIBRARY_PATH) / str(image.file_path)
            poster_path = get_video_poster_path(image_path, IMAGE_RESOURCES_PATH)
            thumbnail_path = get_video_thumbnail_path(image_path, IMAGE_RESOURCES_PATH)
            if poster_path.exists() and poster_path.is_file():
                merged["video_poster_url"] = f"/api/images/{image.file_hash}/video_poster"
            if thumbnail_path.exists() and thumbnail_path.is_file():
                merged["video_thumbnail_url"] = (
                    f"/api/images/{image.file_hash}/video_thumbnail"
                )
        merged["collection_names"] = [c.name for c in image.collections]
        merged["collection_ids"] = [c.id for c in image.collections]
        merged["artist_name"] = image.artist.name if image.artist is not None else None
        merged["artist_deleted"] = image.artist.civitai_user_deleted if image.artist is not None else None
        merged["artist_original_name"] = image.artist.civitai_user_original_name if image.artist is not None else None
        nsfw_ratings = _read_nsfw_ratings_for_image(image)
        merged["nsfw_ratings"] = nsfw_ratings
        merged["nsfw_rating"] = nsfw_ratings[0] if nsfw_ratings else None
        merged["user_nsfw_rating"] = image.user_nsfw_rating
        merged["user_nsfw_safety_class"] = image.user_nsfw_safety_class
        db_user_tags = getattr(image, "user_tags", None)
        if isinstance(db_user_tags, list) and db_user_tags:
            merged["user_tags"] = db_user_tags
        db_user_neg_tags = getattr(image, "user_negative_tags", None)
        if isinstance(db_user_neg_tags, list) and db_user_neg_tags:
            merged["user_negative_tags"] = db_user_neg_tags
        
        # Query CivitAI tags from image_concept_observations (post-backfill data)
        civitai_tag_rows = (
            db.query(AuthorityTerm.external_name)
            .join(
                ImageConceptObservation,
                ImageConceptObservation.authority_term_id == AuthorityTerm.id,
            )
            .join(TagAuthority, TagAuthority.id == AuthorityTerm.authority_id)
            .filter(
                ImageConceptObservation.image_id == image.id,
                TagAuthority.name == "civitai",
            )
            .order_by(AuthorityTerm.external_name.asc())
            .all()
        )
        merged["civitai_tags"] = [row[0] for row in civitai_tag_rows if row[0]]
        
        display_items.extend(
            _build_display_items_for_image(
                image, merged, group_variants=group_variants,
                variant_group_id=image_to_variant_group.get(image.id),
            )
        )

    if group_variants:
        display_items = _merge_duplicate_grouped_items(display_items)

    # --- Cursor pagination trim -------------------------------------------
    next_cursor: Optional[int] = None
    if use_cursor and limit is not None:
        if len(display_items) > limit:
            display_items = display_items[:limit]
        if display_items and images:
            next_cursor = images[-1].id
            if len(display_items) < limit:
                next_cursor = None

    # --- Total count ------------------------------------------------------
    if use_cursor:
        if constrained_ids is not None:
            total_count = len(constrained_ids)
        else:
            base_count_query = (
                db.query(func.count(ImageModel.id))
                .filter(_active_image_filter())
            )
            if search:
                base_count_query = _apply_image_list_filters(
                    base_count_query, search=search,
                )
            total_count = base_count_query.scalar() or 0
    else:
        if constrained_ids is not None:
            total_count = len(constrained_ids)
        else:
            base_count_query = (
                db.query(func.count(ImageModel.id))
                .filter(_active_image_filter())
            )
            if search:
                base_count_query = _apply_image_list_filters(
                    base_count_query, search=search,
                )
            total_count = base_count_query.scalar() or 0

    _t2 = _time.perf_counter()
    print(f"[PERF] unified display loop: {_t2-_t1:.3f}s  items={len(display_items)}")
    print(f"[PERF] unified TOTAL: {_t2-_t0:.3f}s")

    return display_items, total_count, next_cursor


def _load_display_image_items(
    db: Session,
    *,
    sort_by: str,
    search: Optional[str],
    generation_software: Optional[list[str]],
    source_site: Optional[list[str]],
    mimetype: Optional[list[str]],
    nsfw_rating: Optional[list[str]],
    nsfw_safety: Optional[list[str]],
    artist_name: Optional[list[str]],
    collection_name: Optional[list[str]],
    exclude_artist_name: Optional[list[str]] = None,
    exclude_collection_name: Optional[list[str]] = None,
    a1111_hires: Optional[list[str]] = None,
    a1111_regional_prompter: Optional[list[str]] = None,
    a1111_adetailer: Optional[list[str]] = None,
    include_tag: Optional[list[str]] = None,
    exclude_tag: Optional[list[str]] = None,
    variant_group_id: Optional[list[int]] = None,
    group_variants: bool,
    skip: int = 0,
    limit: Optional[int] = None,
    cursor: Optional[int] = None,
    missing_data: Optional[list[str]] = None,
    missing_source: Optional[list[str]] = None,
) -> tuple[list[dict[str, Any]], int, Optional[int]]:
    """Return (paginated_display_items, total_grouped_count, next_cursor).

    When *cursor* is provided, keyset pagination is used instead of offset:
      - ``first_added`` sort  →  ``WHERE id > cursor ORDER BY id ASC``
      - ``last_added`` sort   →  ``WHERE id < cursor ORDER BY id DESC``

    To guarantee a full page of grouped display items, the DB over-fetches
    rows (up to ``limit * _OVERFETCH_FACTOR``) before grouping and merging.
    The result is then trimmed to exactly *limit* display items.
    """
    display_cache_key = _build_search_cache_key(
        "images_display_items",
        payload={
            "sort_by": str(sort_by),
            "search": str(search or "").strip().lower(),
            "generation_software": _normalize_cache_list(generation_software),
            "source_site": _normalize_cache_list(source_site),
            "mimetype": _normalize_cache_list(mimetype),
            "nsfw_rating": _normalize_cache_list(nsfw_rating),
            "nsfw_safety": _normalize_cache_list(nsfw_safety),
            "artist_name": _normalize_cache_list(artist_name),
            "collection_name": _normalize_cache_list(collection_name),
            "exclude_artist_name": _normalize_cache_list(exclude_artist_name),
            "exclude_collection_name": _normalize_cache_list(exclude_collection_name),
            "a1111_hires": _normalize_cache_list(a1111_hires),
            "a1111_regional_prompter": _normalize_cache_list(a1111_regional_prompter),
            "a1111_adetailer": _normalize_cache_list(a1111_adetailer),
            "include_tag": _normalize_cache_list(include_tag),
            "exclude_tag": _normalize_cache_list(exclude_tag),
            "variant_group_id": _normalize_cache_list(variant_group_id),
            "group_variants": bool(group_variants),
            "skip": skip,
            "limit": limit,
            "cursor": cursor,
            "missing_data": _normalize_cache_list(missing_data),
            "missing_source": _normalize_cache_list(missing_source),
        },
    )
    cached_result = _search_cache_get(display_cache_key)
    if isinstance(cached_result, dict) and "items" in cached_result:
        return (
            cached_result["items"],
            cached_result["filtered_count"],
            cached_result.get("next_cursor"),
        )

    import time as _time

    _t0 = _time.perf_counter()

    images_query = (
        db.query(ImageModel)
        .options(
            joinedload(ImageModel.artist),
            joinedload(ImageModel.license),
            joinedload(ImageModel.collections),
        )
        .filter(_active_image_filter())
    )
    images_query = _apply_image_list_filters(
        images_query,
        search=search,
        source_sites=source_site,
        mimetypes=mimetype,
        artist_names=artist_name,
        collection_names=collection_name,
        exclude_artist_names=exclude_artist_name,
        exclude_collection_names=exclude_collection_name,
    )
    _t1 = _time.perf_counter()
    print(f"[PERF] query build: {_t1-_t0:.3f}s")

    generation_filtered_ids = _filter_image_ids_by_generation_software(
        images_query, generation_software
    )
    _t2 = _time.perf_counter()
    print(
        f"[PERF] gen_sw filter: {_t2-_t1:.3f}s  ids={len(generation_filtered_ids) if generation_filtered_ids else 'skip'}"
    )

    nsfw_filtered_ids = _filter_image_ids_by_nsfw_ratings(images_query, nsfw_rating)
    _t3 = _time.perf_counter()
    print(
        f"[PERF] nsfw_rating filter: {_t3-_t2:.3f}s  ids={len(nsfw_filtered_ids) if nsfw_filtered_ids else 'skip'}"
    )

    nsfw_safety_filtered_ids = _filter_image_ids_by_nsfw_safety_classes(
        images_query, nsfw_safety
    )
    _t4 = _time.perf_counter()
    print(
        f"[PERF] nsfw_safety filter: {_t4-_t3:.3f}s  ids={len(nsfw_safety_filtered_ids) if nsfw_safety_filtered_ids else 'skip'}"
    )

    a1111_filtered_ids = _filter_image_ids_by_a1111_features(
        images_query,
        a1111_hires=a1111_hires,
        a1111_regional_prompter=a1111_regional_prompter,
        a1111_adetailer=a1111_adetailer,
    )
    _t5 = _time.perf_counter()
    print(
        f"[PERF] a1111 filter: {_t5-_t4:.3f}s  ids={len(a1111_filtered_ids) if a1111_filtered_ids else 'skip'}"
    )

    tag_filtered_ids = _filter_image_ids_by_tag_names(
        images_query,
        include_tags=include_tag,
        exclude_tags=exclude_tag,
    )
    _t5b = _time.perf_counter()
    print(
        f"[PERF] tag filter: {_t5b-_t5:.3f}s  ids={len(tag_filtered_ids) if tag_filtered_ids is not None else 'skip'}"
    )

    missing_data_filtered_ids = _filter_image_ids_by_missing_data(
        images_query, missing_data
    )
    _t5c = _time.perf_counter()
    print(
        f"[PERF] missing_data filter: {_t5c-_t5b:.3f}s  ids={len(missing_data_filtered_ids) if missing_data_filtered_ids is not None else 'skip'}"
    )

    missing_source_filtered_ids = _filter_image_ids_by_missing_source(
        images_query, missing_source
    )
    _t5d = _time.perf_counter()
    print(
        f"[PERF] missing_source filter: {_t5d-_t5c:.3f}s  ids={len(missing_source_filtered_ids) if missing_source_filtered_ids is not None else 'skip'}"
    )

    constrained_ids: Optional[set[int]] = None
    for filtered_ids in (
        generation_filtered_ids,
        nsfw_filtered_ids,
        nsfw_safety_filtered_ids,
        a1111_filtered_ids,
        tag_filtered_ids,
        missing_data_filtered_ids,
        missing_source_filtered_ids,
    ):
        if filtered_ids is None:
            continue
        filtered_set = set(filtered_ids)
        constrained_ids = (
            filtered_set
            if constrained_ids is None
            else constrained_ids.intersection(filtered_set)
        )

    if constrained_ids is not None:
        if constrained_ids:
            images_query = images_query.filter(ImageModel.id.in_(list(constrained_ids)))
        else:
            images_query = images_query.filter(text("1 = 0"))

    # --- variant_group_id filter ------------------------------------------
    # When variant_group_id is specified, constrain results to images that
    # belong to at least one of the specified variant groups.
    if variant_group_id:
        try:
            vg_image_ids = [
                row[0]
                for row in db.query(ImageVariantGroupMembership.image_id)
                .filter(ImageVariantGroupMembership.group_id.in_(variant_group_id))
                .all()
            ]
            if vg_image_ids:
                images_query = images_query.filter(ImageModel.id.in_(vg_image_ids))
            else:
                images_query = images_query.filter(text("1 = 0"))
        except Exception:
            pass  # variant_groups tables may not exist yet

    if sort_by == "last_added":
        images_query = images_query.order_by(ImageModel.id.desc())
    elif sort_by == "civitai_image_id":
        images_query = images_query.order_by(
            ImageModel.civitai_image_id.desc().nulls_last()
        )
    else:
        images_query = images_query.order_by(ImageModel.id.asc())

    # --- Cursor-based (keyset) pagination --------------------------------
    # When *cursor* is supplied, use keyset pagination for stable page
    # boundaries regardless of variant grouping.  Over-fetch DB rows so
    # that after grouping/merging we still have ≥ limit display items.
    #
    # When *group_variants* is True we ALWAYS over-fetch and compute a
    # next_cursor — even on the first page (cursor=None, skip=0) — so the
    # frontend can rely on X-Next-Cursor to decide whether more pages exist
    # instead of comparing offset < filtered_count (which breaks because
    # the count is pre-grouping while offset is post-grouping).
    use_overfetch = group_variants and limit is not None
    use_cursor = use_overfetch  # over-fetch path is now the same as cursor path
    if use_overfetch:
        if cursor is not None:
            if sort_by == "last_added":
                images_query = images_query.filter(ImageModel.id < cursor)
            else:
                images_query = images_query.filter(ImageModel.id > cursor)
        # else: first page — no cursor filter, start from the beginning
        db_row_limit = limit * _OVERFETCH_FACTOR
        images_query = images_query.limit(db_row_limit)
    else:
        # Legacy offset path — for non-grouped queries without cursor.
        if skip > 0:
            images_query = images_query.offset(skip)
        if limit is not None:
            images_query = images_query.limit(limit)

    images = images_query.all()
    _t6 = _time.perf_counter()
    print(f"[PERF] main query exec: {_t6-_t5:.3f}s  images={len(images)}")

    # --- Pre-load variant group memberships for fetched images ------------
    # Map image_id → first variant_group_id.  When grouping is enabled, the
    # gallery_item_key uses variant_group.id instead of file_hash, which
    # guarantees unique keys and eliminates the cycling bug caused by hash
    # collisions across different groups.
    image_to_variant_group: dict[int, int] = {}
    if group_variants and images:
        try:
            image_ids = [img.id for img in images]
            membership_rows = (
                db.query(
                    ImageVariantGroupMembership.image_id,
                    ImageVariantGroupMembership.group_id,
                )
                .filter(ImageVariantGroupMembership.image_id.in_(image_ids))
                .all()
            )
            for img_id, grp_id in membership_rows:
                # An image can be in multiple groups; use the first one found.
                if img_id not in image_to_variant_group:
                    image_to_variant_group[img_id] = grp_id
        except Exception:
            # If variant_groups tables don't exist yet (pre-migration),
            # silently fall back to legacy hash-based grouping.
            pass

    display_items: list[dict[str, Any]] = []
    for image in images:
        db_dict = ImageData.from_db_record(image).to_dict()

        # DB-only display: no sidecar reads.  All gallery-critical fields
        # come from DB columns or json_metadata (a JSON column).
        # Strip detail-only blob fields from the gallery list payload; they
        # are fetched on demand via GET /api/images/{id} when an image is
        # selected.  This keeps the gallery response lean — civitai_data and
        # json_metadata can be 5–20 KB per image.
        db_dict["exif_data"] = None
        db_dict["civitai_data"] = None
        db_dict["json_metadata"] = None
        merged = db_dict

        if (image.mimetype or "").lower().startswith("video/"):
            image_path = Path(IMAGE_LIBRARY_PATH) / str(image.file_path)
            poster_path = get_video_poster_path(image_path, IMAGE_RESOURCES_PATH)
            thumbnail_path = get_video_thumbnail_path(image_path, IMAGE_RESOURCES_PATH)
            if poster_path.exists() and poster_path.is_file():
                merged["video_poster_url"] = f"/api/images/{image.file_hash}/video_poster"
            if thumbnail_path.exists() and thumbnail_path.is_file():
                merged["video_thumbnail_url"] = (
                    f"/api/images/{image.file_hash}/video_thumbnail"
                )
        merged["collection_names"] = [c.name for c in image.collections]
        merged["collection_ids"] = [c.id for c in image.collections]
        # Inject artist_name from the already-joined relationship so the
        # frontend has it for detail-panel display and client-side filtering.
        merged["artist_name"] = image.artist.name if image.artist is not None else None
        merged["artist_deleted"] = image.artist.civitai_user_deleted if image.artist is not None else None
        merged["artist_original_name"] = image.artist.civitai_user_original_name if image.artist is not None else None
        nsfw_ratings = _read_nsfw_ratings_for_image(image)
        merged["nsfw_ratings"] = nsfw_ratings
        merged["nsfw_rating"] = nsfw_ratings[0] if nsfw_ratings else None
        merged["user_nsfw_rating"] = image.user_nsfw_rating
        merged["user_nsfw_safety_class"] = image.user_nsfw_safety_class
        db_user_tags = getattr(image, "user_tags", None)
        if isinstance(db_user_tags, list) and db_user_tags:
            merged["user_tags"] = db_user_tags
        db_user_neg_tags = getattr(image, "user_negative_tags", None)
        if isinstance(db_user_neg_tags, list) and db_user_neg_tags:
            merged["user_negative_tags"] = db_user_neg_tags
        
            # Query CivitAI tags from image_concept_observations (post-backfill data)
            civitai_tag_rows = (
                db.query(AuthorityTerm.external_name)
                .join(
                    ImageConceptObservation,
                    ImageConceptObservation.authority_term_id == AuthorityTerm.id,
                )
                .join(TagAuthority, TagAuthority.id == AuthorityTerm.authority_id)
                .filter(
                    ImageConceptObservation.image_id == image.id,
                    TagAuthority.name == "civitai",
                )
                .order_by(AuthorityTerm.external_name.asc())
                .all()
            )
            merged["civitai_tags"] = [row[0] for row in civitai_tag_rows if row[0]]
        
            display_items.extend(
                _build_display_items_for_image(
                    image, merged, group_variants=group_variants,
                    variant_group_id=image_to_variant_group.get(image.id),
                )
            )

    # Merge display items that share the same gallery_item_key (duplicate
    # assets with the same file_hash but different CivitAI IDs).  When
    # group_variants is enabled, each duplicate independently produced a
    # grouped display item.  We merge them into a single item whose variant
    # list is the union of all duplicates' variants, keeping each variant
    # independently selectable.
    if group_variants:
        display_items = _merge_duplicate_grouped_items(display_items)

    # --- Cursor pagination: trim to exactly *limit* display items ---------
    next_cursor: Optional[int] = None
    if use_cursor and limit is not None:
        if len(display_items) > limit:
            display_items = display_items[:limit]
        # Derive next cursor from the LAST DB row consumed (max image ID),
        # NOT from the display item's base_image_id.  This ensures the
        # cursor advances past every grouped/merged DB row so the next page
        # starts after all consumed images — fixing the fullscreen cycling bug
        # where the cursor would land on the first image of a merged group.
        if display_items and images:
            next_cursor = images[-1].id
            # If the page is short (fewer items than limit), no more results.
            if len(display_items) < limit:
                next_cursor = None

    # --- Total count for X-Filtered-Count header -------------------------
    # For cursor pagination, we count total matching DB rows (pre-grouping)
    # using a separate query without the cursor/limit applied.  This count
    # is informational — the frontend relies on X-Next-Cursor to determine
    # whether more pages exist.
    if use_cursor:
        # Build a count query from the same constrained ID set.
        if constrained_ids is not None:
            total_count = len(constrained_ids)
        else:
            # No ID-level constraints applied — count from the base query.
            base_count_query = (
                db.query(func.count(ImageModel.id))
                .filter(_active_image_filter())
            )
            base_count_query = _apply_image_list_filters(
                base_count_query,
                search=search,
                source_sites=source_site,
                mimetypes=mimetype,
                artist_names=artist_name,
                collection_names=collection_name,
                exclude_artist_names=exclude_artist_name,
                exclude_collection_names=exclude_collection_name,
            )
            total_count = base_count_query.scalar() or 0
    else:
        # Legacy offset path: use the constrained ID count when filters are
        # active so X-Filtered-Count reflects the full matching set, not just
        # the current page.  This lets the frontend correctly determine
        # hasMore = offset < filteredMatchCount when serverFilterMode is on.
        if constrained_ids is not None:
            total_count = len(constrained_ids)
        else:
            base_count_query = (
                db.query(func.count(ImageModel.id))
                .filter(_active_image_filter())
            )
            base_count_query = _apply_image_list_filters(
                base_count_query,
                search=search,
                source_sites=source_site,
                mimetypes=mimetype,
                artist_names=artist_name,
                collection_names=collection_name,
                exclude_artist_names=exclude_artist_name,
                exclude_collection_names=exclude_collection_name,
            )
            total_count = base_count_query.scalar() or 0

    _t7 = _time.perf_counter()
    print(f"[PERF] display loop: {_t7-_t6:.3f}s  items={len(display_items)}")
    print(f"[PERF] TOTAL: {_t7-_t0:.3f}s")

    _search_cache_put(
        display_cache_key,
        {
            "items": display_items,
            "filtered_count": total_count,
            "next_cursor": next_cursor,
        },
    )
    return display_items, total_count, next_cursor


def _replace_image_with_uploaded_file(
    db: Session,
    *,
    image: ImageModel,
    uploaded_file_path: Path,
    original_filename: str,
    replacement_reason: str,
    artist_name: Optional[str] = None,
    source_url: Optional[str] = None,
    license_id: Optional[int] = None,
    prepared_variant: Optional[_PreparedCivitaiImport] = None,
) -> dict[str, Any]:
    ingest_result = ImageCollection(db).ingest_uploaded_file(
        uploaded_file_path=uploaded_file_path,
        original_filename=original_filename,
        artist_name=artist_name,
        source_url=source_url,
        license_id=license_id,
    )

    repaired_image_id = ingest_result.get("image_id") or ingest_result.get(
        "existing_image_id"
    )
    repaired_image = None
    if isinstance(repaired_image_id, int):
        repaired_image = (
            db.query(ImageModel).filter(ImageModel.id == repaired_image_id).first()
        )

    created_new_image = False
    if repaired_image is not None and int(repaired_image.id) != int(image.id):
        for collection in image.collections:
            _ensure_image_in_collection(db, repaired_image.id, collection.id)
        image.image_status = "tombstoned"
        image.status_reason = replacement_reason
        image.replaced_by_image_id = repaired_image.id
        created_new_image = True

    if repaired_image is not None and prepared_variant is not None:
        _preserve_civitai_source_variant(
            db,
            prepared=prepared_variant,
            image_db_id=repaired_image.id,
        )
        _persist_mismatch_static_variant(
            db,
            prepared=prepared_variant,
            image_db_id=repaired_image.id,
        )

    return {
        "ingest_result": ingest_result,
        "repaired_image": repaired_image,
        "created_new_image": created_new_image,
    }


def _archive_static_civitai_source_variant(
    *,
    image: ImageModel,
    civitai_image_id: int,
    expected_source_url: Optional[str],
    declared_mime_type: Optional[str],
) -> Optional[dict[str, Any]]:
    actual_path = Path(IMAGE_LIBRARY_PATH) / str(image.file_path)
    if not actual_path.exists():
        return None

    actual_mime_type = _normalize_mime_type(getattr(image, "mimetype", None))
    if not actual_mime_type.startswith("image/"):
        return None

    variant_root = Path(IMAGE_RESOURCES_PATH) / _CIVITAI_SOURCE_VARIANT_DIRNAME
    variant_root.mkdir(parents=True, exist_ok=True)
    suffix = actual_path.suffix.lower() or _guess_suffix(actual_mime_type)
    variant_path = variant_root / f"{civitai_image_id}_static_source{suffix}"
    shutil.copy2(actual_path, variant_path)
    variant_file_hash = _sha256_file(variant_path)

    variant_metadata = {
        "image_id": civitai_image_id,
        "declared_mimetype": declared_mime_type,
        "actual_mimetype": image.mimetype,
        "declared_filename": image.file_name,
        "original_variant_file_name": image.file_name,
        "library_file_path": str(image.file_path),
        "library_file_hash": image.file_hash,
        "variant_file_path": str(variant_path.relative_to(Path(IMAGE_RESOURCES_PATH))),
        "variant_file_hash": variant_file_hash,
        "source_url": image.source_url,
        "expected_source_url": expected_source_url,
        "actual_file_size": actual_path.stat().st_size,
        "reason": "archived_static_variant_before_civitai_video_replacement",
        "saved_at": datetime.utcnow().isoformat() + "Z",
    }

    metadata_path = variant_path.with_suffix(f"{variant_path.suffix}.json")
    with open(metadata_path, "w", encoding="utf-8") as handle:
        json.dump(variant_metadata, handle, indent=2)

    return variant_metadata


def _collect_repair_result(
    *,
    image: ImageModel,
    actions_taken: list[str],
    issues_found: list[str],
    warnings: list[str],
    created_new_image: bool,
    repaired_image: Optional[ImageModel],
    png_inspection: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    target_image = repaired_image or image
    return {
        "message": "Repair completed.",
        "original_file_hash": image.file_hash,
        "repaired_file_hash": target_image.file_hash,
        "repaired_image_id": target_image.id,
        "created_new_image": created_new_image,
        "actions_taken": actions_taken,
        "issues_found": issues_found,
        "warnings": warnings,
        "png_inspection": png_inspection,
    }


def _update_civitai_pipeline_heartbeat(
    task_context: TaskContext,
    *,
    futures_count: int,
    completed_count: int,
    total_count: int,
    waiting: bool = False,
) -> None:
    if waiting:
        task_context.heartbeat(
            f"Waiting on {futures_count} active CivitAI request(s); completed {completed_count}/{total_count}"
        )
        return

    task_context.heartbeat(
        f"Processing CivitAI import: active {futures_count}, completed {completed_count}/{total_count}"
    )


def _maybe_add_desired_image_db_id(
    result: dict[str, Any], desired_image_db_ids: set[int]
) -> None:
    """Add image_db_id from a result dict to the desired set if present."""
    image_db_id = result.get("image_db_id")
    if isinstance(image_db_id, int):
        desired_image_db_ids.add(image_db_id)


def _process_civitai_image_ids(
    task_context: TaskContext,
    *,
    api: CivitaiAPI,
    image_ids: list[int],
    attach_collection_id: Optional[int],
    item_key_prefix: str,
    collection_context: Optional[dict[str, Any]] = None,
    on_item_progress: Optional[Callable[[int, int], None]] = None,
) -> tuple[list[dict], set[int]]:
    results: list[dict] = []
    desired_image_db_ids: set[int] = set()
    download_candidates: list[tuple[int, bool]] = []

    with SessionLocal() as db:
        for image_id in image_ids:
            item_key = _task_item_key(item_key_prefix, image_id)
            task_context.mark_item(
                item_key, "checking_existing", "Checking local library"
            )
            try:
                result, recovered_existing = _handle_existing_civitai_image(
                    db,
                    image_id=image_id,
                    attach_collection_id=attach_collection_id,
                    backfill_metadata=False,
                )
                if result is not None:
                    _commit_with_lock_retry(
                        db, context=f"Existing image update for {image_id}"
                    )
                    image_db_id = result.get("image_db_id")
                    if isinstance(image_db_id, int):
                        desired_image_db_ids.add(image_db_id)
                    results.append(result)
                    _apply_civitai_task_result(
                        task_context,
                        item_key=item_key,
                        image_id=image_id,
                        result=result,
                    )
                    if on_item_progress is not None:
                        on_item_progress(len(results), len(image_ids))
                    continue

                if recovered_existing:
                    _commit_with_lock_retry(
                        db, context=f"Stale record cleanup for {image_id}"
                    )
                download_candidates.append((image_id, recovered_existing))
                task_context.mark_item(item_key, "queued", "Queued for remote fetch")
            except Exception as exc:
                if logging.getLogger(__name__).isEnabledFor(logging.DEBUG):
                    import traceback

                    traceback.print_exc()
                db.rollback()
                if _is_civitai_remote_not_found_error(exc):
                    result = _build_civitai_unavailable_result(
                        image_id,
                        exc,
                        api=api,
                        db=db,
                        attach_collection_id=attach_collection_id,
                        collection_id=(collection_context or {}).get("collection_id"),
                        collection_name=(collection_context or {}).get(
                            "collection_name"
                        ),
                        collection_item=_collection_context_item(
                            collection_context, image_id
                        ),
                    )
                else:
                    result = _build_failed_civitai_import_result(image_id, str(exc))
                _maybe_add_desired_image_db_id(result, desired_image_db_ids)
                results.append(result)
                _apply_civitai_task_result(
                    task_context,
                    item_key=item_key,
                    image_id=image_id,
                    result=result,
                )
                if on_item_progress is not None:
                    on_item_progress(len(results), len(image_ids))

    if not download_candidates:
        return results, desired_image_db_ids

    max_workers = max(
        1, min(_CIVITAI_IMPORT_NETWORK_CONCURRENCY, len(download_candidates))
    )
    next_index = 0
    futures: dict[Any, tuple[int, bool, str]] = {}
    last_heartbeat_at = time.monotonic()
    total_count = len(image_ids)

    def submit_available(executor: ThreadPoolExecutor) -> None:
        nonlocal next_index
        while (
            next_index < len(download_candidates)
            and len(futures) < max_workers
            and not task_context.cancel_requested
        ):
            image_id, recovered_existing = download_candidates[next_index]
            next_index += 1
            item_key = _task_item_key(item_key_prefix, image_id)
            futures[
                executor.submit(
                    _prepare_civitai_download,
                    api,
                    image_id=image_id,
                    item_key=item_key,
                    task_context=task_context,
                    collection_context=collection_context,
                )
            ] = (image_id, recovered_existing, item_key)
        _update_civitai_pipeline_heartbeat(
            task_context,
            futures_count=len(futures),
            completed_count=len(results),
            total_count=total_count,
        )

    with ThreadPoolExecutor(
        max_workers=max_workers, thread_name_prefix="civitai-fetch"
    ) as executor:
        submit_available(executor)

        while futures:
            if task_context.cancel_requested:
                while next_index < len(download_candidates):
                    image_id, _ = download_candidates[next_index]
                    next_index += 1
                    item_key = _task_item_key(item_key_prefix, image_id)
                    result = _build_cancelled_civitai_import_result(image_id)
                    results.append(result)
                    _apply_civitai_task_result(
                        task_context,
                        item_key=item_key,
                        image_id=image_id,
                        result=result,
                    )

            done, _ = wait(
                list(futures.keys()), timeout=0.25, return_when=FIRST_COMPLETED
            )
            if not done:
                now = time.monotonic()
                if now - last_heartbeat_at >= 2.0:
                    _update_civitai_pipeline_heartbeat(
                        task_context,
                        futures_count=len(futures),
                        completed_count=len(results),
                        total_count=total_count,
                        waiting=True,
                    )
                    last_heartbeat_at = now
                continue

            for future in done:
                image_id, recovered_existing, item_key = futures.pop(future)
                prepared: Optional[_PreparedCivitaiImport] = None
                try:
                    prepared = future.result()
                    if task_context.cancel_requested:
                        result = _build_cancelled_civitai_import_result(image_id)
                    else:
                        task_context.set_message(
                            f"Ingesting image {image_id}; completed {len(results)}/{total_count}"
                        )
                        task_context.mark_item(
                            item_key, "ingesting", "Importing into local library"
                        )
                        with SessionLocal() as db:
                            result = _ingest_prepared_civitai_import(
                                db,
                                prepared=prepared,
                                attach_collection_id=attach_collection_id,
                                recovered_existing=recovered_existing,
                            )
                            image_db_id = result.get("image_db_id")
                            if isinstance(image_db_id, int):
                                desired_image_db_ids.add(image_db_id)
                except Exception as exc:
                    if logging.getLogger(__name__).isEnabledFor(logging.DEBUG):
                        import traceback

                        traceback.print_exc()
                    if _is_civitai_remote_not_found_error(exc):
                        with SessionLocal() as db:
                            result = _build_civitai_unavailable_result(
                                image_id,
                                exc,
                                api=api,
                                db=db,
                                attach_collection_id=attach_collection_id,
                                collection_id=(collection_context or {}).get(
                                    "collection_id"
                                ),
                                collection_name=(collection_context or {}).get(
                                    "collection_name"
                                ),
                                collection_item=_collection_context_item(
                                    collection_context, image_id
                                ),
                            )
                    else:
                        result = _build_failed_civitai_import_result(image_id, str(exc))
                    _maybe_add_desired_image_db_id(result, desired_image_db_ids)
                finally:
                    if prepared is not None:
                        _cleanup_temp_file(prepared.temp_path)
                        _cleanup_temp_file(prepared.mismatch_static_temp_path)

                results.append(result)
                _apply_civitai_task_result(
                    task_context,
                    item_key=item_key,
                    image_id=image_id,
                    result=result,
                )
                if on_item_progress is not None:
                    on_item_progress(len(results), len(image_ids))
                last_heartbeat_at = time.monotonic()

            submit_available(executor)

    return results, desired_image_db_ids


def _ensure_variant_group_for_civitai_post(
    db: Session,
    post_id: int,
    image_db_ids: list[int],
    post_title: Optional[str] = None,
) -> None:
    """Create or update a civitai_post variant group linking all images from a single CivitAI post.

    Idempotent — safe to call multiple times.  Only creates the group when
    there are 2+ images.  Fail-open on error so post import is never blocked.
    """
    if len(image_db_ids) < 2:
        return

    group_key = f"civitai_post:{post_id}"
    group_label = post_title or f"CivitAI Post {post_id}"

    try:
        existing_group = (
            db.query(VariantGroup)
            .filter(VariantGroup.group_key == group_key)
            .first()
        )

        if existing_group is None:
            existing_group = VariantGroup(
                group_key=group_key,
                group_type="civitai_post",
                group_label=group_label,
                cover_preference="sort_order",
            )
            db.add(existing_group)
            db.flush()

        for sort_idx, img_id in enumerate(image_db_ids):
            existing_membership = (
                db.query(ImageVariantGroupMembership)
                .filter(
                    ImageVariantGroupMembership.image_id == img_id,
                    ImageVariantGroupMembership.group_id == existing_group.id,
                )
                .first()
            )
            if existing_membership is None:
                new_membership = ImageVariantGroupMembership(
                    image_id=img_id,
                    group_id=existing_group.id,
                    role_in_group="member",
                    sort_index=sort_idx,
                    source="auto_civitai",
                )
                db.add(new_membership)

        db.flush()

    except Exception:
        logging.getLogger(__name__).warning(
            "Failed to create civitai_post variant group for post %s", post_id, exc_info=True
        )


def _run_civitai_post_import_pipeline(
    task_context: TaskContext,
    *,
    api: CivitaiAPI,
    post_id: int,
    limit: Optional[int] = None,
) -> dict:
    """Import all images from a single CivitAI post.

    Fetches post metadata (title, user, etc.), then fetches all images via
    image.getInfinite with postId filter.  Images are ingested through the
    standard _process_civitai_image_ids pipeline.  A civitai_post variant
    group is created when the post has 2+ images.
    """
    task_context.set_message(f"Fetching CivitAI post {post_id}")

    # 1. Fetch post metadata for title / context
    post_data = api.fetch_post(post_id)
    post_title = None
    post_user = None
    if isinstance(post_data, dict):
        post_title = post_data.get("title")
        if not post_title:
            detail = post_data.get("detail")
            if isinstance(detail, dict):
                post_title = detail.get("title")
        user_obj = post_data.get("user")
        if isinstance(user_obj, dict):
            post_user = user_obj.get("username")

    label = post_title or f"Post {post_id}"
    task_context.set_message(f"Fetching images for CivitAI post: {label}")

    # 2. Fetch all images belonging to this post
    raw_images = api.fetch_post_images(post_id)
    if not raw_images:
        raise RuntimeError(f"Post {post_id} has no images or could not be fetched.")

    # 3. Deduplicate by image ID
    seen_ids: set[int] = set()
    image_ids: list[int] = []
    for img in raw_images:
        raw_id = img.get("id") if isinstance(img, dict) else None
        try:
            img_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if img_id in seen_ids:
            continue
        seen_ids.add(img_id)
        image_ids.append(img_id)

    if limit is not None and limit > 0:
        image_ids = image_ids[:limit]

    collection_total = len(image_ids)
    task_context.set_total(collection_total)
    task_context.set_message(
        f"Post \"{label}\": importing {collection_total} images"
    )

    # 4. Process images through the standard pipeline (no collection — posts are variant groups, not collections)
    results, desired_image_db_ids = _process_civitai_image_ids(
        task_context,
        api=api,
        image_ids=image_ids,
        attach_collection_id=None,
        item_key_prefix=f"civitai-post-{post_id}",
    )

    # 5. Create variant group for multi-image posts
    if desired_image_db_ids:
        with SessionLocal() as db:
            _ensure_variant_group_for_civitai_post(
                db,
                post_id=post_id,
                image_db_ids=desired_image_db_ids,
                post_title=post_title,
            )
            _commit_with_lock_retry(
                db, context=f"Post variant group commit for {post_id}"
            )

    images_added = sum(int(r.get("images_added", 0) or 0) for r in results)
    images_skipped = sum(int(r.get("images_skipped", 0) or 0) for r in results)
    images_recovered = sum(int(r.get("images_recovered", 0) or 0) for r in results)

    return {
        "civitai_post_id": post_id,
        "civitai_post_title": post_title,
        "civitai_post_user": post_user,
        "requested": len(image_ids),
        "images_added": images_added,
        "images_skipped": images_skipped,
        "images_recovered": images_recovered,
        "images_cancelled": sum(1 for r in results if r.get("cancelled")),
        "json_files_created": sum(
            int(r.get("json_files_created", 0) or 0) for r in results
        ),
        "errors": [
            f"Image {r.get('image_id')}: {r['error']}"
            for r in results
            if r.get("error")
        ],
        "results": results,
        "image_db_ids": list(desired_image_db_ids),
    }


def _run_civitai_post_import_job(
    task_context: TaskContext,
    *,
    post_id: int,
    limit: Optional[int] = None,
) -> dict:
    """Task handler for importing a single CivitAI post."""
    api = CivitaiAPI.get_instance()
    retry_metrics_before = _snapshot_civitai_payload_retry_metrics(api)
    summary = _run_civitai_post_import_pipeline(
        task_context,
        api=api,
        post_id=post_id,
        limit=limit,
    )
    retry_metrics = _diff_civitai_payload_retry_metrics(
        retry_metrics_before,
        _snapshot_civitai_payload_retry_metrics(api),
    )
    _record_civitai_payload_retry_metrics(task_context, retry_metrics)
    result = _build_civitai_import_summary(
        import_type="post",
        requested=int(summary.get("requested", 0) or 0),
        results=list(summary.get("results", [])),
        local_collection=None,
        civitai_payload_retry_metrics=retry_metrics,
    )
    if task_context.cancel_requested:
        task_context.cancel(result, "Cancelled")
    return result


def _run_civitai_post_collection_import_pipeline(
    task_context: TaskContext,
    *,
    api: CivitaiAPI,
    collection_id: int,
    collection_name: str,
    limit: Optional[int] = None,
    post_ids: Optional[list[int]] = None,
) -> dict:
    """Import all images from a post-type CivitAI collection.

    Post-type collections contain Posts (not individual images).  Each post
    may contain multiple images.  This pipeline:
      1. Creates/gets the local collection in the DB.
      2. Fetches the list of posts via a two-tier fallback strategy:
         - Tier 1: post.getInfinite with collectionId (published posts).
         - Tier 2: post.getInfinite with section=draft/draftOnly=true/pending=true
           (unpublished posts) when Tier 1 returns nothing and the authenticated
           user owns the collection.
         - Manual override: explicit post_ids parameter.
      3. For each post, fetches images via fetch_post_images().
      4. Ingests images through _process_civitai_image_ids.
      5. Creates a civitai_post variant group for multi-image posts.
      6. Ensures all imported images are members of the local collection.
      7. Reconciles stale memberships (images no longer in remote collection).
    """
    label = collection_name or f"Post Collection {collection_id}"
    task_context.set_message(f"Fetching posts for CivitAI collection: {label}")

    # 1. Ensure the local collection exists in DB
    with SessionLocal() as db:
        local_collection = _get_or_create_collection(
            db,
            name=collection_name,
            source="civitai",
            civitai_collection_id=collection_id,
        )
        _commit_with_lock_retry(
            db,
            context=f"Post collection setup commit for {collection_id}",
        )
        local_collection_id = int(local_collection.id)

    # 2. Fetch all posts in the collection via two-tier fallback
    posts: list[dict] = []
    _fallback_used: Optional[str] = None

    if post_ids:
        # Manual override: fetch each post individually by ID
        task_context.set_message(
            f"{label}: fetching {len(post_ids)} posts by explicit ID..."
        )
        for pid in post_ids:
            post_data = api.fetch_post(pid)
            if post_data and isinstance(post_data, dict):
                posts.append(post_data)
            else:
                logging.getLogger(__name__).warning(
                    "Could not fetch post %s for collection %s",
                    pid, collection_id,
                )
        if posts:
            _fallback_used = "manual_post_ids"

    if not posts:
        # Tier 1: standard collection query (finds published posts)
        posts = api.fetch_collection_posts(collection_id)

    if not posts:
        # Tier 2: try draft/unpublished posts via profile hook.
        # post.getInfinite supports combining collectionId with section=draft,
        # draftOnly=true, pending=true to return unpublished posts scoped to
        # a specific collection. See CIVITAI_API_REFERENCE.md → post.getInfinite.
        task_context.set_message(
            f"{label}: no published posts found, trying draft fallback..."
        )
        try:
            # Resolve collection owner username from collection metadata
            coll_raw = api._make_raw_request(
                "collection.getById",
                {"id": collection_id},
                strict=True,
            )
            coll_json = (
                coll_raw.get("result", {}).get("data", {}).get("json", {})
                if isinstance(coll_raw, dict)
                else {}
            )
            coll_user = None
            coll_meta = coll_json.get("collection") or coll_json
            if isinstance(coll_meta, dict):
                user_obj = coll_meta.get("user")
                if isinstance(user_obj, dict):
                    coll_user = user_obj.get("username")
            if not coll_user:
                perms = coll_json.get("permissions", {})
                if isinstance(perms, dict):
                    coll_user = perms.get("username")

            if coll_user:
                # Tier 2a: collectionId + draft params — returns drafts
                # scoped to this specific collection (verified 2026-05-15).
                draft_response = api._make_request(
                    endpoint="post.getInfinite",
                    payload_data={
                        "collectionId": collection_id,
                        "section": "draft",
                        "draftOnly": True,
                        "pending": True,
                        "username": coll_user,
                        "browsingLevel": api.default_params.get("browsingLevel", 31),
                        "period": "AllTime",
                        "periodMode": "published",
                        "sort": "Newest",
                        "excludedTagIds": [],
                        "authed": True,
                    },
                )
                draft_items = (
                    draft_response.get("items", [])
                    if isinstance(draft_response, dict)
                    else []
                )
                if not draft_items:
                    # Tier 2b: broader fallback — fetch all user drafts
                    # (not scoped to collection, but catches edge cases).
                    draft_response = api.fetch_user_draft_posts(coll_user)
                    if draft_response and isinstance(draft_response, dict):
                        draft_items = draft_response.get("items", [])

                if draft_items:
                    posts = draft_items
                    _fallback_used = "draft_fallback"
                    logging.getLogger(__name__).info(
                        "Draft fallback found %d unpublished post(s) for user %s "
                        "in collection %s",
                        len(posts), coll_user, collection_id,
                    )
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Draft fallback failed for collection %s: %s",
                collection_id, exc,
            )

    if not posts:
        raise RuntimeError(
            f"No posts found in CivitAI collection {collection_id} ({label}). "
            "The collection may be empty, private, or inaccessible. "
            "Try passing explicit post_ids to import specific posts."
        )

    # Apply limit to the number of posts
    if limit is not None and limit > 0:
        posts = posts[:limit]

    total_posts = len(posts)
    task_context.set_message(
        f"{label}: found {total_posts} posts, importing images..."
    )

    # 3. Iterate posts, import each one's images
    all_results: list[dict] = []
    post_summaries: list[dict] = []
    all_image_db_ids: set[int] = set()

    for post_index, post in enumerate(posts, start=1):
        post_id = post.get("id")
        post_title = post.get("title") or f"Post {post_id}"
        post_image_count = post.get("imageCount", 0)

        task_context.set_message(
            f"{label}: post {post_index}/{total_posts} — "
            f"\"{post_title}\" ({post_image_count} images)"
        )

        if task_context.cancel_requested:
            break

        try:
            post_summary = _run_civitai_post_import_pipeline(
                task_context,
                api=api,
                post_id=post_id,
                limit=None,  # import all images per post
            )
            post_summaries.append(post_summary)
            all_results.extend(post_summary.get("results", []))
            # Collect DB image IDs for membership tracking
            all_image_db_ids.update(post_summary.get("image_db_ids", []))

        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Failed to import post %s from collection %s: %s",
                post_id, collection_id, exc,
            )
            post_summaries.append({
                "post_id": post_id,
                "post_title": post_title,
                "error": str(exc),
            })

    # 4. Ensure memberships for all imported images + reconcile stale ones
    memberships_removed = 0
    with SessionLocal() as db:
        for image_db_id in all_image_db_ids:
            _ensure_image_in_collection(db, image_db_id, local_collection_id)
        if not task_context.cancel_requested:
            memberships_removed = _remove_images_not_in_collection_set(
                db, local_collection_id, all_image_db_ids
            )
        _commit_with_lock_retry(
            db,
            context=f"Post collection membership commit for {collection_id}",
        )

    # 5. Build summary
    images_added = sum(int(r.get("images_added", 0) or 0) for r in post_summaries)
    images_skipped = sum(int(r.get("images_skipped", 0) or 0) for r in post_summaries)
    images_recovered = sum(int(r.get("images_recovered", 0) or 0) for r in post_summaries)
    errors = [
        f"Post {s.get('post_id')}: {s['error']}"
        for s in post_summaries
        if s.get("error")
    ]

    # Serialize local collection for the summary
    local_collection_snapshot = None
    with SessionLocal() as db:
        lc = db.query(CollectionModel).filter(CollectionModel.id == local_collection_id).first()
        if lc is not None:
            local_collection_snapshot = _serialize_collection(lc)

    return {
        "civitai_collection_id": collection_id,
        "civitai_collection_name": collection_name,
        "civitai_collection_type": "post",
        "local_collection": local_collection_snapshot,
        "posts_total": total_posts,
        "posts_imported": sum(1 for s in post_summaries if not s.get("error")),
        "posts_errored": len(errors),
        "requested": images_added + images_skipped,
        "images_added": images_added,
        "images_skipped": images_skipped,
        "images_recovered": images_recovered,
        "images_cancelled": sum(1 for r in all_results if r.get("cancelled")),
        "json_files_created": sum(
            int(r.get("json_files_created", 0) or 0) for r in all_results
        ),
        "memberships_removed": memberships_removed,
        "errors": errors,
        "results": all_results,
        "post_summaries": post_summaries,
    }


def _run_civitai_collection_import_pipeline(
    task_context: TaskContext,
    *,
    api: CivitaiAPI,
    collection_id: int,
    collection_name: str,
    limit: Optional[int],
    reset_total: bool,
    collection_index: Optional[int] = None,
    collection_count: Optional[int] = None,
    overall_processed_before: int = 0,
    overall_discovered_before: int = 0,
    on_collection_progress: Optional[Callable[..., None]] = None,
    on_pending_activity: Optional[Callable[[str], None]] = None,
    prefetched_probe: Optional[_CivitaiCollectionProbe] = None,
) -> dict:
    collection_label = collection_name or f"Collection {collection_id}"

    def _set_collection_state(**metadata: Any) -> None:
        base_metadata = {
            "current_collection_id": collection_id,
            "current_collection_name": collection_label,
        }
        if collection_index is not None:
            base_metadata["current_collection_index"] = collection_index
        if collection_count is not None:
            base_metadata["current_collection_count"] = collection_count
        base_metadata.update(metadata)
        for key, value in base_metadata.items():
            task_context.set_metadata(key, value)

    def _format_collection_prefix() -> str:
        if collection_index is not None and collection_count is not None:
            return (
                f"Collection {collection_index}/{collection_count}: {collection_label}"
            )
        return f"Collection: {collection_label}"

    def _on_collection_page(
        page_number: int, page_items: int, discovered_count: int
    ) -> None:
        overall_discovered = overall_discovered_before + discovered_count
        _set_collection_state(
            current_collection_page=page_number,
            current_collection_page_items=page_items,
            current_collection_discovered=discovered_count,
            current_collection_total=discovered_count,
            current_collection_processed=0,
            overall_items_discovered=overall_discovered,
            overall_items_processed=overall_processed_before,
        )
        if on_collection_progress is not None:
            on_collection_progress(
                discovered=discovered_count,
                total=discovered_count,
                message=f"Discovered {discovered_count} items (page {page_number})",
            )
        if on_pending_activity is not None:
            on_pending_activity(
                f"Fetching items for {collection_label} (page {page_number})"
            )
        task_context.set_message(
            f"{_format_collection_prefix()} | Discovered {discovered_count} items | All discovered {overall_discovered}"
        )

    task_context.set_message(f"Fetching collection items for {collection_label}")
    if on_collection_progress is not None:
        on_collection_progress(status="fetching", message="Fetching collection items")
    if on_pending_activity is not None:
        on_pending_activity(f"Fetching items for {collection_label}")
    scraper = CivitaiPrivateScraper(auto_authenticate=True)
    # Reuse probe's first page to avoid redundant API call for page 1.
    _probe_items = None
    _probe_cursor = None
    if prefetched_probe is not None and prefetched_probe.first_page_items:
        _probe_items = prefetched_probe.first_page_items
        _probe_cursor = prefetched_probe.first_page_cursor
    collection_items = scraper.fetch_collection_items(
        collection_id=collection_id,
        limit=limit,
        progress_callback=_on_collection_page,
        collection_name=collection_name,
        initial_items=_probe_items,
        initial_cursor=_probe_cursor,
    )
    if isinstance(collection_items, list):
        normalized_items = [item for item in collection_items if isinstance(item, dict)]
        _archive_civitai_collection_items(normalized_items)

    if not collection_items:
        # ── Probe: is this a post-type collection? ──
        # image.getInfinite returns nothing for post-type collections.
        # Check the collection metadata and redirect if needed.
        try:
            coll_data = api._make_raw_request(
                "collection.getById",
                {"id": collection_id},
                strict=True,
            )
            coll_json = (
                coll_data.get("result", {}).get("data", {}).get("json", {})
                if isinstance(coll_data, dict)
                else {}
            )
            coll_type = (
                coll_json.get("collection", {})
                .get("type", "")
            ) if isinstance(coll_json.get("collection"), dict) else ""
            if not coll_type:
                permissions = coll_json.get("permissions", {})
                coll_type = (
                    permissions.get("collectionType", "")
                    if isinstance(permissions, dict)
                    else ""
                )
            if coll_type.strip().lower() == "post":
                logging.getLogger(__name__).info(
                    "Collection %s is a post-type collection, redirecting to post collection pipeline.",
                    collection_id,
                )
                return _run_civitai_post_collection_import_pipeline(
                    task_context,
                    api=api,
                    collection_id=collection_id,
                    collection_name=collection_name or coll_json.get("collection", {}).get("name", ""),
                    limit=limit,
                )
        except Exception as probe_exc:
            logging.getLogger(__name__).debug(
                "Collection type probe failed for %s: %s", collection_id, probe_exc
            )
        raise RuntimeError(_build_civitai_empty_collection_message(collection_id))

    seen_ids: set[int] = set()
    image_ids: list[int] = []
    collection_item_index: dict[int, dict[str, Any]] = {}
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
        if isinstance(item, dict):
            collection_item_index[image_id] = item

    collection_total = len(image_ids)
    overall_discovered = overall_discovered_before + collection_total
    _set_collection_state(
        current_collection_discovered=collection_total,
        current_collection_total=collection_total,
        current_collection_processed=0,
        overall_items_discovered=overall_discovered,
        overall_items_processed=overall_processed_before,
    )
    if on_collection_progress is not None:
        on_collection_progress(
            discovered=collection_total,
            total=collection_total,
            status="processing",
            message=f"Discovered {collection_total} unique items",
        )
    if on_pending_activity is not None:
        on_pending_activity(f"Processing {collection_total} items for {collection_label}")
    task_context.set_message(
        f"{_format_collection_prefix()} | Discovered {collection_total} items | All discovered {overall_discovered}"
    )

    if reset_total:
        task_context.set_total(len(image_ids))
    else:
        task_context.increment_total(len(image_ids))

    with SessionLocal() as db:
        local_collection = _get_or_create_collection(
            db,
            name=collection_name,
            source="civitai",
            civitai_collection_id=collection_id,
        )
        _commit_with_lock_retry(
            db, context=f"Collection setup commit for {collection_id}"
        )
        local_collection_snapshot = _serialize_collection(local_collection)
        local_collection_id = int(local_collection.id)

    task_context.set_metadata("local_collection", local_collection_snapshot)

    def _on_item_progress(collection_processed: int, collection_size: int) -> None:
        overall_processed = overall_processed_before + collection_processed
        _set_collection_state(
            current_collection_processed=collection_processed,
            current_collection_total=collection_size,
            current_collection_discovered=collection_total,
            overall_items_discovered=overall_discovered,
            overall_items_processed=overall_processed,
        )
        if on_collection_progress is not None:
            on_collection_progress(
                metadata_gathered=collection_processed,
                images_fetched=collection_processed,
                message=f"Processing {collection_processed}/{collection_size}",
            )
        if on_pending_activity is not None:
            on_pending_activity(
                f"Processing items for {collection_label}: {collection_processed}/{collection_size}"
            )
        task_context.set_message(
            f"{_format_collection_prefix()} | Collection {collection_processed}/{collection_size} | All {overall_processed}/{overall_discovered}"
        )

    results, desired_image_db_ids = _process_civitai_image_ids(
        task_context,
        api=api,
        image_ids=image_ids,
        attach_collection_id=local_collection_id,
        item_key_prefix=f"collection:{collection_id}",
        collection_context={
            "collection_id": collection_id,
            "collection_name": collection_name,
            "collection_item_index": collection_item_index,
        },
        on_item_progress=_on_item_progress,
    )

    with SessionLocal() as db:
        memberships_removed = _remove_images_not_in_collection_set(
            db,
            local_collection_id,
            desired_image_db_ids,
        )
        local_collection = (
            db.query(CollectionModel)
            .filter(CollectionModel.id == local_collection_id)
            .first()
        )
        if local_collection is not None:
            _apply_civitai_collection_probe_state(
                local_collection,
                probe=_CivitaiCollectionProbe(
                    image_ids=image_ids[:_CIVITAI_COLLECTION_HEAD_PROBE_SIZE],
                    fingerprint=_build_civitai_collection_fingerprint(
                        image_ids[:_CIVITAI_COLLECTION_HEAD_PROBE_SIZE]
                    ),
                    has_more=len(image_ids) > _CIVITAI_COLLECTION_HEAD_PROBE_SIZE,
                ),
                synced_at=datetime.utcnow(),
                # Use the actual membership count, not the remote count.
                # Images that fail ingest (e.g. corrupted PNGs) have no DB
                # entry, so len(desired_image_db_ids) is the truth of what we
                # have.  Using len(image_ids) would permanently mismatch.
                full_item_count=len(desired_image_db_ids),
                mark_full_scan=True,
            )
            local_collection_snapshot = _serialize_collection(local_collection)
        _commit_with_lock_retry(
            db,
            context=f"Collection membership sync commit for {collection_id}",
        )

    task_context.increment_counter("collections_synced", 1)
    task_context.increment_counter("memberships_removed", memberships_removed)

    # Finalize structured progress entry from import results.
    images_added = sum(int(r.get("images_added", 0) or 0) for r in results)
    images_skipped = sum(int(r.get("images_skipped", 0) or 0) for r in results)
    error_count = sum(1 for r in results if r.get("error"))
    if on_collection_progress is not None:
        on_collection_progress(
            imported=images_added,
            skipped=images_skipped,
            errors=error_count,
            total=len(image_ids),
            discovered=collection_total,
            metadata_gathered=len(results),
            images_fetched=len(results),
            status="completed",
            message=f"Done: {images_added} imported, {images_skipped} skipped, {error_count} errors",
        )
    if on_pending_activity is not None:
        on_pending_activity("")

    return {
        "civitai_collection_id": collection_id,
        "civitai_collection_name": collection_name,
        "local_collection": local_collection_snapshot,
        "requested": len(image_ids),
        "images_added": sum(int(r.get("images_added", 0) or 0) for r in results),
        "images_skipped": sum(int(r.get("images_skipped", 0) or 0) for r in results),
        "images_recovered": sum(
            int(r.get("images_recovered", 0) or 0) for r in results
        ),
        "images_cancelled": sum(1 for r in results if r.get("cancelled")),
        "json_files_created": sum(
            int(r.get("json_files_created", 0) or 0) for r in results
        ),
        "memberships_removed": memberships_removed,
        "errors": [
            f"Image {r.get('image_id')}: {r['error']}"
            for r in results
            if r.get("error")
        ],
        "unavailable_items": _collect_civitai_unavailable_items(results),
        "results": results,
        "sync_state": "full_verify",
    }


def _run_civitai_image_import_job(task_context: TaskContext, image_id: int) -> dict:
    api = CivitaiAPI.get_instance()
    retry_metrics_before = _snapshot_civitai_payload_retry_metrics(api)
    task_context.set_total(1)
    task_context.set_message(f"Importing CivitAI image {image_id}")
    results, _ = _process_civitai_image_ids(
        task_context,
        api=api,
        image_ids=[image_id],
        attach_collection_id=None,
        item_key_prefix="civitai-image-import",
    )
    retry_metrics = _diff_civitai_payload_retry_metrics(
        retry_metrics_before,
        _snapshot_civitai_payload_retry_metrics(api),
    )
    _record_civitai_payload_retry_metrics(task_context, retry_metrics)
    summary = _build_civitai_import_summary(
        import_type="image",
        requested=1,
        results=results,
        local_collection=None,
        civitai_payload_retry_metrics=retry_metrics,
    )
    if task_context.cancel_requested:
        task_context.cancel(summary, "Cancelled")
    return summary


def _run_civitai_collection_import_job(
    task_context: TaskContext,
    *,
    collection_id: int,
    limit: Optional[int],
) -> dict:
    api = CivitaiAPI.get_instance()
    retry_metrics_before = _snapshot_civitai_payload_retry_metrics(api)
    collection_name = _fetch_civitai_collection_name(api, collection_id)
    summary = _run_civitai_collection_import_pipeline(
        task_context,
        api=api,
        collection_id=collection_id,
        collection_name=collection_name,
        limit=limit,
        reset_total=True,
    )
    retry_metrics = _diff_civitai_payload_retry_metrics(
        retry_metrics_before,
        _snapshot_civitai_payload_retry_metrics(api),
    )
    _record_civitai_payload_retry_metrics(task_context, retry_metrics)
    result = _build_civitai_import_summary(
        import_type="collection",
        requested=int(summary.get("requested", 0) or 0),
        results=list(summary.get("results", [])),
        local_collection=summary.get("local_collection"),
        civitai_payload_retry_metrics=retry_metrics,
    )
    if task_context.cancel_requested:
        task_context.cancel(result, "Cancelled")
    return result


def _run_civitai_collection_sync_job(
    task_context: TaskContext, *, limit: Optional[int]
) -> dict:
    api = CivitaiAPI.get_instance()
    retry_metrics_before = _snapshot_civitai_payload_retry_metrics(api)
    task_context.set_total(0)
    task_context.set_message("Fetching your CivitAI collections")
    try:
        remote_collections = _fetch_civitai_user_image_collections(api)
    except HTTPException as exc:
        detail = str(exc.detail) if exc.detail else ""
        is_auth_error = (
            exc.status_code == 502 and "401" in detail and "Unauthorized" in detail
        )
        is_unavailable = exc.status_code == 503
        if is_auth_error:
            task_context.set_metadata("auth_required", True)
            task_context.fail(f"CivitAI authentication expired. {detail}")
        elif is_unavailable:
            task_context.set_metadata("upstream_unavailable", True)
            task_context.fail(
                detail or "CivitAI is currently unavailable (HTTP 503)."
            )
        else:
            task_context.fail(
                detail or f"Could not fetch collections (HTTP {exc.status_code})."
            )
        return {
            "message": detail,
            "collections_requested": 0,
            "collections_synced": 0,
            "images_added": 0,
            "images_skipped": 0,
            "images_recovered": 0,
            "images_cancelled": 0,
            "json_files_created": 0,
            "memberships_removed": 0,
            "errors": [detail],
            "warnings": _get_runtime_warnings(),
            "collections": [],
            "orphaned_local_collections": [],
            "civitai_payload_retry_metrics": _diff_civitai_payload_retry_metrics(
                retry_metrics_before,
                _snapshot_civitai_payload_retry_metrics(api),
            ),
        }
    if not remote_collections:
        retry_metrics = _diff_civitai_payload_retry_metrics(
            retry_metrics_before,
            _snapshot_civitai_payload_retry_metrics(api),
        )
        _record_civitai_payload_retry_metrics(task_context, retry_metrics)
        return {
            "message": "No CivitAI collections found.",
            "collections_requested": 0,
            "collections_synced": 0,
            "images_added": 0,
            "images_skipped": 0,
            "images_recovered": 0,
            "images_cancelled": 0,
            "json_files_created": 0,
            "memberships_removed": 0,
            "errors": [],
            "warnings": _get_runtime_warnings(),
            "collections": [],
            "orphaned_local_collections": [],
            "civitai_payload_retry_metrics": retry_metrics,
        }

    # Initialize per-collection structured progress tracking.
    collections_progress: list[dict[str, Any]] = []
    for remote in remote_collections:
        cid = int(remote["id"])
        cname = str(remote["name"])
        collections_progress.append(
            {
                "collection_id": cid,
                "collection_name": cname,
                "status": "pending",
                "discovered": 0,
                "metadata_gathered": 0,
                "images_fetched": 0,
                "skipped": 0,
                "errors": 0,
                "imported": 0,
                "total": 0,
                "message": "",
            }
        )
    task_context.set_metadata("collections_progress", collections_progress)
    task_context.set_metadata("pending_activities", [])

    collection_summaries: list[dict] = []
    overall_processed = 0
    overall_discovered = 0
    for index, remote in enumerate(remote_collections, start=1):
        collection_id = int(remote["id"])
        collection_name = str(remote["name"])
        cp_entry = next(
            (
                entry
                for entry in collections_progress
                if int(entry.get("collection_id", 0) or 0) == collection_id
            ),
            None,
        )
        if cp_entry is None:
            cp_entry = {
                "collection_id": collection_id,
                "collection_name": collection_name,
                "status": "pending",
                "discovered": 0,
                "metadata_gathered": 0,
                "images_fetched": 0,
                "skipped": 0,
                "errors": 0,
                "imported": 0,
                "total": 0,
                "message": "",
            }
            collections_progress.append(cp_entry)

        cp_entry = cast(dict[str, Any], cp_entry)

        def _update_cp_entry(
            _cp_entry: dict[str, Any] = cp_entry, **updates: Any
        ) -> None:
            _cp_entry.update(updates)
            task_context.set_metadata("collections_progress", collections_progress)

        def _set_pending_activity(activity: str) -> None:
            task_context.set_metadata("pending_activities", [activity] if activity else [])

        collection_item_key = f"collection:{collection_id}"
        task_context.mark_item(
            collection_item_key, "running", f"Syncing {collection_name}"
        )

        # ── Route post-type entries to the post collection import pipeline ──
        remote_type = str(remote.get("type") or "").strip().lower()
        if remote_type == "post":
            task_context.set_message(
                f"Syncing post collection {index}/{len(remote_collections)}: {collection_name}"
            )
            try:
                post_summary = _run_civitai_post_collection_import_pipeline(
                    task_context,
                    api=api,
                    collection_id=collection_id,
                    collection_name=collection_name,
                    limit=limit,
                )
                cp_entry["status"] = "completed"
                cp_entry["imported"] = int(post_summary.get("images_added", 0) or 0)
                cp_entry["skipped"] = int(post_summary.get("images_skipped", 0) or 0)
                cp_entry["errors"] = len(post_summary.get("errors", []))
                cp_entry["total"] = int(post_summary.get("requested", 0) or 0)
                cp_entry["discovered"] = cp_entry["total"]
                cp_entry["message"] = (
                    f"Synced {cp_entry['imported']} imported, {cp_entry['skipped']} skipped"
                )
                task_context.set_metadata("pending_activities", [])
                collection_summaries.append(
                    {
                        "civitai_collection_id": collection_id,
                        "civitai_collection_name": collection_name,
                        "local_collection": post_summary.get("local_collection"),
                        "requested": int(post_summary.get("requested", 0) or 0),
                        "images_added": int(post_summary.get("images_added", 0) or 0),
                        "images_skipped": int(
                            post_summary.get("images_skipped", 0) or 0
                        ),
                        "images_recovered": int(
                            post_summary.get("images_recovered", 0) or 0
                        ),
                        "images_cancelled": sum(
                            1 for r in post_summary.get("results", []) if r.get("cancelled")
                        ),
                        "json_files_created": sum(
                            int(r.get("json_files_created", 0) or 0)
                            for r in post_summary.get("results", [])
                        ),
                        "memberships_removed": int(
                            post_summary.get("memberships_removed", 0) or 0
                        ),
                        "errors": post_summary.get("errors", []),
                        "unavailable_items": [],
                        "results": post_summary.get("results", []),
                        "sync_state": "full_verify",
                    }
                )
                posts_total = post_summary.get("posts_total", 0)
                task_context.mark_item(
                    collection_item_key,
                    "completed",
                    f"Synced {posts_total} posts ({post_summary.get('images_added', 0)} images)",
                )
            except Exception as exc:
                error_text = str(exc)
                cp_entry["status"] = "failed"
                cp_entry["errors"] = 1
                cp_entry["message"] = error_text
                task_context.set_metadata("pending_activities", [])
                task_context.add_error(f"Post collection {collection_id}: {error_text}")
                task_context.mark_item(collection_item_key, "failed", error_text)
                collection_summaries.append(
                    {
                        "civitai_collection_id": collection_id,
                        "civitai_collection_name": collection_name,
                        "local_collection": None,
                        "requested": 0,
                        "images_added": 0,
                        "images_skipped": 0,
                        "images_recovered": 0,
                        "images_cancelled": 0,
                        "json_files_created": 0,
                        "memberships_removed": 0,
                        "errors": [error_text],
                        "unavailable_items": [],
                        "results": [],
                        "sync_state": "failed",
                    }
                )
            continue  # Skip to next remote entry (collection or post)

        # ── Standard collection sync ──
        task_context.set_message(
            f"Syncing collection {index}/{len(remote_collections)}: {collection_name}"
        )
        try:
            with SessionLocal() as db:
                local_collection = _get_or_create_collection(
                    db,
                    name=collection_name,
                    source="civitai",
                    civitai_collection_id=collection_id,
                )
                _commit_with_lock_retry(
                    db,
                    context=f"Collection sync setup commit for {collection_id}",
                )
                local_collection_id = int(local_collection.id)

            scraper = CivitaiPrivateScraper(auto_authenticate=True)
            task_context.set_message(
                f"Checking collection {index}/{len(remote_collections)}: {collection_name}"
            )
            probe = _probe_civitai_collection_head(scraper, collection_id=collection_id)
            summary: Optional[dict[str, Any]] = None

            with SessionLocal() as db:
                collection_row = (
                    db.query(CollectionModel)
                    .filter(CollectionModel.id == local_collection_id)
                    .first()
                )
                if collection_row is None:
                    raise RuntimeError(
                        f"Local collection {local_collection_id} disappeared during sync."
                    )

                local_membership_count, local_media_incomplete = (
                    _inspect_local_civitai_collection_health(
                        db,
                        local_collection_id=local_collection_id,
                    )
                )
                needs_full_verify, sync_state = (
                    _civitai_collection_requires_full_verify(
                        collection_row,
                        probe=probe,
                        local_membership_count=local_membership_count,
                        local_media_incomplete=local_media_incomplete,
                        force_full_verify=limit is not None,
                    )
                )

                if not probe.image_ids and not probe.has_more:
                    memberships_removed = _remove_images_not_in_collection_set(
                        db,
                        local_collection_id,
                        set(),
                    )
                    _apply_civitai_collection_probe_state(
                        collection_row,
                        probe=probe,
                        synced_at=datetime.utcnow(),
                        full_item_count=0,
                        mark_full_scan=True,
                    )
                    local_snapshot = _serialize_collection(collection_row)
                    _commit_with_lock_retry(
                        db,
                        context=f"Empty collection sync commit for {collection_id}",
                    )
                    cp_entry["status"] = "completed"
                    cp_entry["message"] = "Empty collection synced"
                    task_context.set_metadata("pending_activities", [])
                    task_context.increment_counter("collections_synced", 1)
                    task_context.increment_counter(
                        "memberships_removed", memberships_removed
                    )
                    summary = _build_civitai_empty_collection_sync_summary(
                        collection_id=collection_id,
                        collection_name=collection_name,
                        local_collection_snapshot=local_snapshot,
                        memberships_removed=memberships_removed,
                    )
                elif not needs_full_verify:
                    _apply_civitai_collection_probe_state(
                        collection_row,
                        probe=probe,
                        synced_at=datetime.utcnow(),
                    )
                    local_snapshot = _serialize_collection(collection_row)
                    _commit_with_lock_retry(
                        db,
                        context=f"Collection incremental sync commit for {collection_id}",
                    )
                    cp_entry["status"] = "skipped"
                    cp_entry["message"] = f"No remote changes ({sync_state})"
                    task_context.set_metadata("pending_activities", [])
                    task_context.increment_counter("collections_synced", 1)
                    summary = _build_civitai_collection_skip_summary(
                        collection_id=collection_id,
                        collection_name=collection_name,
                        local_collection_snapshot=local_snapshot,
                        sync_state=sync_state,
                    )

            if summary is None:
                cp_entry["status"] = "processing"
                cp_entry["message"] = "Importing items"
                summary = _run_civitai_collection_import_pipeline(
                    task_context,
                    api=api,
                    collection_id=collection_id,
                    collection_name=collection_name,
                    limit=limit,
                    reset_total=False,
                    collection_index=index,
                    collection_count=len(remote_collections),
                    overall_processed_before=overall_processed,
                    overall_discovered_before=overall_discovered,
                    on_collection_progress=_update_cp_entry,
                    on_pending_activity=_set_pending_activity,
                    prefetched_probe=probe,
                )
            # Finalize structured progress from pipeline results.
            if cp_entry["status"] not in ("completed", "skipped", "failed"):
                cp_entry["status"] = "completed"
                cp_entry["imported"] = int(summary.get("images_added", 0) or 0)
                cp_entry["skipped"] = int(summary.get("images_skipped", 0) or 0)
                cp_entry["errors"] = len(summary.get("errors", []))
                cp_entry["total"] = int(summary.get("requested", 0) or 0)
                cp_entry["discovered"] = cp_entry["total"]
                cp_entry["message"] = (
                    f"Synced {cp_entry['imported']} imported, {cp_entry['skipped']} skipped"
                )
            task_context.set_metadata("pending_activities", [])

            collection_summaries.append(summary)
            overall_processed += int(summary.get("requested", 0) or 0)
            overall_discovered += int(summary.get("requested", 0) or 0)
            sync_state_text = str(summary.get("sync_state") or "")
            task_context.mark_item(
                collection_item_key,
                "completed",
                (
                    f"No remote changes ({sync_state_text})"
                    if sync_state_text and sync_state_text != "full_verify"
                    else f"Synced {summary.get('requested', 0)} items"
                ),
            )
        except Exception as exc:
            error_text = str(exc)
            cp_entry["status"] = "failed"
            cp_entry["errors"] = 1
            cp_entry["message"] = error_text
            task_context.set_metadata("pending_activities", [])
            task_context.add_error(f"Collection {collection_id}: {error_text}")
            task_context.mark_item(collection_item_key, "failed", error_text)
            collection_summaries.append(
                {
                    "civitai_collection_id": collection_id,
                    "civitai_collection_name": collection_name,
                    "local_collection": None,
                    "requested": 0,
                    "images_added": 0,
                    "images_skipped": 0,
                    "images_recovered": 0,
                    "images_cancelled": 0,
                    "json_files_created": 0,
                    "memberships_removed": 0,
                    "errors": [error_text],
                    "unavailable_items": [],
                    "results": [],
                    "sync_state": "failed",
                }
            )

        if task_context.cancel_requested:
            break

    remote_collection_ids = {int(item["id"]) for item in remote_collections}
    with SessionLocal() as db:
        orphaned_local_collections = [
            _serialize_collection(collection)
            for collection in db.query(CollectionModel)
            .filter(CollectionModel.source == "civitai")
            .order_by(CollectionModel.name.asc())
            .all()
            if collection.civitai_collection_id is not None
            and int(collection.civitai_collection_id) not in remote_collection_ids
        ]

    summary = {
        "message": "CivitAI collection sync complete.",
        "collections_requested": len(remote_collections),
        "collections_synced": len(
            [item for item in collection_summaries if not item.get("errors")]
        ),
        "images_added": sum(
            int(item.get("images_added", 0) or 0) for item in collection_summaries
        ),
        "images_skipped": sum(
            int(item.get("images_skipped", 0) or 0) for item in collection_summaries
        ),
        "images_recovered": sum(
            int(item.get("images_recovered", 0) or 0) for item in collection_summaries
        ),
        "images_cancelled": sum(
            int(item.get("images_cancelled", 0) or 0) for item in collection_summaries
        ),
        "json_files_created": sum(
            int(item.get("json_files_created", 0) or 0) for item in collection_summaries
        ),
        "memberships_removed": sum(
            int(item.get("memberships_removed", 0) or 0)
            for item in collection_summaries
        ),
        "errors": [
            f"Collection {item.get('civitai_collection_id')}: {error}"
            for item in collection_summaries
            for error in item.get("errors", [])
        ],
        "unavailable_items": [
            detail
            for item in collection_summaries
            for detail in item.get("unavailable_items", [])
            if isinstance(detail, dict)
        ],
        "warnings": _get_runtime_warnings(),
        "collections": collection_summaries,
        "orphaned_local_collections": orphaned_local_collections,
    }
    retry_metrics = _diff_civitai_payload_retry_metrics(
        retry_metrics_before,
        _snapshot_civitai_payload_retry_metrics(api),
    )
    _record_civitai_payload_retry_metrics(task_context, retry_metrics)
    summary["civitai_payload_retry_metrics"] = retry_metrics
    if task_context.cancel_requested:
        task_context.cancel(summary, "Cancelled")
    return summary


def _image_has_civitai_nsfw_level(image: ImageModel) -> bool:
    db_json = image.json_metadata if isinstance(image.json_metadata, dict) else {}
    db_civitai_payload = db_json.get("civitai")
    sidecar_payload = _read_image_sidecar_payload(image)
    sidecar_civitai_payload = (
        sidecar_payload.get("civitai") if isinstance(sidecar_payload, dict) else None
    )
    return _payload_has_nsfw_level(db_civitai_payload) or _payload_has_nsfw_level(
        sidecar_civitai_payload
    )


def _run_civitai_nsfw_backfill_job(
    task_context: TaskContext,
    *,
    limit: Optional[int],
    reimport_if_missing: bool,
) -> dict:
    api = CivitaiAPI.get_instance()
    retry_metrics_before = _snapshot_civitai_payload_retry_metrics(api)

    with SessionLocal() as db:
        query = (
            db.query(ImageModel.id)
            .filter(_active_image_filter())
            .filter(
                func.lower(ImageModel.source_url).like("%civitai.com/images/%")
                | func.lower(ImageModel.source_url).like("%civitai.red/images/%")
            )
            .order_by(ImageModel.id.asc())
        )
        if limit is not None:
            query = query.limit(limit)
        image_ids = [int(row[0]) for row in query.all()]

    task_context.set_total(len(image_ids))
    task_context.set_message("Backfilling CivitAI NSFW metadata")

    results: list[dict[str, Any]] = []
    reimport_candidates: list[dict[str, Any]] = []
    processed_count = 0
    metadata_backfilled_count = 0
    reimport_recovered_count = 0
    already_complete_count = 0
    remote_unavailable_count = 0
    failed_count = 0
    skipped_count = 0

    for index, image_db_id in enumerate(image_ids, start=1):
        if task_context.cancel_requested:
            break

        with SessionLocal() as db:
            image = db.query(ImageModel).filter(ImageModel.id == image_db_id).first()
            if image is None:
                task_context.mark_item(
                    f"nsfw-backfill:{image_db_id}", "skipped", "Image no longer exists"
                )
                task_context.increment_counter("images_skipped", 1)
                skipped_count += 1
                processed_count += 1
                task_context.advance()
                continue

            source_url = str(image.source_url or "").strip()
            item_key = f"nsfw-backfill:{image.file_hash}"
            task_context.set_message(
                f"Backfilling CivitAI NSFW metadata {index}/{len(image_ids)}"
            )

            if not source_url or not is_civitai_image_url(source_url):
                task_context.mark_item(
                    item_key, "skipped", "Not a CivitAI image source"
                )
                task_context.increment_counter("images_skipped", 1)
                skipped_count += 1
                processed_count += 1
                task_context.advance()
                continue

            if _image_has_civitai_nsfw_level(image):
                task_context.mark_item(item_key, "skipped", "Already has nsfwLevel")
                task_context.increment_counter("already_complete", 1)
                already_complete_count += 1
                processed_count += 1
                task_context.advance()
                continue

            try:
                metadata_backfilled = _ensure_civitai_metadata_for_existing_image(
                    db=db,
                    image=image,
                    source_url=source_url,
                )
                if metadata_backfilled:
                    _commit_with_lock_retry(
                        db,
                        context=f"NSFW metadata backfill commit for image {image.id}",
                    )
                    task_context.mark_item(
                        item_key, "completed", "Backfilled nsfwLevel metadata"
                    )
                    task_context.increment_counter("metadata_backfilled", 1)
                    metadata_backfilled_count += 1
                    processed_count += 1
                    task_context.advance()
                    results.append(
                        {
                            "image_db_id": image.id,
                            "file_hash": image.file_hash,
                            "source_url": source_url,
                            "status": "metadata_backfilled",
                        }
                    )
                    continue

                if reimport_if_missing:
                    parsed_image_id = extract_civitai_image_id(source_url)
                    if parsed_image_id is None:
                        task_context.mark_item(
                            item_key,
                            "failed",
                            "Could not parse CivitAI image id for reimport",
                        )
                        task_context.increment_counter("images_failed", 1)
                        failed_count += 1
                        processed_count += 1
                        task_context.advance()
                        reimport_candidates.append(
                            {
                                "image_db_id": image.id,
                                "file_hash": image.file_hash,
                                "source_url": source_url,
                                "reason": "missing_nsfw_level_and_unparseable_image_id",
                            }
                        )
                        continue

                    reimport_result = _import_single_civitai_image(
                        api,
                        db,
                        parsed_image_id,
                        force_reimport_on_missing_metadata=True,
                    )
                    if reimport_result.get("error"):
                        task_context.mark_item(
                            item_key, "failed", str(reimport_result.get("error"))
                        )
                        task_context.increment_counter("images_failed", 1)
                        failed_count += 1
                        processed_count += 1
                        task_context.add_error(
                            f"Image {parsed_image_id}: {reimport_result.get('error')}"
                        )
                        task_context.advance()
                        reimport_candidates.append(
                            {
                                "image_db_id": image.id,
                                "file_hash": image.file_hash,
                                "source_url": source_url,
                                "reason": "reimport_failed",
                                "error": reimport_result.get("error"),
                            }
                        )
                        continue

                    _commit_with_lock_retry(
                        db, context=f"NSFW reimport commit for image {parsed_image_id}"
                    )
                    refreshed = _find_existing_image_by_source_url(db, source_url)
                    if refreshed is not None and _image_has_civitai_nsfw_level(
                        refreshed
                    ):
                        task_context.mark_item(
                            item_key, "completed", "Recovered via reimport"
                        )
                        task_context.increment_counter("reimport_recovered", 1)
                        reimport_recovered_count += 1
                        processed_count += 1
                        task_context.advance()
                        results.append(
                            {
                                "image_db_id": refreshed.id,
                                "file_hash": refreshed.file_hash,
                                "source_url": source_url,
                                "status": "reimport_recovered",
                            }
                        )
                        continue

                task_context.mark_item(
                    item_key, "skipped", "Remote nsfwLevel unavailable"
                )
                task_context.increment_counter("remote_unavailable", 1)
                remote_unavailable_count += 1
                processed_count += 1
                task_context.advance()
                reimport_candidates.append(
                    {
                        "image_db_id": image.id,
                        "file_hash": image.file_hash,
                        "source_url": source_url,
                        "reason": "missing_nsfw_level_after_backfill",
                    }
                )
            except Exception as exc:
                task_context.mark_item(item_key, "failed", str(exc))
                task_context.increment_counter("images_failed", 1)
                failed_count += 1
                processed_count += 1
                task_context.add_error(f"Image {image.id}: {exc}")
                task_context.advance()

    summary = {
        "message": "CivitAI NSFW metadata backfill complete.",
        "requested": len(image_ids),
        "processed": processed_count,
        "metadata_backfilled": metadata_backfilled_count,
        "reimport_recovered": reimport_recovered_count,
        "already_complete": already_complete_count,
        "remote_unavailable": remote_unavailable_count,
        "images_failed": failed_count,
        "images_skipped": skipped_count,
        "reimport_if_missing": reimport_if_missing,
        "reimport_candidates": reimport_candidates,
        "results": results,
    }
    retry_metrics = _diff_civitai_payload_retry_metrics(
        retry_metrics_before,
        _snapshot_civitai_payload_retry_metrics(api),
    )
    _record_civitai_payload_retry_metrics(task_context, retry_metrics)
    summary["civitai_payload_retry_metrics"] = retry_metrics
    if task_context.cancel_requested:
        task_context.cancel(summary, "Cancelled")
    return summary


def _run_retry_failed_items_job(
    task_context: TaskContext,
    *,
    source_task: dict[str, Any],
) -> dict:
    retry_items, skipped_items = _get_retry_failed_items_from_task(source_task)
    if not retry_items:
        raise RuntimeError(
            "No retryable failed CivitAI items were found in the selected job."
        )

    api = CivitaiAPI.get_instance()
    retry_metrics_before = _snapshot_civitai_payload_retry_metrics(api)
    grouped_collection_items: dict[int, list[int]] = {}
    standalone_image_ids: list[int] = []
    for entry in retry_items:
        if entry.civitai_collection_id is None:
            standalone_image_ids.append(entry.image_id)
            continue
        grouped_collection_items.setdefault(entry.civitai_collection_id, []).append(
            entry.image_id
        )

    task_context.set_total(len(retry_items))
    task_context.set_metadata("source_task_id", source_task.get("id"))
    task_context.set_metadata("retry_requested", len(retry_items))
    if skipped_items:
        task_context.set_metadata("skipped_item_keys", skipped_items)

    all_results: list[dict] = []
    group_summaries: list[dict] = []

    if standalone_image_ids:
        task_context.set_message(
            f"Retrying {len(standalone_image_ids)} standalone failed image(s)"
        )
        results, _ = _process_civitai_image_ids(
            task_context,
            api=api,
            image_ids=standalone_image_ids,
            attach_collection_id=None,
            item_key_prefix="retry:standalone",
        )
        all_results.extend(results)
        group_summaries.append(
            {
                "type": "standalone",
                "requested": len(standalone_image_ids),
                "results": results,
            }
        )

    for civitai_collection_id, image_ids in grouped_collection_items.items():
        collection_snapshot, local_collection_id = (
            _ensure_local_civitai_collection_for_retry(
                api,
                civitai_collection_id=civitai_collection_id,
            )
        )
        task_context.set_message(
            f"Retrying {len(image_ids)} failed item(s) for collection {civitai_collection_id}"
        )
        results, _ = _process_civitai_image_ids(
            task_context,
            api=api,
            image_ids=image_ids,
            attach_collection_id=local_collection_id,
            item_key_prefix=f"collection:{civitai_collection_id}",
            collection_context={
                "collection_id": civitai_collection_id,
                "collection_name": str(collection_snapshot.get("name") or ""),
            },
        )
        all_results.extend(results)
        group_summaries.append(
            {
                "type": "collection",
                "civitai_collection_id": civitai_collection_id,
                "local_collection": collection_snapshot,
                "requested": len(image_ids),
                "results": results,
            }
        )

    summary = {
        "message": "Retry failed items complete.",
        "source_task_id": source_task.get("id"),
        "requested": len(retry_items),
        "skipped_item_keys": skipped_items,
        "images_added": sum(int(r.get("images_added", 0) or 0) for r in all_results),
        "images_skipped": sum(
            int(r.get("images_skipped", 0) or 0) for r in all_results
        ),
        "images_recovered": sum(
            int(r.get("images_recovered", 0) or 0) for r in all_results
        ),
        "images_cancelled": sum(1 for r in all_results if r.get("cancelled")),
        "json_files_created": sum(
            int(r.get("json_files_created", 0) or 0) for r in all_results
        ),
        "errors": [
            f"Image {r.get('image_id')}: {r['error']}"
            for r in all_results
            if r.get("error")
        ],
        "unavailable_items": _collect_civitai_unavailable_items(all_results),
        "warnings": _get_runtime_warnings(),
        "groups": group_summaries,
        "results": all_results,
    }
    retry_metrics = _diff_civitai_payload_retry_metrics(
        retry_metrics_before,
        _snapshot_civitai_payload_retry_metrics(api),
    )
    _record_civitai_payload_retry_metrics(task_context, retry_metrics)
    summary["civitai_payload_retry_metrics"] = retry_metrics
    if task_context.cancel_requested:
        task_context.cancel(summary, "Cancelled")
    return summary


def _run_retry_specific_image_ids_job(
    task_context: TaskContext,
    *,
    image_ids: list[int],
    source_task_id: str,
) -> dict:
    """Retry a flat list of image IDs (used by retry-missing and retry-temporary endpoints)."""
    if not image_ids:
        raise RuntimeError("No image IDs to retry.")

    api = CivitaiAPI.get_instance()
    retry_metrics_before = _snapshot_civitai_payload_retry_metrics(api)

    task_context.set_total(len(image_ids))
    task_context.set_metadata("source_task_id", source_task_id)
    task_context.set_metadata("retry_requested", len(image_ids))
    task_context.set_message(f"Retrying {len(image_ids)} image(s)")

    results, _ = _process_civitai_image_ids(
        task_context,
        api=api,
        image_ids=image_ids,
        attach_collection_id=None,
        item_key_prefix="retry-specific",
    )

    summary = {
        "message": "Retry complete.",
        "source_task_id": source_task_id,
        "requested": len(image_ids),
        "images_added": sum(int(r.get("images_added", 0) or 0) for r in results),
        "images_skipped": sum(int(r.get("images_skipped", 0) or 0) for r in results),
        "images_recovered": sum(
            int(r.get("images_recovered", 0) or 0) for r in results
        ),
        "images_cancelled": sum(1 for r in results if r.get("cancelled")),
        "json_files_created": sum(
            int(r.get("json_files_created", 0) or 0) for r in results
        ),
        "errors": [
            f"Image {r.get('image_id')}: {r['error']}"
            for r in results
            if r.get("error")
        ],
        "unavailable_items": _collect_civitai_unavailable_items(results),
        "warnings": _get_runtime_warnings(),
        "results": results,
    }
    retry_metrics = _diff_civitai_payload_retry_metrics(
        retry_metrics_before,
        _snapshot_civitai_payload_retry_metrics(api),
    )
    _record_civitai_payload_retry_metrics(task_context, retry_metrics)
    summary["civitai_payload_retry_metrics"] = retry_metrics
    if task_context.cancel_requested:
        task_context.cancel(summary, "Cancelled")
    return summary


def _sync_single_civitai_collection(
    api: CivitaiAPI,
    scraper: CivitaiPrivateScraper,
    db: Session,
    civitai_collection_id: int,
    civitai_collection_name: str,
    limit: Optional[int] = None,
) -> dict:
    local_collection = _get_or_create_collection(
        db,
        name=civitai_collection_name,
        source="civitai",
        civitai_collection_id=civitai_collection_id,
    )
    _commit_with_lock_retry(
        db, context=f"Collection sync setup commit for {civitai_collection_id}"
    )

    collection_items = scraper.fetch_collection_items(
        collection_id=civitai_collection_id,
        limit=limit,
        collection_name=civitai_collection_name,
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

    results: list[dict] = []
    desired_image_db_ids: set[int] = set()
    for image_id in image_ids:
        existing_image = _find_existing_image_by_source_url(
            db, _build_civitai_image_source_url(image_id)
        )
        if existing_image is not None:
            desired_image_db_ids.add(int(existing_image.id))

        result = _import_single_civitai_image(api, db, image_id)
        if not result.get("error"):
            image_db_id = result.get("image_db_id")
            if isinstance(image_db_id, int):
                desired_image_db_ids.add(image_db_id)
                _ensure_image_in_collection(db, image_db_id, local_collection.id)
            try:
                _commit_with_lock_retry(
                    db, context=f"Import commit for image {image_id}"
                )
            except HTTPException as e:
                result["images_added"] = 0
                result["images_skipped"] = 0
                result["images_recovered"] = 0
                result["json_files_created"] = 0
                result["image_db_id"] = None
                result["error"] = str(e.detail)
        results.append(result)

    memberships_removed = _remove_images_not_in_collection_set(
        db,
        local_collection.id,
        desired_image_db_ids,
    )
    _commit_with_lock_retry(
        db,
        context=f"Collection membership sync commit for {civitai_collection_id}",
    )

    return {
        "civitai_collection_id": civitai_collection_id,
        "civitai_collection_name": civitai_collection_name,
        "local_collection": _serialize_collection(local_collection),
        "requested": len(image_ids),
        "images_added": sum(int(r.get("images_added", 0)) for r in results),
        "images_skipped": sum(int(r.get("images_skipped", 0)) for r in results),
        "images_recovered": sum(int(r.get("images_recovered", 0)) for r in results),
        "json_files_created": sum(int(r.get("json_files_created", 0)) for r in results),
        "memberships_removed": memberships_removed,
        "errors": [
            f"Image {r.get('image_id')}: {r['error']}"
            for r in results
            if r.get("error")
        ],
        "results": results,
    }


# create_initial_data() extracted to services/db_migrations.py

# Define the lifespan context manager
@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_uvicorn_access_logging(
        suppress_status_get_logs=_read_env_flag(
            "ATELIER_SUPPRESS_STATUS_GET_LOGS", False
        )
    )
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
                    # Non-destructive migration path for additive schema changes.
                    migrated = False
                    if (
                        str(version_result or "") == "1.3"
                        and str(CURRENT_SCHEMA_VERSION) == "1.4"
                    ):
                        print("   Attempting in-place schema upgrade 1.3 -> 1.4...")
                        try:
                            Base.metadata.create_all(bind=engine, checkfirst=True)
                            with SessionLocal() as db:
                                db.query(SchemaVersion).delete()
                                db.add(
                                    SchemaVersion(version_num=CURRENT_SCHEMA_VERSION)
                                )
                                db.commit()
                            migrated = True
                            print("✅ In-place schema upgrade complete.")
                        except Exception as migrate_exc:
                            print(f"⚠️ In-place schema upgrade failed: {migrate_exc}")

                    if (
                        str(version_result or "") == "1.4"
                        and str(CURRENT_SCHEMA_VERSION) == "1.5"
                    ):
                        print(
                            "   Attempting in-place schema upgrade 1.4 -> 1.5 (user_tags column)..."
                        )
                        try:
                            _ensure_user_tags_column()
                            # Backfill user_tags from sidecar JSON files into DB column
                            _backfill_user_tags_from_sidecars()
                            with SessionLocal() as db:
                                db.query(SchemaVersion).delete()
                                db.add(
                                    SchemaVersion(version_num=CURRENT_SCHEMA_VERSION)
                                )
                                db.commit()
                            migrated = True
                            print("✅ In-place schema upgrade complete.")
                        except Exception as migrate_exc:
                            print(f"⚠️ In-place schema upgrade failed: {migrate_exc}")

                    if (
                        str(version_result or "") == "1.5"
                        and str(CURRENT_SCHEMA_VERSION) == "1.6"
                    ):
                        print(
                            "   Attempting in-place schema upgrade 1.5 -> 1.6 "
                            "(CivitaiUser, CivitaiBaseModel, ModelObservation tables + FK columns)..."
                        )
                        try:
                            Base.metadata.create_all(bind=engine, checkfirst=True)
                            _ensure_civitai_user_columns()
                            _ensure_civitai_creator_id_column()
                            _ensure_base_model_id_column()
                            _seed_civitai_base_models()
                            _backfill_civitai_base_model_ids()
                            _backfill_civitai_users()
                            with SessionLocal() as db:
                                db.query(SchemaVersion).delete()
                                db.add(
                                    SchemaVersion(version_num=CURRENT_SCHEMA_VERSION)
                                )
                                db.commit()
                            migrated = True
                            print("✅ In-place schema upgrade complete.")
                        except Exception as migrate_exc:
                            print(f"⚠️ In-place schema upgrade failed: {migrate_exc}")

                    if migrated:
                        version_result = CURRENT_SCHEMA_VERSION

                    if db_file_path and os.path.exists(db_file_path):
                        if version_result == CURRENT_SCHEMA_VERSION:
                            print("✅ Database schema is up to date.")
                        elif ALLOW_SCHEMA_RESET:
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
                    print(
                        "   Recreating database to be safe (ALLOW_SCHEMA_RESET=true)..."
                    )
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
    _ensure_collection_sync_columns()
    _ensure_collection_civitai_mappings_table()
    _ensure_user_nsfw_columns()
    _ensure_civitai_uuid_column()
    _ensure_civitai_hash_column()
    _ensure_user_tags_column()
    _ensure_user_negative_tags_column()
    _ensure_observation_authority_term_unique_index()
    _backfill_user_tags_to_observations()
    _ensure_image_variant_columns()
    _ensure_promoted_metadata_columns()
    _ensure_original_file_name_column()
    _ensure_blurhash_column()
    _ensure_civitai_image_id_column()
    _ensure_civitai_post_id_column()
    _ensure_civitai_deleted_at_column()
    _ensure_civitai_post_title_index_columns()
    _ensure_civitai_cdn_url_column()
    _ensure_civitai_user_columns()
    _ensure_civitai_creator_id_column()
    _ensure_base_model_id_column()
    _seed_civitai_base_models()
    _backfill_civitai_base_model_ids()
    _backfill_civitai_users()
    _ensure_observation_unique_constraint()
    _ensure_file_hash_nonunique()
    _ensure_is_corrupt_column()
    _ensure_expected_file_size_column()
    _ensure_concept_prototype_columns()

    # --- CLIP provider auto-detection ---
    from services.clip_provider import (
        LocalCLIPProvider,
        RemoteCLIPProvider,
        set_clip_provider,
        close_http_client,
    )
    from config import (
        CLIP_LOCAL_ENABLED,
        CLIP_FORCE_CPU,
        CLIP_PEER_URL,
        CLIP_MODEL_NAME,
        CLIP_PRETRAINED,
    )

    clip_provider = None
    if CLIP_LOCAL_ENABLED:
        try:
            clip_provider = LocalCLIPProvider(
                model_name=CLIP_MODEL_NAME,
                pretrained=CLIP_PRETRAINED,
                force_cpu=CLIP_FORCE_CPU,
            )
            print(f"[CLIP] Local provider ready (model={CLIP_MODEL_NAME})")
        except Exception as exc:
            print(f"[CLIP] Local provider failed: {exc}")

    if clip_provider is None and CLIP_PEER_URL:
        clip_provider = RemoteCLIPProvider(peer_url=CLIP_PEER_URL)
        print(f"[CLIP] Remote provider ready (peer={CLIP_PEER_URL})")

    if clip_provider is None:
        print("[CLIP] No provider available — CLIP features disabled")

    set_clip_provider(clip_provider)

    print("AtelierAI API is ready to go!")

    yield

    print("Shutting down AtelierAI API...")

    # Cleanup CLIP provider
    from services.clip_provider import get_clip_provider, close_http_client
    provider = get_clip_provider()
    if provider is not None and hasattr(provider, "close"):
        try:
            await provider.close()
        except Exception:
            pass
    try:
        await close_http_client()
    except Exception:
        pass

    task_manager.shutdown()


# Pass the lifespan manager to the FastAPI app
app = FastAPI(
    title="AtelierAI API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)

# Compress larger JSON responses (e.g., taxonomy tree state payloads).
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)

# Allow local cross-origin fetches so dragging gallery images into external local
# tools (e.g. ComfyUI running on a different localhost port) can resolve URLs.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "HEAD", "OPTIONS"],
    allow_headers=["*"],
)

# Mount the static files directory
# This will serve files from the 'frontend' directory under the '/frontend/' URL path
app.mount("/frontend", StaticFiles(directory="frontend"), name="frontend")
# Expose processed images so the gallery can render thumbnails/full previews.
app.mount(
    "/image_library", StaticFiles(directory=IMAGE_LIBRARY_PATH), name="image_library"
)
app.mount(
    "/image_resources",
    StaticFiles(directory=IMAGE_RESOURCES_PATH),
    name="image_resources",
)
app.mount("/frontend/images", StaticFiles(directory="images"), name="frontend_images")


# ---------------------------------------------------------------------------
# Routers — registered before the legacy @app.* routes so that extracted
# routes take priority.  Legacy @app.* duplicates remain as dead-code fallbacks
# until Phase 4.5 removes them.
# ---------------------------------------------------------------------------
from routers import health as _health_router_mod  # noqa: E402, PLC0415
from routers import taxonomy as _taxonomy_router_mod  # noqa: E402, PLC0415
from routers import images as _images_router_mod  # noqa: E402, PLC0415
from routers import collections as _collections_router_mod  # noqa: E402, PLC0415
from routers import generation as _generation_router_mod  # noqa: E402, PLC0415
from routers import concept_review as _concept_review_router_mod  # noqa: E402, PLC0415
from routers.civitai import router as _civitai_router  # noqa: E402, PLC0415
from routers import models_tree as _models_tree_router_mod  # noqa: E402, PLC0415
from routers import clip_router as _clip_router_mod  # noqa: E402, PLC0415

app.include_router(_health_router_mod.router)
app.include_router(_taxonomy_router_mod.router, prefix="/api")
app.include_router(_images_router_mod.router, prefix="/api")
app.include_router(_collections_router_mod.router, prefix="/api")
app.include_router(_generation_router_mod.router, prefix="/api")
app.include_router(_concept_review_router_mod.router, prefix="/api")
app.include_router(_civitai_router, prefix="/api")
app.include_router(_models_tree_router_mod.router, prefix="/api")
app.include_router(_clip_router_mod.router, prefix="/api")


# Define a root endpoint to serve the main index.html file
async def read_index():
    return FileResponse("frontend/index.html")


# ---------------------------------------------------------------------------
# Frontend configuration endpoint — exposes safe, read-only settings to the UI.
# ---------------------------------------------------------------------------
async def get_frontend_config():
    """Return CivitAI domain configuration for frontend URL construction."""
    return {
        "civitai_web_base_url": getattr(
            app_config, "CIVITAI_WEB_BASE_URL", "https://civitai.red"
        ),
        "civitai_base_domain": getattr(
            app_config, "CIVITAI_BASE_DOMAIN", "civitai.red"
        ),
        "civitai_cdn_base_url": getattr(
            app_config,
            "CIVITAI_CDN_BASE_URL",
            "https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA",
        ),
        "civitai_cdn_alt_base_url": getattr(
            app_config, "CIVITAI_CDN_ALT_BASE_URL", "https://image-b2.civitai.com"
        ),
    }


async def read_tree_prototype():
    return FileResponse("frontend/tree.html")


async def read_generation_lab():
    return FileResponse("frontend/generation-lab.html")


def read_healthz(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database healthcheck failed: {exc}",
        )

    return {
        "status": "ok",
        "database": "ok",
        "app": "atelierai",
    }


async def read_model_lab():
    return FileResponse("frontend/model-lab.html")


async def read_folder_lab():
    return FileResponse("frontend/folder-lab.html")


async def read_perceptual_lab():
    return FileResponse("frontend/perceptual-lab.html")


async def read_expression_lab():
    return FileResponse("frontend/expression-lab.html")


async def read_comfyui_lab():
    return FileResponse("frontend/comfyui-lab.html")


# ── ComfyUI proxy ─────────────────────────────────────────────
# Forwards requests to a ComfyUI instance to avoid CORS issues
# from the browser.  The ComfyUI URL is passed via ?target= query param.

_PROXY_TIMEOUT = 30  # seconds


def _comfyui_target(target: str) -> str:
    """Validate and return the ComfyUI base URL from the target param."""
    if not target:
        raise HTTPException(status_code=400, detail="Missing ?target= ComfyUI URL")
    parsed = _urlparse.urlparse(target)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="target must be http(s)://…")
    # Strip trailing slash
    return target.rstrip("/")


async def comfyui_proxy_upload_image(
    request: Request,
    target: str = Query(..., description="ComfyUI base URL, e.g. http://127.0.0.1:8188"),
):
    """Proxy an image upload to ComfyUI's /upload/image endpoint.

    Forwards the raw multipart body as-is so the client can send whatever
    fields ComfyUI expects (image, overwrite, subfolder, type, etc.).
    """
    base = _comfyui_target(target)
    url = f"{base}/upload/image"

    body = await request.body()
    content_type = request.headers.get("content-type", "")

    try:
        resp = requests.post(
            url,
            data=body,
            headers={"Content-Type": content_type},
            timeout=_PROXY_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"ComfyUI unreachable: {exc}")

    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text[:500])

    return JSONResponse(content=resp.json(), status_code=resp.status_code)


async def comfyui_proxy_prompt(
    target: str = Query(..., description="ComfyUI base URL"),
    prompt: str = Body(..., media_type="application/json"),
):
    """Proxy a /prompt call to ComfyUI."""
    base = _comfyui_target(target)
    url = f"{base}/prompt"

    try:
        resp = requests.post(
            url,
            data=prompt,
            headers={"Content-Type": "application/json"},
            timeout=_PROXY_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"ComfyUI unreachable: {exc}")

    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text[:500])

    return JSONResponse(content=resp.json(), status_code=resp.status_code)


async def list_expression_sets():
    """Return the list of expression image files in /images/expressions."""
    from pathlib import Path

    expr_dir = Path("images") / "expressions"
    if not expr_dir.is_dir():
        return {"files": []}
    files = sorted(
        f.name
        for f in expr_dir.iterdir()
        if f.is_file() and f.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    )
    return {"files": files}


def list_background_tasks(limit: int = 20):
    capped_limit = max(1, min(int(limit), 50))
    return task_manager.list_tasks(limit=capped_limit)


def get_background_task(task_id: str):
    try:
        return task_manager.get_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found")


def cancel_background_task(task_id: str):
    try:
        return task_manager.cancel_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


def get_civitai_generation_prototype(image_id: int):
    return _build_generation_prototype_civitai_payload(image_id)


def get_local_generation_prototype(file_hash: str, db: Session = Depends(get_db)):
    image = (
        db.query(ImageModel)
        .filter(ImageModel.file_hash == file_hash)
        .filter(_active_image_filter())
        .first()
    )
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found.")
    return _build_generation_prototype_local_payload(image)


def get_civitai_generation_comfy_workspace(
    image_id: int,
    catalog_url: Optional[str] = Query(default=None),
    checkpoints_url: Optional[str] = Query(default=None),
    loras_url: Optional[str] = Query(default=None),
    include_full_catalog_raw: bool = Query(default=False),
):
    local_catalog = model_reference_service.fetch_local_catalog(
        catalog_url=catalog_url,
        checkpoints_url=checkpoints_url,
        loras_url=loras_url,
        include_full_raw=include_full_catalog_raw,
    )
    generation_payload = _build_generation_prototype_civitai_payload(image_id)
    return _build_generation_comfy_workspace_export_payload(
        generation_payload,
        local_catalog=local_catalog,
    )


def _extract_required_comfy_raw_payload(
    export_payload: dict, *, payload_kind: str
) -> dict:
    workspace_bundle = _dict_payload(export_payload.get("workspace_bundle"))
    validation = _dict_payload(export_payload.get("validation"))
    warnings = [
        str(item)
        for item in _list_payload(validation.get("warnings"))
        if str(item).strip()
    ]
    errors = [
        str(item)
        for item in _list_payload(validation.get("errors"))
        if str(item).strip()
    ]

    if payload_kind == "workflow":
        key = "comfy_workflow_ui"
        label = "Comfy workspace workflow"
    elif payload_kind == "prompt":
        key = "comfy_prompt_api"
        label = "Comfy API prompt"
    else:
        raise HTTPException(
            status_code=500, detail=f"Unsupported raw payload kind: {payload_kind}"
        )

    raw_payload = workspace_bundle.get(key)
    if isinstance(raw_payload, dict):
        return raw_payload

    details: list[str] = [f"{label} JSON is not available for this image."]
    details.extend(errors)
    if warnings:
        details.append("Warnings: " + " | ".join(warnings))
    raise HTTPException(status_code=422, detail=" ".join(details).strip())


def _inject_fresh_local_exif_metadata_for_comfy(
    generation_payload: dict,
    image: ImageModel,
    db: Session,
) -> tuple[dict, list[str]]:
    warnings: list[str] = []
    try:
        image_path = _resolve_image_library_path(image)
    except Exception as exc:
        return generation_payload, [
            f"Could not resolve image path for fresh EXIF refresh: {exc}"
        ]

    if not image_path.exists() or not image_path.is_file():
        return generation_payload, [
            "Local image file is unavailable for fresh EXIF refresh."
        ]

    try:
        processor = ImageProcessor(str(image_path), db, IMAGE_LIBRARY_PATH)
    except Exception as exc:
        return generation_payload, [f"Fresh EXIF refresh failed: {exc}"]

    fresh_exif = _dict_payload(processor.exif_data)
    if not fresh_exif:
        return generation_payload, [
            "Fresh EXIF refresh did not yield any metadata fields."
        ]

    raw = _dict_payload(generation_payload.get("raw"))
    merged = _dict_payload(raw.get("merged"))
    sidecar = _dict_payload(raw.get("sidecar"))
    db_payload = _dict_payload(raw.get("db"))

    raw["exif_data_fresh"] = fresh_exif
    merged["exif_data_fresh"] = fresh_exif

    if not isinstance(sidecar.get("exif_data"), dict) or not sidecar.get("exif_data"):
        sidecar["exif_data"] = fresh_exif
    if not isinstance(db_payload.get("exif_data"), dict) or not db_payload.get(
        "exif_data"
    ):
        db_payload["exif_data"] = fresh_exif

    raw["merged"] = merged
    raw["sidecar"] = sidecar
    raw["db"] = db_payload
    generation_payload["raw"] = raw
    warnings.append(
        "Included fresh EXIF metadata from the local media file for Comfy graph extraction."
    )
    return generation_payload, warnings


def get_civitai_generation_comfy_workflow_raw(
    image_id: int,
    catalog_url: Optional[str] = Query(default=None),
    checkpoints_url: Optional[str] = Query(default=None),
    loras_url: Optional[str] = Query(default=None),
    include_full_catalog_raw: bool = Query(default=False),
):
    local_catalog = model_reference_service.fetch_local_catalog(
        catalog_url=catalog_url,
        checkpoints_url=checkpoints_url,
        loras_url=loras_url,
        include_full_raw=include_full_catalog_raw,
    )
    generation_payload = _build_generation_prototype_civitai_payload(image_id)
    export_payload = _build_generation_comfy_workspace_export_payload(
        generation_payload,
        local_catalog=local_catalog,
    )
    return _extract_required_comfy_raw_payload(export_payload, payload_kind="workflow")


def get_civitai_generation_comfy_prompt_raw(
    image_id: int,
    catalog_url: Optional[str] = Query(default=None),
    checkpoints_url: Optional[str] = Query(default=None),
    loras_url: Optional[str] = Query(default=None),
    include_full_catalog_raw: bool = Query(default=False),
):
    local_catalog = model_reference_service.fetch_local_catalog(
        catalog_url=catalog_url,
        checkpoints_url=checkpoints_url,
        loras_url=loras_url,
        include_full_raw=include_full_catalog_raw,
    )
    generation_payload = _build_generation_prototype_civitai_payload(image_id)
    export_payload = _build_generation_comfy_workspace_export_payload(
        generation_payload,
        local_catalog=local_catalog,
    )
    return _extract_required_comfy_raw_payload(export_payload, payload_kind="prompt")


def get_local_generation_comfy_workspace(
    file_hash: str,
    catalog_url: Optional[str] = Query(default=None),
    checkpoints_url: Optional[str] = Query(default=None),
    loras_url: Optional[str] = Query(default=None),
    include_full_catalog_raw: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    image = (
        db.query(ImageModel)
        .filter(ImageModel.file_hash == file_hash)
        .filter(_active_image_filter())
        .first()
    )
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found.")
    local_catalog = model_reference_service.fetch_local_catalog(
        catalog_url=catalog_url,
        checkpoints_url=checkpoints_url,
        loras_url=loras_url,
        include_full_raw=include_full_catalog_raw,
    )
    generation_payload = _build_generation_prototype_local_payload(image)
    generation_payload, refresh_warnings = _inject_fresh_local_exif_metadata_for_comfy(
        generation_payload, image, db
    )
    export_payload = _build_generation_comfy_workspace_export_payload(
        generation_payload,
        local_catalog=local_catalog,
    )
    if refresh_warnings:
        validation = _dict_payload(export_payload.get("validation"))
        warnings = [
            str(item)
            for item in _list_payload(validation.get("warnings"))
            if str(item).strip()
        ]
        warnings.extend(refresh_warnings)
        validation["warnings"] = warnings
        validation.setdefault("errors", _list_payload(validation.get("errors")))
        validation["status"] = _summarize_validation(
            warnings, _list_payload(validation.get("errors"))
        ).get("status")
        export_payload["validation"] = validation
    return export_payload


def get_local_generation_comfy_workflow_raw(
    file_hash: str,
    catalog_url: Optional[str] = Query(default=None),
    checkpoints_url: Optional[str] = Query(default=None),
    loras_url: Optional[str] = Query(default=None),
    include_full_catalog_raw: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    image = (
        db.query(ImageModel)
        .filter(ImageModel.file_hash == file_hash)
        .filter(_active_image_filter())
        .first()
    )
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found.")

    local_catalog = model_reference_service.fetch_local_catalog(
        catalog_url=catalog_url,
        checkpoints_url=checkpoints_url,
        loras_url=loras_url,
        include_full_raw=include_full_catalog_raw,
    )
    generation_payload = _build_generation_prototype_local_payload(image)
    generation_payload, _ = _inject_fresh_local_exif_metadata_for_comfy(
        generation_payload, image, db
    )
    export_payload = _build_generation_comfy_workspace_export_payload(
        generation_payload,
        local_catalog=local_catalog,
    )
    return _extract_required_comfy_raw_payload(export_payload, payload_kind="workflow")


def get_local_generation_comfy_prompt_raw(
    file_hash: str,
    catalog_url: Optional[str] = Query(default=None),
    checkpoints_url: Optional[str] = Query(default=None),
    loras_url: Optional[str] = Query(default=None),
    include_full_catalog_raw: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    image = (
        db.query(ImageModel)
        .filter(ImageModel.file_hash == file_hash)
        .filter(_active_image_filter())
        .first()
    )
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found.")

    local_catalog = model_reference_service.fetch_local_catalog(
        catalog_url=catalog_url,
        checkpoints_url=checkpoints_url,
        loras_url=loras_url,
        include_full_raw=include_full_catalog_raw,
    )
    generation_payload = _build_generation_prototype_local_payload(image)
    generation_payload, _ = _inject_fresh_local_exif_metadata_for_comfy(
        generation_payload, image, db
    )
    export_payload = _build_generation_comfy_workspace_export_payload(
        generation_payload,
        local_catalog=local_catalog,
    )
    return _extract_required_comfy_raw_payload(export_payload, payload_kind="prompt")


def _coerce_a1111_parameter_value(key: str, value: Any) -> Any:
    return _a1111_svc._coerce_a1111_parameter_value(key, value)


def _extract_a1111_user_comment_candidates(
    generation_payload: dict,
) -> list[dict[str, str]]:
    return _a1111_svc.extract_a1111_user_comment_candidates(generation_payload)


def _a1111_candidate_source_priority(source: Any) -> int:
    return _a1111_svc._a1111_candidate_source_priority(source)


def _select_preferred_a1111_user_comment_candidate(
    candidates: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    return _a1111_svc.select_preferred_a1111_user_comment_candidate(candidates)


def _build_authoritative_a1111_parse_payload(
    candidates: list[dict[str, Any]],
) -> tuple[dict[str, Any], Optional[dict[str, Any]]]:
    return _a1111_svc.build_authoritative_a1111_parse_payload(candidates)


def _build_a1111_capability_signals(parse_payload: dict[str, Any]) -> dict[str, Any]:
    return _a1111_svc.build_a1111_capability_signals(parse_payload)


def _sanitize_a1111_positive_prompt_for_comfy(
    prompt_text: Any,
) -> tuple[str, list[str]]:
    return _a1111_svc.sanitize_a1111_positive_prompt_for_comfy(prompt_text)


def _looks_like_a1111_user_comment(text: str) -> bool:
    return _a1111_svc.looks_like_a1111_user_comment(text)


def _parse_a1111_user_comment(text: str) -> dict[str, Any]:
    return _a1111_svc.parse_a1111_user_comment(text)


def _normalize_scalar_for_lookup(value: Any) -> Optional[str]:
    return _a1111_svc._normalize_scalar_for_lookup(value)


def _flatten_json_scalars(
    value: Any,
    *,
    prefix: str = "",
    output: Optional[dict[str, Any]] = None,
    depth: int = 0,
    max_depth: int = 20,
) -> dict[str, Any]:
    return _a1111_svc._flatten_json_scalars(
        value, prefix=prefix, output=output, depth=depth, max_depth=max_depth
    )


def _build_scalar_lookup(flattened: dict[str, Any]) -> dict[str, list[str]]:
    return _a1111_svc._build_scalar_lookup(flattened)


def _compare_json_scalar_structures(
    left: Any, right: Any, *, sample_limit: int = 25
) -> dict[str, Any]:
    return _a1111_svc._compare_json_scalar_structures(
        left, right, sample_limit=sample_limit
    )


def _build_a1111_field_alignment(
    parsed_fields: dict[str, Any], workflow_payload: Any, *, sample_limit: int = 10
) -> dict[str, Any]:
    return _a1111_svc.build_a1111_field_alignment(
        parsed_fields, workflow_payload, sample_limit=sample_limit
    )


def _normalize_sampler_name_for_comfy(value: Any) -> dict[str, Any]:
    return _a1111_svc._normalize_sampler_name_for_comfy(value)


def _normalize_scheduler_name_for_comfy(value: Any) -> Optional[str]:
    return _a1111_svc._normalize_scheduler_name_for_comfy(value)


def _normalize_model_name_key(value: Any) -> Optional[str]:
    return _a1111_svc._normalize_model_name_key(value)


def _extract_hex_hash_tokens(value: Any) -> set[str]:
    return _a1111_svc._extract_hex_hash_tokens(value)


def _hash_token_sets_match(left_tokens: set[str], right_tokens: set[str]) -> bool:
    return _a1111_svc._hash_token_sets_match(left_tokens, right_tokens)


def _normalize_prompt_text_for_match(
    value: Any, *, strip_lora_tags: bool = False
) -> str:
    return _a1111_svc._normalize_prompt_text_for_match(
        value, strip_lora_tags=strip_lora_tags
    )


def _find_first_text_diff(left: str, right: str) -> Optional[dict[str, Any]]:
    return _a1111_svc._find_first_text_diff(left, right)


def _build_prompt_mismatch_diagnostics(
    local_value: Any, parameter_scalars: list[tuple[str, Any]]
) -> dict[str, Any]:
    return _a1111_svc.build_prompt_mismatch_diagnostics(local_value, parameter_scalars)


def _is_parameter_like_workflow_path(path: str) -> bool:
    return _a1111_svc._is_parameter_like_workflow_path(path)


def _field_value_matches_expected(
    field_name: str, local_value: Any, expected_value: Any
) -> bool:
    return _a1111_svc._field_value_matches_expected(
        field_name, local_value, expected_value
    )


def _extract_expected_workflow_parameters(
    workflow_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    return _a1111_svc._extract_expected_workflow_parameters(workflow_payload)


def _build_semantic_workflow_match_buckets(
    canonical_fields: dict[str, Any],
    workflow_payload: dict[str, Any],
    *,
    model_hash_evidence: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return _a1111_svc.build_semantic_workflow_match_buckets(
        canonical_fields, workflow_payload, model_hash_evidence=model_hash_evidence
    )


def _build_parity_candidate_audit(
    *,
    file_hash: str,
    parse_payload: dict[str, Any],
    provided_workflow_json: dict[str, Any],
    provided_workflow_supplied: bool,
    model_hash_evidence: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    parsed_fields = _dict_payload(parse_payload.get("parsed_fields"))
    positive_prompt = str(parse_payload.get("positive_prompt") or "").strip()
    negative_prompt = str(parse_payload.get("negative_prompt") or "").strip()
    lora_tags = _list_payload(parse_payload.get("lora_tags"))

    sampler_info = _normalize_sampler_name_for_comfy(parsed_fields.get("sampler"))
    canonical_fields = {
        "prompt_positive": positive_prompt or None,
        "prompt_negative": negative_prompt or None,
        "sampler_name": sampler_info.get("normalized"),
        "scheduler_name": parsed_fields.get("scheduler"),
        "seed": parsed_fields.get("seed"),
        "steps": parsed_fields.get("steps"),
        "cfg_scale": parsed_fields.get("cfg_scale"),
        "width": parsed_fields.get("width"),
        "height": parsed_fields.get("height"),
        "denoise": parsed_fields.get("denoising_strength"),
        "clip_skip": parsed_fields.get("clip_skip"),
        "model": parsed_fields.get("model"),
        "model_hash": parsed_fields.get("model_hash"),
        "lora_tags": lora_tags,
    }

    required_field_names = [
        "prompt_positive",
        "sampler_name",
        "seed",
        "steps",
        "cfg_scale",
        "width",
        "height",
        "model",
    ]
    missing_fields = [
        field_name
        for field_name in required_field_names
        if _is_missing_process_value(canonical_fields.get(field_name))
    ]

    warnings = [
        str(item)
        for item in _list_payload(parse_payload.get("warnings"))
        if str(item).strip()
    ]
    capability_signals = _build_a1111_capability_signals(parse_payload)
    mapping_notes = _list_payload(sampler_info.get("notes"))
    conflicts: list[dict[str, Any]] = []

    field_alignment = None
    structural = None
    semantic_workflow_match = None
    if provided_workflow_supplied and provided_workflow_json:
        field_alignment = _build_a1111_field_alignment(
            canonical_fields, provided_workflow_json
        )
        structural = _compare_json_scalar_structures(
            parsed_fields, provided_workflow_json
        )
        semantic_workflow_match = _build_semantic_workflow_match_buckets(
            canonical_fields,
            provided_workflow_json,
            model_hash_evidence=model_hash_evidence,
        )
        alignment_score = float(field_alignment.get("alignment_score") or 0.0)
        if alignment_score < 0.35:
            conflicts.append(
                {
                    "type": "low_alignment",
                    "message": "Parsed A1111 fields align weakly with provided workflow values.",
                    "alignment_score": alignment_score,
                }
            )

    classification = "generatable_now"
    if not positive_prompt and not parsed_fields:
        classification = "non_generatable_missing_generation_data"
    elif len(missing_fields) >= 4:
        classification = "needs_manual_intervention"
    elif len(missing_fields) > 0:
        classification = "generatable_with_inference"

    if _list_payload(capability_signals.get("unsupported_features")):
        classification = "needs_manual_intervention"
        conflicts.append(
            {
                "type": "unsupported_a1111_processing",
                "message": "Detected A1111 processing that is not currently emulated in Comfy parity workflows.",
                "unsupported_features": _list_payload(
                    capability_signals.get("unsupported_features")
                ),
            }
        )

    # Build readiness_score and action_items
    readiness_score = 0
    action_items: list[str] = []

    if required_field_names:
        present_count = len(required_field_names) - len(missing_fields)
        readiness_score = int((present_count / len(required_field_names)) * 100)

    # Readiness modifiers
    if isinstance(model_hash_evidence, dict) and model_hash_evidence.get(
        "confirmed_exact_match"
    ):
        tier = str(model_hash_evidence.get("confirmation_tier") or "")
        if tier == "same_source":
            readiness_score = min(100, readiness_score + 10)
        elif tier == "cross_source":
            readiness_score = min(100, readiness_score + 5)

    if _list_payload(capability_signals.get("unsupported_features")):
        readiness_score = max(0, readiness_score - 30)

    if provided_workflow_supplied and isinstance(semantic_workflow_match, dict):
        counts = _dict_payload(semantic_workflow_match.get("counts"))
        total_semantic = sum(
            int(counts.get(k) or 0)
            for k in ("matched", "mismatched", "local_only", "workflow_only")
        )
        if total_semantic > 0:
            match_ratio = int(counts.get("matched") or 0) / total_semantic
            if match_ratio > 0.80:
                readiness_score = min(100, readiness_score + 5)
            elif match_ratio < 0.35:
                readiness_score = max(0, readiness_score - 10)

    if conflicts:
        readiness_score = max(0, readiness_score - 5 * len(conflicts))

    # Action items
    if missing_fields:
        action_items.append(
            f"Set {', '.join(missing_fields)} — missing from extracted metadata"
        )
    if classification == "needs_manual_intervention":
        action_items.append("Resolve blocking issues before attempting generation")
    if isinstance(model_hash_evidence, dict) and not model_hash_evidence.get(
        "confirmed_exact_match"
    ):
        action_items.append(
            "Verify model — local checkpoint could not be confirmed by hash"
        )
    if _list_payload(capability_signals.get("unsupported_features")):
        unsupported_names = [
            str(f)
            for f in _list_payload(capability_signals.get("unsupported_features"))
        ]
        action_items.append(
            f"Unsupported A1111 features detected: {', '.join(unsupported_names)}"
        )
    if not provided_workflow_supplied:
        action_items.append(
            "Provide a workflow JSON to enable field alignment checking"
        )

    # Build unified_field_status
    unified_field_status: dict[str, dict[str, Any]] = {}
    for field_name, local_value in canonical_fields.items():
        if field_name == "lora_tags":
            continue
        unified_field_status[field_name] = {
            "status": "not_checked",
            "local_value": local_value,
            "workflow_value": None,
            "confidence": None,
            "detail": None,
        }

    if provided_workflow_supplied and isinstance(semantic_workflow_match, dict):
        for entry in _list_payload(semantic_workflow_match.get("matched")):
            fname = str(entry.get("field") or "")
            if fname in unified_field_status:
                unified_field_status[fname]["status"] = "matched"
                unified_field_status[fname]["workflow_value"] = entry.get("local_value")
                unified_field_status[fname]["detail"] = entry.get("match_reason")
        for entry in _list_payload(semantic_workflow_match.get("mismatched")):
            fname = str(entry.get("field") or "")
            if fname in unified_field_status:
                unified_field_status[fname]["status"] = "mismatched"
                unified_field_status[fname]["workflow_value"] = entry.get(
                    "expected_value"
                )
                unified_field_status[fname]["detail"] = entry.get("reason")
        for entry in _list_payload(semantic_workflow_match.get("local_only")):
            fname = str(entry.get("field") or "")
            if fname in unified_field_status:
                unified_field_status[fname]["status"] = "local_only"
                unified_field_status[fname]["detail"] = entry.get("match_reason")
        for entry in _list_payload(semantic_workflow_match.get("workflow_only")):
            fname = str(entry.get("field") or "")
            if fname in unified_field_status:
                unified_field_status[fname]["status"] = "workflow_only"
                unified_field_status[fname]["workflow_value"] = entry.get(
                    "expected_value"
                )
                unified_field_status[fname]["detail"] = entry.get("reason")

    # Override model_hash with verification tier
    if isinstance(model_hash_evidence, dict) and "model_hash" in unified_field_status:
        mh_status = unified_field_status["model_hash"]
        if model_hash_evidence.get("confirmed_exact_match"):
            tier = str(model_hash_evidence.get("confirmation_tier") or "")
            mh_status["status"] = "verified"
            if tier == "same_source":
                mh_status["confidence"] = "verified"
                sources = _list_payload(model_hash_evidence.get("sources"))
                src_labels = [
                    str(s.get("source", "")) for s in sources if isinstance(s, dict)
                ]
                mh_status["detail"] = (
                    f"Hash confirmed via {', '.join(src_labels[:3]) or 'external sources'}"
                )
            elif tier == "cross_source":
                mh_status["confidence"] = "probable"
                cross_detail = model_hash_evidence.get("cross_source_detail")
                detail_str = ", ".join(
                    str(d)
                    for d in (_list_payload(cross_detail) if cross_detail else [])
                )
                mh_status["detail"] = (
                    f"Cross-source verification: {detail_str}"
                    if detail_str
                    else "Cross-source hash match"
                )
        else:
            mh_status["status"] = "local_only"
            mh_status["detail"] = "No external hash confirmation found"

    return {
        "ok": True,
        "mode": "generation_audit",
        "target": {
            "file_hash": file_hash,
        },
        "candidate": {
            "parsed_fields": parsed_fields,
            "canonical_fields": canonical_fields,
            "missing_required_fields": missing_fields,
            "mapping_notes": mapping_notes,
            "warnings": warnings,
            "conflicts": conflicts,
            "capability_signals": capability_signals,
            "classification": classification,
            "readiness_score": readiness_score,
            "action_items": action_items,
        },
        "comparison": {
            "provided_workflow_supplied": provided_workflow_supplied,
            "field_alignment": field_alignment,
            "structural": structural,
            "workflow_match_buckets_semantic": semantic_workflow_match,
            "model_hash_evidence": model_hash_evidence,
            "unified_field_status": unified_field_status,
        },
    }


def _build_model_hash_evidence_for_parity(
    *,
    image: ImageModel,
    canonical_model: Any,
    canonical_model_hash: Any,
    include_non_prefix_local_reference_matches: bool = False,
) -> dict[str, Any]:
    local_hash_tokens = _extract_hex_hash_tokens(canonical_model_hash)
    canonical_model_key = _normalize_model_name_key(canonical_model)
    source_evidence: list[dict[str, Any]] = []

    def _model_keys_compatible(left: Any, right: Any) -> bool:
        left_key = _normalize_model_name_key(left)
        right_key = _normalize_model_name_key(right)
        if not left_key or not right_key:
            return False
        return left_key == right_key or left_key in right_key or right_key in left_key

    civitai_image_id: Optional[int] = None
    source_url = str(image.source_url or "").strip()
    if source_url:
        try:
            url_type, parsed_id = _detect_civitai_url_type(source_url)
            if url_type == "image":
                civitai_image_id = int(parsed_id)
        except Exception:
            civitai_image_id = None

    if civitai_image_id is not None:
        try:
            civitai_payload = _build_generation_prototype_civitai_payload(
                civitai_image_id
            )

            raw_payload = _dict_payload(civitai_payload.get("raw"))
            generation_data = _dict_payload(raw_payload.get("generation_data"))
            meta_payload = _dict_payload(generation_data.get("meta"))

            civitai_hash_tokens: set[str] = set()
            civitai_model_name_candidates: set[str] = set()

            civitai_hash_tokens.update(
                _extract_hex_hash_tokens(meta_payload.get("Model hash"))
            )
            civitai_hash_tokens.update(
                _extract_hex_hash_tokens(
                    _dict_payload(meta_payload.get("hashes")).get("model")
                )
            )

            meta_model_name = str(meta_payload.get("Model") or "").strip()
            if meta_model_name:
                civitai_model_name_candidates.add(meta_model_name)

            civitai_model_id_candidates: set[int] = set()
            civitai_model_version_id_candidates: set[int] = set()

            for resource in _list_payload(meta_payload.get("resources")):
                if not isinstance(resource, dict):
                    continue
                resource_type = str(resource.get("type") or "").strip().lower()
                if resource_type and resource_type not in {"model", "checkpoint"}:
                    continue
                civitai_hash_tokens.update(
                    _extract_hex_hash_tokens(resource.get("hash"))
                )
                resource_name = str(resource.get("name") or "").strip()
                if resource_name:
                    civitai_model_name_candidates.add(resource_name)
                rid = _coerce_optional_int(resource.get("modelId"))
                if rid is not None:
                    civitai_model_id_candidates.add(rid)
                vid = _coerce_optional_int(resource.get("modelVersionId"))
                if vid is not None:
                    civitai_model_version_id_candidates.add(vid)

            # Extract IDs from URN-style Model field (e.g. "urn:air:sdxl:checkpoint:civitai:1342490@2568276")
            air_ids = _parse_civitai_air_identifier(meta_payload.get("Model"))
            if air_ids:
                if air_ids.get("civitai_model_id") is not None:
                    civitai_model_id_candidates.add(air_ids["civitai_model_id"])
                if air_ids.get("civitai_model_version_id") is not None:
                    civitai_model_version_id_candidates.add(
                        air_ids["civitai_model_version_id"]
                    )

            # Extract IDs from civitaiResources entries (modelVersionId only)
            for civ_res in _list_payload(meta_payload.get("civitaiResources")):
                if not isinstance(civ_res, dict):
                    continue
                res_type = str(civ_res.get("type") or "").strip().lower()
                if res_type and res_type not in {"model", "checkpoint", ""}:
                    continue
                cr_vid = _coerce_optional_int(civ_res.get("modelVersionId"))
                if cr_vid is not None:
                    civitai_model_version_id_candidates.add(cr_vid)

            # Fallback: parse user-comment style metadata if direct meta extraction is sparse.
            if not civitai_hash_tokens:
                civitai_candidates = _extract_a1111_user_comment_candidates(
                    civitai_payload
                )
                civitai_preferred = next(
                    (
                        candidate
                        for candidate in civitai_candidates
                        if _looks_like_a1111_user_comment(
                            str(candidate.get("text") or "")
                        )
                    ),
                    civitai_candidates[0] if civitai_candidates else None,
                )
                civitai_parse = (
                    _parse_a1111_user_comment(str(civitai_preferred.get("text") or ""))
                    if civitai_preferred
                    else {}
                )
                civitai_fields = _dict_payload(civitai_parse.get("parsed_fields"))
                civitai_hash_tokens.update(
                    _extract_hex_hash_tokens(civitai_fields.get("model_hash"))
                )
                parsed_model_name = str(civitai_fields.get("model") or "").strip()
                if parsed_model_name:
                    civitai_model_name_candidates.add(parsed_model_name)

            model_name_compatible = False
            for candidate_name in civitai_model_name_candidates:
                candidate_key = _normalize_model_name_key(candidate_name)
                if not canonical_model_key or not candidate_key:
                    continue
                if (
                    canonical_model_key == candidate_key
                    or canonical_model_key in candidate_key
                    or candidate_key in canonical_model_key
                ):
                    model_name_compatible = True
                    break

            source_evidence.append(
                {
                    "source": "civitai_metadata",
                    "model": (
                        sorted(civitai_model_name_candidates)[0]
                        if civitai_model_name_candidates
                        else None
                    ),
                    "hash_tokens": sorted(civitai_hash_tokens),
                    "model_name_candidates": sorted(civitai_model_name_candidates),
                    "model_name_compatible": model_name_compatible,
                    "hash_prefix_match": _hash_token_sets_match(
                        local_hash_tokens, civitai_hash_tokens
                    ),
                    "civitai_model_ids": (
                        sorted(civitai_model_id_candidates)
                        if civitai_model_id_candidates
                        else None
                    ),
                    "civitai_model_version_ids": (
                        sorted(civitai_model_version_id_candidates)
                        if civitai_model_version_id_candidates
                        else None
                    ),
                    "evidence_paths": [
                        "raw.generation_data.meta.Model hash",
                        "raw.generation_data.meta.hashes.model",
                        "raw.generation_data.meta.resources[*].hash",
                        "raw.generation_data.meta.resources[*].modelId",
                        "raw.generation_data.meta.resources[*].modelVersionId",
                        "raw.generation_data.meta.Model",
                        "raw.generation_data.meta.civitaiResources[*].modelVersionId",
                    ],
                }
            )
        except Exception as exc:
            source_evidence.append(
                {
                    "source": "civitai_metadata",
                    "error": str(exc),
                }
            )

    try:
        local_catalog = model_reference_service.fetch_local_catalog()
        if isinstance(local_catalog, dict) and isinstance(
            local_catalog.get("entries"), list
        ):
            local_generation_payload = _build_generation_prototype_local_payload(image)
            extracted_references = (
                model_reference_service.extract_references_from_generation_payload(
                    local_generation_payload
                )
            )
            matched_references = model_reference_service.apply_local_catalog_matches(
                extracted_references, local_catalog
            )

            for reference in matched_references:
                if not isinstance(reference, dict):
                    continue
                reference_type = (
                    str(reference.get("resource_type") or "").strip().lower()
                )
                if reference_type not in {"checkpoint", "model"}:
                    continue
                local_matches = _list_payload(reference.get("local_matches"))
                if not local_matches:
                    continue

                reference_name_candidates = [
                    reference.get("display_name"),
                    reference.get("version_name"),
                    reference.get("model_name"),
                    reference.get("source_identifier"),
                ]
                reference_model_id = reference.get("civitai_model_id")
                reference_version_id = reference.get("civitai_model_version_id")

                for match in local_matches:
                    if not isinstance(match, dict):
                        continue
                    match_hash_tokens = _extract_hex_hash_tokens(match.get("hashes"))
                    if not match_hash_tokens:
                        continue

                    hash_prefix_match = _hash_token_sets_match(
                        local_hash_tokens, match_hash_tokens
                    )
                    if (
                        not hash_prefix_match
                        and not include_non_prefix_local_reference_matches
                    ):
                        continue

                    match_name_candidates = [
                        match.get("display_name"),
                        match.get("model_name"),
                        match.get("version_name"),
                        match.get("file_name"),
                        match.get("source_identifier"),
                    ]

                    model_name_compatible = any(
                        _model_keys_compatible(canonical_model_key, candidate)
                        for candidate in [
                            *match_name_candidates,
                            *reference_name_candidates,
                        ]
                    )

                    ids_compatible = False
                    match_model_id = match.get("civitai_model_id")
                    match_version_id = match.get("civitai_model_version_id")
                    if (
                        reference_version_id is not None
                        and match_version_id is not None
                    ):
                        ids_compatible = int(reference_version_id) == int(
                            match_version_id
                        )
                    elif reference_model_id is not None and match_model_id is not None:
                        ids_compatible = int(reference_model_id) == int(match_model_id)

                    source_evidence.append(
                        {
                            "source": "local_reference_match",
                            "resource_type": reference_type,
                            "reference_display_name": reference.get("display_name"),
                            "display_name": match.get("display_name"),
                            "model_name": match.get("model_name"),
                            "version_name": match.get("version_name"),
                            "file_name": match.get("file_name"),
                            "file_path": match.get("file_path"),
                            "match_basis": match.get("match_basis"),
                            "civitai_model_id": match_model_id,
                            "civitai_model_version_id": match_version_id,
                            "hash_tokens": sorted(match_hash_tokens),
                            "model_name_compatible": bool(
                                model_name_compatible or ids_compatible
                            ),
                            "hash_prefix_match": hash_prefix_match,
                            "evidence_paths": [
                                "normalized.references[*].local_matches[*].hashes",
                            ],
                        }
                    )

            for entry in local_catalog.get("entries") or []:
                if not isinstance(entry, dict):
                    continue
                entry_model_key = _normalize_model_name_key(
                    entry.get("model_name")
                    or entry.get("version_name")
                    or entry.get("display_name")
                    or entry.get("source_identifier")
                )
                if not canonical_model_key or not entry_model_key:
                    continue
                if not (_model_keys_compatible(canonical_model_key, entry_model_key)):
                    continue
                entry_hashes = set(
                    str(item).strip().lower()
                    for item in _list_payload(entry.get("hashes"))
                )
                if not entry_hashes:
                    continue
                entry_hash_prefix_match = _hash_token_sets_match(
                    local_hash_tokens, entry_hashes
                )
                if (
                    not entry_hash_prefix_match
                    and not include_non_prefix_local_reference_matches
                ):
                    continue
                source_evidence.append(
                    {
                        "source": "local_catalog",
                        "display_name": entry.get("display_name"),
                        "model_name": entry.get("model_name"),
                        "version_name": entry.get("version_name"),
                        "civitai_model_id": entry.get("civitai_model_id"),
                        "civitai_model_version_id": entry.get(
                            "civitai_model_version_id"
                        ),
                        "hash_tokens": sorted(entry_hashes),
                        "model_name_compatible": True,
                        "hash_prefix_match": entry_hash_prefix_match,
                    }
                )
    except Exception as exc:
        source_evidence.append(
            {
                "source": "local_catalog",
                "error": str(exc),
            }
        )

    # Tier 1: Same-source confirmation — a single source has both
    # model_name_compatible and hash_prefix_match.
    confirmed_tier_1 = any(
        isinstance(item, dict)
        and item.get("model_name_compatible")
        and item.get("hash_prefix_match")
        for item in source_evidence
    )

    # Tier 2: Cross-source confirmation — one source provides hash_prefix_match
    # and another source provides matching CivitAI model/version IDs.
    confirmed_tier_2 = False
    cross_source_detail: list[str] = []
    if not confirmed_tier_1:
        sources_with_hash_match = [
            item
            for item in source_evidence
            if isinstance(item, dict) and item.get("hash_prefix_match")
        ]
        sources_with_ids: list[dict[str, Any]] = []
        for item in source_evidence:
            if not isinstance(item, dict):
                continue
            item_mids: list[Any] = list(item.get("civitai_model_ids") or [])
            item_vids: list[Any] = list(item.get("civitai_model_version_ids") or [])
            if item.get("civitai_model_id") is not None:
                item_mids.append(item["civitai_model_id"])
            if item.get("civitai_model_version_id") is not None:
                item_vids.append(item["civitai_model_version_id"])
            if item_mids or item_vids:
                sources_with_ids.append(
                    {
                        "source_label": str(item.get("source") or "unknown"),
                        "model_ids": sorted(
                            set(int(x) for x in item_mids if x is not None)
                        ),
                        "version_ids": sorted(
                            set(int(x) for x in item_vids if x is not None)
                        ),
                    }
                )

        if sources_with_hash_match and sources_with_ids:
            # Collect any IDs present on hash-match sources themselves.
            all_hash_match_ids_m: set[int] = set()
            all_hash_match_ids_v: set[int] = set()
            for hm in sources_with_hash_match:
                for mid in _list_payload(hm.get("civitai_model_ids")) or []:
                    if isinstance(mid, (int, float)):
                        all_hash_match_ids_m.add(int(mid))
                for vid in _list_payload(hm.get("civitai_model_version_ids")) or []:
                    if isinstance(vid, (int, float)):
                        all_hash_match_ids_v.add(int(vid))
                if hm.get("civitai_model_id") is not None:
                    all_hash_match_ids_m.add(int(hm["civitai_model_id"]))
                if hm.get("civitai_model_version_id") is not None:
                    all_hash_match_ids_v.add(int(hm["civitai_model_version_id"]))

            # Sub-case A: Hash-match sources carry IDs that overlap with
            # IDs from a separate source (e.g. CivitAI metadata provides
            # model_id/version_id, and local_reference_match has the same IDs
            # plus a hash_prefix_match).
            for id_source in sources_with_ids:
                if id_source["model_ids"] and all_hash_match_ids_m:
                    shared_m = set(id_source["model_ids"]) & all_hash_match_ids_m
                    if shared_m:
                        confirmed_tier_2 = True
                        cross_source_detail.append(
                            f"model_id overlap: {sorted(shared_m)} "
                            f"(hash_match sources + {id_source['source_label']})"
                        )
                if id_source["version_ids"] and all_hash_match_ids_v:
                    shared_v = set(id_source["version_ids"]) & all_hash_match_ids_v
                    if shared_v:
                        confirmed_tier_2 = True
                        cross_source_detail.append(
                            f"version_id overlap: {sorted(shared_v)} "
                            f"(hash_match sources + {id_source['source_label']})"
                        )

            # Sub-case B: Hash-match sources have no IDs but one source with
            # IDs also provides model_name_compatible, giving contextual
            # confidence that the hash match refers to the same model.
            if (
                not confirmed_tier_2
                and not all_hash_match_ids_m
                and not all_hash_match_ids_v
            ):
                for id_src in sources_with_ids:
                    id_label = id_src["source_label"]
                    for ev in source_evidence:
                        if not isinstance(ev, dict):
                            continue
                        if str(ev.get("source") or "") != id_label:
                            continue
                        if ev.get("model_name_compatible") and not ev.get(
                            "hash_prefix_match"
                        ):
                            # This source has name context + IDs; hash-match
                            # source provides the hash evidence.
                            confirmed_tier_2 = True
                            cross_source_detail.append(
                                f"name_compat+ids from {id_label} + "
                                f"hash_prefix_match from "
                                f"{sources_with_hash_match[0].get('source') if sources_with_hash_match else 'unknown'}"
                            )
                            break
                    if confirmed_tier_2:
                        break

    confirmed_exact = confirmed_tier_1 or confirmed_tier_2
    confirmation_tier = (
        "verified" if confirmed_tier_1 else "probable" if confirmed_tier_2 else None
    )

    return {
        "local_model": canonical_model,
        "local_model_hash": canonical_model_hash,
        "local_hash_tokens": sorted(local_hash_tokens),
        "confirmed_exact_match": bool(confirmed_exact),
        "confirmation_tier": confirmation_tier,
        "cross_source_detail": cross_source_detail if cross_source_detail else None,
        "sources": source_evidence,
    }


def _empty_local_catalog_payload() -> dict[str, Any]:
    return {
        "configured": False,
        "sources": {},
        "entries": [],
        "error": None,
        "raw": None,
        "raw_compacted": True,
    }


def _sanitize_export_filename(raw_name: str, *, default_stem: str) -> str:
    name = str(raw_name or "").strip()
    if not name:
        name = default_stem
    name = Path(name).name
    if not name:
        name = default_stem

    if not name.lower().endswith(".json"):
        name = f"{name}.json"

    stem = Path(name).stem
    suffix = Path(name).suffix or ".json"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    if not stem:
        stem = default_stem
    return f"{stem}{suffix}"


def _a1111_bridge_export_dir() -> Path:
    app_root = Path(__file__).resolve().parent.parent
    export_dir = app_root / "data" / "a1111_bridge_exports"
    export_dir.mkdir(parents=True, exist_ok=True)
    return export_dir


_A1111_PROCESS_CORE_FIELDS: tuple[str, ...] = (
    "sampler",
    "steps",
    "cfg_scale",
    "seed",
    "width",
    "height",
    "model",
    "model_hash",
    "denoising_strength",
    "clip_skip",
)


def _is_missing_process_value(value: Any) -> bool:
    return _a1111_svc._is_missing_process_value(value)


def _to_float(value: Any) -> Optional[float]:
    return _a1111_svc._to_float(value)


def _build_a1111_bridge_dataset_quality_report() -> dict[str, Any]:
    export_dir = _a1111_bridge_export_dir()
    files = sorted(export_dir.glob("*.json"))

    records: list[dict[str, Any]] = []
    load_errors: list[dict[str, Any]] = []
    unique_fingerprints: set[str] = set()

    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                payload = _dict_payload(json.load(handle))
        except Exception as exc:
            load_errors.append(
                {
                    "file_name": path.name,
                    "error": str(exc),
                }
            )
            continue

        analysis = _dict_payload(payload.get("analysis"))
        if not analysis:
            load_errors.append(
                {
                    "file_name": path.name,
                    "error": "Missing analysis object.",
                }
            )
            continue

        user_comment = _dict_payload(analysis.get("user_comment"))
        parsed = _dict_payload(user_comment.get("parsed"))
        parsed_fields = _dict_payload(parsed.get("parsed_fields"))
        parameters = _dict_payload(parsed.get("parameters"))
        warnings = [
            str(item)
            for item in _list_payload(parsed.get("warnings"))
            if str(item).strip()
        ]
        validation = _dict_payload(analysis.get("validation"))
        target = _dict_payload(analysis.get("target"))
        raw_text = str(parsed.get("raw_text") or "")

        fingerprint_payload = {
            "target_file_hash": target.get("file_hash"),
            "raw_text": raw_text,
            "parsed_fields": parsed_fields,
            "parameters": parameters,
        }
        fingerprint = hashlib.sha1(
            json.dumps(
                fingerprint_payload, sort_keys=True, ensure_ascii=True, default=str
            ).encode("utf-8")
        ).hexdigest()
        unique_fingerprints.add(fingerprint)

        records.append(
            {
                "file_name": path.name,
                "saved_at": payload.get("saved_at"),
                "analysis_ok": bool(analysis.get("ok")),
                "validation_status": str(validation.get("status") or "missing"),
                "target_file_hash": str(target.get("file_hash") or "").strip() or None,
                "parsed_fields": parsed_fields,
                "parameters": parameters,
                "raw_text": raw_text,
                "positive_prompt": str(parsed.get("positive_prompt") or ""),
                "negative_prompt": str(parsed.get("negative_prompt") or ""),
                "warnings": warnings,
                "lora_tags": _list_payload(parsed.get("lora_tags")),
                "fingerprint": fingerprint,
            }
        )

    unique_count = len(unique_fingerprints)
    record_count = len(records)

    coverage_present = {field: 0 for field in _A1111_PROCESS_CORE_FIELDS}
    coverage_missing = {field: 0 for field in _A1111_PROCESS_CORE_FIELDS}
    validation_status_counts: dict[str, int] = {}

    signal_counts = {
        "img2img_or_inpaint_detected": 0,
        "hires_fix_detected": 0,
        "adetailer_detected": 0,
        "lora_detected": 0,
        "civitai_resources_detected": 0,
        "truncated_positive_prompt": 0,
        "missing_seed": 0,
        "missing_model_identity": 0,
    }

    records_with_all_core_fields = 0

    for record in records:
        parsed_fields = _dict_payload(record.get("parsed_fields"))
        parameters = _dict_payload(record.get("parameters"))
        raw_text = str(record.get("raw_text") or "")
        positive_prompt = str(record.get("positive_prompt") or "")
        lora_tags = _list_payload(record.get("lora_tags"))

        missing_any = False
        for core_field in _A1111_PROCESS_CORE_FIELDS:
            value = parsed_fields.get(core_field)
            if _is_missing_process_value(value):
                coverage_missing[core_field] += 1
                missing_any = True
            else:
                coverage_present[core_field] += 1
        if not missing_any:
            records_with_all_core_fields += 1

        denoising = _to_float(parsed_fields.get("denoising_strength"))
        if denoising is not None and 0.0 < denoising < 1.0:
            signal_counts["img2img_or_inpaint_detected"] += 1

        if any(str(key).lower().startswith("hires") for key in parameters.keys()):
            signal_counts["hires_fix_detected"] += 1

        if any(str(key).lower().startswith("adetailer") for key in parameters.keys()):
            signal_counts["adetailer_detected"] += 1

        has_lora_hashes = any(
            str(key).lower() == "lora hashes" for key in parameters.keys()
        )
        if has_lora_hashes or bool(lora_tags):
            signal_counts["lora_detected"] += 1

        civitai_resources = any(
            str(key).lower() == "civitai resources" for key in parameters.keys()
        )
        if civitai_resources:
            signal_counts["civitai_resources_detected"] += 1

        if not positive_prompt.strip() and "negative prompt" in raw_text.lower():
            signal_counts["truncated_positive_prompt"] += 1

        if _is_missing_process_value(parsed_fields.get("seed")):
            signal_counts["missing_seed"] += 1

        if _is_missing_process_value(
            parsed_fields.get("model")
        ) or _is_missing_process_value(parsed_fields.get("model_hash")):
            signal_counts["missing_model_identity"] += 1

        status_name = str(record.get("validation_status") or "missing")
        validation_status_counts[status_name] = (
            validation_status_counts.get(status_name, 0) + 1
        )

    def _pct(value: int, total: int) -> float:
        if total <= 0:
            return 0.0
        return round((value / total) * 100.0, 2)

    field_coverage: dict[str, Any] = {}
    for core_field in _A1111_PROCESS_CORE_FIELDS:
        present = coverage_present[core_field]
        missing = coverage_missing[core_field]
        field_coverage[core_field] = {
            "present": present,
            "missing": missing,
            "coverage_percent": _pct(present, record_count),
        }

    core_fields_present_total = sum(coverage_present.values())
    max_core_slots = record_count * len(_A1111_PROCESS_CORE_FIELDS)
    core_coverage_percent = _pct(core_fields_present_total, max_core_slots)
    all_fields_records_percent = _pct(records_with_all_core_fields, record_count)

    # Readiness heuristic for whether process inference is reliable enough for automation.
    sample_size_sufficient = unique_count >= 40
    core_coverage_sufficient = core_coverage_percent >= 85.0
    seed_coverage_sufficient = field_coverage["seed"]["coverage_percent"] >= 85.0
    model_coverage_sufficient = (
        field_coverage["model"]["coverage_percent"] >= 85.0
        and field_coverage["model_hash"]["coverage_percent"] >= 85.0
    )
    reliable_for_process_inference = all(
        [
            sample_size_sufficient,
            core_coverage_sufficient,
            seed_coverage_sufficient,
            model_coverage_sufficient,
        ]
    )

    confidence_label = "low"
    if reliable_for_process_inference:
        confidence_label = "high"
    elif unique_count >= 15 and core_coverage_percent >= 75.0:
        confidence_label = "moderate"

    return {
        "ok": True,
        "mode": "a1111_bridge_dataset_quality",
        "paths": {
            "export_dir_absolute": str(export_dir),
            "export_dir_relative": str(
                export_dir.relative_to(Path(__file__).resolve().parent.parent)
            ),
        },
        "summary": {
            "file_count": len(files),
            "loaded_record_count": record_count,
            "unique_record_count": unique_count,
            "duplicate_count_estimate": max(0, record_count - unique_count),
            "load_error_count": len(load_errors),
            "analysis_ok_count": sum(1 for item in records if item.get("analysis_ok")),
            "parse_present_count": sum(
                1 for item in records if _dict_payload(item.get("parsed_fields"))
            ),
            "validation_status_counts": validation_status_counts,
        },
        "coverage": {
            "core_fields": field_coverage,
            "core_fields_aggregate_coverage_percent": core_coverage_percent,
            "records_with_all_core_fields": records_with_all_core_fields,
            "records_with_all_core_fields_percent": all_fields_records_percent,
        },
        "signals": {
            "img2img_or_inpaint_detected": {
                "count": signal_counts["img2img_or_inpaint_detected"],
                "percent": _pct(
                    signal_counts["img2img_or_inpaint_detected"], record_count
                ),
            },
            "hires_fix_detected": {
                "count": signal_counts["hires_fix_detected"],
                "percent": _pct(signal_counts["hires_fix_detected"], record_count),
            },
            "adetailer_detected": {
                "count": signal_counts["adetailer_detected"],
                "percent": _pct(signal_counts["adetailer_detected"], record_count),
            },
            "lora_detected": {
                "count": signal_counts["lora_detected"],
                "percent": _pct(signal_counts["lora_detected"], record_count),
            },
            "civitai_resources_detected": {
                "count": signal_counts["civitai_resources_detected"],
                "percent": _pct(
                    signal_counts["civitai_resources_detected"], record_count
                ),
            },
        },
        "quality_issues": {
            "truncated_positive_prompt": {
                "count": signal_counts["truncated_positive_prompt"],
                "percent": _pct(
                    signal_counts["truncated_positive_prompt"], record_count
                ),
            },
            "missing_seed": {
                "count": signal_counts["missing_seed"],
                "percent": _pct(signal_counts["missing_seed"], record_count),
            },
            "missing_model_identity": {
                "count": signal_counts["missing_model_identity"],
                "percent": _pct(signal_counts["missing_model_identity"], record_count),
            },
        },
        "inference_readiness": {
            "reliable_for_process_inference": reliable_for_process_inference,
            "confidence": confidence_label,
            "gates": {
                "sample_size_sufficient": sample_size_sufficient,
                "core_coverage_sufficient": core_coverage_sufficient,
                "seed_coverage_sufficient": seed_coverage_sufficient,
                "model_coverage_sufficient": model_coverage_sufficient,
            },
            "recommended_min_samples_basic": 50,
            "recommended_min_samples_full": 100,
            "recommended_batches_min": 3,
        },
        "load_errors": load_errors,
    }


def analyze_a1111_bridge(
    request: A1111BridgeAnalyzeRequest,
    db: Session = Depends(get_db),
):
    file_hash = str(request.file_hash or "").strip()
    if not file_hash:
        raise HTTPException(status_code=422, detail="file_hash is required.")

    image = (
        db.query(ImageModel)
        .filter(ImageModel.file_hash == file_hash)
        .filter(_active_image_filter())
        .first()
    )
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found.")

    generation_payload = _build_generation_prototype_local_payload(image)
    generation_payload, exif_refresh_warnings = (
        _inject_fresh_local_exif_metadata_for_comfy(generation_payload, image, db)
    )

    user_comment_candidates = _extract_a1111_user_comment_candidates(generation_payload)
    parse_payload, preferred_candidate = _build_authoritative_a1111_parse_payload(
        user_comment_candidates
    )

    comfy_export = _build_generation_comfy_workspace_export_payload(
        generation_payload,
        local_catalog=_empty_local_catalog_payload(),
    )
    workspace_bundle = _dict_payload(comfy_export.get("workspace_bundle"))
    inferred_workflow = _dict_payload(workspace_bundle.get("comfy_workflow_ui"))
    inferred_prompt_api = _dict_payload(workspace_bundle.get("comfy_prompt_api"))

    provided_workflow_json = _dict_payload(request.comfy_workflow_json)
    provided_workflow_supplied = request.comfy_workflow_json is not None

    comparison: dict[str, Any] = {
        "provided_workflow_supplied": provided_workflow_supplied,
        "inferred_workflow_available": bool(inferred_workflow),
        "structural": None,
        "field_alignment": None,
    }
    if provided_workflow_supplied and provided_workflow_json:
        comparison["structural"] = _compare_json_scalar_structures(
            inferred_workflow, provided_workflow_json
        )
        comparison["field_alignment"] = _build_a1111_field_alignment(
            _dict_payload(parse_payload.get("parsed_fields")),
            provided_workflow_json,
        )

    warnings = [
        *[str(item) for item in exif_refresh_warnings if str(item).strip()],
        *[
            str(item)
            for item in _list_payload(parse_payload.get("warnings"))
            if str(item).strip()
        ],
    ]
    if provided_workflow_supplied and not provided_workflow_json:
        warnings.append(
            "Provided comfy_workflow_json was empty; structural comparison was skipped."
        )
    if not inferred_workflow:
        warnings.append(
            "No inferred Comfy workflow was available from the local generation payload."
        )

    validation = _summarize_validation(warnings, [])
    response_payload: dict[str, Any] = {
        "ok": validation.get("status") != "error",
        "mode": "a1111_bridge",
        "target": {
            "file_hash": image.file_hash,
            "image_db_id": image.id,
            "source_url": image.source_url,
        },
        "user_comment": {
            "source": (
                preferred_candidate.get("source") if preferred_candidate else None
            ),
            "candidate_count": len(user_comment_candidates),
            "candidates": user_comment_candidates,
            "parsed": parse_payload,
        },
        "comfy": {
            "inferred_workflow_ui": inferred_workflow,
            "inferred_prompt_api": inferred_prompt_api,
            "workspace_summary": _dict_payload(comfy_export.get("overview")),
            "workspace_validation": _dict_payload(comfy_export.get("validation")),
            "provided_workflow_ui": (
                provided_workflow_json if provided_workflow_supplied else None
            ),
        },
        "comparison": comparison,
        "validation": validation,
    }
    if request.include_generation_payload:
        response_payload["generation_payload"] = generation_payload
    return response_payload


def analyze_parity_candidate(
    request: ParityCandidateAuditRequest,
    db: Session = Depends(get_db),
):
    file_hash = str(request.file_hash or "").strip()
    if not file_hash:
        raise HTTPException(status_code=422, detail="file_hash is required.")

    image = (
        db.query(ImageModel)
        .filter(ImageModel.file_hash == file_hash)
        .filter(_active_image_filter())
        .first()
    )
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found.")

    generation_payload = _build_generation_prototype_local_payload(image)
    generation_payload, exif_refresh_warnings = (
        _inject_fresh_local_exif_metadata_for_comfy(generation_payload, image, db)
    )

    user_comment_candidates = _extract_a1111_user_comment_candidates(generation_payload)
    parse_payload, preferred_candidate = _build_authoritative_a1111_parse_payload(
        user_comment_candidates
    )

    provided_workflow_json = _dict_payload(request.comfy_workflow_json)
    provided_workflow_supplied = request.comfy_workflow_json is not None

    # Always include all evidence sources (prefix + non-prefix)
    audit = _build_parity_candidate_audit(
        file_hash=file_hash,
        parse_payload=parse_payload,
        provided_workflow_json=provided_workflow_json,
        provided_workflow_supplied=provided_workflow_supplied,
        model_hash_evidence=_build_model_hash_evidence_for_parity(
            image=image,
            canonical_model=_dict_payload(parse_payload.get("parsed_fields")).get(
                "model"
            ),
            canonical_model_hash=_dict_payload(parse_payload.get("parsed_fields")).get(
                "model_hash"
            ),
            include_non_prefix_local_reference_matches=True,
        ),
    )

    audit["candidate"]["warnings"] = [
        *[
            str(item)
            for item in _list_payload(audit["candidate"].get("warnings"))
            if str(item).strip()
        ],
        *[str(item) for item in exif_refresh_warnings if str(item).strip()],
    ]
    audit["user_comment"] = {
        "source": preferred_candidate.get("source") if preferred_candidate else None,
        "candidate_count": len(user_comment_candidates),
        "parsed": parse_payload,
    }
    return audit


def save_a1111_bridge_analysis(payload: A1111BridgeSaveRequest):
    analysis_payload = _dict_payload(payload.analysis_payload)
    if not analysis_payload:
        raise HTTPException(
            status_code=422, detail="analysis_payload must be a JSON object."
        )

    target = _dict_payload(analysis_payload.get("target"))
    file_hash = str(target.get("file_hash") or "").strip()
    hash_token = re.sub(r"[^a-fA-F0-9]+", "", file_hash)[:12] or "unknown"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    default_stem = f"a1111_bridge_{hash_token}_{stamp}"

    resolved_file_name = _sanitize_export_filename(
        str(payload.file_name or ""), default_stem=default_stem
    )
    export_dir = _a1111_bridge_export_dir()
    export_path = export_dir / resolved_file_name

    sequence = 1
    while export_path.exists():
        candidate_name = _sanitize_export_filename(
            f"{Path(resolved_file_name).stem}_{sequence}.json",
            default_stem=default_stem,
        )
        export_path = export_dir / candidate_name
        sequence += 1

    wrapped_payload = {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "analysis": analysis_payload,
    }
    with open(export_path, "w", encoding="utf-8") as handle:
        json.dump(wrapped_payload, handle, indent=2, ensure_ascii=False, default=str)

    return {
        "ok": True,
        "saved": {
            "file_name": export_path.name,
            "absolute_path": str(export_path),
            "relative_path": str(
                export_path.relative_to(Path(__file__).resolve().parent.parent)
            ),
        },
        "target": {
            "file_hash": file_hash or None,
        },
    }


def get_a1111_bridge_dataset_quality_report():
    return _build_a1111_bridge_dataset_quality_report()


def _resolve_comfyui_base_url() -> str:
    base = str(getattr(app_config, "ATELIER_COMFYUI_BASE_URL", "") or "").strip()
    return base.rstrip("/")


def _build_prompt_node_class_map(prompt_graph: dict[str, Any]) -> dict[str, str]:
    node_class_map: dict[str, str] = {}
    for node_id, node in _dict_payload(prompt_graph).items():
        node_dict = _dict_payload(node)
        class_type = str(node_dict.get("class_type") or "").strip()
        if class_type:
            node_class_map[str(node_id)] = class_type
    return node_class_map


def _collect_comfy_history_images(
    history_entry: dict[str, Any],
    node_class_map: Optional[dict[str, str]] = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    outputs = _dict_payload(history_entry.get("outputs"))
    class_lookup = node_class_map or {}
    for node_id, output in outputs.items():
        node_key = str(node_id)
        output_dict = _dict_payload(output)
        images = _list_payload(output_dict.get("images"))
        for index, image_item in enumerate(images):
            image_meta = _dict_payload(image_item)
            filename = str(image_meta.get("filename") or "").strip()
            if not filename:
                continue
            subfolder = str(image_meta.get("subfolder") or "")
            image_type = str(image_meta.get("type") or "output")
            results.append(
                {
                    "node_id": node_key,
                    "node_class_type": str(class_lookup.get(node_key) or ""),
                    "index": index,
                    "filename": filename,
                    "subfolder": subfolder,
                    "type": image_type,
                }
            )
    return results


def _history_has_output_images(history_entry: dict[str, Any]) -> bool:
    return bool(_collect_comfy_history_images(history_entry))


def _build_comfy_view_url(base_url: str, image_meta: dict[str, Any]) -> str:
    query = urlencode(
        {
            "filename": str(image_meta.get("filename") or ""),
            "subfolder": str(image_meta.get("subfolder") or ""),
            "type": str(image_meta.get("type") or "output"),
        }
    )
    return f"{base_url}/view?{query}"


def _to_data_url(image_bytes: bytes, mime_type: str) -> str:
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _apply_comfy_filename_prefix_cache_bust(
    prompt_graph: dict[str, Any], suffix: str
) -> tuple[dict[str, Any], int]:
    """Clone a prompt graph and make SaveImage filename_prefix unique to avoid output filename collisions."""
    cloned = _clone_json_value(prompt_graph)
    if not isinstance(cloned, dict):
        return {}, 0

    mutated = 0
    for node in cloned.values():
        node_dict = _dict_payload(node)
        class_type = str(node_dict.get("class_type") or "").strip().lower()
        if class_type != "saveimage":
            continue
        inputs = node_dict.get("inputs")
        if not isinstance(inputs, dict):
            continue

        original_prefix = str(inputs.get("filename_prefix") or "ComfyUI")
        inputs["filename_prefix"] = f"{original_prefix}_{suffix}"
        mutated += 1

    return cloned, mutated


def generate_and_compare_comfy_workspace(
    payload: ComfyGenerateCompareRequest,
    db: Session = Depends(get_db),
):
    _assert_imagehash_available()

    base_url = _resolve_comfyui_base_url()
    if not base_url:
        raise HTTPException(
            status_code=422,
            detail=(
                "ComfyUI base URL is not configured. Set ATELIER_COMFYUI_BASE_URL."
            ),
        )

    workflow_json = _dict_payload(payload.workflow_json)
    if not workflow_json:
        raise HTTPException(
            status_code=422, detail="workflow_json must be a JSON object."
        )

    prompt_graph = _extract_comfy_prompt_graph(workflow_json, {})
    if not prompt_graph and _looks_like_comfy_prompt_graph(workflow_json):
        prompt_graph = workflow_json
    if not prompt_graph:
        raise HTTPException(
            status_code=422,
            detail=(
                "Could not extract a Comfy prompt graph from workflow_json. "
                "Provide a Comfy API prompt JSON object (the graph submitted to /prompt)."
            ),
        )

    submitted_prompt_graph = prompt_graph
    active_prompt_node_class_map = _build_prompt_node_class_map(submitted_prompt_graph)
    cache_bust_seed_count = 0
    used_cache_bust_retry = False
    cache_bust_filename_prefix_count = 0
    used_filename_prefix_retry = False
    include_all_workspace_images = bool(payload.include_all_workspace_images)
    close_enough_similarity_threshold = max(
        0.0, min(1.0, ATELIER_COMFY_MATCH_THRESHOLD)
    )

    reference_file_hash = str(payload.reference_file_hash or "").strip()
    if not reference_file_hash:
        raise HTTPException(status_code=422, detail="reference_file_hash is required.")

    threshold_override = payload.match_threshold_override
    threshold_source = "default"
    if threshold_override is not None:
        close_enough_similarity_threshold = max(
            0.0, min(1.0, float(threshold_override))
        )
        threshold_source = "request_override"

    tweak_label = str(payload.tweak_label or "").strip() or None
    tweak_parameters = _dict_payload(payload.tweaked_parameters)

    next_attempt_index = (
        int(
            db.query(func.max(GenerationMatchAttempt.attempt_index))
            .filter(GenerationMatchAttempt.reference_file_hash == reference_file_hash)
            .scalar()
            or 0
        )
        + 1
    )

    def _persist_generation_match_attempt(
        *,
        prompt_id_value: Optional[str],
        outputs_value: list[dict[str, Any]],
        best_match_value: Optional[dict[str, Any]],
        best_similarity_value: Optional[float],
        is_matched_value: bool,
        is_fundamental_issue_value: bool,
        notes_value: Optional[str],
        error_message_value: Optional[str],
    ) -> None:
        try:
            best_phash_distance = None
            best_output_filename = None
            if isinstance(best_match_value, dict):
                best_output_filename = (
                    str(best_match_value.get("filename") or "").strip() or None
                )
                distance_raw = _dict_payload(best_match_value.get("phash")).get(
                    "distance"
                )
                try:
                    best_phash_distance = (
                        int(distance_raw) if distance_raw is not None else None
                    )
                except (TypeError, ValueError):
                    best_phash_distance = None

            attempt = GenerationMatchAttempt(
                reference_file_hash=reference_file_hash,
                comfy_prompt_id=(str(prompt_id_value or "").strip() or None),
                attempt_index=next_attempt_index,
                tweak_label=tweak_label,
                tweak_parameters_json=_clone_json_value(tweak_parameters),
                effective_parameters_json={
                    "include_all_workspace_images": include_all_workspace_images,
                    "threshold": close_enough_similarity_threshold,
                    "threshold_source": threshold_source,
                },
                generated_outputs_json=_clone_json_value(outputs_value),
                best_output_filename=best_output_filename,
                best_phash_distance=best_phash_distance,
                best_similarity=best_similarity_value,
                threshold_used=close_enough_similarity_threshold,
                is_matched=is_matched_value,
                is_fundamental_generation_issue=is_fundamental_issue_value,
                notes=notes_value,
                error_message=error_message_value,
                created_at=datetime.now(timezone.utc),
            )
            db.add(attempt)
            db.commit()
        except Exception:
            db.rollback()

    reference_image = _get_image_or_404(db, reference_file_hash)
    reference_path = _resolve_image_library_path(reference_image)
    if not reference_path.exists() or not reference_path.is_file():
        raise HTTPException(
            status_code=404, detail="Reference image file not found on disk."
        )

    if imagehash is None:
        raise HTTPException(
            status_code=503,
            detail="imagehash dependency is not installed; parity matching is unavailable.",
        )

    try:
        with Image.open(reference_path) as reference_handle:
            reference_hash = imagehash.phash(reference_handle.convert("RGB"), 8)
    except OSError as exc:
        raise HTTPException(
            status_code=400, detail=f"Unable to open reference image for hashing: {exc}"
        )

    timeout_seconds = int(payload.timeout_seconds or 120)
    poll_interval_seconds = float(payload.poll_interval_seconds or 1.25)

    def _submit_and_wait(
        graph_to_submit: dict[str, Any],
    ) -> tuple[dict[str, Any], str, dict[str, Any], str]:
        submit_url = f"{base_url}/prompt"
        try:
            submit_response = requests.post(
                submit_url,
                json={"prompt": graph_to_submit},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Unable to reach ComfyUI submit endpoint: {exc}",
            )

        submit_payload_local = _dict_payload(
            submit_response.json() if submit_response.content else {}
        )
        if not submit_response.ok:
            raise HTTPException(
                status_code=502,
                detail=str(
                    submit_payload_local.get("error")
                    or submit_payload_local.get("detail")
                    or f"ComfyUI prompt submit failed with HTTP {submit_response.status_code}."
                ),
            )

        prompt_id_local = str(submit_payload_local.get("prompt_id") or "").strip()
        if not prompt_id_local:
            raise HTTPException(
                status_code=502, detail="ComfyUI did not return a prompt_id."
            )

        history_entry_local: dict[str, Any] = {}
        poll_started = time.monotonic()
        status_value_local = "queued"
        while (time.monotonic() - poll_started) < timeout_seconds:
            history_url = f"{base_url}/history/{quote(prompt_id_local, safe='')}"
            try:
                history_response = requests.get(history_url, timeout=20)
            except requests.RequestException:
                time.sleep(poll_interval_seconds)
                continue

            history_payload = _dict_payload(
                history_response.json() if history_response.content else {}
            )
            entry = _dict_payload(history_payload.get(prompt_id_local))
            if not entry:
                time.sleep(poll_interval_seconds)
                continue

            history_entry_local = entry
            status_obj = _dict_payload(entry.get("status"))
            status_value_local = str(
                status_obj.get("status_str") or status_value_local or "queued"
            )
            if bool(status_obj.get("completed")):
                break
            if status_value_local.lower() in {"error", "failed"}:
                break
            time.sleep(poll_interval_seconds)

        if not history_entry_local:
            raise HTTPException(
                status_code=504,
                detail=f"Timed out waiting for ComfyUI completion for prompt_id {prompt_id_local}.",
            )

        # Comfy can report completion slightly before outputs are fully visible in history.
        # Give it a short grace window to avoid false "0 output" responses.
        if not _history_has_output_images(history_entry_local):
            output_wait_deadline = time.monotonic() + 8.0
            while time.monotonic() < output_wait_deadline:
                history_url = f"{base_url}/history/{quote(prompt_id_local, safe='')}"
                try:
                    history_response = requests.get(history_url, timeout=20)
                except requests.RequestException:
                    time.sleep(max(0.25, min(1.0, poll_interval_seconds)))
                    continue

                history_payload = _dict_payload(
                    history_response.json() if history_response.content else {}
                )
                entry = _dict_payload(history_payload.get(prompt_id_local))
                if entry:
                    history_entry_local = entry
                    if _history_has_output_images(history_entry_local):
                        break
                time.sleep(max(0.25, min(1.0, poll_interval_seconds)))

        return (
            submit_payload_local,
            prompt_id_local,
            history_entry_local,
            status_value_local,
        )

    submit_payload, prompt_id, history_entry, status_value = _submit_and_wait(
        submitted_prompt_graph
    )
    generated_items = _collect_comfy_history_images(
        history_entry, active_prompt_node_class_map
    )

    if not generated_items:
        status_obj = _dict_payload(history_entry.get("status"))
        status_text = str(status_obj.get("status_str") or status_value or "unknown")
        status_messages = _list_payload(status_obj.get("messages"))

        has_execution_cached = any(
            isinstance(message, list)
            and len(message) >= 1
            and str(message[0]).strip().lower() == "execution_cached"
            for message in status_messages
        )
        if has_execution_cached:
            retry_suffix = f"run_{int(time.time() * 1000)}"
            retry_graph, retry_prefix_count = _apply_comfy_filename_prefix_cache_bust(
                prompt_graph, retry_suffix
            )
            if retry_graph and retry_prefix_count > 0:
                submit_payload, prompt_id, history_entry, status_value = (
                    _submit_and_wait(retry_graph)
                )
                active_prompt_node_class_map = _build_prompt_node_class_map(retry_graph)
                generated_items = _collect_comfy_history_images(
                    history_entry, active_prompt_node_class_map
                )
                cache_bust_filename_prefix_count = retry_prefix_count
                used_filename_prefix_retry = True
                cache_bust_seed_count = 0
                used_cache_bust_retry = True

        if not generated_items:
            failure_detail = (
                "ComfyUI run produced zero image outputs. "
                f"status={status_text}. Ensure your prompt graph includes SaveImage output nodes. "
                f"prompt_id={prompt_id}. messages={status_messages}"
            )
            _persist_generation_match_attempt(
                prompt_id_value=prompt_id,
                outputs_value=[],
                best_match_value=None,
                best_similarity_value=None,
                is_matched_value=False,
                is_fundamental_issue_value=True,
                notes_value="No outputs generated from Comfy run.",
                error_message_value=failure_detail,
            )
            raise HTTPException(
                status_code=502,
                detail=failure_detail,
            )

    selected_items = list(generated_items)
    if not include_all_workspace_images:
        save_image_items = [
            item
            for item in selected_items
            if str(item.get("node_class_type") or "").strip().lower() == "saveimage"
        ]
        if save_image_items:
            selected_items = save_image_items

    results: list[dict[str, Any]] = []
    fetch_errors: list[dict[str, Any]] = []

    for item in selected_items:
        view_url = _build_comfy_view_url(base_url, item)
        try:
            image_response = requests.get(view_url, timeout=45)
        except requests.RequestException as exc:
            fetch_errors.append(
                {
                    "node_id": item.get("node_id"),
                    "filename": item.get("filename"),
                    "error": str(exc),
                }
            )
            continue

        if not image_response.ok:
            fetch_errors.append(
                {
                    "node_id": item.get("node_id"),
                    "filename": item.get("filename"),
                    "error": f"HTTP {image_response.status_code}",
                }
            )
            continue

        mime_type = (
            str(image_response.headers.get("Content-Type") or "image/png")
            .split(";")[0]
            .strip()
            or "image/png"
        )
        image_bytes = image_response.content
        try:
            with Image.open(io.BytesIO(image_bytes)) as generated_handle:
                generated_hash = imagehash.phash(generated_handle.convert("RGB"), 8)
        except OSError as exc:
            fetch_errors.append(
                {
                    "node_id": item.get("node_id"),
                    "filename": item.get("filename"),
                    "error": f"Could not decode generated image: {exc}",
                }
            )
            continue

        distance = int(reference_hash - generated_hash)
        similarity = max(0.0, min(1.0, 1.0 - (distance / 64.0)))
        results.append(
            {
                "node_id": item.get("node_id"),
                "node_class_type": item.get("node_class_type"),
                "index": item.get("index"),
                "filename": item.get("filename"),
                "subfolder": item.get("subfolder"),
                "type": item.get("type"),
                "mime_type": mime_type,
                "byte_size": len(image_bytes),
                "view_url": view_url,
                "image_data_url": _to_data_url(image_bytes, mime_type),
                "phash": {
                    "reference": str(reference_hash),
                    "generated": str(generated_hash),
                    "distance": distance,
                    "similarity": round(similarity, 6),
                    "close_enough": similarity >= close_enough_similarity_threshold,
                },
            }
        )

    results.sort(
        key=lambda item: (
            int(_dict_payload(item.get("phash")).get("distance") or 9999),
            str(item.get("filename") or ""),
        )
    )
    if not include_all_workspace_images and len(results) > 1:
        results = [results[0]]
    best_match = results[0] if results else None
    best_similarity_raw = (
        _dict_payload(best_match.get("phash")).get("similarity")
        if isinstance(best_match, dict)
        else None
    )
    try:
        best_similarity = (
            float(best_similarity_raw) if best_similarity_raw is not None else None
        )
    except (TypeError, ValueError):
        best_similarity = None
    is_close_enough = bool(
        best_similarity is not None
        and best_similarity >= close_enough_similarity_threshold
    )
    is_fundamental_issue = not is_close_enough

    status_obj = _dict_payload(history_entry.get("status"))
    completed = bool(status_obj.get("completed"))
    status_text = str(status_obj.get("status_str") or status_value or "unknown")

    _persist_generation_match_attempt(
        prompt_id_value=prompt_id,
        outputs_value=results,
        best_match_value=best_match if isinstance(best_match, dict) else None,
        best_similarity_value=best_similarity,
        is_matched_value=is_close_enough,
        is_fundamental_issue_value=is_fundamental_issue,
        notes_value="Matched threshold" if is_close_enough else "Below match threshold",
        error_message_value=None,
    )

    return {
        "ok": completed and bool(results),
        "comfy": {
            "base_url": base_url,
            "prompt_id": prompt_id,
            "cache_bust_seed_count": cache_bust_seed_count,
            "used_cache_bust_retry": used_cache_bust_retry,
            "cache_bust_filename_prefix_count": cache_bust_filename_prefix_count,
            "used_filename_prefix_retry": used_filename_prefix_retry,
            "submit": submit_payload,
            "status": {
                "completed": completed,
                "status_str": status_text,
                "messages": _list_payload(status_obj.get("messages")),
            },
        },
        "reference": {
            "file_hash": reference_image.file_hash,
            "file_name": reference_image.file_name,
            "mimetype": reference_image.mimetype,
            "image_url": f"/image_library/{_normalize_media_url_path(str(reference_image.file_path))}",
            "phash": str(reference_hash),
        },
        "generated": {
            "count": len(results),
            "outputs": results,
            "best_match": best_match,
            "best_similarity": (
                round(best_similarity, 6) if best_similarity is not None else None
            ),
            "close_enough": is_close_enough,
            "close_enough_threshold": close_enough_similarity_threshold,
            "matched": is_close_enough,
            "match_threshold": close_enough_similarity_threshold,
            "fundamental_generation_issue": is_fundamental_issue,
            "fetch_errors": fetch_errors,
            "selection": {
                "include_all_workspace_images": include_all_workspace_images,
                "total_workspace_outputs": len(generated_items),
                "selected_output_count": len(selected_items),
                "save_image_output_count": sum(
                    1
                    for item in generated_items
                    if str(item.get("node_class_type") or "").strip().lower()
                    == "saveimage"
                ),
                "policy": "all" if include_all_workspace_images else "best_save_image",
            },
        },
        "history": {
            "outputs": _dict_payload(history_entry.get("outputs")),
        },
    }


def list_comfy_generation_match_attempts(
    reference_file_hash: Optional[str] = Query(default=None),
    only_fundamental_issues: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    query = db.query(GenerationMatchAttempt)
    if reference_file_hash:
        query = query.filter(
            GenerationMatchAttempt.reference_file_hash
            == str(reference_file_hash).strip()
        )
    if only_fundamental_issues:
        query = query.filter(
            GenerationMatchAttempt.is_fundamental_generation_issue.is_(True)
        )

    total_count = int(query.count())
    attempts = (
        query.order_by(
            GenerationMatchAttempt.created_at.desc(), GenerationMatchAttempt.id.desc()
        )
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        "ok": True,
        "count": len(attempts),
        "total_count": total_count,
        "offset": offset,
        "limit": limit,
        "attempts": [
            {
                "id": attempt.id,
                "reference_file_hash": attempt.reference_file_hash,
                "comfy_prompt_id": attempt.comfy_prompt_id,
                "attempt_index": attempt.attempt_index,
                "tweak_label": attempt.tweak_label,
                "tweak_parameters": _clone_json_value(
                    _dict_payload(attempt.tweak_parameters_json)
                ),
                "best_output_filename": attempt.best_output_filename,
                "best_phash_distance": attempt.best_phash_distance,
                "best_similarity": attempt.best_similarity,
                "threshold_used": attempt.threshold_used,
                "matched": bool(attempt.is_matched),
                "fundamental_generation_issue": bool(
                    attempt.is_fundamental_generation_issue
                ),
                "notes": attempt.notes,
                "error_message": attempt.error_message,
                "created_at": (
                    attempt.created_at.isoformat()
                    if getattr(attempt, "created_at", None) is not None
                    else None
                ),
            }
            for attempt in attempts
        ],
    }


def import_generation_template_workspace(
    payload: GenerationTemplateImportRequest, db: Session = Depends(get_db)
):
    template_name = str(payload.name or "").strip()
    if not template_name:
        raise HTTPException(status_code=422, detail="Template name is required.")

    existing = (
        db.query(GenerationTemplate)
        .filter(GenerationTemplate.name == template_name)
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail="A generation template with that name already exists.",
        )

    workflow_json = _dict_payload(payload.workflow_json)
    if not workflow_json:
        raise HTTPException(
            status_code=422, detail="workflow_json must be a JSON object."
        )
    nodes = workflow_json.get("nodes")
    if not isinstance(nodes, list):
        raise HTTPException(
            status_code=422, detail="workflow_json must include a ComfyUI nodes array."
        )

    for item in payload.mappings:
        token_name = str(item.token or "").strip()
        target_path = str(item.target_path or "").strip()
        if not token_name:
            raise HTTPException(
                status_code=422, detail="Template mapping token cannot be empty."
            )
        if not target_path:
            raise HTTPException(
                status_code=422, detail="Template mapping target_path cannot be empty."
            )
        try:
            _tokenize_template_path(target_path)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    now = datetime.now(timezone.utc)
    template = GenerationTemplate(
        name=template_name,
        description=str(payload.description or "").strip() or None,
        workflow_json=_clone_json_value(workflow_json),
        mappings_json=[item.model_dump() for item in payload.mappings],
        default_tokens_json=_clone_json_value(_dict_payload(payload.default_tokens)),
        created_at=now,
        updated_at=now,
    )
    db.add(template)
    db.commit()
    db.refresh(template)

    return {
        "ok": True,
        "template": _serialize_generation_template(template, include_workflow=False),
    }


def list_generation_templates(
    include_workflow: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    query = (
        db.query(GenerationTemplate)
        .order_by(GenerationTemplate.updated_at.desc(), GenerationTemplate.id.desc())
        .offset(offset)
        .limit(limit)
    )
    templates = query.all()
    total_count = db.query(func.count(GenerationTemplate.id)).scalar() or 0
    return {
        "ok": True,
        "templates": [
            _serialize_generation_template(template, include_workflow=include_workflow)
            for template in templates
        ],
        "count": len(templates),
        "total_count": int(total_count),
        "offset": offset,
        "limit": limit,
    }


def _resolve_template_local_catalog(
    *,
    catalog_url: Optional[str],
    checkpoints_url: Optional[str],
    loras_url: Optional[str],
    include_full_catalog_raw: bool,
) -> dict[str, Any]:
    local_catalog = {
        "configured": False,
        "sources": {},
        "entries": [],
        "error": None,
        "raw": {},
        "raw_compacted": True,
    }
    if any(
        str(value or "").strip() for value in (catalog_url, checkpoints_url, loras_url)
    ):
        local_catalog = model_reference_service.fetch_local_catalog(
            catalog_url=catalog_url,
            checkpoints_url=checkpoints_url,
            loras_url=loras_url,
            include_full_raw=include_full_catalog_raw,
        )
    return local_catalog


def preview_generation_template_tokens(
    source_mode: Literal["local", "civitai"] = Query(...),
    file_hash: Optional[str] = Query(default=None),
    image_id: Optional[int] = Query(default=None),
    catalog_url: Optional[str] = Query(default=None),
    checkpoints_url: Optional[str] = Query(default=None),
    loras_url: Optional[str] = Query(default=None),
    include_full_catalog_raw: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    source_request = GenerationTemplateResolveRequest(
        source_mode=source_mode,
        file_hash=file_hash,
        image_id=image_id,
    )
    generation_payload = _resolve_generation_payload_for_template_request(
        source_request, db
    )
    local_catalog = _resolve_template_local_catalog(
        catalog_url=catalog_url,
        checkpoints_url=checkpoints_url,
        loras_url=loras_url,
        include_full_catalog_raw=include_full_catalog_raw,
    )
    preview = _build_generation_template_token_preview(
        generation_payload, local_catalog
    )
    tokens = _dict_payload(preview.get("tokens"))
    global_tokens = _dict_payload(preview.get("global_tokens"))
    step_groups = _list_payload(preview.get("step_groups"))
    return {
        "ok": True,
        "source": {
            "mode": generation_payload.get("mode"),
            "target": _dict_payload(generation_payload.get("target")),
        },
        "token_count": len(tokens),
        "global_token_count": len(global_tokens),
        "step_count": len(step_groups),
        "tokens": tokens,
        "global_tokens": global_tokens,
        "step_groups": step_groups,
        "catalog": {
            "configured": bool(local_catalog.get("configured")),
            "sources": _dict_payload(local_catalog.get("sources")),
            "error": local_catalog.get("error"),
            "entry_count": len(_list_payload(local_catalog.get("entries"))),
        },
    }


def get_generation_template(
    template_id: int,
    include_workflow: bool = Query(default=True),
    db: Session = Depends(get_db),
):
    template = (
        db.query(GenerationTemplate)
        .filter(GenerationTemplate.id == template_id)
        .first()
    )
    if template is None:
        raise HTTPException(status_code=404, detail="Generation template not found.")
    return {
        "ok": True,
        "template": _serialize_generation_template(
            template, include_workflow=include_workflow
        ),
    }


def update_generation_template(
    template_id: int,
    payload: GenerationTemplateUpdateRequest,
    db: Session = Depends(get_db),
):
    template = (
        db.query(GenerationTemplate)
        .filter(GenerationTemplate.id == template_id)
        .first()
    )
    if template is None:
        raise HTTPException(status_code=404, detail="Generation template not found.")

    if payload.name is not None:
        next_name = str(payload.name or "").strip()
        if not next_name:
            raise HTTPException(
                status_code=422, detail="Template name cannot be empty."
            )
        existing = (
            db.query(GenerationTemplate)
            .filter(GenerationTemplate.name == next_name)
            .filter(GenerationTemplate.id != template_id)
            .first()
        )
        if existing is not None:
            raise HTTPException(
                status_code=409,
                detail="A generation template with that name already exists.",
            )
        template.name = next_name

    if payload.description is not None:
        template.description = str(payload.description or "").strip() or None

    if payload.workflow_json is not None:
        workflow_json = _dict_payload(payload.workflow_json)
        if not workflow_json:
            raise HTTPException(
                status_code=422, detail="workflow_json must be a JSON object."
            )
        nodes = workflow_json.get("nodes")
        if not isinstance(nodes, list):
            raise HTTPException(
                status_code=422,
                detail="workflow_json must include a ComfyUI nodes array.",
            )
        template.workflow_json = _clone_json_value(workflow_json)

    if payload.mappings is not None:
        for item in payload.mappings:
            token_name = str(item.token or "").strip()
            target_path = str(item.target_path or "").strip()
            if not token_name:
                raise HTTPException(
                    status_code=422, detail="Template mapping token cannot be empty."
                )
            if not target_path:
                raise HTTPException(
                    status_code=422,
                    detail="Template mapping target_path cannot be empty.",
                )
            try:
                _tokenize_template_path(target_path)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
        template.mappings_json = [item.model_dump() for item in payload.mappings]

    if payload.default_tokens is not None:
        template.default_tokens_json = _clone_json_value(
            _dict_payload(payload.default_tokens)
        )

    template.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(template)
    return {
        "ok": True,
        "template": _serialize_generation_template(template, include_workflow=False),
    }


def delete_generation_template(template_id: int, db: Session = Depends(get_db)):
    template = (
        db.query(GenerationTemplate)
        .filter(GenerationTemplate.id == template_id)
        .first()
    )
    if template is None:
        raise HTTPException(status_code=404, detail="Generation template not found.")
    template_snapshot = _serialize_generation_template(template, include_workflow=False)
    db.delete(template)
    db.commit()
    return {
        "ok": True,
        "deleted": template_snapshot,
    }


def _resolve_generation_payload_for_template_request(
    request: GenerationTemplateResolveRequest, db: Session
) -> dict[str, Any]:
    if request.source_mode == "local":
        file_hash = str(request.file_hash or "").strip()
        if not file_hash:
            raise HTTPException(
                status_code=422, detail="file_hash is required when source_mode=local."
            )
        image = (
            db.query(ImageModel)
            .filter(ImageModel.file_hash == file_hash)
            .filter(_active_image_filter())
            .first()
        )
        if image is None:
            raise HTTPException(status_code=404, detail="Image not found.")
        return _build_generation_prototype_local_payload(image)

    if request.source_mode == "civitai":
        if request.image_id is None:
            raise HTTPException(
                status_code=422, detail="image_id is required when source_mode=civitai."
            )
        return _build_generation_prototype_civitai_payload(int(request.image_id))

    raise HTTPException(status_code=422, detail="Unsupported source_mode.")


def resolve_generation_template(
    template_id: int,
    request: GenerationTemplateResolveRequest,
    catalog_url: Optional[str] = Query(default=None),
    checkpoints_url: Optional[str] = Query(default=None),
    loras_url: Optional[str] = Query(default=None),
    include_full_catalog_raw: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    template = (
        db.query(GenerationTemplate)
        .filter(GenerationTemplate.id == template_id)
        .first()
    )
    if template is None:
        raise HTTPException(status_code=404, detail="Generation template not found.")

    generation_payload = _resolve_generation_payload_for_template_request(request, db)

    local_catalog = _resolve_template_local_catalog(
        catalog_url=catalog_url,
        checkpoints_url=checkpoints_url,
        loras_url=loras_url,
        include_full_catalog_raw=include_full_catalog_raw,
    )

    resolved_payload = _resolve_generation_template_workflow(
        template,
        generation_payload,
        token_overrides=_dict_payload(request.token_overrides),
        local_catalog=local_catalog,
    )
    if request.include_generation_payload:
        resolved_payload["generation_payload"] = generation_payload

    return {
        **resolved_payload,
        "catalog": {
            "configured": bool(local_catalog.get("configured")),
            "sources": _dict_payload(local_catalog.get("sources")),
            "error": local_catalog.get("error"),
            "entry_count": len(_list_payload(local_catalog.get("entries"))),
        },
    }


def analyze_local_image_perceptual_hashes(
    file_hash: str,
    hash_size: int = Query(default=8, ge=4, le=32),
    db: Session = Depends(get_db),
):
    _assert_imagehash_available()
    image = _get_image_or_404(db, file_hash)
    image_path = _resolve_image_library_path(image)
    if not image_path.exists() or not image_path.is_file():
        raise HTTPException(status_code=404, detail="Image file not found on disk.")

    mimetype = str(image.mimetype or "").lower()
    if mimetype.startswith("video/"):
        raise HTTPException(
            status_code=400,
            detail="Perceptual image hashes currently support still images only.",
        )

    try:
        local_hashes = _build_perceptual_hash_suite(image_path, hash_size=hash_size)
    except OSError as exc:
        raise HTTPException(
            status_code=400, detail=f"Unable to open image for hashing: {exc}"
        )

    civitai_payload = _read_civitai_payload_for_image(image)
    civitai_image_hash = _extract_primary_civitai_image_hash(image, civitai_payload)
    civitai_comparison = _build_civitai_hash_comparison_payload(
        local_hashes, civitai_payload
    )
    blurhash_report = _build_blurhash_report(
        image_path, civitai_hash=civitai_image_hash
    )
    civitai_hash_candidates = [
        {
            "path": str(candidate.get("path") or "civitai"),
            "value": str(candidate.get("value") or ""),
        }
        for candidate in civitai_comparison.get("candidates", [])
        if str(candidate.get("value") or "")
    ]

    hash_report = {
        algorithm: {
            "hex": str(values.get("hex") or ""),
            "hash_size": int(values.get("hash_size") or hash_size),
            "bit_length": int(values.get("bit_length") or 0),
            "value_int": int(values.get("value_int") or 0),
            "base16": str(values.get("base16") or ""),
            "base32": str(values.get("base32") or ""),
            "base64": str(values.get("base64") or ""),
            "uuencode": str(values.get("uuencode") or ""),
        }
        for algorithm, values in local_hashes.items()
    }

    return {
        "ok": True,
        "file_hash": image.file_hash,
        "civitai_uuid": image.civitai_uuid,
        "file_name": image.file_name,
        "file_path": image.file_path,
        "mimetype": image.mimetype,
        "image_url": f"/image_library/{_normalize_media_url_path(str(image.file_path))}",
        "hashes": hash_report,
        "blurhash": blurhash_report,
        "civitai": {
            "has_payload": bool(civitai_payload),
            "image_hash": civitai_image_hash,
            "metadata_hash_candidates": civitai_hash_candidates,
            "comparison": civitai_comparison,
        },
    }


def search_perceptual_similarity(
    file_hash: str,
    algorithm: str = Query(default="phash"),
    hash_size: int = Query(default=8, ge=4, le=32),
    max_distance: int = Query(default=12, ge=0, le=256),
    limit: int = Query(default=50, ge=1, le=200),
    max_candidates: int = Query(default=1200, ge=50, le=5000),
    db: Session = Depends(get_db),
):
    algorithms: dict[str, Callable[..., Any]] = {}
    selected_algorithm = str(algorithm or "").strip().lower()
    if selected_algorithm != "blurhash":
        algorithms = _get_perceptual_hash_algorithms()
    supported_algorithms = {"blurhash", "phash", "dhash", "ahash", "whash"}
    if selected_algorithm not in supported_algorithms:
        raise HTTPException(
            status_code=400,
            detail=(
                "Unsupported algorithm. Choose one of: "
                + ", ".join(sorted(supported_algorithms))
            ),
        )

    target_image = _get_image_or_404(db, file_hash)
    target_path = _resolve_image_library_path(target_image)
    if not target_path.exists() or not target_path.is_file():
        raise HTTPException(
            status_code=404, detail="Target image file not found on disk."
        )

    if str(target_image.mimetype or "").lower().startswith("video/"):
        raise HTTPException(
            status_code=400,
            detail="Perceptual similarity search currently supports still images only.",
        )

    target_hex: Optional[str] = None
    target_blurhash: Optional[str] = None
    if selected_algorithm == "blurhash":
        target_blurhash, _ = _resolve_local_blurhash_4x4(target_image, target_path)
        if not target_blurhash:
            raise HTTPException(
                status_code=400,
                detail=(
                    "Blurhash similarity requires local blurhash data for the target image. "
                    "Run a rescan first or ensure blurhash support is installed."
                ),
            )
    else:
        builder = algorithms[selected_algorithm]
        try:
            with Image.open(target_path) as handle:
                target_hash_obj = builder(handle.convert("RGB"), hash_size)
        except OSError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Unable to open target image for hashing: {exc}",
            )
        target_hex = str(target_hash_obj)

    query = (
        db.query(ImageModel)
        .filter(_active_image_filter())
        .order_by(ImageModel.id.desc())
        .limit(max_candidates)
    )
    candidates = query.all()

    matches: list[dict[str, Any]] = []
    scanned_count = 0
    skipped_missing_blurhash = 0
    for candidate in candidates:
        if str(candidate.file_hash or "") == str(target_image.file_hash or ""):
            continue
        if str(candidate.mimetype or "").lower().startswith("video/"):
            continue

        candidate_path = _resolve_image_library_path(candidate)
        if not candidate_path.exists() or not candidate_path.is_file():
            continue

        if selected_algorithm == "blurhash":
            candidate_blurhash, blurhash_source = _resolve_local_blurhash_4x4(
                candidate, candidate_path
            )
            if not candidate_blurhash:
                skipped_missing_blurhash += 1
                continue

            scanned_count += 1
            preview_distance = _blurhash_preview_distance(
                candidate_blurhash,
                target_blurhash,
                width=32,
                height=32,
            )
            if not isinstance(preview_distance, dict):
                continue

            distance_value = float(preview_distance.get("mean_absolute_error") or 0.0)
            if distance_value > float(max_distance):
                continue

            matches.append(
                {
                    "file_hash": candidate.file_hash,
                    "civitai_uuid": candidate.civitai_uuid,
                    "file_name": candidate.file_name,
                    "file_path": candidate.file_path,
                    "mimetype": candidate.mimetype,
                    "source_url": candidate.source_url,
                    "distance": round(distance_value, 4),
                    "distance_type": "blurhash_mae",
                    "similarity": preview_distance.get("normalized_similarity"),
                    "preview_distance": preview_distance,
                    "blurhash": candidate_blurhash,
                    "blurhash_source": blurhash_source,
                    "image_url": f"/image_library/{_normalize_media_url_path(str(candidate.file_path))}",
                }
            )
        else:
            try:
                with Image.open(candidate_path) as handle:
                    candidate_hash_obj = builder(handle.convert("RGB"), hash_size)
            except OSError:
                continue

            scanned_count += 1
            distance = int(target_hash_obj - candidate_hash_obj)
            if distance > max_distance:
                continue

            matches.append(
                {
                    "file_hash": candidate.file_hash,
                    "civitai_uuid": candidate.civitai_uuid,
                    "file_name": candidate.file_name,
                    "file_path": candidate.file_path,
                    "mimetype": candidate.mimetype,
                    "source_url": candidate.source_url,
                    "distance": distance,
                    "distance_type": "hamming",
                    "image_url": f"/image_library/{_normalize_media_url_path(str(candidate.file_path))}",
                }
            )

    matches.sort(
        key=lambda item: (
            float(item.get("distance") or 0.0),
            str(item.get("file_name") or ""),
        )
    )
    limited_matches = matches[:limit]

    return {
        "ok": True,
        "target": {
            "file_hash": target_image.file_hash,
            "civitai_uuid": target_image.civitai_uuid,
            "file_name": target_image.file_name,
            "file_path": target_image.file_path,
            "mimetype": target_image.mimetype,
            "image_url": f"/image_library/{_normalize_media_url_path(str(target_image.file_path))}",
            "algorithm": selected_algorithm,
            "hash_size": hash_size,
            "hex": target_hex,
            "blurhash": target_blurhash,
            "distance_type": (
                "blurhash_mae" if selected_algorithm == "blurhash" else "hamming"
            ),
        },
        "search": {
            "max_distance": max_distance,
            "max_candidates": max_candidates,
            "limit": limit,
            "scanned_count": scanned_count,
            "match_count": len(matches),
            "returned_count": len(limited_matches),
            "skipped_missing_blurhash": skipped_missing_blurhash,
        },
        "matches": limited_matches,
    }


def get_civitai_model_prototype(
    image_id: int,
    catalog_url: Optional[str] = Query(default=None),
    checkpoints_url: Optional[str] = Query(default=None),
    loras_url: Optional[str] = Query(default=None),
    include_full_catalog_raw: bool = Query(default=False),
):
    local_catalog = model_reference_service.fetch_local_catalog(
        catalog_url=catalog_url,
        checkpoints_url=checkpoints_url,
        loras_url=loras_url,
        include_full_raw=include_full_catalog_raw,
    )
    generation_payload = _build_generation_prototype_civitai_payload(image_id)
    return model_reference_service.build_item_payload(
        generation_payload,
        local_catalog=local_catalog,
    )


def get_local_model_prototype(
    file_hash: str,
    catalog_url: Optional[str] = Query(default=None),
    checkpoints_url: Optional[str] = Query(default=None),
    loras_url: Optional[str] = Query(default=None),
    include_full_catalog_raw: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    image = (
        db.query(ImageModel)
        .filter(ImageModel.file_hash == file_hash)
        .filter(_active_image_filter())
        .first()
    )
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found.")
    local_catalog = model_reference_service.fetch_local_catalog(
        catalog_url=catalog_url,
        checkpoints_url=checkpoints_url,
        loras_url=loras_url,
        include_full_raw=include_full_catalog_raw,
    )
    generation_payload = _build_generation_prototype_local_payload(image)
    return model_reference_service.build_item_payload(
        generation_payload,
        local_catalog=local_catalog,
    )


def get_model_catalog_prototype(
    image_limit: int = Query(default=250, ge=1, le=2000),
    catalog_url: Optional[str] = Query(default=None),
    checkpoints_url: Optional[str] = Query(default=None),
    loras_url: Optional[str] = Query(default=None),
    include_full_catalog_raw: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    local_catalog = model_reference_service.fetch_local_catalog(
        catalog_url=catalog_url,
        checkpoints_url=checkpoints_url,
        loras_url=loras_url,
        include_full_raw=include_full_catalog_raw,
    )
    return model_reference_service.build_library_catalog_payload(
        db,
        image_limit=image_limit,
        local_catalog=local_catalog,
    )


def get_model_prototype_local_match_preview(
    display_name: str = Query(..., min_length=1),
    resource_type: Optional[str] = Query(default=None),
    file_path: Optional[str] = Query(default=None),
    file_name: Optional[str] = Query(default=None),
    model_name: Optional[str] = Query(default=None),
    version_name: Optional[str] = Query(default=None),
    civitai_model_id: Optional[int] = Query(default=None),
    civitai_model_version_id: Optional[int] = Query(default=None),
    catalog_url: Optional[str] = Query(default=None),
    checkpoints_url: Optional[str] = Query(default=None),
    loras_url: Optional[str] = Query(default=None),
    checkpoints_metadata_url: Optional[str] = Query(default=None),
    loras_metadata_url: Optional[str] = Query(default=None),
):
    payload = model_reference_service.fetch_local_model_preview(
        search_name=display_name,
        resource_type=resource_type,
        file_path=file_path,
        file_name=file_name,
        model_name=model_name,
        version_name=version_name,
        civitai_model_id=civitai_model_id,
        civitai_model_version_id=civitai_model_version_id,
        catalog_url=catalog_url,
        checkpoints_url=checkpoints_url,
        loras_url=loras_url,
        checkpoints_metadata_url=checkpoints_metadata_url,
        loras_metadata_url=loras_metadata_url,
    )
    if not payload.get("ok"):
        detail = str(payload.get("error") or "No preview metadata found.")
        if "configured" in detail.lower():
            raise HTTPException(status_code=400, detail=detail)
        if "could not fetch preview data" in detail.lower():
            raise HTTPException(status_code=502, detail=detail)
        raise HTTPException(status_code=404, detail=detail)
    return payload


def trigger_model_prototype_local_model_download(payload: dict = Body(...)):
    request_payload = payload if isinstance(payload, dict) else {}
    result = model_reference_service.download_local_model(
        civitai_model_id=request_payload.get("civitai_model_id"),
        civitai_model_version_id=request_payload.get("civitai_model_version_id"),
        resource_type=request_payload.get("resource_type"),
        relative_path=str(request_payload.get("relative_path") or ""),
        use_default_paths=bool(request_payload.get("use_default_paths", False)),
        download_id=request_payload.get("download_id"),
        catalog_url=request_payload.get("catalog_url"),
        checkpoints_url=request_payload.get("checkpoints_url"),
        loras_url=request_payload.get("loras_url"),
    )
    if not result.get("ok"):
        detail = str(result.get("error") or "Could not start local model download.")
        if "could not start lora manager download" in detail.lower():
            raise HTTPException(status_code=502, detail=detail)
        raise HTTPException(status_code=400, detail=detail)
    return result


def retry_failed_items_from_task(task_id: str):
    try:
        source_task = task_manager.get_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found")

    retry_items, skipped_items = _get_retry_failed_items_from_task(source_task)
    if not retry_items:
        skipped_hint = ""
        if skipped_items:
            preview = ", ".join(skipped_items[:3])
            extra = (
                "" if len(skipped_items) <= 3 else f" (+{len(skipped_items) - 3} more)"
            )
            skipped_hint = f" Skipped failed item keys: {preview}{extra}."
        raise HTTPException(
            status_code=400,
            detail=(
                "No retryable failed items found for this task. "
                "The task may have no failed items, or its failed items were not marked "
                "with a supported CivitAI import key format."
                f"{skipped_hint}"
            ),
        )

    task = task_manager.create_task(
        kind="civitai-retry-failed-items",
        title=f"Retry failed items from job {task_id}",
        metadata={
            "source_task_id": task_id,
            "failed_items_count": len(retry_items),
            "skipped_item_keys": skipped_items,
        },
        runner=lambda context: _run_retry_failed_items_job(
            context, source_task=source_task
        ),
    )
    return {
        "message": "Retry failed items task queued.",
        "task": task,
    }


def retry_missing_failures_from_task(task_id: str):
    """Retry items that failed due to missing/unavailable (404/deleted) conditions."""
    try:
        source_task = task_manager.get_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found")

    missing_items = source_task.get("missing_failures", [])
    if not missing_items:
        raise HTTPException(
            status_code=400,
            detail="No missing/unavailable failures found for this task.",
        )

    image_ids = []
    for entry in missing_items:
        item_data = entry.get("item_data") if isinstance(entry, dict) else None
        if isinstance(item_data, dict):
            iid = item_data.get("image_id")
            if isinstance(iid, int):
                image_ids.append(iid)
    if not image_ids:
        raise HTTPException(
            status_code=400,
            detail="No retryable image IDs found in missing failures.",
        )

    task = task_manager.create_task(
        kind="civitai-retry-missing",
        title=f"Retry {len(image_ids)} missing items from job {task_id}",
        metadata={
            "source_task_id": task_id,
            "retry_image_ids": image_ids,
        },
        runner=lambda context: _run_retry_specific_image_ids_job(
            context,
            image_ids=image_ids,
            source_task_id=task_id,
        ),
    )
    return {
        "message": f"Retry missing items task queued ({len(image_ids)} items).",
        "task": task,
    }


def retry_temporary_failures_from_task(task_id: str):
    """Retry items that failed due to temporary errors (timeout, rate-limit, etc.)."""
    try:
        source_task = task_manager.get_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found")

    temp_items = source_task.get("temporary_failures", [])
    if not temp_items:
        raise HTTPException(
            status_code=400,
            detail="No temporary failures found for this task.",
        )

    image_ids = []
    for entry in temp_items:
        item_data = entry.get("item_data") if isinstance(entry, dict) else None
        if isinstance(item_data, dict):
            iid = item_data.get("image_id")
            if isinstance(iid, int):
                image_ids.append(iid)
    if not image_ids:
        raise HTTPException(
            status_code=400,
            detail="No retryable image IDs found in temporary failures.",
        )

    task = task_manager.create_task(
        kind="civitai-retry-temporary",
        title=f"Retry {len(image_ids)} temporary-failed items from job {task_id}",
        metadata={
            "source_task_id": task_id,
            "retry_image_ids": image_ids,
        },
        runner=lambda context: _run_retry_specific_image_ids_job(
            context,
            image_ids=image_ids,
            source_task_id=task_id,
        ),
    )
    return {
        "message": f"Retry temporary-failed items task queued ({len(image_ids)} items).",
        "task": task,
    }


def read_images(
    request: Request,
    response: Response,
    skip: int = 0,
    limit: int = 10,
    group_variants: bool = Query(default=True),
    sort_by: Literal["first_added", "last_added", "civitai_image_id"] = "first_added",
    search: Optional[str] = None,
    # --- Unified filter params (preferred) ---
    included: Optional[list[str]] = Query(default=None),
    excluded: Optional[list[str]] = Query(default=None),
    hidden: Optional[list[str]] = Query(default=None),
    missing: Optional[list[str]] = Query(default=None),
    # --- Legacy filter params (deprecated, kept for backward compat) ---
    generation_software: Optional[list[str]] = Query(default=None),
    source_site: Optional[list[str]] = Query(default=None),
    mimetype: Optional[list[str]] = Query(default=None),
    nsfw_rating: Optional[list[str]] = Query(default=None),
    nsfw_safety: Optional[list[str]] = Query(default=None),
    artist_name: Optional[list[str]] = Query(default=None),
    collection_name: Optional[list[str]] = Query(default=None),
    exclude_artist_name: Optional[list[str]] = Query(default=None),
    exclude_collection_name: Optional[list[str]] = Query(default=None),
    a1111_hires: Optional[list[str]] = Query(default=None),
    a1111_regional_prompter: Optional[list[str]] = Query(default=None),
    a1111_adetailer: Optional[list[str]] = Query(default=None),
    include_tag: Optional[list[str]] = Query(default=None),
    exclude_tag: Optional[list[str]] = Query(default=None),
    variant_group_id: Optional[list[int]] = Query(default=None),
    cursor: Optional[int] = None,
    missing_data: Optional[list[str]] = Query(default=None),
    missing_source: Optional[list[str]] = Query(default=None),
    db: Session = Depends(get_db),
):
    """
    Returns a list of images with their associated artist and license info.
    Uses ImageData class to encapsulate and display image metadata.

    Supports both legacy offset pagination (*skip*) and cursor-based keyset
    pagination (*cursor*).  When *cursor* is supplied, it takes precedence:
    the server over-fetches DB rows to guarantee a full page after variant
    grouping, and returns ``X-Next-Cursor`` for the next page boundary.

    **Filter modes**:
    - *Unified* (preferred): pass ``included``, ``excluded``, ``hidden``,
      ``missing`` as typed CGI terms (e.g. ``included=tag:portrait``).
    - *Legacy*: pass individual filter params (``include_tag``,
      ``nsfw_rating``, etc.).  Deprecated; will be removed in a future
      release.
    """
    # Detect which filter mode is active.
    _has_unified = bool(included or excluded or hidden or missing)

    # Build cache key from whichever params are active.
    if _has_unified:
        cache_key = _build_search_cache_key(
            "images",
            payload={
                "skip": int(skip),
                "limit": int(limit),
                "group_variants": bool(group_variants),
                "sort_by": str(sort_by),
                "search": str(search or "").strip().lower(),
                "included": _normalize_cache_list(included),
                "excluded": _normalize_cache_list(excluded),
                "hidden": _normalize_cache_list(hidden),
                "missing": _normalize_cache_list(missing),
                "variant_group_id": _normalize_cache_list(variant_group_id),
                "cursor": cursor,
            },
        )
    else:
        cache_key = _build_search_cache_key(
            "images",
            payload={
                "skip": int(skip),
                "limit": int(limit),
                "group_variants": bool(group_variants),
                "sort_by": str(sort_by),
                "search": str(search or "").strip().lower(),
                "generation_software": _normalize_cache_list(generation_software),
                "source_site": _normalize_cache_list(source_site),
                "mimetype": _normalize_cache_list(mimetype),
                "nsfw_rating": _normalize_cache_list(nsfw_rating),
                "nsfw_safety": _normalize_cache_list(nsfw_safety),
                "artist_name": _normalize_cache_list(artist_name),
                "collection_name": _normalize_cache_list(collection_name),
                "exclude_artist_name": _normalize_cache_list(exclude_artist_name),
                "exclude_collection_name": _normalize_cache_list(exclude_collection_name),
                "a1111_hires": _normalize_cache_list(a1111_hires),
                "a1111_regional_prompter": _normalize_cache_list(a1111_regional_prompter),
                "a1111_adetailer": _normalize_cache_list(a1111_adetailer),
                "include_tag": _normalize_cache_list(include_tag),
                "exclude_tag": _normalize_cache_list(exclude_tag),
                "variant_group_id": _normalize_cache_list(variant_group_id),
                "cursor": cursor,
                "missing_data": _normalize_cache_list(missing_data),
                "missing_source": _normalize_cache_list(missing_source),
            },
        )
    cache_headers = _build_json_cache_headers(cache_key, gallery=True)
    for header_name, header_value in cache_headers.items():
        response.headers[header_name] = header_value
    if _should_return_json_not_modified(request, cache_headers):
        return Response(status_code=304, headers=cache_headers)

    cached_payload = _search_cache_get(cache_key)
    if isinstance(cached_payload, dict):
        response.headers["X-Filtered-Count"] = str(
            int(cached_payload.get("filtered_count") or 0)
        )
        next_cursor_val = cached_payload.get("next_cursor")
        if next_cursor_val is not None:
            response.headers["X-Next-Cursor"] = str(int(next_cursor_val))
        items = cached_payload.get("items")
        if isinstance(items, list):
            return items

    if _has_unified:
        display_items, filtered_count, next_cursor = _load_display_image_items_unified(
            db,
            query_service=image_query_service,
            included=included or [],
            excluded=excluded or [],
            hidden=hidden or [],
            missing=missing or [],
            sort_by=sort_by,
            search=search,
            variant_group_id=variant_group_id,
            group_variants=group_variants,
            skip=skip,
            limit=limit,
            cursor=cursor,
        )
    else:
        display_items, filtered_count, next_cursor = _load_display_image_items(
            db,
            sort_by=sort_by,
            search=search,
            generation_software=generation_software,
            source_site=source_site,
            mimetype=mimetype,
            nsfw_rating=nsfw_rating,
            nsfw_safety=nsfw_safety,
            artist_name=artist_name,
            collection_name=collection_name,
            exclude_artist_name=exclude_artist_name,
            exclude_collection_name=exclude_collection_name,
            a1111_hires=a1111_hires,
            a1111_regional_prompter=a1111_regional_prompter,
            a1111_adetailer=a1111_adetailer,
            include_tag=include_tag,
            exclude_tag=exclude_tag,
            variant_group_id=variant_group_id,
            group_variants=group_variants,
            skip=skip,
            limit=limit,
            cursor=cursor,
            missing_data=missing_data,
            missing_source=missing_source,
        )
    response.headers["X-Filtered-Count"] = str(filtered_count)
    if next_cursor is not None:
        response.headers["X-Next-Cursor"] = str(int(next_cursor))

    _search_cache_put(
        cache_key,
        {
            "filtered_count": filtered_count,
            "items": display_items,
            "next_cursor": next_cursor,
        },
    )

    return display_items


def read_images_state(
    # Unified filter params (preferred).
    included: Optional[list[str]] = Query(default=None),
    excluded: Optional[list[str]] = Query(default=None),
    hidden: Optional[list[str]] = Query(default=None),
    missing: Optional[list[str]] = Query(default=None),
    # Legacy param (deprecated).
    nsfw_rating: Optional[list[str]] = Query(default=None),
    db: Session = Depends(get_db),
):
    """Return lightweight image-library state for polling-based UI refresh logic.

    Supports both unified (``included``/``excluded``/``hidden``/``missing``)
    and legacy ``nsfw_rating`` params.
    """
    _has_unified = bool(included or excluded or hidden or missing)
    base_query = db.query(ImageModel).filter(_active_image_filter())

    if _has_unified:
        parsed = parse_gallery_filter(included or [], excluded or [], hidden or [], missing or [])
        filtered_query, constrained_ids = apply_gallery_filter(
            base_query, parsed, db, image_query_service,
        )
        if constrained_ids is not None:
            visible_ids = constrained_ids
            total_count = len(visible_ids)
            latest_row = (
                db.query(ImageModel.id)
                .filter(_active_image_filter(), ImageModel.id.in_(visible_ids))
                .order_by(ImageModel.id.desc())
                .first()
            )
        else:
            total_count = filtered_query.count()
            latest_row = (
                filtered_query.with_entities(ImageModel.id)
                .order_by(ImageModel.id.desc())
                .first()
            )
    elif nsfw_rating:
        nsfw_filtered = _filter_image_ids_by_nsfw_ratings(base_query, nsfw_rating)
        if nsfw_filtered is not None:
            visible_ids = set(nsfw_filtered)
            total_count = len(visible_ids)
            latest_row = (
                db.query(ImageModel.id)
                .filter(_active_image_filter(), ImageModel.id.in_(visible_ids))
                .order_by(ImageModel.id.desc())
                .first()
            )
        else:
            total_count = base_query.count()
            latest_row = (
                db.query(ImageModel.id)
                .filter(_active_image_filter())
                .order_by(ImageModel.id.desc())
                .first()
            )
    else:
        total_count = base_query.count()
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
        "warnings": _get_runtime_warnings(),
        "capabilities": _get_media_capabilities(),
    }


def read_image_keys(
    request: Request,
    response: Response,
    group_variants: bool = Query(default=True),
    search: Optional[str] = None,
    # Unified filter params (preferred).
    included: Optional[list[str]] = Query(default=None),
    excluded: Optional[list[str]] = Query(default=None),
    hidden: Optional[list[str]] = Query(default=None),
    missing: Optional[list[str]] = Query(default=None),
    # Legacy filter params (deprecated).
    generation_software: Optional[list[str]] = Query(default=None),
    source_site: Optional[list[str]] = Query(default=None),
    mimetype: Optional[list[str]] = Query(default=None),
    nsfw_rating: Optional[list[str]] = Query(default=None),
    nsfw_safety: Optional[list[str]] = Query(default=None),
    artist_name: Optional[list[str]] = Query(default=None),
    collection_name: Optional[list[str]] = Query(default=None),
    exclude_artist_name: Optional[list[str]] = Query(default=None),
    exclude_collection_name: Optional[list[str]] = Query(default=None),
    a1111_hires: Optional[list[str]] = Query(default=None),
    a1111_regional_prompter: Optional[list[str]] = Query(default=None),
    a1111_adetailer: Optional[list[str]] = Query(default=None),
    include_tag: Optional[list[str]] = Query(default=None),
    exclude_tag: Optional[list[str]] = Query(default=None),
    variant_group_id: Optional[list[int]] = Query(default=None),
    missing_data: Optional[list[str]] = Query(default=None),
    missing_source: Optional[list[str]] = Query(default=None),
    db: Session = Depends(get_db),
):
    """Return filtered image keys (file hashes or group keys).

    Supports both unified (``included``/``excluded``/``hidden``/``missing``)
    and legacy filter params.
    """
    _has_unified = bool(included or excluded or hidden or missing)

    if _has_unified:
        cache_key = _build_search_cache_key(
            "image_keys",
            payload={
                "group_variants": bool(group_variants),
                "search": str(search or "").strip().lower(),
                "included": _normalize_cache_list(included),
                "excluded": _normalize_cache_list(excluded),
                "hidden": _normalize_cache_list(hidden),
                "missing": _normalize_cache_list(missing),
            },
        )
    else:
        cache_key = _build_search_cache_key(
            "image_keys",
            payload={
                "group_variants": bool(group_variants),
                "search": str(search or "").strip().lower(),
                "generation_software": _normalize_cache_list(generation_software),
                "source_site": _normalize_cache_list(source_site),
                "mimetype": _normalize_cache_list(mimetype),
                "nsfw_rating": _normalize_cache_list(nsfw_rating),
                "nsfw_safety": _normalize_cache_list(nsfw_safety),
                "artist_name": _normalize_cache_list(artist_name),
                "collection_name": _normalize_cache_list(collection_name),
                "exclude_artist_name": _normalize_cache_list(exclude_artist_name),
                "exclude_collection_name": _normalize_cache_list(exclude_collection_name),
                "a1111_hires": _normalize_cache_list(a1111_hires),
                "a1111_regional_prompter": _normalize_cache_list(a1111_regional_prompter),
                "a1111_adetailer": _normalize_cache_list(a1111_adetailer),
                "include_tag": _normalize_cache_list(include_tag),
                "exclude_tag": _normalize_cache_list(exclude_tag),
            },
        )
    cache_headers = _build_json_cache_headers(cache_key, gallery=True)
    for header_name, header_value in cache_headers.items():
        response.headers[header_name] = header_value
    if _should_return_json_not_modified(request, cache_headers):
        return Response(status_code=304, headers=cache_headers)

    cached_keys = _search_cache_get(cache_key)
    if isinstance(cached_keys, list):
        return [str(file_hash) for file_hash in cached_keys if file_hash]

    if _has_unified:
        keys = _load_filtered_image_keys_unified(
            db,
            query_service=image_query_service,
            included=included or [],
            excluded=excluded or [],
            hidden=hidden or [],
            missing=missing or [],
            search=search,
            group_variants=group_variants,
        )
    else:
        keys = _load_filtered_image_keys(
            db,
            search=search,
            generation_software=generation_software,
            source_site=source_site,
            mimetype=mimetype,
            nsfw_rating=nsfw_rating,
            nsfw_safety=nsfw_safety,
            artist_name=artist_name,
            collection_name=collection_name,
            exclude_artist_name=exclude_artist_name,
            exclude_collection_name=exclude_collection_name,
            a1111_hires=a1111_hires,
            a1111_regional_prompter=a1111_regional_prompter,
            a1111_adetailer=a1111_adetailer,
            include_tag=include_tag,
            exclude_tag=exclude_tag,
            group_variants=group_variants,
        )
    _search_cache_put(cache_key, keys)
    return keys


def get_image_detail(image_id: int, db: Session = Depends(get_db)):
    """Return full image detail for a single image including blob fields.

    The gallery list endpoint (/api/images/) strips exif_data, civitai_data,
    and json_metadata to keep the payload lean.  This endpoint restores them
    for the selected-image detail panel.  Identified by integer primary key
    (base_image_id in gallery responses).
    """
    image = (
        db.query(ImageModel)
        .filter(ImageModel.id == image_id, _active_image_filter())
        .first()
    )
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found")

    detail = ImageData.from_db_record(image).to_dict()
    detail["id"] = image.id
    detail["collection_names"] = [c.name for c in image.collections]
    detail["collection_ids"] = [c.id for c in image.collections]
    detail["artist_name"] = image.artist.name if image.artist is not None else None
    detail["artist_deleted"] = (
        image.artist.civitai_user_deleted if image.artist is not None else None
    )
    detail["artist_original_name"] = (
        image.artist.civitai_user_original_name if image.artist is not None else None
    )
    nsfw_ratings = _read_nsfw_ratings_for_image(image)
    detail["nsfw_ratings"] = nsfw_ratings
    detail["nsfw_rating"] = nsfw_ratings[0] if nsfw_ratings else None
    detail["user_nsfw_rating"] = image.user_nsfw_rating
    detail["user_nsfw_safety_class"] = image.user_nsfw_safety_class
    db_user_tags = getattr(image, "user_tags", None)
    if isinstance(db_user_tags, list) and db_user_tags:
        detail["user_tags"] = db_user_tags
    db_user_neg_tags = getattr(image, "user_negative_tags", None)
    if isinstance(db_user_neg_tags, list) and db_user_neg_tags:
        detail["user_negative_tags"] = db_user_neg_tags

    # Query CivitAI tags from image_concept_observations (post-backfill data)
    civitai_tag_rows = (
        db.query(AuthorityTerm.external_name)
        .join(
            ImageConceptObservation,
            ImageConceptObservation.authority_term_id == AuthorityTerm.id,
        )
        .join(TagAuthority, TagAuthority.id == AuthorityTerm.authority_id)
        .filter(
            ImageConceptObservation.image_id == image_id,
            TagAuthority.name == "civitai",
        )
        .order_by(AuthorityTerm.external_name.asc())
        .all()
    )
    detail["civitai_tags"] = [row[0] for row in civitai_tag_rows if row[0]]

    return detail


def update_image(
    file_hash: str, payload: ImageUpdateRequest, db: Session = Depends(get_db)
):
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
            artist_obj = (
                db.query(Artist).filter(Artist.name == normalized_artist).first()
            )
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

    if payload.user_tags is not None:
        normalized_user_tags: list[str] = []
        seen_user_tags: set[str] = set()
        for raw_tag in payload.user_tags:
            normalized_tag = str(raw_tag or "").strip()
            if not normalized_tag or normalized_tag.lower() in seen_user_tags:
                continue
            seen_user_tags.add(normalized_tag.lower())
            normalized_user_tags.append(normalized_tag)
        sidecar_additional_data["user_tags"] = normalized_user_tags
        update_values[ImageModel.user_tags] = normalized_user_tags

    if payload.user_negative_tags is not None:
        normalized_negative_tags: list[str] = []
        seen_negative_tags: set[str] = set()
        for raw_tag in payload.user_negative_tags:
            normalized_tag = str(raw_tag or "").strip().lower()
            if not normalized_tag or normalized_tag in seen_negative_tags:
                continue
            seen_negative_tags.add(normalized_tag)
            normalized_negative_tags.append(normalized_tag)
        sidecar_additional_data["user_negative_tags"] = normalized_negative_tags
        update_values[ImageModel.user_negative_tags] = normalized_negative_tags

    if payload.user_nsfw_rating is not None:
        raw_rating = payload.user_nsfw_rating.strip().lower()
        if raw_rating == "":
            update_values[ImageModel.user_nsfw_rating] = None
        elif raw_rating in {"pg", "pg13", "r", "x", "xxx"}:
            update_values[ImageModel.user_nsfw_rating] = raw_rating
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid user_nsfw_rating {payload.user_nsfw_rating!r}. "
                "Allowed values: pg, pg13, r, x, xxx (or empty string to clear).",
            )

    if payload.user_nsfw_safety_class is not None:
        raw_class = payload.user_nsfw_safety_class.strip().lower()
        if raw_class == "":
            update_values[ImageModel.user_nsfw_safety_class] = None
        elif raw_class in {"safe", "mature", "explicit"}:
            update_values[ImageModel.user_nsfw_safety_class] = raw_class
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid user_nsfw_safety_class {payload.user_nsfw_safety_class!r}. "
                "Allowed values: safe, mature, explicit (or empty string to clear).",
            )

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

        # Sync user-tag observations: create/upsert authority_terms and
        # observations for positive user_tags (is_present=True) and negative
        # user_tags (is_present=False).  Remove stale observations for tags
        # no longer in the list.
        _sync_user_tag_observations(
            db,
            image_id=image.id,
            user_tags=(
                update_values.get(ImageModel.user_tags)
                if ImageModel.user_tags in update_values
                else image.user_tags
            ),
            user_negative_tags=(
                update_values.get(ImageModel.user_negative_tags)
                if ImageModel.user_negative_tags in update_values
                else getattr(image, "user_negative_tags", None)
            ),
            touched_fields=set(update_values.keys()),
        )

        db.refresh(image)
        processor = ImageProcessor(str(image_path), db, IMAGE_LIBRARY_PATH)
        processor.save_json_metadata(
            image_path,
            image,
            additional_data=(
                sidecar_additional_data if sidecar_additional_data else None
            ),
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
        # Prefer DB columns for user_tags and user_negative_tags (always
        # up-to-date after the commit above).
        response_user_tags: list[str] = (
            list(image.user_tags) if isinstance(image.user_tags, list) else []
        )
        response_user_negative_tags: list[str] = (
            list(image.user_negative_tags)
            if isinstance(image.user_negative_tags, list)
            else []
        )
        sidecar_path = (Path(IMAGE_LIBRARY_PATH) / str(image.file_path)).with_suffix(
            ".json"
        )
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
        raise HTTPException(
            status_code=500, detail=f"Failed to update image metadata: {e}"
        )

    return {
        "message": "Image metadata updated",
        "file_hash": image.file_hash,
        "source_url": image.source_url,
        "source_site": image.source_site,
        "artist_id": image.artist_id,
        "artist_name": image.artist.name if image.artist is not None else None,
        "artist_profile": sidecar_artist_profile,
        "user_tags": response_user_tags,
        "user_negative_tags": response_user_negative_tags,
        "user_nsfw_rating": image.user_nsfw_rating,
        "user_nsfw_safety_class": image.user_nsfw_safety_class,
    }


def get_image_video_poster(
    file_hash: str, request: Request, db: Session = Depends(get_db)
):
    image = db.query(ImageModel).filter(ImageModel.file_hash == file_hash).first()
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found")

    image_path = Path(IMAGE_LIBRARY_PATH) / str(image.file_path)
    if not image_path.exists() or not image_path.is_file():
        raise HTTPException(status_code=404, detail="Image file not found on disk")

    mimetype = (image.mimetype or "").lower()
    is_video = (
        mimetype.startswith("video/")
        or image_path.suffix.lower() in _VIDEO_FILE_SUFFIXES
    )
    if not is_video:
        raise HTTPException(status_code=400, detail="Image is not a video asset")

    poster_path = ensure_video_poster(image_path, IMAGE_RESOURCES_PATH)
    if poster_path is None or not poster_path.exists():
        raise HTTPException(status_code=404, detail="Video poster unavailable")

    cache_headers = _build_media_cache_headers(poster_path)
    if _should_return_not_modified(request, poster_path, cache_headers):
        return Response(status_code=304, headers=cache_headers)

    return FileResponse(
        str(poster_path), media_type="image/jpeg", headers=cache_headers
    )


def get_image_video_thumbnail(
    file_hash: str, request: Request, db: Session = Depends(get_db)
):
    image = db.query(ImageModel).filter(ImageModel.file_hash == file_hash).first()
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found")

    image_path = Path(IMAGE_LIBRARY_PATH) / str(image.file_path)
    if not image_path.exists() or not image_path.is_file():
        raise HTTPException(status_code=404, detail="Image file not found on disk")

    mimetype = (image.mimetype or "").lower()
    is_video = (
        mimetype.startswith("video/")
        or image_path.suffix.lower() in _VIDEO_FILE_SUFFIXES
    )
    if not is_video:
        raise HTTPException(status_code=400, detail="Image is not a video asset")

    thumbnail_path = ensure_video_thumbnail(image_path, IMAGE_RESOURCES_PATH)
    if thumbnail_path is None or not thumbnail_path.exists():
        raise HTTPException(status_code=404, detail="Video thumbnail unavailable")

    cache_headers = _build_media_cache_headers(thumbnail_path)
    if _should_return_not_modified(request, thumbnail_path, cache_headers):
        return Response(status_code=304, headers=cache_headers)

    return FileResponse(
        str(thumbnail_path),
        media_type=get_video_thumbnail_media_type(thumbnail_path),
        headers=cache_headers,
    )


def repair_image_file(file_hash: str, db: Session = Depends(get_db)):
    """Repair a media record by normalizing metadata/resources and replacing bad media when needed."""
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
        # File is missing from disk. Attempt CivitAI re-download before giving up.
        civitai_image_id_missing: Optional[int] = None
        if isinstance(image.source_url, str) and is_civitai_image_url(image.source_url):
            civitai_image_id_missing = extract_civitai_image_id(image.source_url)

        if civitai_image_id_missing is None:
            raise HTTPException(status_code=404, detail="Image file is missing on disk")

        _actions: list[str] = []
        _issues: list[str] = ["Image file was missing on disk."]
        _warnings: list[str] = []
        _civitai_target_missing: Optional[dict[str, Any]] = None
        try:
            _civitai_target_missing = _resolve_civitai_image_target(
                CivitaiAPI.get_instance(), civitai_image_id_missing, strict=False
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Image file is missing on disk and CivitAI re-download failed: {exc}",
            )

        _temp_path: Optional[Path] = None
        _mismatch_temp_path: Optional[Path] = None
        try:
            _download_result = _download_civitai_image_with_validation(
                image_id=civitai_image_id_missing,
                target=_civitai_target_missing,
            )
            _temp_path = _download_result.temp_path
            _mismatch_temp_path = _download_result.mismatch_static_temp_path
            try:
                _dl_processor = ImageProcessor(str(_temp_path), db, IMAGE_LIBRARY_PATH)
            except Exception as exc:
                raise HTTPException(
                    status_code=502,
                    detail=f"Downloaded file from CivitAI is not a usable media file: {exc}",
                )
            _dl_hash = _dl_processor.file_hash

            if _dl_hash == image.file_hash:
                # Same content as original record — restore the file in-place.
                # We deliberately bypass ingest_uploaded_file here because it
                # expects the existing library path to exist for housekeeping.
                _actual_ext = (
                    ImageProcessor.mime_to_extension(_dl_processor.mimetype)
                    or _temp_path.suffix.lower()
                    or ".png"
                )
                _dest_name = f"{image.file_hash}{_actual_ext}"
                _dest_path = Path(IMAGE_LIBRARY_PATH) / _dest_name
                shutil.copy2(str(_temp_path), str(_dest_path))

                image.file_path = _dest_name
                image.mimetype = _dl_processor.mimetype
                image.file_size = _dl_processor.file_size
                image.width = _dl_processor.width
                image.height = _dl_processor.height
                image.date_modified = _dl_processor.date_modified
                image.exif_data = _dl_processor.exif_data

                _dl_processor.save_json_metadata(_dest_path, image)

                _civitai_backfilled = _ensure_civitai_metadata_for_existing_image(
                    db=db,
                    image=image,
                    source_url=str(_civitai_target_missing["source_url"]),
                )
                if _civitai_backfilled:
                    _actions.append("Backfilled missing CivitAI metadata.")

                if _normalize_mime_type(image.mimetype).startswith("video/"):
                    _poster = ensure_video_poster(_dest_path, IMAGE_RESOURCES_PATH)
                    _thumb = ensure_video_thumbnail(_dest_path, IMAGE_RESOURCES_PATH)
                    if _poster is not None:
                        _actions.append(f"Rebuilt video poster {_poster.name}.")
                    if _thumb is not None:
                        _actions.append(f"Rebuilt video thumbnail {_thumb.name}.")

                _actions.append(
                    "Re-downloaded missing file from CivitAI source and restored to library."
                )
                _commit_with_lock_retry(
                    db, context=f"Repair commit for missing image {file_hash}"
                )
                return _collect_repair_result(
                    image=image,
                    actions_taken=_actions,
                    issues_found=_issues,
                    warnings=_warnings,
                    created_new_image=False,
                    repaired_image=None,
                    png_inspection=None,
                )
            else:
                # Re-downloaded content has a different hash (CivitAI re-encoded or
                # served a variant). Use the standard replacement pipeline which will
                # create a new record and tombstone the old one.
                _prepared = _PreparedCivitaiImport(
                    image_id=civitai_image_id_missing,
                    image_url=str(_civitai_target_missing["image_url"]),
                    mime_type=_civitai_target_missing.get("mime_type"),
                    declared_file_size=_civitai_target_missing.get(
                        "declared_file_size"
                    ),
                    preview_image_url=_civitai_target_missing.get("preview_image_url"),
                    original_filename=str(_civitai_target_missing["original_filename"]),
                    artist_name=_civitai_target_missing.get("artist_name"),
                    source_url=str(_civitai_target_missing["source_url"]),
                    temp_path=_temp_path,
                    civitai_uuid=_civitai_target_missing.get("civitai_uuid"),
                    civitai_hash=_civitai_target_missing.get("civitai_hash"),
                    effective_image_url=_download_result.selected_url,
                    mismatch_static_temp_path=_download_result.mismatch_static_temp_path,
                    mismatch_source_url=_download_result.mismatch_source_url,
                    mismatch_mime_type=_download_result.mismatch_mime_type,
                    mismatch_file_hash=_download_result.mismatch_file_hash,
                    author_id=_civitai_target_missing.get("author_id"),
                    author_deleted=_civitai_target_missing.get("author_deleted", False),
                    author_original_name=_civitai_target_missing.get(
                        "author_original_name"
                    ),
                    civitai_post_id=_civitai_target_missing.get("civitai_post_id"),
                    civitai_post_title=_civitai_target_missing.get("civitai_post_title"),
                    civitai_post_index=_civitai_target_missing.get("civitai_post_index"),
                )
                _replacement = _replace_image_with_uploaded_file(
                    db,
                    image=image,
                    uploaded_file_path=_temp_path,
                    original_filename=str(_civitai_target_missing["original_filename"]),
                    replacement_reason="restored_missing_file_from_civitai",
                    artist_name=_civitai_target_missing.get("artist_name"),
                    source_url=str(_civitai_target_missing["source_url"]),
                    license_id=image.license_id,
                    prepared_variant=_prepared,
                )
                _repaired = _replacement.get("repaired_image")
                _created_new = bool(_replacement.get("created_new_image"))
                if _repaired is not None:
                    _repaired_path = Path(IMAGE_LIBRARY_PATH) / str(_repaired.file_path)
                    if _normalize_mime_type(_repaired.mimetype).startswith("video/"):
                        _poster = ensure_video_poster(
                            _repaired_path, IMAGE_RESOURCES_PATH
                        )
                        _thumb = ensure_video_thumbnail(
                            _repaired_path, IMAGE_RESOURCES_PATH
                        )
                        if _poster is not None:
                            _actions.append(f"Rebuilt video poster {_poster.name}.")
                        if _thumb is not None:
                            _actions.append(f"Rebuilt video thumbnail {_thumb.name}.")
                _actions.append(
                    "Re-downloaded missing file from CivitAI (content changed); created replacement record."
                )
                _commit_with_lock_retry(
                    db, context=f"Repair commit for missing image {file_hash}"
                )
                return _collect_repair_result(
                    image=image,
                    actions_taken=_actions,
                    issues_found=_issues,
                    warnings=_warnings,
                    created_new_image=_created_new,
                    repaired_image=_repaired,
                    png_inspection=None,
                )
        except HTTPException:
            db.rollback()
            raise
        except Exception as exc:
            db.rollback()
            raise HTTPException(
                status_code=500,
                detail=f"Failed to restore missing image from CivitAI: {exc}",
            )
        finally:
            _cleanup_temp_file(_temp_path)
            _cleanup_temp_file(_mismatch_temp_path)

    actions_taken: list[str] = []
    issues_found: list[str] = []
    warnings: list[str] = []
    repaired_image: Optional[ImageModel] = None
    created_new_image = False
    png_inspection: Optional[dict[str, Any]] = None

    try:
        processor = ImageProcessor(str(image_path), db, IMAGE_LIBRARY_PATH)
    except Exception as e:
        raise HTTPException(
            status_code=500, detail=f"Could not inspect image file: {e}"
        )

    actual_mime = _normalize_mime_type(processor.mimetype)
    actual_extension = (
        ImageProcessor.mime_to_extension(processor.mimetype)
        or image_path.suffix.lower()
        or ".jpg"
    )
    db_mime = _normalize_mime_type(image.mimetype)
    sidecar_before = _load_image_sidecar_payload(image)
    sidecar_mime = _normalize_mime_type(sidecar_before.get("mimetype"))

    civitai_target: Optional[dict[str, Any]] = None
    civitai_image_id: Optional[int] = None
    if isinstance(image.source_url, str) and is_civitai_image_url(image.source_url):
        civitai_image_id = extract_civitai_image_id(image.source_url)
        if civitai_image_id is not None:
            try:
                civitai_target = _resolve_civitai_image_target(
                    CivitaiAPI.get_instance(), civitai_image_id, strict=False
                )
            except Exception as exc:
                warnings.append(
                    f"Could not refresh CivitAI metadata during repair: {exc}"
                )

    declared_civitai_mime = (
        _normalize_mime_type(civitai_target.get("mime_type")) if civitai_target else ""
    )
    expected_library_name = f"{image.file_hash}{actual_extension}"
    preferred_file_name = _derive_preferred_file_name(
        image,
        actual_extension=actual_extension,
        civitai_target=civitai_target,
    )

    if db_mime and db_mime != actual_mime:
        issues_found.append(
            f"Database MIME type {db_mime} did not match actual file type {actual_mime}."
        )
    if sidecar_mime and sidecar_mime != actual_mime:
        issues_found.append(
            f"Sidecar MIME type {sidecar_mime} did not match actual file type {actual_mime}."
        )
    if image_path.name != expected_library_name:
        issues_found.append(
            f"Library filename {image_path.name} did not match expected normalized name {expected_library_name}."
        )
    if _looks_like_hashed_display_name(
        image.file_name, file_hash=image.file_hash, file_path=image.file_path
    ):
        issues_found.append(
            "Display filename looked like the library hash/path rather than an original source filename."
        )
    if declared_civitai_mime and declared_civitai_mime != actual_mime:
        issues_found.append(
            f"CivitAI declares {declared_civitai_mime}, but the local file is {actual_mime}."
        )

    if (
        declared_civitai_mime
        and declared_civitai_mime != actual_mime
        and civitai_target
        and civitai_image_id is not None
    ):
        temp_path = None
        mismatch_temp_path = None
        try:
            declared_video_like = declared_civitai_mime.startswith(
                "video/"
            ) or _url_looks_like_video(
                civitai_target.get("image_url")
                if isinstance(civitai_target, dict)
                else None
            )
            archived_variant = None
            if declared_video_like:
                archived_variant = _archive_static_civitai_source_variant(
                    image=image,
                    civitai_image_id=civitai_image_id,
                    expected_source_url=(
                        str(civitai_target.get("source_url") or "")
                        if isinstance(civitai_target, dict)
                        else None
                    ),
                    declared_mime_type=(
                        civitai_target.get("mime_type")
                        if isinstance(civitai_target, dict)
                        else None
                    ),
                )
                if archived_variant:
                    actions_taken.append(
                        "Archived existing static source variant under image_resources/civitai_source_variants."
                    )

            download_result = _download_civitai_image_with_validation(
                image_id=civitai_image_id,
                target=civitai_target,
            )
            temp_path = download_result.temp_path
            mismatch_temp_path = download_result.mismatch_static_temp_path
            prepared_variant = _PreparedCivitaiImport(
                image_id=civitai_image_id,
                image_url=str(civitai_target["image_url"]),
                mime_type=civitai_target.get("mime_type"),
                declared_file_size=civitai_target.get("declared_file_size"),
                preview_image_url=civitai_target.get("preview_image_url"),
                original_filename=str(civitai_target["original_filename"]),
                artist_name=civitai_target.get("artist_name"),
                source_url=str(civitai_target["source_url"]),
                temp_path=temp_path,
                civitai_uuid=civitai_target.get("civitai_uuid"),
                civitai_hash=civitai_target.get("civitai_hash"),
                effective_image_url=download_result.selected_url,
                mismatch_static_temp_path=download_result.mismatch_static_temp_path,
                mismatch_source_url=download_result.mismatch_source_url,
                mismatch_mime_type=download_result.mismatch_mime_type,
                mismatch_file_hash=download_result.mismatch_file_hash,
                author_id=civitai_target.get("author_id"),
                author_deleted=civitai_target.get("author_deleted", False),
                author_original_name=civitai_target.get("author_original_name"),
                civitai_post_id=civitai_target.get("civitai_post_id"),
                civitai_post_title=civitai_target.get("civitai_post_title"),
                civitai_post_index=civitai_target.get("civitai_post_index"),
            )
            replacement = _replace_image_with_uploaded_file(
                db,
                image=image,
                uploaded_file_path=temp_path,
                original_filename=str(civitai_target["original_filename"]),
                replacement_reason="replaced_by_media_repair",
                artist_name=civitai_target.get("artist_name"),
                source_url=str(civitai_target["source_url"]),
                license_id=image.license_id,
                prepared_variant=prepared_variant,
            )
            repaired_image = replacement.get("repaired_image")
            created_new_image = bool(replacement.get("created_new_image"))
            if repaired_image is not None and archived_variant:
                merged_json = (
                    dict(repaired_image.json_metadata)
                    if isinstance(repaired_image.json_metadata, dict)
                    else {}
                )
                merged_json["civitai_source_variant_static"] = archived_variant
                repaired_image.json_metadata = merged_json
                repaired_path_for_sidecar = Path(IMAGE_LIBRARY_PATH) / str(
                    repaired_image.file_path
                )
                if repaired_path_for_sidecar.exists():
                    repaired_processor = ImageProcessor(
                        str(repaired_path_for_sidecar), db, IMAGE_LIBRARY_PATH
                    )
                    repaired_processor.save_json_metadata(
                        repaired_path_for_sidecar,
                        repaired_image,
                        additional_data={
                            "civitai_source_variant_static": archived_variant
                        },
                    )
            if repaired_image is not None:
                repaired_path = Path(IMAGE_LIBRARY_PATH) / str(repaired_image.file_path)
                if _normalize_mime_type(repaired_image.mimetype).startswith("video/"):
                    poster_path = ensure_video_poster(
                        repaired_path, IMAGE_RESOURCES_PATH
                    )
                    thumbnail_path = ensure_video_thumbnail(
                        repaired_path, IMAGE_RESOURCES_PATH
                    )
                    if poster_path is not None:
                        actions_taken.append(
                            f"Rebuilt video poster {poster_path.name}."
                        )
                    if thumbnail_path is not None:
                        actions_taken.append(
                            f"Rebuilt video thumbnail {thumbnail_path.name}."
                        )
            actions_taken.append(
                "Downloaded and ingested the canonical source asset to replace the mismatched local media."
            )
            _commit_with_lock_retry(db, context=f"Repair commit for image {file_hash}")
            return _collect_repair_result(
                image=image,
                actions_taken=actions_taken,
                issues_found=issues_found,
                warnings=warnings,
                created_new_image=created_new_image,
                repaired_image=repaired_image,
                png_inspection=png_inspection,
            )
        except HTTPException:
            db.rollback()
            raise
        except Exception as exc:
            db.rollback()
            raise HTTPException(
                status_code=500,
                detail=f"Failed to replace mismatched CivitAI media: {exc}",
            )
        finally:
            _cleanup_temp_file(temp_path)
            _cleanup_temp_file(mismatch_temp_path)

    current_path = image_path
    if current_path.name != expected_library_name:
        processor.save_to_library()
        current_path = Path(IMAGE_LIBRARY_PATH) / expected_library_name
        image.file_path = expected_library_name
        actions_taken.append(f"Renamed library file to {expected_library_name}.")

    if preferred_file_name and preferred_file_name != image.file_name:
        image.file_name = preferred_file_name
        actions_taken.append(f"Updated display filename to {preferred_file_name}.")

    image.file_size = processor.file_size
    image.width = processor.width
    image.height = processor.height
    image.mimetype = processor.mimetype
    image.date_modified = processor.date_modified
    image.exif_data = processor.exif_data

    if civitai_target is not None and isinstance(image.source_url, str):
        metadata_backfilled = _ensure_civitai_metadata_for_existing_image(
            db=db,
            image=image,
            source_url=image.source_url,
        )
        if metadata_backfilled:
            actions_taken.append("Backfilled missing CivitAI metadata.")

    if actual_mime.startswith("video/"):
        poster_path = ensure_video_poster(current_path, IMAGE_RESOURCES_PATH)
        thumbnail_path = ensure_video_thumbnail(current_path, IMAGE_RESOURCES_PATH)
        if poster_path is not None:
            actions_taken.append(f"Rebuilt video poster {poster_path.name}.")
        else:
            warnings.append("Video poster could not be generated.")
        if thumbnail_path is not None:
            actions_taken.append(f"Rebuilt video thumbnail {thumbnail_path.name}.")
        else:
            warnings.append("Video thumbnail could not be generated.")

        if civitai_target is not None and civitai_image_id is not None:
            before_variant = None
            if isinstance(image.json_metadata, dict):
                before_variant = image.json_metadata.get("civitai_source_variant")
            _preserve_civitai_source_variant(
                db,
                prepared=_PreparedCivitaiImport(
                    image_id=civitai_image_id,
                    image_url=str(civitai_target["image_url"]),
                    mime_type=civitai_target.get("mime_type"),
                    declared_file_size=civitai_target.get("declared_file_size"),
                    preview_image_url=civitai_target.get("preview_image_url"),
                    original_filename=str(civitai_target["original_filename"]),
                    artist_name=civitai_target.get("artist_name"),
                    source_url=str(civitai_target["source_url"]),
                    temp_path=current_path,
                    civitai_uuid=civitai_target.get("civitai_uuid"),
                    civitai_hash=civitai_target.get("civitai_hash"),
                    author_id=civitai_target.get("author_id"),
                    author_deleted=civitai_target.get("author_deleted", False),
                    author_original_name=civitai_target.get("author_original_name"),
                    civitai_post_id=civitai_target.get("civitai_post_id"),
                    civitai_post_title=civitai_target.get("civitai_post_title"),
                    civitai_post_index=civitai_target.get("civitai_post_index"),
                ),
                image_db_id=image.id,
            )
            after_variant = (
                image.json_metadata.get("civitai_source_variant")
                if isinstance(image.json_metadata, dict)
                else None
            )
            if after_variant and after_variant != before_variant:
                actions_taken.append("Refreshed CivitAI source variant metadata.")
    else:
        actions_taken.extend(_remove_stale_video_resources(current_path))

    if actual_mime == "image/png":
        raw_bytes = current_path.read_bytes()
        repacker = PngRepacker(copy_exif=True, copy_text=True, keep_idat_separate=False)
        inspection = repacker.inspect_bytes(raw_bytes)
        png_inspection = {
            "parsed_chunks": inspection.parsed_chunks,
            "bad_crc_count": inspection.bad_crc_count,
            "is_damaged": inspection.is_damaged,
            "parse_error": inspection.parse_error,
        }
        if inspection.is_damaged:
            issues_found.append("PNG structure appears damaged and required repacking.")
            temp_path = None
            try:
                repacked = repacker.repack_bytes(raw_bytes)
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    prefix=f"temp_repair_{image.file_hash}_",
                    suffix=".png",
                    dir=IMAGE_LIBRARY_PATH,
                    delete=False,
                ) as temp_file:
                    temp_file.write(repacked.output_bytes)
                    temp_path = Path(temp_file.name)

                replacement = _replace_image_with_uploaded_file(
                    db,
                    image=image,
                    uploaded_file_path=temp_path,
                    original_filename=preferred_file_name
                    or image.file_name
                    or current_path.name,
                    replacement_reason="replaced_by_media_repair_png",
                    artist_name=image.artist.name if image.artist is not None else None,
                    source_url=image.source_url,
                    license_id=image.license_id,
                )
                repaired_image = replacement.get("repaired_image")
                created_new_image = bool(replacement.get("created_new_image"))
                actions_taken.append(
                    "Repacked damaged PNG payload into a repaired library item."
                )
                _commit_with_lock_retry(
                    db, context=f"Repair commit for image {file_hash}"
                )
                return _collect_repair_result(
                    image=image,
                    actions_taken=actions_taken,
                    issues_found=issues_found,
                    warnings=warnings,
                    created_new_image=created_new_image,
                    repaired_image=repaired_image,
                    png_inspection=png_inspection,
                )
            except Exception as exc:
                db.rollback()
                raise HTTPException(status_code=500, detail=f"PNG repair failed: {exc}")
            finally:
                _cleanup_temp_file(temp_path)
        else:
            actions_taken.append("Verified PNG integrity; no PNG repack was needed.")

    sidecar_missing = not current_path.with_suffix(".json").exists()
    processor.save_json_metadata(current_path, image)
    if sidecar_missing:
        actions_taken.append("Rebuilt missing sidecar metadata.")
    else:
        actions_taken.append("Refreshed sidecar metadata.")

    _commit_with_lock_retry(db, context=f"Repair commit for image {file_hash}")
    db.refresh(image)
    if repaired_image is not None:
        db.refresh(repaired_image)

    return _collect_repair_result(
        image=image,
        actions_taken=actions_taken,
        issues_found=issues_found,
        warnings=warnings,
        created_new_image=created_new_image,
        repaired_image=repaired_image,
        png_inspection=png_inspection,
    )


def rescan_image_metadata(file_hash: str, db: Session = Depends(get_db)):
    """Rescan one media file and rerun metadata hydration/backfill steps."""
    image = db.query(ImageModel).filter(ImageModel.file_hash == file_hash).first()
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found")

    try:
        collection = ImageCollection(db)
        return collection.rescan_existing_file(image)
    except FileNotFoundError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc))
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(
            status_code=500, detail=f"Could not rescan image metadata: {exc}"
        )


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
            locked_error = (
                "database is locked" in str(e).lower()
                or "sqlite_busy" in str(e).lower()
            )
            if not locked_error or attempt >= max_attempts:
                raise HTTPException(
                    status_code=503,
                    detail=f"Failed to delete image due to database lock: {e}",
                )
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


def get_image_status_counts(db: Session = Depends(get_db)):
    active = db.query(ImageModel).filter(_active_image_filter()).count()
    deleted = db.query(ImageModel).filter(ImageModel.image_status == "deleted").count()
    tombstoned = (
        db.query(ImageModel).filter(ImageModel.image_status == "tombstoned").count()
    )
    placeholder = (
        db.query(ImageModel).filter(ImageModel.image_status == "placeholder").count()
    )
    return {
        "active": active,
        "deleted": deleted,
        "tombstoned": tombstoned,
        "placeholder": placeholder,
    }


def get_inactive_images(
    status: Literal["all", "deleted", "tombstoned", "placeholder"] = "all",
    limit: int = 200,
    db: Session = Depends(get_db),
):
    capped_limit = max(1, min(int(limit), 1000))

    query = db.query(ImageModel).order_by(ImageModel.id.desc())
    if status == "deleted":
        query = query.filter(ImageModel.image_status == "deleted")
    elif status == "tombstoned":
        query = query.filter(ImageModel.image_status == "tombstoned")
    elif status == "placeholder":
        query = query.filter(ImageModel.image_status == "placeholder")
    else:
        query = query.filter(
            (ImageModel.image_status == "deleted")
            | (ImageModel.image_status == "tombstoned")
            | (ImageModel.image_status == "placeholder")
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


def get_placeholder_images(
    limit: int = 200,
    classification: Optional[str] = None,
    collection_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    capped_limit = max(1, min(int(limit), 1000))
    normalized_classification = str(classification or "").strip().lower()

    query = (
        db.query(ImageModel)
        .options(joinedload(ImageModel.collections))
        .filter(ImageModel.image_status == "placeholder")
        .order_by(ImageModel.id.desc())
    )
    rows = query.limit(capped_limit).all()

    items: list[dict[str, Any]] = []
    for row in rows:
        civitai_payload = (
            row.json_metadata.get("civitai")
            if isinstance(row.json_metadata, dict)
            else {}
        )
        unavailable_detail = (
            civitai_payload.get("unavailable_detail")
            if isinstance(civitai_payload, dict)
            else {}
        )
        if not isinstance(unavailable_detail, dict):
            unavailable_detail = {}

        item_classification = (
            str(unavailable_detail.get("classification") or "").strip().lower()
        )
        if (
            normalized_classification
            and item_classification != normalized_classification
        ):
            continue

        row_collection_ids = [int(c.id) for c in row.collections]
        if collection_id is not None and int(collection_id) not in row_collection_ids:
            continue

        items.append(
            {
                "id": row.id,
                "file_hash": row.file_hash,
                "file_name": row.file_name,
                "file_path": row.file_path,
                "image_status": row.image_status or "active",
                "status_reason": row.status_reason,
                "source_url": row.source_url,
                "source_site": row.source_site,
                "mimetype": row.mimetype,
                "date_modified": (
                    row.date_modified.isoformat()
                    if row.date_modified is not None
                    else None
                ),
                "collection_ids": row_collection_ids,
                "collection_names": [str(c.name) for c in row.collections],
                "unavailable_detail": unavailable_detail,
                "classification": unavailable_detail.get("classification"),
                "endpoint": unavailable_detail.get("endpoint"),
                "status_code": unavailable_detail.get("status_code"),
            }
        )

    return items


def get_placeholder_summary(
    collection_id: Optional[int] = None,
    db: Session = Depends(get_db),
):
    query = (
        db.query(ImageModel)
        .options(joinedload(ImageModel.collections))
        .filter(ImageModel.image_status == "placeholder")
        .order_by(ImageModel.id.desc())
    )
    rows = query.all()

    by_classification: dict[str, int] = {}
    by_endpoint: dict[str, int] = {}
    by_status_code: dict[str, int] = {}
    total = 0

    for row in rows:
        row_collection_ids = {int(c.id) for c in row.collections}
        if collection_id is not None and int(collection_id) not in row_collection_ids:
            continue

        civitai_payload = (
            row.json_metadata.get("civitai")
            if isinstance(row.json_metadata, dict)
            else {}
        )
        unavailable_detail = (
            civitai_payload.get("unavailable_detail")
            if isinstance(civitai_payload, dict)
            else {}
        )
        if not isinstance(unavailable_detail, dict):
            unavailable_detail = {}

        classification = (
            str(unavailable_detail.get("classification") or "unknown").strip().lower()
            or "unknown"
        )
        endpoint = (
            str(unavailable_detail.get("endpoint") or "unknown").strip() or "unknown"
        )
        raw_status = unavailable_detail.get("status_code")
        status_code = str(raw_status) if raw_status is not None else "unknown"

        total += 1
        by_classification[classification] = by_classification.get(classification, 0) + 1
        by_endpoint[endpoint] = by_endpoint.get(endpoint, 0) + 1
        by_status_code[status_code] = by_status_code.get(status_code, 0) + 1

    return {
        "total": total,
        "collection_id": collection_id,
        "by_classification": dict(
            sorted(by_classification.items(), key=lambda item: item[0])
        ),
        "by_endpoint": dict(sorted(by_endpoint.items(), key=lambda item: item[0])),
        "by_status_code": dict(
            sorted(by_status_code.items(), key=lambda item: item[0])
        ),
    }


def restore_image_record(file_hash: str, db: Session = Depends(get_db)):
    image = db.query(ImageModel).filter(ImageModel.file_hash == file_hash).first()
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found")

    previous_status = str(image.image_status or "active")
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


def purge_deleted_files(db: Session = Depends(get_db)):
    """Permanently remove deleted records and their on-disk files/sidecars."""
    deleted_images = (
        db.query(ImageModel).filter(ImageModel.image_status == "deleted").all()
    )

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
        db.query(ImageTag).filter(ImageTag.image_id == image.id).delete(
            synchronize_session=False
        )
        db.query(DatasetImage).filter(DatasetImage.image_id == image.id).delete(
            synchronize_session=False
        )
        db.query(AnalysisData).filter(AnalysisData.image_id == image.id).delete(
            synchronize_session=False
        )

        db.delete(image)
        purged += 1

    db.commit()
    return {
        "message": "Deleted-image purge complete.",
        "purged_records": purged,
        "file_errors": file_errors,
    }


# Also, update the /artists/ endpoint to return the new artist objects
def get_artists(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Returns a list of all artists."""
    cache_key = _build_search_cache_key("artists", payload={})
    cache_headers = _build_json_cache_headers(cache_key)
    for header_name, header_value in cache_headers.items():
        response.headers[header_name] = header_value
    if _should_return_json_not_modified(request, cache_headers):
        return Response(status_code=304, headers=cache_headers)

    artists = db.query(Artist).all()
    return [
        {
            "id": artist.id,
            "name": artist.name,
            "nickname": artist.nickname,
            "civitai_user_id": artist.civitai_user_id,
            "civitai_user_deleted": artist.civitai_user_deleted,
            "civitai_user_original_name": artist.civitai_user_original_name,
        }
        for artist in artists
    ]


def get_filter_options(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    cache_key = _build_search_cache_key("filter_options", payload={})
    cache_headers = _build_json_cache_headers(cache_key, max_age_seconds=15)
    for header_name, header_value in cache_headers.items():
        response.headers[header_name] = header_value
    if _should_return_json_not_modified(request, cache_headers):
        return Response(status_code=304, headers=cache_headers)

    cached_options = _search_cache_get(cache_key)
    if isinstance(cached_options, dict):
        return cached_options

    def _sorted_unique_text(values: list[Any]) -> list[str]:
        normalized: set[str] = set()
        for value in values:
            text = str(value or "").strip()
            if text:
                normalized.add(text)
        return sorted(normalized, key=lambda item: item.lower())

    def _coerce_int(value: Any) -> Optional[int]:
        if value is None:
            return None
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        text_value = str(value or "").strip()
        if not text_value:
            return None
        try:
            return int(float(text_value))
        except ValueError:
            return None

    def _labels_from_nsfw_level(level: int) -> tuple[set[str], set[str]]:
        rating_labels: set[str] = set()
        safety_labels: set[str] = set()
        if level <= 0:
            safety_labels.add("Safe")
            return rating_labels, safety_labels

        if level & 1:
            rating_labels.add("PG")
            safety_labels.add("Safe")
        if level & 2:
            rating_labels.add("PG13")
            safety_labels.add("Safe")
        if level & 4:
            rating_labels.add("R")
            safety_labels.add("Mature")
        if level & 8:
            rating_labels.add("X")
            safety_labels.add("Explicit")
        if level & 16:
            rating_labels.add("XXX")
            safety_labels.add("Explicit")

        if not safety_labels:
            safety_labels.add("Explicit")
        return rating_labels, safety_labels

    def _collect_nsfw_tokens_from_value(
        value: Any, rating_tokens: set[str], safety_tokens: set[str]
    ) -> None:
        if value is None:
            return

        if isinstance(value, list):
            for item in value:
                _collect_nsfw_tokens_from_value(item, rating_tokens, safety_tokens)
            return

        if isinstance(value, str):
            text_value = value.strip()
            if not text_value:
                return

            if text_value.startswith("[") and text_value.endswith("]"):
                try:
                    parsed = json.loads(text_value)
                except json.JSONDecodeError:
                    parsed = None
                if isinstance(parsed, list):
                    _collect_nsfw_tokens_from_value(
                        parsed, rating_tokens, safety_tokens
                    )
                    return

            upper_value = text_value.upper()
            if upper_value in {"PG", "PG13", "R", "X", "XXX"}:
                rating_tokens.add(upper_value)
                if upper_value in {"PG", "PG13"}:
                    safety_tokens.add("Safe")
                elif upper_value == "R":
                    safety_tokens.add("Mature")
                else:
                    safety_tokens.add("Explicit")
                return
            if upper_value in {"SAFE", "MATURE", "EXPLICIT"}:
                safety_tokens.add(upper_value.title())
                return

        level = _coerce_int(value)
        if level is None:
            return
        ratings, safeties = _labels_from_nsfw_level(level)
        rating_tokens.update(ratings)
        safety_tokens.update(safeties)

    tag_names_by_source = _gallery_tag_names_by_source_from_observations(db)
    tag_names = sorted(
        {name for names in tag_names_by_source.values() for name in names}
    )

    source_site_rows = (
        db.query(ImageModel.source_site).filter(_active_image_filter()).distinct().all()
    )
    mimetype_rows = (
        db.query(ImageModel.mimetype).filter(_active_image_filter()).distinct().all()
    )

    artist_name_rows = (
        db.query(Artist.name)
        .join(ImageModel, ImageModel.artist_id == Artist.id)
        .filter(_active_image_filter())
        .distinct()
        .all()
    )
    collection_name_rows = (
        db.query(CollectionModel.name)
        .join(
            ImageCollectionMembership,
            ImageCollectionMembership.collection_id == CollectionModel.id,
        )
        .join(ImageModel, ImageModel.id == ImageCollectionMembership.image_id)
        .filter(_active_image_filter())
        .distinct()
        .all()
    )

    generation_software_rows = (
        db.query(func.json_extract(ImageModel.json_metadata, "$.generation_software"))
        .filter(_active_image_filter())
        .distinct()
        .all()
    )

    rating_tokens: set[str] = set()
    safety_tokens: set[str] = set()
    generation_software_values = [row[0] for row in generation_software_rows]

    nsfw_rows = (
        db.query(
            ImageModel.user_nsfw_rating,
            ImageModel.user_nsfw_safety_class,
            func.json_extract(ImageModel.json_metadata, "$.nsfw_rating"),
            func.json_extract(ImageModel.json_metadata, "$.nsfw_safety"),
            func.json_extract(ImageModel.json_metadata, "$.nsfw_ratings"),
            func.json_extract(ImageModel.json_metadata, "$.civitai.nsfwLevel"),
            func.json_extract(ImageModel.json_metadata, "$.civitai.meta.nsfwLevel"),
            func.json_extract(ImageModel.json_metadata, "$.civitai.image.nsfwLevel"),
        )
        .filter(_active_image_filter())
        .all()
    )
    for nsfw_row in nsfw_rows:
        for value in nsfw_row:
            _collect_nsfw_tokens_from_value(value, rating_tokens, safety_tokens)

    payload = {
        "tag_names_by_source": tag_names_by_source,
        "tag_names": tag_names,
        "generation_software": _sorted_unique_text(generation_software_values),
        "source_sites": _sorted_unique_text([row[0] for row in source_site_rows]),
        "mimetypes": _sorted_unique_text([row[0] for row in mimetype_rows]),
        "nsfw_ratings": sorted(
            rating_tokens,
            key=lambda item: (
                ["PG", "PG13", "R", "X", "XXX", "N/A"].index(item)
                if item in {"PG", "PG13", "R", "X", "XXX", "N/A"}
                else 999
            ),
        ),
        "nsfw_safety": sorted(
            safety_tokens,
            key=lambda item: (
                ["Safe", "Mature", "Explicit", "N/A"].index(item)
                if item in {"Safe", "Mature", "Explicit", "N/A"}
                else 999
            ),
        ),
        "artist_names": _sorted_unique_text([row[0] for row in artist_name_rows]),
        "collection_names": _sorted_unique_text(
            [row[0] for row in collection_name_rows]
        ),
    }
    _search_cache_put(cache_key, payload, ttl_seconds=_FILTER_OPTIONS_CACHE_TTL_SECONDS)
    return payload


# ---------------------------------------------------------------------------
# Variant Group API
# ---------------------------------------------------------------------------


def _serialize_variant_group(group: VariantGroup) -> dict:
    """Serialize a VariantGroup to a JSON-friendly dict."""
    return {
        "id": group.id,
        "group_key": group.group_key,
        "group_type": group.group_type,
        "group_label": group.group_label,
        "cover_image_id": group.cover_image_id,
        "cover_preference": group.cover_preference,
        "created_at": group.created_at.isoformat() if group.created_at is not None else None,
        "updated_at": group.updated_at.isoformat() if group.updated_at is not None else None,
    }


def list_variant_groups(
    group_type: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """List all variant groups, optionally filtered by type."""
    query = db.query(VariantGroup)
    if group_type:
        query = query.filter(VariantGroup.group_type == group_type)
    groups = query.order_by(VariantGroup.created_at.desc()).all()

    result = []
    for g in groups:
        member_count = (
            db.query(func.count(ImageVariantGroupMembership.image_id))
            .filter(ImageVariantGroupMembership.group_id == g.id)
            .scalar()
        ) or 0
        serialized = _serialize_variant_group(g)
        serialized["member_count"] = member_count
        result.append(serialized)
    return result


def get_variant_group(group_id: int, db: Session = Depends(get_db)):
    """Get a variant group with its member list."""
    group = db.query(VariantGroup).filter(VariantGroup.id == group_id).first()
    if group is None:
        raise HTTPException(status_code=404, detail="Variant group not found.")

    memberships = (
        db.query(ImageVariantGroupMembership)
        .filter(ImageVariantGroupMembership.group_id == group_id)
        .order_by(ImageVariantGroupMembership.sort_index)
        .all()
    )
    result = _serialize_variant_group(group)
    result["members"] = [
        {
            "image_id": m.image_id,
            "role_in_group": m.role_in_group,
            "sort_index": m.sort_index,
            "source": m.source,
        }
        for m in memberships
    ]
    result["member_count"] = len(memberships)
    return result


def create_variant_group(payload: VariantGroupCreateRequest, db: Session = Depends(get_db)):
    """Create a new variant group, optionally with initial member images."""
    group_key = f"manual:{uuid4().hex[:12]}"
    group = VariantGroup(
        group_key=group_key,
        group_type=payload.group_type,
        group_label=payload.group_label,
        cover_preference="sort_order",
    )
    db.add(group)
    db.flush()

    for idx, image_id in enumerate(payload.image_ids):
        image = db.query(ImageModel).filter(ImageModel.id == image_id).first()
        if image is None:
            raise HTTPException(
                status_code=400, detail=f"Image {image_id} not found."
            )
        membership = ImageVariantGroupMembership(
            image_id=image_id,
            group_id=group.id,
            role_in_group="member",
            sort_index=idx,
            source="manual",
        )
        db.add(membership)

    db.commit()
    db.refresh(group)
    result = _serialize_variant_group(group)
    result["member_count"] = len(payload.image_ids)
    return result


def update_variant_group(
    group_id: int, payload: VariantGroupUpdateRequest, db: Session = Depends(get_db)
):
    """Update a variant group's label, cover image, or cover preference."""
    group = db.query(VariantGroup).filter(VariantGroup.id == group_id).first()
    if group is None:
        raise HTTPException(status_code=404, detail="Variant group not found.")

    if payload.group_label is not None:
        group.group_label = payload.group_label
    if payload.cover_image_id is not None:
        # Validate that the cover image is a member of this group
        membership = (
            db.query(ImageVariantGroupMembership)
            .filter(
                ImageVariantGroupMembership.group_id == group_id,
                ImageVariantGroupMembership.image_id == payload.cover_image_id,
            )
            .first()
        )
        if membership is None:
            raise HTTPException(
                status_code=400,
                detail="Cover image must be a member of this variant group.",
            )
        group.cover_image_id = payload.cover_image_id
    if payload.cover_preference is not None:
        group.cover_preference = payload.cover_preference

    db.commit()
    db.refresh(group)
    return _serialize_variant_group(group)


def delete_variant_group(group_id: int, db: Session = Depends(get_db)):
    """Delete a variant group and all its memberships. Images are not deleted."""
    group = db.query(VariantGroup).filter(VariantGroup.id == group_id).first()
    if group is None:
        raise HTTPException(status_code=404, detail="Variant group not found.")

    db.query(ImageVariantGroupMembership).filter(
        ImageVariantGroupMembership.group_id == group_id
    ).delete(synchronize_session=False)
    db.delete(group)
    db.commit()
    return {"message": "Variant group deleted.", "group_id": group_id}


def add_members_to_variant_group(
    group_id: int,
    payload: VariantGroupAddMembersRequest,
    db: Session = Depends(get_db),
):
    """Add image(s) to an existing variant group."""
    group = db.query(VariantGroup).filter(VariantGroup.id == group_id).first()
    if group is None:
        raise HTTPException(status_code=404, detail="Variant group not found.")

    max_sort = (
        db.query(func.max(ImageVariantGroupMembership.sort_index))
        .filter(ImageVariantGroupMembership.group_id == group_id)
        .scalar()
    ) or 0

    added = 0
    for idx, image_id in enumerate(payload.image_ids):
        image = db.query(ImageModel).filter(ImageModel.id == image_id).first()
        if image is None:
            raise HTTPException(
                status_code=400, detail=f"Image {image_id} not found."
            )
        # Skip if already a member
        existing = (
            db.query(ImageVariantGroupMembership)
            .filter(
                ImageVariantGroupMembership.group_id == group_id,
                ImageVariantGroupMembership.image_id == image_id,
            )
            .first()
        )
        if existing:
            continue

        membership = ImageVariantGroupMembership(
            image_id=image_id,
            group_id=group_id,
            role_in_group=payload.role_in_group or "member",
            sort_index=max_sort + idx + 1,
            source="manual",
        )
        db.add(membership)
        added += 1

    db.commit()
    return {
        "message": f"Added {added} image(s) to variant group.",
        "group_id": group_id,
        "added": added,
    }


def remove_member_from_variant_group(
    group_id: int, image_id: int, db: Session = Depends(get_db)
):
    """Remove an image from a variant group. Deletes the group if it was the last member."""
    group = db.query(VariantGroup).filter(VariantGroup.id == group_id).first()
    if group is None:
        raise HTTPException(status_code=404, detail="Variant group not found.")

    membership = (
        db.query(ImageVariantGroupMembership)
        .filter(
            ImageVariantGroupMembership.group_id == group_id,
            ImageVariantGroupMembership.image_id == image_id,
        )
        .first()
    )
    if membership is None:
        raise HTTPException(
            status_code=404, detail="Image is not a member of this variant group."
        )

    db.delete(membership)

    # Check if group is now empty
    remaining = (
        db.query(func.count(ImageVariantGroupMembership.image_id))
        .filter(ImageVariantGroupMembership.group_id == group_id)
        .scalar()
    )
    if remaining == 0:
        db.delete(group)

    db.commit()
    return {
        "message": "Image removed from variant group.",
        "group_id": group_id,
        "image_id": image_id,
        "group_deleted": remaining == 0,
    }


def get_collections(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    cache_key = _build_search_cache_key("collections", payload={})
    cache_headers = _build_json_cache_headers(cache_key)
    for header_name, header_value in cache_headers.items():
        response.headers[header_name] = header_value
    if _should_return_json_not_modified(request, cache_headers):
        return Response(status_code=304, headers=cache_headers)

    collections = db.query(CollectionModel).order_by(CollectionModel.name.asc()).all()
    return [_serialize_collection(c) for c in collections]


def create_collection(payload: CollectionCreateRequest, db: Session = Depends(get_db)):
    normalized_name = _normalize_collection_name(payload.name)
    existing = (
        db.query(CollectionModel)
        .filter(CollectionModel.name == normalized_name)
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=409, detail="Collection with this name already exists."
        )

    created = CollectionModel(name=normalized_name, source="user")
    db.add(created)
    db.commit()
    db.refresh(created)
    return _serialize_collection(created)


def rename_collection(
    collection_id: int, payload: CollectionRenameRequest, db: Session = Depends(get_db)
):
    collection = (
        db.query(CollectionModel).filter(CollectionModel.id == collection_id).first()
    )
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection not found.")

    normalized_name = _normalize_collection_name(payload.name)
    duplicate = (
        db.query(CollectionModel)
        .filter(
            CollectionModel.name == normalized_name, CollectionModel.id != collection_id
        )
        .first()
    )
    if duplicate is not None:
        raise HTTPException(
            status_code=409, detail="Collection with this name already exists."
        )

    collection.name = normalized_name
    db.commit()
    db.refresh(collection)
    return _serialize_collection(collection)


def delete_collection(collection_id: int, db: Session = Depends(get_db)):
    collection = (
        db.query(CollectionModel).filter(CollectionModel.id == collection_id).first()
    )
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection not found.")

    db.query(ImageCollectionMembership).filter(
        ImageCollectionMembership.collection_id == collection_id
    ).delete(synchronize_session=False)
    db.delete(collection)
    db.commit()
    return {"message": "Collection deleted.", "collection_id": collection_id}


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


def add_image_to_collection(
    file_hash: str, collection_id: int, db: Session = Depends(get_db)
):
    image = db.query(ImageModel).filter(ImageModel.file_hash == file_hash).first()
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found")

    collection = (
        db.query(CollectionModel).filter(CollectionModel.id == collection_id).first()
    )
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection not found")

    _ensure_image_in_collection(db, image.id, collection_id)
    db.commit()
    return {
        "message": "Image added to collection.",
        "file_hash": file_hash,
        "collection": _serialize_collection(collection),
    }


def add_images_to_collection(
    collection_id: int,
    payload: CollectionBulkMembershipRequest,
    db: Session = Depends(get_db),
):
    collection = (
        db.query(CollectionModel).filter(CollectionModel.id == collection_id).first()
    )
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection not found")

    normalized_hashes: list[str] = []
    seen_hashes: set[str] = set()
    for raw_hash in payload.file_hashes:
        file_hash = str(raw_hash or "").strip()
        if not file_hash or file_hash in seen_hashes:
            continue
        seen_hashes.add(file_hash)
        normalized_hashes.append(file_hash)

    if not normalized_hashes:
        raise HTTPException(
            status_code=400, detail="At least one file hash is required."
        )

    images = (
        db.query(ImageModel).filter(ImageModel.file_hash.in_(normalized_hashes)).all()
    )
    images_by_hash = {str(image.file_hash): image for image in images}
    missing_hashes = [
        file_hash for file_hash in normalized_hashes if file_hash not in images_by_hash
    ]

    added_count = 0
    already_member_count = 0
    for file_hash in normalized_hashes:
        image = images_by_hash.get(file_hash)
        if image is None:
            continue

        existing = (
            db.query(ImageCollectionMembership)
            .filter(
                ImageCollectionMembership.image_id == image.id,
                ImageCollectionMembership.collection_id == collection_id,
            )
            .first()
        )
        if existing is not None:
            already_member_count += 1
            continue

        _ensure_image_in_collection(db, image.id, collection_id)
        added_count += 1

    db.commit()
    return {
        "message": "Items added to collection.",
        "collection": _serialize_collection(collection),
        "requested_count": len(normalized_hashes),
        "added_count": added_count,
        "already_member_count": already_member_count,
        "missing_hashes": missing_hashes,
    }


def remove_image_from_collection(
    file_hash: str, collection_id: int, db: Session = Depends(get_db)
):
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


def remove_images_from_collection(
    collection_id: int,
    payload: CollectionBulkMembershipRequest,
    db: Session = Depends(get_db),
):
    collection = (
        db.query(CollectionModel).filter(CollectionModel.id == collection_id).first()
    )
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection not found")

    normalized_hashes: list[str] = []
    seen_hashes: set[str] = set()
    for raw_hash in payload.file_hashes:
        file_hash = str(raw_hash or "").strip()
        if not file_hash or file_hash in seen_hashes:
            continue
        seen_hashes.add(file_hash)
        normalized_hashes.append(file_hash)

    if not normalized_hashes:
        raise HTTPException(
            status_code=400, detail="At least one file hash is required."
        )

    images = (
        db.query(ImageModel).filter(ImageModel.file_hash.in_(normalized_hashes)).all()
    )
    images_by_hash = {str(image.file_hash): image for image in images}
    missing_hashes = [
        file_hash for file_hash in normalized_hashes if file_hash not in images_by_hash
    ]

    removed_count = 0
    not_member_count = 0
    for file_hash in normalized_hashes:
        image = images_by_hash.get(file_hash)
        if image is None:
            continue

        membership = (
            db.query(ImageCollectionMembership)
            .filter(
                ImageCollectionMembership.image_id == image.id,
                ImageCollectionMembership.collection_id == collection_id,
            )
            .first()
        )
        if membership is None:
            not_member_count += 1
            continue

        db.delete(membership)
        removed_count += 1

    db.commit()
    return {
        "message": "Items removed from collection.",
        "collection_id": collection_id,
        "requested_count": len(normalized_hashes),
        "removed_count": removed_count,
        "not_member_count": not_member_count,
        "missing_hashes": missing_hashes,
    }


def get_licenses(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Returns a list of all available licenses."""
    cache_key = _build_search_cache_key("licenses", payload={})
    cache_headers = _build_json_cache_headers(cache_key)
    for header_name, header_value in cache_headers.items():
        response.headers[header_name] = header_value
    if _should_return_json_not_modified(request, cache_headers):
        return Response(status_code=304, headers=cache_headers)

    licenses = db.query(License).all()
    return [
        {"id": license.id, "name": license.name, "short_name": license.short_name}
        for license in licenses
    ]


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


def import_civitai_images(payload: CivitaiImportRequest, db: Session = Depends(get_db)):
    """Import CivitAI images by image URL/ID or collection URL/ID.

    If a URL is provided, the import type is auto-detected.
    If the auto-detected type differs from the requested type, a warning is returned.
    """
    value = (payload.value or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="Import value is required.")

    if payload.limit is not None and payload.limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be greater than 0.")

    # Auto-detect URL type if a URL is provided
    detected_type = None
    detected_id = None
    type_mismatch_warning = None

    try:
        detected_type, detected_id = _detect_civitai_url_type(value)
        if detected_type != payload.import_type:
            type_mismatch_warning = f"URL contains a {detected_type}, not a {payload.import_type}. Importing as {detected_type}."
    except HTTPException:
        # If auto-detection fails, fall through to traditional parsing
        pass

    # Use detected type if available, otherwise fall back to explicitly parsing the requested type
    if detected_type:
        import_type = detected_type
        import_id = detected_id
    else:
        import_type = payload.import_type
        if import_type == "image":
            import_id = _parse_civitai_image_id(value)
        else:
            import_id = _parse_civitai_collection_id(value)

    response_data = {}
    if type_mismatch_warning:
        response_data["warning"] = type_mismatch_warning

    if import_type == "image":
        task = task_manager.create_task(
            kind="civitai-image-import",
            title=f"Import CivitAI image {import_id}",
            metadata={
                "import_type": "image",
                "image_id": import_id,
                "requested_value": value,
            },
            runner=lambda context: _run_civitai_image_import_job(context, import_id),
        )
    else:
        task = task_manager.create_task(
            kind="civitai-collection-import",
            title=f"Import CivitAI collection {import_id}",
            metadata={
                "import_type": "collection",
                "collection_id": import_id,
                "requested_value": value,
                "limit": payload.limit,
            },
            runner=lambda context: _run_civitai_collection_import_job(
                context,
                collection_id=import_id,
                limit=payload.limit,
            ),
        )

    response_data["message"] = "CivitAI import task queued."
    response_data["task"] = task
    response_data["detected_import_type"] = import_type
    return response_data


# ---------------------------------------------------------------------------
# CivitAI Authentication Endpoints
# ---------------------------------------------------------------------------


def civitai_auth_status():
    """Check whether the current CivitAI session cookie is valid.

    Returns ``{ authenticated: bool, message: str }``.  Probes
    ``collection.getAllUser`` (an authenticated endpoint) to verify the token.
    """
    try:
        from atelierai.civitai.civitai_auth import _validate_token_with_civitai
    except ImportError:
        return {"authenticated": False, "message": "CivitAI auth module not available."}

    api = CivitaiAPI.get_instance()
    cookie = getattr(api, "session_cookie", None)
    if not cookie or len(cookie) < 100:
        return {"authenticated": False, "message": "No session cookie is configured."}

    is_valid, _definitive, message = _validate_token_with_civitai(cookie)
    return {"authenticated": is_valid, "message": message}


def civitai_auth_save_cookie(payload: CivitaiCookieRequest):
    """Accept a manually-pasted CivitAI session cookie.

    The caller supplies just the ``__Secure-civitai-token`` value (the long
    JWT-like string starting with ``eyJ``).  The endpoint validates it against
    CivitAI before persisting.
    """
    try:
        from atelierai.civitai.civitai_auth import (
            _validate_token_with_civitai,
            _normalize_token,
        )
    except ImportError:
        raise HTTPException(
            status_code=500, detail="CivitAI auth module not available."
        )

    token = _normalize_token(payload.cookie)
    if not token:
        raise HTTPException(
            status_code=400,
            detail="The provided value does not look like a valid CivitAI session token.",
        )

    is_valid, is_definitive, message = _validate_token_with_civitai(token)
    if not is_valid and is_definitive:
        raise HTTPException(
            status_code=401,
            detail=f"Token rejected by CivitAI: {message}",
        )

    # Update the running singleton so subsequent requests use the new cookie.
    try:
        api = CivitaiAPI.get_instance()
        api.update_session_cookie(token)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update session cookie: {exc}",
        )

    status_msg = (
        "CivitAI session cookie saved and validated."
        if is_valid
        else (
            f"Cookie saved (validation inconclusive: {message}). It will be used for future requests."
        )
    )
    return {"success": True, "message": status_msg, "validated": is_valid}


def civitai_auth_refresh():
    """Trigger a Playwright-based re-authentication with CivitAI.

    This runs synchronously and may take 30+ seconds.  The browser window
    will open on the server host for the user to complete OAuth login.
    """
    try:
        from atelierai.civitai.civitai_auth import get_cached_or_refresh_session_token
    except ImportError:
        raise HTTPException(
            status_code=500, detail="CivitAI auth module not available."
        )

    cache_file = _get_civitai_session_cache_path()

    try:
        token = get_cached_or_refresh_session_token(
            cache_file=cache_file,
            headless=False,  # OAuth requires visible browser
            force_reauth=True,
            non_interactive=True,
        )
    except Exception as exc:
        return {"success": False, "error": str(exc)}

    # Update the running singleton.
    try:
        api = CivitaiAPI.get_instance()
        api.update_session_cookie(token)
    except Exception as exc:
        return {
            "success": False,
            "error": f"Token obtained but failed to update singleton: {exc}",
        }

    return {"success": True, "message": "CivitAI session refreshed successfully."}


def _get_civitai_session_cache_path() -> str:
    """Return the session cache file path from config, with a sensible default."""
    try:
        from atelierai.config import CIVITAI_SESSION_CACHE

        return CIVITAI_SESSION_CACHE
    except ImportError:
        pass
    env_val = os.getenv("CIVITAI_SESSION_CACHE", "")
    return env_val or ".civitai_session"


def sync_civitai_collections(
    payload: CivitaiCollectionSyncRequest, db: Session = Depends(get_db)
):
    if payload.limit is not None and payload.limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be greater than 0.")

    task = task_manager.create_task(
        kind="civitai-collection-sync",
        title="Sync My CivitAI Collections",
        metadata={
            "limit": payload.limit,
        },
        runner=lambda context: _run_civitai_collection_sync_job(
            context, limit=payload.limit
        ),
    )

    return {
        "message": "CivitAI collection sync task queued.",
        "task": task,
    }


def backfill_civitai_nsfw_levels(
    payload: CivitaiNsfwBackfillRequest,
    db: Session = Depends(get_db),
):
    if payload.limit is not None and payload.limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be greater than 0.")

    task = task_manager.create_task(
        kind="civitai-nsfw-backfill",
        title="Backfill CivitAI NSFW Levels",
        metadata={
            "limit": payload.limit,
            "reimport_if_missing": payload.reimport_if_missing,
        },
        runner=lambda context: _run_civitai_nsfw_backfill_job(
            context,
            limit=payload.limit,
            reimport_if_missing=payload.reimport_if_missing,
        ),
    )

    return {
        "message": "CivitAI NSFW backfill task queued.",
        "task": task,
    }


# ---------------------------------------------------------------------------
# CivitAI Search Proxy
# ---------------------------------------------------------------------------

_civitai_search_client = None
_civitai_search_client_lock = threading.Lock()


def _get_civitai_search_client():
    """Lazy-init singleton for the search client."""
    global _civitai_search_client
    if _civitai_search_client is not None:
        return _civitai_search_client
    with _civitai_search_client_lock:
        if _civitai_search_client is not None:
            return _civitai_search_client
        from atelierai.civitai.civitai_search import CivitaiSearchClient

        _civitai_search_client = CivitaiSearchClient()
        return _civitai_search_client


# CivitAI image CDN base for constructing URLs from Meilisearch UUIDs.
_CIVITAI_IMAGE_CDN = getattr(
    app_config,
    "CIVITAI_CDN_BASE_URL",
    "https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA",
)


def _normalize_meili_hit(hit: dict) -> dict:
    """Transform a raw Meilisearch hit into a frontend-friendly dict.

    Raw Meilisearch hits use internal field names (e.g. ``url`` is just a
    UUID, stats use ``*AllTime`` suffixes).  This function:

    * Builds ``thumbnail_url`` and full ``url`` from the UUID.
    * Passes through the BlurHash ``hash`` for progressive loading.
    * Normalises stats keys to match what the Search Lab frontend expects.
    """
    out = dict(hit)

    # ── Image URLs ──
    uuid = hit.get("url", "")
    if uuid and "/" not in uuid:
        # Meilisearch stores just the UUID slug.
        out["thumbnail_url"] = f"{_CIVITAI_IMAGE_CDN}/{uuid}/width=450/{uuid}"
        out["url"] = f"{_CIVITAI_IMAGE_CDN}/{uuid}/original=true/{uuid}"

    # Keep the BlurHash as ``blurhash`` for client-side decoding.
    if hit.get("hash"):
        out["blurhash"] = hit["hash"]

    # ── Stats normalisation ──
    stats = hit.get("stats")
    if isinstance(stats, dict):
        normalised = {}
        for key, value in stats.items():
            # Strip the ``AllTime`` suffix so frontend can use shorter names.
            short = key.replace("AllTime", "")
            normalised[short] = value
        out["stats"] = normalised

    return out


def civitai_search_proxy(payload: CivitaiSearchRequest):
    """Proxy a search request to the CivitAI Meilisearch host.

    Handles bearer-token acquisition automatically from the cached session
    cookie. Returns the raw Meilisearch result envelope.
    """
    # Cache key for identical requests.
    cache_key = _build_search_cache_key(
        "civitai-search",
        payload={
            "query": payload.query,
            "tags": sorted(payload.tags),
            "exclude_tags": sorted(payload.exclude_tags),
            "sort_by": payload.sort_by,
            "limit": payload.limit,
            "offset": payload.offset,
            "nsfw_levels": sorted(payload.nsfw_levels or []),
            "base_models": sorted(payload.base_models or []),
            "exclude_poi": payload.exclude_poi,
            "exclude_minor": payload.exclude_minor,
            "username": payload.username,
            "extra_filters": payload.extra_filters,
        },
    )
    cached = _search_cache_get(cache_key)
    if cached is not None:
        return cached

    client = _get_civitai_search_client()

    try:
        result = client.search_images(
            query=payload.query,
            tags=payload.tags or None,
            exclude_tags=payload.exclude_tags or None,
            sort_by=payload.sort_by,
            limit=payload.limit,
            offset=payload.offset,
            nsfw_levels=payload.nsfw_levels,
            base_models=payload.base_models,
            exclude_poi=payload.exclude_poi,
            exclude_minor=payload.exclude_minor,
            username=payload.username,
            facets=payload.facets,
            extra_filters=payload.extra_filters,
        )
    except CivitaiRequestError as exc:
        raise _classify_civitai_upstream_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail=f"CivitAI search proxy error: {exc}"
        )

    # Build normalized response with frontend-friendly field names.
    raw_hits = result.get("hits", [])
    normalized_hits = [_normalize_meili_hit(h) for h in raw_hits]

    response = {
        "hits": normalized_hits,
        "total": result.get("estimatedTotalHits", 0),
        "offset": result.get("offset", payload.offset),
        "limit": result.get("limit", payload.limit),
        "processing_time_ms": result.get("processingTimeMs", 0),
        "facets": {
            "distribution": result.get("facetDistribution"),
            "stats": result.get("facetStats"),
        },
    }

    # Cache with a shorter TTL for search results.
    _search_cache_put(cache_key, response, ttl_seconds=60)

    return response


def civitai_search_auth_status():
    """Check whether the Meilisearch key is available for search."""
    from atelierai.civitai.civitai_search import CivitaiSearchClient

    client = CivitaiSearchClient()
    key = client._get_meili_key()
    has_key = bool(key)

    return {
        "authenticated": has_key,
        "message": (
            "Meilisearch key available — primary backend ready."
            if has_key
            else "No Meilisearch key. Search will use REST API fallback (slower, limited filters)."
        ),
    }


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
            "last_seen_at": (
                term.last_seen_at.isoformat() if term.last_seen_at else None
            ),
        }
        for term, auth in rows
    ]


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
            db.query(AuthorityTerm)
            .filter(AuthorityTerm.concept_id == concept.id)
            .count()
        )
        observation_count = (
            db.query(ImageConceptObservation)
            .filter(ImageConceptObservation.concept_id == concept.id)
            .count()
        )
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
            raise HTTPException(
                status_code=400, detail="canonical_name cannot be empty"
            )
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
            (_normalize_taxonomy_text(a.normalized_alias or a.alias or ""))
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
        if _is_descendant(
            db, ancestor_id=concept.id, candidate_descendant_id=new_parent_id
        ):
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
    db.query(Concept).filter(Concept.id.in_(branch_ids)).delete(
        synchronize_session=False
    )
    db.commit()

    return {
        "message": "Concept branch deleted.",
        "deleted_concept_ids": sorted(branch_ids),
    }


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

    response = {
        "message": (
            "Dry-run purge preview."
            if payload.dry_run
            else "Root concept branches purged."
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
        return response

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

    return response


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
                "display_prefix": _concept_display_prefix(
                    source_map.get(int(concept.id), [])
                ),
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
                # Prompt terms created during rescan can reuse mapped Danbooru IDs as external_tag_id.
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
        alias_data = alias_data_by_concept.get(
            concept_id, {"aliases": [], "implies": []}
        )
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

    payload = {
        "concepts": [
            {
                "id": int(c.id),
                "canonical_name": c.canonical_name,
                "parent_concept_id": (
                    int(c.parent_concept_id)
                    if c.parent_concept_id is not None
                    else None
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
    _search_cache_put(cache_key, payload, ttl_seconds=_FILTER_OPTIONS_CACHE_TTL_SECONDS)
    return payload


# Columnar tag columns for the per-source tag endpoint.
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

    # Reuse a shared gallery-names cache so concurrent cold source requests
    # don't each independently scan the full images table.
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

    # Reuse a shared cache for the danbooru name-by-ext-id lookup used by the
    # prompt source; avoids re-scanning 100k rows per cold prompt request.
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
            "gallery"
            if (gallery_norm and gallery_norm in gallery_scope_names)
            else "image"
        )

        metadata = term.metadata_json if isinstance(term.metadata_json, dict) else {}

        post_count = None
        if source_lower in {"danbooru", "civitai"}:
            raw_pc = metadata.get("post_count") if isinstance(metadata, dict) else None
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

        rows.append(
            [
                int(term.id),
                term.external_name,
                external_tag_id,
                scope,
                post_count,
                int(concept.id) if concept else None,
                mdtag_id,
                mdtag_name,
            ]
        )

    # For the "user" source, also include user-assigned tags from the
    # ImageModel.user_tags column that are not already in authority_terms.
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

        # Reuse shared gallery usage counts if available, otherwise use
        # the aggregate just computed.
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
            rows.append(
                [
                    _synthetic_user_id,  # unique synthetic ID per tag
                    tag_name,
                    None,  # no external_tag_id
                    synthetic_scope,
                    gallery_count if gallery_count > 0 else None,
                    None,  # no concept
                    None,  # no mapped danbooru tag
                    None,
                ]
            )
            _synthetic_user_id -= 1

    payload = {"source": source_lower, "cols": _TAG_SOURCE_COLS, "rows": rows}
    _search_cache_put(cache_key, payload, ttl_seconds=_FILTER_OPTIONS_CACHE_TTL_SECONDS)
    return payload


def taxonomy_tree_associate_tag(
    payload: TaxonomyTagAssociationRequest, db: Session = Depends(get_db)
):
    concept = db.query(Concept).filter(Concept.id == payload.concept_id).first()
    if concept is None:
        raise HTTPException(status_code=404, detail="Concept not found")

    term_id = payload.authority_term_id
    created_term = False

    # Synthetic (negative) ID — auto-create a real AuthorityTerm for user tags.
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


def taxonomy_tree_disassociate_tag(
    authority_term_id: int, db: Session = Depends(get_db)
):
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


def taxonomy_tree_delete_tag(authority_term_id: int, db: Session = Depends(get_db)):
    term = db.query(AuthorityTerm).filter(AuthorityTerm.id == authority_term_id).first()
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
    # Fix historical malformed base path values such as '/wiki pages/'.
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


def taxonomy_tree_tag_details(authority_term_id: int, db: Session = Depends(get_db)):
    term = db.query(AuthorityTerm).filter(AuthorityTerm.id == authority_term_id).first()
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


def taxonomy_tree_update_tag_details(
    authority_term_id: int,
    payload: TaxonomyTagDetailsUpdateRequest,
    db: Session = Depends(get_db),
):
    term = db.query(AuthorityTerm).filter(AuthorityTerm.id == authority_term_id).first()
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


# ── Tag Maintenance Endpoints ────────────────────────────────────

_VALID_TAG_MAINT_SOURCES = {"civitai", "danbooru", "prompt", "user"}


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

        entry: dict = {
            "id": int(t.id),
            "name": t.external_name,
            "external_tag_id": t.external_tag_id,
            "concept_name": concept_name,
            "metadata": metadata,
        }
        terms.append(entry)

    return {
        "authority": source_lower,
        "exported_at": datetime.now(timezone.utc).isoformat() + "Z",
        "total": len(terms),
        "terms": terms,
    }


def taxonomy_tag_maint_purge(
    source: str, payload: TaxonomyTagMaintPurgeRequest, db: Session = Depends(get_db)
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
            "Dry-run purge preview."
            if payload.dry_run
            else "All authority terms purged."
        ),
        "source": source_lower,
        "deleted": term_count,
        "dry_run": payload.dry_run,
    }


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

    # post_count is nested in metadata_json — use SQLite JSON extraction for sorting
    if sort_col == "post_count":
        post_count_expr = func.json_extract(AuthorityTerm.metadata_json, "$.post_count")
        sort_expr = (
            post_count_expr.desc() if sort_direction == "desc" else post_count_expr.asc()
        )
    else:
        sort_expr = col_map.get(sort_col, AuthorityTerm.external_name)
        sort_expr = sort_expr.desc() if sort_direction == "desc" else sort_expr.asc()

    term_rows = (
        q.order_by(sort_expr).offset((page - 1) * page_size).limit(page_size).all()
    )

    # Build gallery scope lookup
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
            "gallery"
            if (gallery_norm and gallery_norm in gallery_scope_names)
            else "image"
        )

        metadata = term.metadata_json if isinstance(term.metadata_json, dict) else {}
        post_count = None
        if source_lower in {"danbooru", "civitai"}:
            raw_pc = metadata.get("post_count") if isinstance(metadata, dict) else None
            try:
                pc = int(raw_pc) if raw_pc is not None else None
            except (TypeError, ValueError):
                pc = None
            if pc is not None and pc > 0:
                post_count = pc

        external_tag_id = term.external_tag_id

        mdtag_id = None
        mdtag_name = None

        rows.append(
            [
                int(term.id),
                term.external_name,
                external_tag_id,
                scope,
                post_count,
                int(term.concept_id)
                if getattr(term, "concept_id", None) is not None
                else None,
                mdtag_id,
                mdtag_name,
            ]
        )

    return {
        "source": source_lower,
        "cols": _TAG_SOURCE_COLS,
        "rows": rows,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def taxonomy_tag_maint_update(
    source: str, payload: TaxonomyTagMaintUpdateRequest, db: Session = Depends(get_db)
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

    # Verify the term belongs to the requested source
    authority = (
        db.query(TagAuthority).filter(TagAuthority.id == term.authority_id).first()
    )
    if authority is None or authority.name.strip().lower() != source_lower:
        raise HTTPException(
            status_code=409, detail="Authority term does not belong to this source"
        )

    if payload.field == "external_name":
        new_name = str(payload.value or "").strip()
        if not new_name:
            raise HTTPException(status_code=400, detail="external_name cannot be empty")
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
    db.commit()

    return {
        "message": "Tag updated.",
        "authority_term_id": int(term.id),
        "field": payload.field,
        "value": payload.value,
    }


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
            "message": "Authority not found.",
            "deleted": 0,
            "dry_run": payload.dry_run,
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


def taxonomy_tag_maint_rescan_civitai_observations(
    dry_run: bool = Query(False, description="Preview changes without committing"),
):
    """SSE endpoint: rescan gallery sidecar JSON files to populate CivitAI
    authority_terms and image_concept_observations.

    Emits ``progress`` events per image and a final ``complete`` event with
    aggregated statistics.  Individual per-image errors are emitted as
    ``error_event`` so the client can continue scanning.
    """

    def _sse_event(event: str, data: dict) -> str:
        payload = json.dumps(data)
        return f"event: {event}\ndata: {payload}\n\n"

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


def _rescan_civitai_observations_inner(
    db: Session,
    dry_run: bool,
    emit: Callable[[str, dict], str],
) -> Generator[str, None, None]:
    """Generator that yields SSE events for the CivitAI observation rescan.

    Separated from the route handler so the ``db`` session lifetime is
    explicit (created/closed in ``event_stream`` wrapper).
    """
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

    # Collect all sidecar files first for accurate total count
    sidecar_files = sorted(library_path.glob("*.json"))
    total_images = len(sidecar_files)

    # Running counters
    tags_processed = 0
    unique_tag_names: set[str] = set()
    pre_existing_tags = 0
    new_tags = 0
    observations_created = 0
    observations_skipped = 0
    error_count = 0

    # Pre-load CivitAI authority and known term names
    authority = _get_or_create_authority(db, "civitai")
    known_term_names: set[str] = set()
    if authority:
        rows = db.query(AuthorityTerm.normalized_external_name).filter(
            AuthorityTerm.authority_id == authority.id
        ).all()
        known_term_names = {r[0] for r in rows if r[0]}

    now = datetime.utcnow()

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

        # ── Step 1: Upsert authority terms ──
        try:
            stats = _upsert_civitai_authority_terms(db, civitai)
            tags_processed += stats.get("terms_upserted", 0)

            # Track unique / pre-existing / new
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
                "current_image": idx, "file": json_file.name,
                "error": f"upsert_terms: {exc}",
            })

        # ── Step 2: Create observations for resolved terms ──
        if not dry_run:
            try:
                image_stem = json_file.stem
                # file_path includes the extension (e.g. "abc123.png"),
                # sidecar stem is just the hash ("abc123").
                image_row = (
                    db.query(ImageModel.id)
                    .filter(ImageModel.file_path.like(image_stem + ".%"))
                    .first()
                )
                if image_row is not None:
                    image_id = image_row[0]

                    # Load normalized tag names from civitai tags
                    tag_norms = set()
                    for tag in tags:
                        if not isinstance(tag, dict):
                            continue
                        raw_name = str(tag.get("name") or "").strip()
                        if raw_name:
                            tag_norms.add(_normalize_taxonomy_text(raw_name))

                    if tag_norms and authority:
                        # Find all matching terms (with or without concept_id).
                        matched_terms = (
                            db.query(AuthorityTerm)
                            .filter(
                                AuthorityTerm.authority_id == authority.id,
                                AuthorityTerm.normalized_external_name.in_(tag_norms),
                            )
                            .all()
                        )

                        # Track concept_ids queued in this batch to prevent
                        # UNIQUE constraint violations from multiple terms
                        # mapping to the same concept for the same image.
                        seen_concept_ids: set[int] = set()

                        for term in matched_terms:
                            # Idempotent: skip if an observation already exists
                            # for this (image, term) pair.
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

                            # Guard the (image_id, concept_id, authority_id)
                            # unique constraint.  Two terms may map to the same
                            # concept — check in-memory set first (covers
                            # pending session additions), then committed DB.
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
                    "current_image": idx, "file": json_file.name,
                    "error": f"observations: {exc}",
                })

        # Emit progress after each image
        yield emit("progress", {
            "current_image": idx, "total_images": total_images,
            "tags_processed": tags_processed, "unique_tags": len(unique_tag_names),
            "pre_existing_tags": pre_existing_tags, "new_tags": new_tags,
            "observations_created": observations_created,
            "observations_skipped": observations_skipped,
        })

    # Commit all changes at the end
    if not dry_run and (observations_created > 0 or tags_processed > 0):
        try:
            db.commit()
        except Exception as exc:
            db.rollback()
            yield emit("error_event", {
                "current_image": total_images, "error": f"commit failed: {exc}",
            })

    # Final event
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


def taxonomy_tag_maint_backfill_civitai_tag_ids(
    dry_run: bool = Query(True, description="Preview changes without committing"),
    limit: int = Query(0, description="Max sidecars to scan (0 = all)"),
    db: Session = Depends(get_db),
):
    """Backfill missing external_tag_id on CivitAI authority_terms from sidecar JSON files.

    Scans image library sidecars for civitai tag records that include numeric
    IDs and feeds them through the existing _upsert_civitai_authority_terms
    logic to update any legacy terms that are missing their external_tag_id.
    """
    library_path = Path(IMAGE_LIBRARY_PATH)
    if not library_path.is_dir():
        raise HTTPException(status_code=500, detail="Image library path not found.")

    # Snapshot how many terms are currently missing IDs
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

        # Only process if at least one tag has a numeric ID
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


# ──────────────────────────────────────────────────────────────────────
# Sync Lab endpoints — step-by-step CivitAI collection import analysis
# ──────────────────────────────────────────────────────────────────────

# In-memory session store for prepared downloads (keyed by image_id).
_sync_lab_prepared: dict[int, _PreparedCivitaiImport] = {}


# ---------------------------------------------------------------------------
# Sync Session CRUD — resumable workflow persistence
# ---------------------------------------------------------------------------

def _sync_session_to_response(session: SyncSession) -> dict:
    """Convert a SyncSession ORM object to a response dict."""
    return {
        "id": session.id,
        "collection_id": session.collection_id,
        "collection_type": session.collection_type,
        "collection_name": session.collection_name,
        "step_3_status": session.step_3_status,
        "step_4_status": session.step_4_status,
        "step_5_status": session.step_5_status,
        "step_6_status": session.step_6_status,
        "step_7_status": session.step_7_status,
        "step_3_data": session.step_3_data,
        "step_4_data": session.step_4_data,
        "step_5_data": session.step_5_data,
        "step_6_data": session.step_6_data,
        "step_7_data": session.step_7_data,
        "active_step": session.active_step,
        "error_message": session.error_message,
        "is_complete": session.is_complete,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "updated_at": session.updated_at.isoformat() if session.updated_at else None,
    }


def sync_session_create(payload: SyncSessionCreateRequest, db: Session):
    """Create a new sync session for a collection."""
    import uuid  # noqa: PLC0415

    session = SyncSession(
        id=str(uuid.uuid4()),
        collection_id=payload.collection_id,
        collection_type=payload.collection_type,
        collection_name=payload.collection_name,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return {"status": "ok", "data": _sync_session_to_response(session)}


def sync_session_list(db: Session, include_complete: bool = False):
    """List sync sessions, optionally including completed ones."""

    query = db.query(SyncSession)
    if not include_complete:
        query = query.filter(SyncSession.is_complete == False)  # noqa: E712
    sessions = query.order_by(SyncSession.updated_at.desc()).all()
    return {
        "status": "ok",
        "data": [_sync_session_to_response(s) for s in sessions],
    }


def sync_session_get(session_id: str, db: Session):
    """Get a single sync session by ID."""

    session = db.query(SyncSession).filter(SyncSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Sync session not found")
    return {"status": "ok", "data": _sync_session_to_response(session)}


def sync_session_update_step(
    session_id: str, payload: SyncSessionStepUpdateRequest, db: Session
):
    """Update a step's status and data for a sync session."""

    session = db.query(SyncSession).filter(SyncSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Sync session not found")

    step = payload.step
    if step < 3 or step > 7:
        raise HTTPException(status_code=400, detail="Step must be between 3 and 7")

    status_col = f"step_{step}_status"
    data_col = f"step_{step}_data"

    setattr(session, status_col, payload.status)
    if payload.data is not None:
        setattr(session, data_col, payload.data)
    if payload.status == "in_progress":
        session.active_step = step
    elif payload.status in ("complete", "failed", "cancelled"):
        if session.active_step == step:
            session.active_step = None
    if payload.error_message is not None:
        session.error_message = payload.error_message

    # Check if all steps are complete
    all_complete = all(
        getattr(session, f"step_{s}_status") == "complete" for s in range(3, 8)
    )
    if all_complete:
        session.is_complete = True

    db.commit()
    db.refresh(session)
    return {"status": "ok", "data": _sync_session_to_response(session)}


def sync_session_delete(session_id: str, db: Session):
    """Delete a sync session."""

    session = db.query(SyncSession).filter(SyncSession.id == session_id).first()
    if not session:
        raise HTTPException(status_code=404, detail="Sync session not found")
    db.delete(session)
    db.commit()
    return {"status": "ok", "detail": "Session deleted"}


def sync_lab_list_collections(
    force_refresh: bool = Query(
        False,
        description="Bypass local API cache and fetch live from CivitAI",
    )
):
    """Step 1: Fetch the authenticated user's CivitAI image collections."""
    t0 = time.monotonic()
    api = CivitaiAPI.get_instance()
    try:
        collections = _fetch_civitai_user_image_collections(
            api,
            max_age=None if force_refresh else timedelta(minutes=2),
            force_refresh=force_refresh,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=503, detail=f"Failed to fetch collections: {exc}"
        )
    elapsed_ms = round((time.monotonic() - t0) * 1000)
    return {
        "status": "ok",
        "timing": {"duration_ms": elapsed_ms},
        "data": {"collections": collections, "total": len(collections)},
    }


def _checkpoint_sync_step(session_id: Optional[str], step: int, status: str,
                          data: Optional[dict] = None, error_message: Optional[str] = None):
    """Persist step status to the sync_sessions table.  No-op when session_id is None."""
    if not session_id:
        return
    try:
        db = SessionLocal()
        try:
            session = db.query(SyncSession).filter(SyncSession.id == session_id).first()
            if session:
                setattr(session, f"step_{step}_status", status)
                if data is not None:
                    setattr(session, f"step_{step}_data", data)
                if status == "in_progress":
                    session.active_step = step
                elif status in ("complete", "failed", "cancelled") and session.active_step == step:
                    session.active_step = None
                if error_message is not None:
                    session.error_message = error_message
                all_complete = all(
                    getattr(session, f"step_{s}_status") == "complete" for s in range(3, 8)
                )
                if all_complete:
                    session.is_complete = True
                db.commit()
        finally:
            db.close()
    except Exception:
        pass  # Checkpointing must never block the main workflow


def sync_lab_fetch_collection_items(
    collection_id: int, limit: int = Query(default=None, ge=1)
):
    """Step 3: Fetch all items for a specific CivitAI collection (SSE streaming)."""
    import queue
    import threading

    def _sse(data: dict) -> str:
        return f"data: {json.dumps(data)}\n\n"

    def event_stream():
        q: queue.Queue = queue.Queue()
        t0 = time.monotonic()

        def on_progress(page: int, page_items: int, total: int):
            q.put(("progress", page, page_items, total))

        def worker():
            try:
                scraper = CivitaiPrivateScraper(auto_authenticate=True)
                items = scraper.fetch_collection_items(
                    collection_id=collection_id,
                    limit=limit,
                    progress_callback=on_progress,
                )
                q.put(("result", items))
            except CivitaiRequestError as exc:
                classified = _classify_civitai_upstream_error(exc)
                q.put(("error", classified.status_code, classified.detail))
            except Exception as exc:
                q.put(("error", 503, f"Failed to fetch collection items: {exc}"))

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        # Yield progress events as they arrive
        while thread.is_alive() or not q.empty():
            try:
                msg = q.get(timeout=0.2)
            except queue.Empty:
                continue

            kind = msg[0]
            if kind == "progress":
                _, page, page_items, total = msg
                yield _sse({
                    "type": "progress",
                    "page": page,
                    "page_items": page_items,
                    "total": total,
                })
            elif kind == "error":
                _, status_code, detail = msg
                yield _sse({
                    "type": "error",
                    "status_code": status_code,
                    "detail": detail,
                })
                return
            elif kind == "result":
                items = msg[1]
                break

        thread.join(timeout=5)

        if not items:
            elapsed_ms = round((time.monotonic() - t0) * 1000)
            yield _sse({
                "type": "complete",
                "timing": {"duration_ms": elapsed_ms},
                "data": {
                    "collection_id": collection_id,
                    "items": [],
                    "total": 0,
                    "image_ids": [],
                },
            })
            return

        # Normalize and archive
        normalized = [item for item in items if isinstance(item, dict)]
        _archive_civitai_collection_items(normalized)

        # Deduplicate and extract image IDs
        seen: set[int] = set()
        image_ids: list[int] = []
        item_index: dict[int, dict[str, Any]] = {}
        for item in normalized:
            raw_id = item.get("id")
            try:
                img_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if img_id in seen:
                continue
            seen.add(img_id)
            image_ids.append(img_id)
            item_index[img_id] = item

        elapsed_ms = round((time.monotonic() - t0) * 1000)
        yield _sse({
            "type": "complete",
            "timing": {"duration_ms": elapsed_ms},
            "data": {
                "collection_id": collection_id,
                "total": len(image_ids),
                "image_ids": image_ids,
                "items": normalized,
                "item_index": {str(k): v for k, v in item_index.items()},
            },
        })

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def sync_lab_analyze_local(payload: SyncLabAnalyzeRequest, session_id: Optional[str] = None):
    """Step 4: Check which CivitAI image IDs exist in the local DB."""
    t0 = time.monotonic()
    image_ids = payload.image_ids
    requested_collection_id = payload.collection_id
    is_retry_run = bool(payload.is_retry_run)
    if not image_ids:
        return {
            "status": "ok",
            "timing": {"duration_ms": 0},
            "data": {
                "existing": [],
                "new": [],
                "tombstoned": [],
                "placeholders": [],
                "summary": {
                    "existing": 0,
                    "new": 0,
                    "tombstoned": 0,
                    "placeholders": 0,
                },
            },
        }

    existing: list[dict] = []
    new_ids: list[int] = []
    tombstoned: list[dict] = []
    placeholders: list[dict] = []
    sync_finalization: Optional[dict[str, Any]] = None

    db = SessionLocal()
    try:
        # Batch-query by civitai_image_id using an indexed column.
        # Falls back to source_url matching for rows not yet backfilled.
        _CHUNK = 500
        rows: list[ImageModel] = []
        for start in range(0, len(image_ids), _CHUNK):
            chunk = image_ids[start : start + _CHUNK]
            rows.extend(
                db.query(ImageModel)
                .filter(ImageModel.civitai_image_id.in_(chunk))
                .all()
            )

        # Also check for rows that haven't been backfilled yet — match by
        # source_url using the old batched-URL approach.
        matched_ids = {row.civitai_image_id for row in rows if row.civitai_image_id is not None}
        unmatched_ids = [iid for iid in image_ids if iid not in matched_ids]

        if unmatched_ids:
            web_base = getattr(
                app_config, "CIVITAI_WEB_BASE_URL", "https://civitai.red"
            )
            alt = "civitai.com" if "civitai.red" in web_base else "civitai.red"
            fallback_urls: list[str] = []
            for img_id in unmatched_ids:
                fallback_urls.append(f"{web_base}/images/{img_id}")
                fallback_urls.append(f"https://{alt}/images/{img_id}")

            # Chunk the fallback URLs too to stay within SQLite limits.
            for fb_start in range(0, len(fallback_urls), _CHUNK * 2):
                url_chunk = fallback_urls[fb_start : fb_start + _CHUNK * 2]
                url_rows = (
                    db.query(ImageModel)
                    .filter(ImageModel.source_url.in_(url_chunk))
                    .all()
                )
                for row in url_rows:
                    if row.civitai_image_id is not None and row.civitai_image_id not in matched_ids:
                        rows.append(row)
                        matched_ids.add(row.civitai_image_id)
                    elif row.civitai_image_id is None:
                        # Not yet backfilled — extract from URL and include.
                        src = str(getattr(row, "source_url", "") or "")
                        m = re.search(r"/images/(\d+)", src)
                        if m:
                            extracted_id = int(m.group(1))
                            if extracted_id not in matched_ids:
                                rows.append(row)
                                matched_ids.add(extracted_id)

        # Build a lookup dict keyed by civitai_image_id
        db_lookup: dict[int, ImageModel] = {}
        for row in rows:
            if row.civitai_image_id is not None:
                db_lookup[row.civitai_image_id] = row
            else:
                # Legacy fallback: extract from source_url
                src = str(getattr(row, "source_url", "") or "")
                m = re.search(r"/images/(\d+)", src)
                if m:
                    db_lookup[int(m.group(1))] = row

        # Classify each requested image ID against the lookup
        for img_id in image_ids:
            match = db_lookup.get(img_id)
            if match is None:
                new_ids.append(img_id)
            elif str(getattr(match, "image_status", "")) == "tombstoned":
                tombstoned.append(
                    {
                        "civitai_image_id": img_id,
                        "image_db_id": match.id,
                        "file_hash": match.file_hash,
                    }
                )
            elif str(getattr(match, "image_status", "")) == "placeholder":
                placeholders.append(
                    {
                        "civitai_image_id": img_id,
                        "image_db_id": match.id,
                        "file_hash": match.file_hash,
                    }
                )
            else:
                existing.append(
                    {
                        "civitai_image_id": img_id,
                        "image_db_id": match.id,
                        "file_hash": match.file_hash,
                        "filename": match.file_name,
                    }
                )

        # Auto-finalize sync status when this run cleanly concludes at Step 4
        # (everything already local, with no tombstoned/placeholders), unless
        # this is an explicit retry flow.
        auto_finalize_eligible = (
            (not is_retry_run)
            and len(image_ids) > 0
            and len(new_ids) == 0
            and len(tombstoned) == 0
            and len(placeholders) == 0
        )

        if auto_finalize_eligible:
            target_collection_id: Optional[int] = requested_collection_id
            if target_collection_id is None and session_id:
                session = (
                    db.query(SyncSession)
                    .filter(SyncSession.id == session_id)
                    .first()
                )
                if session is not None:
                    target_collection_id = int(session.collection_id)

            if target_collection_id is None:
                sync_finalization = {
                    "updated": False,
                    "reason": "missing_collection_id",
                    "eligible": True,
                }
            else:
                try:
                    sync_finalization = _refresh_local_collection_sync_metadata(
                        db,
                        civitai_collection_id=target_collection_id,
                        set_full_snapshot=True,
                    )
                    sync_finalization["eligible"] = True
                    db.commit()
                except Exception as exc:
                    db.rollback()
                    sync_finalization = {
                        "updated": False,
                        "eligible": True,
                        "reason": "auto_finalize_failed",
                        "civitai_collection_id": target_collection_id,
                        "error": str(exc),
                    }
        else:
            reason = "not_eligible"
            if is_retry_run:
                reason = "retry_run"
            elif len(new_ids) > 0:
                reason = "new_items_present"
            elif len(tombstoned) > 0:
                reason = "tombstoned_items_present"
            elif len(placeholders) > 0:
                reason = "placeholder_items_present"
            sync_finalization = {
                "updated": False,
                "eligible": False,
                "reason": reason,
            }
    finally:
        db.close()

    elapsed_ms = round((time.monotonic() - t0) * 1000)
    result = {
        "status": "ok",
        "timing": {"duration_ms": elapsed_ms},
        "data": {
            "existing": existing,
            "new": new_ids,
            "tombstoned": tombstoned,
            "placeholders": placeholders,
            "summary": {
                "total": len(image_ids),
                "existing": len(existing),
                "new": len(new_ids),
                "tombstoned": len(tombstoned),
                "placeholders": len(placeholders),
            },
            "sync_finalization": sync_finalization,
        },
    }
    _checkpoint_sync_step(session_id, 4, "complete", data=result.get("data"))
    return result


def _parse_sync_lab_image_ids(raw_ids: Optional[str]) -> list[int]:
    parsed_ids: list[int] = []
    for part in str(raw_ids or "").split(","):
        part = part.strip()
        if part:
            try:
                parsed_ids.append(int(part))
            except ValueError:
                continue
    return parsed_ids


def _resolve_sync_lab_stage_inputs(
    *,
    image_ids: str,
    selected_ids: Optional[str],
    limit: Optional[int],
) -> tuple[list[int], dict[str, int]]:
    requested = _parse_sync_lab_image_ids(image_ids)
    selected = _parse_sync_lab_image_ids(selected_ids)

    if selected:
        if requested:
            requested_set = set(requested)
            effective = [img_id for img_id in selected if img_id in requested_set]
        else:
            effective = list(selected)
    else:
        effective = list(requested)

    dedup_seen: set[int] = set()
    deduped: list[int] = []
    for img_id in effective:
        if img_id in dedup_seen:
            continue
        dedup_seen.add(img_id)
        deduped.append(img_id)

    truncated = list(deduped)
    skipped_by_limit = 0
    if isinstance(limit, int) and limit > 0 and len(truncated) > limit:
        skipped_by_limit = len(truncated) - limit
        truncated = truncated[:limit]

    counts = {
        "requested_total": len(requested),
        "selected_total": len(selected) if selected else len(deduped),
        "processed_total": len(truncated),
        "skipped_by_limit": skipped_by_limit,
    }
    return truncated, counts


def sync_lab_fetch_metadata(
    image_ids: str = Query(..., description="Comma-separated CivitAI image IDs"),
    selected_ids: Optional[str] = Query(
        None,
        description="Optional comma-separated subset of image IDs to process",
    ),
    limit: Optional[int] = Query(
        None,
        ge=1,
        description="Optional max number of IDs to process",
    ),
    session_id: Optional[str] = None,
):
    """Step 5: Fetch CivitAI API metadata (basic info + generation data) for specified image IDs (SSE streaming)."""
    import queue
    import threading

    parsed_ids, input_counts = _resolve_sync_lab_stage_inputs(
        image_ids=image_ids,
        selected_ids=selected_ids,
        limit=limit,
    )

    _checkpoint_sync_step(session_id, 5, "in_progress")

    def _sse(data: dict) -> str:
        return f"data: {json.dumps(data)}\n\n"

    def event_stream():
        q: queue.Queue = queue.Queue()
        t0 = time.monotonic()

        def worker():
            api = CivitaiAPI.get_instance()
            results: dict[str, dict[str, Any]] = {}
            errors: list[dict] = []

            for idx, img_id in enumerate(parsed_ids, 1):
                id_t0 = time.monotonic()
                info: dict[str, Any] = {
                    "image_id": img_id,
                    "basic_info": None,
                    "generation_data": None,
                    "timing_ms": 0,
                    "error": None,
                }
                try:
                    basic = api.fetch_basic_info_cached(img_id)
                    info["basic_info"] = basic
                except Exception as exc:
                    info["error"] = f"basic_info failed: {exc}"
                    errors.append(
                        {"image_id": img_id, "stage": "basic_info", "error": str(exc)}
                    )

                try:
                    gen_data = api.fetch_generation_data_cached(img_id)
                    info["generation_data"] = gen_data
                except Exception as exc:
                    err_msg = f"generation_data failed: {exc}"
                    info["error"] = (
                        (info["error"] + "; " + err_msg) if info["error"] else err_msg
                    )
                    errors.append(
                        {"image_id": img_id, "stage": "generation_data", "error": str(exc)}
                    )

                info["timing_ms"] = round((time.monotonic() - id_t0) * 1000)
                results[str(img_id)] = info

                q.put(("progress", img_id, idx, info["timing_ms"], info.get("error")))

            elapsed_ms = round((time.monotonic() - t0) * 1000)
            ok_count = sum(1 for v in results.values() if v.get("error") is None)
            complete_payload = {
                "status": "ok" if not errors else "partial",
                "timing": {
                    "duration_ms": elapsed_ms,
                    "per_image_avg_ms": round(elapsed_ms / max(len(parsed_ids), 1)),
                },
                "data": {
                    "total": len(parsed_ids),
                    "successful": ok_count,
                    "failed": len(errors),
                    "results": results,
                    "input_counts": input_counts,
                },
                "errors": errors,
            }
            _checkpoint_sync_step(session_id, 5, "complete", data={
                "total": len(parsed_ids),
                "successful": ok_count,
                "failed": len(errors),
            })
            q.put(("complete", complete_payload))

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        while thread.is_alive() or not q.empty():
            try:
                msg = q.get(timeout=0.2)
            except queue.Empty:
                continue

            kind = msg[0]
            if kind == "progress":
                _, img_id, done, timing_ms, err = msg
                yield _sse({
                    "type": "progress",
                    "image_id": img_id,
                    "done": done,
                    "total": len(parsed_ids),
                    "timing_ms": timing_ms,
                    "error": err,
                })
            elif kind == "complete":
                yield _sse({
                    "type": "complete",
                    **msg[1],
                })
                return

        thread.join(timeout=5)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def sync_lab_download(
    image_ids: str = Query(..., description="Comma-separated CivitAI image IDs"),
    selected_ids: Optional[str] = Query(
        None,
        description="Optional comma-separated subset of image IDs to process",
    ),
    limit: Optional[int] = Query(
        None,
        ge=1,
        description="Optional max number of IDs to process",
    ),
    session_id: Optional[str] = None,
):
    """Step 6: Download images for specified CivitAI image IDs (SSE streaming)."""
    import queue
    import threading

    parsed_ids, input_counts = _resolve_sync_lab_stage_inputs(
        image_ids=image_ids,
        selected_ids=selected_ids,
        limit=limit,
    )

    _checkpoint_sync_step(session_id, 6, "in_progress")

    def _sse(data: dict) -> str:
        return f"data: {json.dumps(data)}\n\n"

    def event_stream():
        q: queue.Queue = queue.Queue()
        t0 = time.monotonic()

        def worker():
            api = CivitaiAPI.get_instance()
            results: dict[str, dict[str, Any]] = {}
            errors: list[dict] = []

            for idx, img_id in enumerate(parsed_ids, 1):
                id_t0 = time.monotonic()
                result: dict[str, Any] = {
                    "image_id": img_id,
                    "status": "pending",
                    "timing_ms": 0,
                    "error": None,
                }
                try:
                    # Resolve metadata
                    target = _resolve_civitai_image_target(api, img_id)

                    # Fetch CivitAI tag records (best-effort, non-blocking)
                    try:
                        raw_tag_records = api.fetch_image_tag_records_cached(img_id)
                    except Exception:
                        raw_tag_records = None

                    # Download
                    image_url = target.get("image_url", "")
                    mime_type = target.get("mime_type")
                    file_size = target.get("declared_file_size")
                    download_result = _download_civitai_image_with_validation(
                        image_id=img_id,
                        target=target,
                    )

                    # Build a prepared import and store it in session
                    prepared = _PreparedCivitaiImport(
                        image_id=img_id,
                        image_url=image_url,
                        mime_type=download_result.selected_mime_type or mime_type,
                        declared_file_size=file_size,
                        preview_image_url=target.get("preview_image_url"),
                        original_filename=target.get("original_filename", f"civitai_{img_id}"),
                        artist_name=target.get("artist_name"),
                        source_url=_build_civitai_image_source_url(img_id),
                        temp_path=download_result.temp_path,
                        civitai_uuid=target.get("civitai_uuid"),
                        civitai_hash=target.get("civitai_hash"),
                        raw_basic_info=target.get("raw_basic_info"),
                        raw_generation_data=target.get("raw_generation_data"),
                        author_id=target.get("author_id"),
                        author_deleted=target.get("author_deleted", False),
                        author_original_name=target.get("author_original_name"),
                        civitai_post_id=target.get("civitai_post_id"),
                        civitai_post_title=target.get("civitai_post_title"),
                        civitai_post_index=target.get("civitai_post_index"),
                        raw_tag_records=raw_tag_records,
                    )
                    _sync_lab_prepared[img_id] = prepared

                    result["status"] = "downloaded"
                    result["temp_path"] = str(download_result.temp_path)
                    result["mime_type"] = download_result.selected_mime_type
                    result["selected_url"] = download_result.selected_url

                except _CivitaiImageUnavailableError as exc:
                    result["status"] = "unavailable"
                    result["error"] = str(exc)
                    errors.append(
                        {"image_id": img_id, "stage": "unavailable", "error": str(exc)}
                    )
                except Exception as exc:
                    result["status"] = "failed"
                    result["error"] = str(exc)
                    errors.append({"image_id": img_id, "stage": "download", "error": str(exc)})

                result["timing_ms"] = round((time.monotonic() - id_t0) * 1000)
                results[str(img_id)] = result

                q.put(("progress", img_id, idx, result["timing_ms"], result.get("status"), result.get("error")))

            elapsed_ms = round((time.monotonic() - t0) * 1000)
            ok_count = sum(1 for v in results.values() if v.get("status") == "downloaded")
            complete_payload = {
                "status": "ok" if not errors else "partial",
                "timing": {
                    "duration_ms": elapsed_ms,
                    "per_image_avg_ms": round(elapsed_ms / max(len(parsed_ids), 1)),
                },
                "data": {
                    "total": len(parsed_ids),
                    "downloaded": ok_count,
                    "unavailable": sum(1 for v in results.values() if v.get("status") == "unavailable"),
                    "failed": sum(1 for v in results.values() if v.get("status") == "failed"),
                    "results": results,
                    "input_counts": input_counts,
                },
                "errors": errors,
            }
            # Serialize prepared imports for session persistence (step 6→7 handoff)
            if session_id:
                try:
                    serialized = {}
                    for _img_id, _prep in _sync_lab_prepared.items():
                        if _img_id in parsed_ids:
                            serialized[str(_img_id)] = {
                                "image_id": _prep.image_id,
                                "image_url": _prep.image_url,
                                "mime_type": _prep.mime_type,
                                "declared_file_size": _prep.declared_file_size,
                                "preview_image_url": _prep.preview_image_url,
                                "original_filename": _prep.original_filename,
                                "artist_name": _prep.artist_name,
                                "source_url": _prep.source_url,
                                "temp_path": str(_prep.temp_path) if _prep.temp_path else None,
                                "civitai_uuid": _prep.civitai_uuid,
                                "civitai_hash": _prep.civitai_hash,
                                "raw_basic_info": _prep.raw_basic_info,
                                "raw_generation_data": _prep.raw_generation_data,
                                "author_id": _prep.author_id,
                                "author_deleted": _prep.author_deleted,
                                "author_original_name": _prep.author_original_name,
                                "civitai_post_id": _prep.civitai_post_id,
                                "civitai_post_title": _prep.civitai_post_title,
                                "civitai_post_index": _prep.civitai_post_index,
                                "raw_tag_records": _prep.raw_tag_records,
                            }
                    _checkpoint_sync_step(session_id, 6, "complete", data={
                        "total": len(parsed_ids),
                        "downloaded": ok_count,
                    })
                    # Store prepared_imports separately on the session
                    _db = SessionLocal()
                    try:
                        _sess = _db.query(SyncSession).filter(SyncSession.id == session_id).first()
                        if _sess:
                            _sess.prepared_imports = serialized
                            _db.commit()
                    finally:
                        _db.close()
                except Exception:
                    pass  # Checkpointing must never block
            q.put(("complete", complete_payload))

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        while thread.is_alive() or not q.empty():
            try:
                msg = q.get(timeout=0.2)
            except queue.Empty:
                continue

            kind = msg[0]
            if kind == "progress":
                _, img_id, done, timing_ms, status, err = msg
                yield _sse({
                    "type": "progress",
                    "image_id": img_id,
                    "done": done,
                    "total": len(parsed_ids),
                    "timing_ms": timing_ms,
                    "status": status,
                    "error": err,
                })
            elif kind == "complete":
                yield _sse({
                    "type": "complete",
                    **msg[1],
                })
                return

        thread.join(timeout=5)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def sync_lab_ingest(
    image_ids: str = Query(..., description="Comma-separated CivitAI image IDs"),
    selected_ids: Optional[str] = Query(
        None,
        description="Optional comma-separated subset of image IDs to process",
    ),
    limit: Optional[int] = Query(
        None,
        ge=1,
        description="Optional max number of IDs to process",
    ),
    collection_id: Optional[int] = Query(None, description="Civitai collection ID to attach"),
    session_id: Optional[str] = None,
):
    """Step 7: Ingest previously downloaded images into the library (SSE streaming).

    When an image has the same SHA256 as an already-ingested record, a duplicate
    asset record is created in ``image_resources/civitai_source_variants/`` instead
    of skipping.  Both records share ``file_hash`` but have unique ``file_path``.
    """
    import queue
    import threading

    parsed_ids, input_counts = _resolve_sync_lab_stage_inputs(
        image_ids=image_ids,
        selected_ids=selected_ids,
        limit=limit,
    )

    # Restore prepared imports from session if in-memory store is empty (server restart recovery)
    if session_id and not _sync_lab_prepared:
        try:
            _restore_db = SessionLocal()
            try:
                _restore_sess = _restore_db.query(SyncSession).filter(SyncSession.id == session_id).first()
                if _restore_sess and _restore_sess.prepared_imports:
                    for _k, _v in _restore_sess.prepared_imports.items():
                        _restored = _PreparedCivitaiImport(
                            image_id=_v["image_id"],
                            image_url=_v.get("image_url"),
                            mime_type=_v.get("mime_type"),
                            declared_file_size=_v.get("declared_file_size"),
                            preview_image_url=_v.get("preview_image_url"),
                            original_filename=_v.get("original_filename", f"civitai_{_v['image_id']}"),
                            artist_name=_v.get("artist_name"),
                            source_url=_v.get("source_url"),
                            temp_path=Path(_v["temp_path"]) if _v.get("temp_path") else None,
                            civitai_uuid=_v.get("civitai_uuid"),
                            civitai_hash=_v.get("civitai_hash"),
                            raw_basic_info=_v.get("raw_basic_info"),
                            raw_generation_data=_v.get("raw_generation_data"),
                            author_id=_v.get("author_id"),
                            author_deleted=_v.get("author_deleted", False),
                            author_original_name=_v.get("author_original_name"),
                            civitai_post_id=_v.get("civitai_post_id"),
                            civitai_post_title=_v.get("civitai_post_title"),
                            civitai_post_index=_v.get("civitai_post_index"),
                            raw_tag_records=_v.get("raw_tag_records"),
                        )
                        _sync_lab_prepared[int(_k)] = _restored
            finally:
                _restore_db.close()
        except Exception:
            pass  # Restore is best-effort

    _checkpoint_sync_step(session_id, 7, "in_progress")

    def _sse(data: dict) -> str:
        return f"data: {json.dumps(data)}\n\n"

    def event_stream():
        q: queue.Queue = queue.Queue()
        t0 = time.monotonic()

        def worker():
            results: dict[str, dict[str, Any]] = {}
            errors: list[dict] = []
            collection_sync_update: Optional[dict[str, Any]] = None

            db = SessionLocal()
            # Track (image_db_id, civitai_post_id, post_title) for variant grouping
            _post_image_pairs: list[tuple[int, int, Optional[str]]] = []
            try:
                # ── Ensure a local collection record exists before ingest ──
                # The frontend passes the CivitAI collection ID; if no local
                # CollectionModel exists yet, _ensure_image_in_collection cannot
                # resolve it and memberships are silently lost (SQLite does not
                # enforce FK constraints by default).  Create the collection
                # upfront so memberships resolve correctly.
                if collection_id is not None:
                    _coll_name = f"CivitAI Collection {collection_id}"
                    if session_id:
                        _sess = db.query(SyncSession).filter(SyncSession.id == session_id).first()
                        if _sess and _sess.collection_name:
                            _coll_name = _sess.collection_name
                    try:
                        _get_or_create_collection(
                            db,
                            _coll_name,
                            source="civitai",
                            civitai_collection_id=collection_id,
                        )
                        db.commit()
                    except Exception:
                        db.rollback()

                for idx, img_id in enumerate(parsed_ids, 1):
                    id_t0 = time.monotonic()
                    result: dict[str, Any] = {
                        "image_id": img_id,
                        "status": "pending",
                        "timing_ms": 0,
                        "error": None,
                    }

                    prepared = _sync_lab_prepared.get(img_id)
                    if prepared is None:
                        result["status"] = "skipped"
                        result["error"] = (
                            "No prepared download found — run the download step first"
                        )
                        errors.append(
                            {"image_id": img_id, "stage": "ingest", "error": result["error"]}
                        )
                        result["timing_ms"] = round((time.monotonic() - id_t0) * 1000)
                        results[str(img_id)] = result
                        q.put(("progress", img_id, idx, result["timing_ms"], result.get("status"), result.get("error")))
                        continue

                    try:
                        # Prefer exact/same-source reconciliation first. This avoids
                        # creating duplicate assets when we already have the same
                        # CivitAI image under a legacy source_url format.
                        existing_by_source = _find_existing_image_by_source_url(
                            db,
                            prepared.source_url,
                        )

                        if existing_by_source is not None:
                            ingest_result = _ingest_prepared_civitai_import(
                                db,
                                prepared=prepared,
                                attach_collection_id=collection_id,
                            )
                        else:
                            # No source-url match: hash collisions represent a distinct
                            # CivitAI asset sharing bytes with an existing local image.
                            temp_hash = _sha256_file(prepared.temp_path)
                            existing_records = _find_existing_by_file_hash(db, temp_hash)

                            if existing_records:
                                # Duplicate asset — create independent record in civitai_source_variants
                                ingest_result = _ingest_civitai_duplicate_asset(
                                    db,
                                    prepared=prepared,
                                    existing_records=existing_records,
                                    attach_collection_id=collection_id,
                                )
                            else:
                                # Normal ingest — no hash collision
                                ingest_result = _ingest_prepared_civitai_import(
                                    db,
                                    prepared=prepared,
                                    attach_collection_id=collection_id,
                                )

                        db.commit()

                        # Track for variant group creation (step after loop)
                        resolved_db_id = (
                            ingest_result.get("image_id")
                            or ingest_result.get("existing_image_id")
                        )
                        if isinstance(resolved_db_id, int) and prepared.civitai_post_id is not None:
                            _post_image_pairs.append(
                                (
                                    resolved_db_id,
                                    prepared.civitai_post_id,
                                    prepared.civitai_post_title,
                                )
                            )

                        result["status"] = "ingested"
                        result["ingest_result"] = ingest_result
                        # Clean up session store
                        _sync_lab_prepared.pop(img_id, None)
                    except Exception as exc:
                        db.rollback()
                        result["status"] = "failed"
                        result["error"] = str(exc)
                        errors.append(
                            {"image_id": img_id, "stage": "ingest", "error": str(exc)}
                        )

                    result["timing_ms"] = round((time.monotonic() - id_t0) * 1000)
                    results[str(img_id)] = result
                    q.put(("progress", img_id, idx, result["timing_ms"], result.get("status"), result.get("error")))

                # ── Create variant groups for images sharing a CivitAI post ──
                if _post_image_pairs:
                    _post_groups: dict[int, list[int]] = {}
                    _post_titles: dict[int, Optional[str]] = {}
                    for _db_id, _pid, _ptitle in _post_image_pairs:
                        _post_groups.setdefault(_pid, []).append(_db_id)
                        if _ptitle and _pid not in _post_titles:
                            _post_titles[_pid] = _ptitle
                    for _pid, _db_ids in _post_groups.items():
                        if len(_db_ids) >= 2:
                            try:
                                _ensure_variant_group_for_civitai_post(
                                    db,
                                    post_id=_pid,
                                    image_db_ids=_db_ids,
                                    post_title=_post_titles.get(_pid),
                                )
                                db.commit()
                            except Exception:
                                db.rollback()

                # Refresh collection sync metadata after ingest completes.
                if collection_id is not None:
                    try:
                        collection_sync_update = _refresh_local_collection_sync_metadata(
                            db,
                            civitai_collection_id=collection_id,
                            set_full_snapshot=(len(errors) == 0),
                        )
                        db.commit()
                    except Exception as exc:
                        db.rollback()
                        collection_sync_update = {
                            "updated": False,
                            "reason": "metadata_refresh_failed",
                            "civitai_collection_id": collection_id,
                            "error": str(exc),
                        }
                        errors.append(
                            {
                                "stage": "collection_metadata_refresh",
                                "collection_id": collection_id,
                                "error": str(exc),
                            }
                        )
            finally:
                db.close()

            elapsed_ms = round((time.monotonic() - t0) * 1000)
            ok_count = sum(1 for v in results.values() if v.get("status") == "ingested")
            complete_payload = {
                "status": "ok" if not errors else "partial",
                "timing": {
                    "duration_ms": elapsed_ms,
                    "per_image_avg_ms": round(elapsed_ms / max(len(parsed_ids), 1)),
                },
                "data": {
                    "total": len(parsed_ids),
                    "ingested": ok_count,
                    "skipped": sum(1 for v in results.values() if v.get("status") == "skipped"),
                    "failed": sum(1 for v in results.values() if v.get("status") == "failed"),
                    "results": results,
                    "collection_sync_update": collection_sync_update,
                    "input_counts": input_counts,
                },
                "errors": errors,
            }
            _checkpoint_sync_step(session_id, 7, "complete", data={
                "total": len(parsed_ids),
                "ingested": ok_count,
            })
            q.put(("complete", complete_payload))

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        while thread.is_alive() or not q.empty():
            try:
                msg = q.get(timeout=0.2)
            except queue.Empty:
                continue

            kind = msg[0]
            if kind == "progress":
                _, img_id, done, timing_ms, status, err = msg
                yield _sse({
                    "type": "progress",
                    "image_id": img_id,
                    "done": done,
                    "total": len(parsed_ids),
                    "timing_ms": timing_ms,
                    "status": status,
                    "error": err,
                })
            elif kind == "complete":
                yield _sse({
                    "type": "complete",
                    **msg[1],
                })
                return

        thread.join(timeout=5)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def sync_lab_collection_status(collection_id: int, db: Session = Depends(get_db)):
    """Get the current local DB state for a CivitAI collection."""
    collection = _resolve_local_collection_by_civitai_id(db, collection_id)
    if not collection:
        return {
            "status": "ok",
            "data": {
                "collection_id": collection_id,
                "exists_locally": False,
                "images": [],
                "total": 0,
            },
        }

    # Get images in this collection
    memberships = (
        db.query(ImageCollectionMembership, ImageModel)
        .join(ImageModel, ImageCollectionMembership.image_id == ImageModel.id)
        .filter(ImageCollectionMembership.collection_id == collection.id)
        .all()
    )

    images = []
    for membership, image in memberships:
        images.append(
            {
                "image_db_id": image.id,
                "file_hash": image.file_hash,
                "filename": image.file_name,
                "status": image.image_status,
                "source_url": image.source_url,
            }
        )

    return {
        "status": "ok",
        "data": {
            "collection_id": collection_id,
            "exists_locally": True,
            "local_collection_id": collection.id,
            "local_name": collection.name,
            "total": len(images),
            "images": images,
        },
    }


def sync_lab_refresh_collection_sync_metadata(
    collection_id: int,
    set_full_snapshot: bool = Query(
        False,
        description="When true, also update full-item snapshot fields",
    ),
    db: Session = Depends(get_db),
):
    """Manually refresh local sync metadata for a CivitAI collection."""
    data = _refresh_local_collection_sync_metadata(
        db,
        civitai_collection_id=collection_id,
        set_full_snapshot=set_full_snapshot,
    )
    db.commit()
    return {
        "status": "ok",
        "data": data,
    }


def sync_lab_page():
    """Serve the Sync Lab page."""
    return FileResponse("frontend/sync-lab.html")


if __name__ == "__main__":
    _main()
