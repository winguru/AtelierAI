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
import csv
import json
import re
import shutil
import tempfile
import time
import threading
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from fastapi import FastAPI, Depends, HTTPException, File, UploadFile, Form, Query, Response, Request, status
from typing import Any, Callable, List, Optional, Literal
from contextlib import asynccontextmanager
from urllib.parse import quote, urlparse

import requests
from PIL import Image
from sqlalchemy import text, func, or_, event
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, joinedload
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.gzip import GZipMiddleware
import uvicorn

import atelierai.config as app_config

IMAGE_LIBRARY_PATH = str(getattr(app_config, "IMAGE_LIBRARY_PATH", "image_library"))
IMAGE_RESOURCES_PATH = str(getattr(app_config, "IMAGE_RESOURCES_PATH", "image_resources"))
CURRENT_SCHEMA_VERSION = str(getattr(app_config, "CURRENT_SCHEMA_VERSION", "1.0"))
DATABASE_URL = str(getattr(app_config, "DATABASE_URL", "sqlite:///image_db.sqlite"))
ALLOW_SCHEMA_RESET = bool(getattr(app_config, "ALLOW_SCHEMA_RESET", False))

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
    Tag,
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
from services.gallery_tag_service import GalleryTagService
from services.image_query_service import ImageQueryService
from services.model_reference_service import ModelReferenceService
from services.taxonomy_service import TaxonomyService
from bootstrap import populate_initial_data
from schemas import (
    ScanRequest,
    ImageUpdateRequest,
    CivitaiImportRequest,
    CivitaiCollectionSyncRequest,
    CivitaiNsfwBackfillRequest,
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
)

try:
    import imagehash  # pyright: ignore[reportMissingImports]
except Exception:  # pragma: no cover - runtime dependency guard
    imagehash = None

try:
    import blurhash  # pyright: ignore[reportMissingImports]
except Exception:  # pragma: no cover - runtime dependency guard
    blurhash = None


_CIVITAI_COLLECTION_PATH_RE = re.compile(r"^/collections/(?P<collection_id>\d+)(?:/.*)?$")
_CIVITAI_IMPORT_NETWORK_CONCURRENCY = 3
_CIVITAI_SOURCE_VARIANT_DIRNAME = "civitai_source_variants"
_CIVITAI_COLLECTION_HEAD_PROBE_SIZE = 50
_CIVITAI_COLLECTION_FULL_VERIFY_MAX_AGE_SECONDS = 24 * 60 * 60
_VIDEO_FILE_SUFFIXES = {".mp4", ".webm", ".mov", ".mkv"}


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
    api_response_paths: dict[str, str] = field(default_factory=dict)  # Pre-saved response file paths
    effective_image_url: Optional[str] = None
    mismatch_static_temp_path: Optional[Path] = None
    mismatch_source_url: Optional[str] = None
    mismatch_mime_type: Optional[str] = None
    mismatch_file_hash: Optional[str] = None


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


@dataclass
class _SearchCacheEntry:
    value: Any
    expires_at_monotonic: float
    version: int


_SEARCH_CACHE_TTL_SECONDS = max(1.0, float(os.getenv("ATELIER_SEARCH_CACHE_TTL_SECONDS", "30")))
_SEARCH_CACHE_MAX_ITEMS = max(16, int(os.getenv("ATELIER_SEARCH_CACHE_MAX_ITEMS", "512")))
_JSON_CACHE_SCHEMA_VERSION = int(os.getenv("ATELIER_JSON_CACHE_SCHEMA_VERSION", "2"))
_FILTER_OPTIONS_CACHE_TTL_SECONDS = max(1.0, float(os.getenv("ATELIER_FILTER_OPTIONS_CACHE_TTL_SECONDS", "120")))
_search_cache_lock = threading.RLock()
_search_cache_version = 0
_search_cache: dict[str, _SearchCacheEntry] = {}


def _normalize_cache_list(values: Optional[list[str]]) -> list[str]:
    normalized: set[str] = set()
    for value in values or []:
        text_value = str(value or "").strip().lower()
        if text_value:
            normalized.add(text_value)
    return sorted(normalized)


def _build_search_cache_key(kind: str, *, payload: dict[str, Any]) -> str:
    canonical_payload = {
        key: value
        for key, value in payload.items()
    }
    canonical_payload["kind"] = kind
    return json.dumps(canonical_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _invalidate_search_cache(reason: str = "unspecified") -> None:
    del reason
    global _search_cache_version
    with _search_cache_lock:
        _search_cache_version += 1
        _search_cache.clear()


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


def _search_cache_put(key: str, value: Any, *, ttl_seconds: Optional[float] = None) -> None:
    now = time.monotonic()
    effective_ttl = _SEARCH_CACHE_TTL_SECONDS if ttl_seconds is None else max(1.0, float(ttl_seconds))
    with _search_cache_lock:
        if len(_search_cache) >= _SEARCH_CACHE_MAX_ITEMS:
            stale_keys = [
                cache_key
                for cache_key, entry in _search_cache.items()
                if entry.version != _search_cache_version or entry.expires_at_monotonic <= now
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
            if method == self._blocked_method:
                if path in self._exact_prefixes:
                    return False
                if any(path.startswith(prefix) for prefix in self._blocked_prefixes):
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
        access_logger.addFilter(_SuppressAccessPathFilter("GET", ["/tasks"]))
        access_logger.addFilter(_SuppressAccessPathFilter("GET", ["/images/state"]))


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


def _should_return_not_modified(request: Request, path: Path, headers: dict[str, str]) -> bool:
    if_none_match = request.headers.get("if-none-match")
    if if_none_match and _if_none_match_contains(if_none_match, headers.get("ETag", "")):
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

    file_modified_dt = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).replace(microsecond=0)
    return file_modified_dt <= modified_since_dt.astimezone(timezone.utc)


def _current_search_cache_version() -> int:
    with _search_cache_lock:
        return int(_search_cache_version)


def _build_json_cache_headers(cache_key: str, *, max_age_seconds: int = 0) -> dict[str, str]:
    version = _current_search_cache_version()
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


def _apply_image_list_filters(
    images_query,
    *,
    search: Optional[str] = None,
    source_sites: Optional[list[str]] = None,
    mimetypes: Optional[list[str]] = None,
    artist_names: Optional[list[str]] = None,
    collection_names: Optional[list[str]] = None,
    nsfw_ratings: Optional[list[str]] = None,
):
    return image_query_service.apply_image_list_filters(
        images_query,
        search=search,
        source_sites=source_sites,
        mimetypes=mimetypes,
        artist_names=artist_names,
        collection_names=collection_names,
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
    parser.add_argument("--reload", action="store_true", help="Enable Uvicorn autoreload.")
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
    _configure_uvicorn_access_logging(suppress_status_get_logs=args.suppress_status_get_logs)
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


def _ensure_user_nsfw_columns() -> None:
    """Backfill user NSFW override columns for existing sqlite databases."""
    with engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(images)")).fetchall()
        }
        if "user_nsfw_rating" not in existing:
            connection.execute(text("ALTER TABLE images ADD COLUMN user_nsfw_rating VARCHAR"))
        if "user_nsfw_safety_class" not in existing:
            connection.execute(text("ALTER TABLE images ADD COLUMN user_nsfw_safety_class VARCHAR"))


def _ensure_collection_sync_columns() -> None:
    """Backfill CivitAI collection sync metadata columns for existing sqlite databases."""
    with engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(collections)")).fetchall()
        }

        if "civitai_head_fingerprint" not in existing:
            connection.execute(text("ALTER TABLE collections ADD COLUMN civitai_head_fingerprint TEXT"))
        if "civitai_head_item_count" not in existing:
            connection.execute(text("ALTER TABLE collections ADD COLUMN civitai_head_item_count INTEGER"))
        if "civitai_head_has_more" not in existing:
            connection.execute(text("ALTER TABLE collections ADD COLUMN civitai_head_has_more BOOLEAN"))
        if "civitai_last_full_item_count" not in existing:
            connection.execute(text("ALTER TABLE collections ADD COLUMN civitai_last_full_item_count INTEGER"))
        if "civitai_last_synced_at" not in existing:
            connection.execute(text("ALTER TABLE collections ADD COLUMN civitai_last_synced_at DATETIME"))
        if "civitai_last_full_scan_at" not in existing:
            connection.execute(text("ALTER TABLE collections ADD COLUMN civitai_last_full_scan_at DATETIME"))


def _ensure_civitai_uuid_column() -> None:
    """Backfill CivitAI UUID column for existing sqlite databases."""
    with engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(images)")).fetchall()
        }

        if "civitai_uuid" not in existing:
            connection.execute(text("ALTER TABLE images ADD COLUMN civitai_uuid VARCHAR"))


def _ensure_civitai_hash_column() -> None:
    """Backfill CivitAI perceptual hash column for existing sqlite databases."""
    with engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(images)")).fetchall()
        }

        if "civitai_hash" not in existing:
            connection.execute(text("ALTER TABLE images ADD COLUMN civitai_hash VARCHAR"))


def _ensure_image_variant_columns() -> None:
    """Backfill image variant grouping columns for existing sqlite databases."""
    with engine.begin() as connection:
        existing = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(images)")).fetchall()
        }

        if "variant_group_key" not in existing:
            connection.execute(text("ALTER TABLE images ADD COLUMN variant_group_key VARCHAR"))
        if "variant_sort_index" not in existing:
            connection.execute(text("ALTER TABLE images ADD COLUMN variant_sort_index INTEGER"))
        if "variant_role" not in existing:
            connection.execute(text("ALTER TABLE images ADD COLUMN variant_role VARCHAR"))

        connection.execute(
            text(
                "UPDATE images "
                "SET variant_group_key = file_hash "
                "WHERE (variant_group_key IS NULL OR variant_group_key = '') "
                "AND file_hash IS NOT NULL AND file_hash != ''"
            )
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


def _detect_civitai_url_type(value: str) -> tuple[str, int]:
    """Detect whether a CivitAI URL/ID is an image or collection and return (type, id).
    
    Returns: tuple of ("image" or "collection", numeric_id)
    Raises HTTPException if the URL/ID doesn't match either pattern.
    """
    cleaned = (value or "").strip()
    
    # Try numeric ID first - if it's just a number, we can't auto-detect, so error
    if cleaned.isdigit():
        raise HTTPException(
            status_code=400,
            detail="Ambiguous numeric ID. Please provide a full CivitAI URL so we can detect if it's an image or collection.",
        )
    
    # Try to extract image ID
    try:
        image_id = extract_civitai_image_id(cleaned)
        if image_id is not None:
            return ("image", image_id)
    except Exception:
        pass
    
    # Try to extract collection ID
    try:
        parsed = urlparse(cleaned)
        hostname = (parsed.hostname or "").lower()
        if hostname in {"civitai.com", "www.civitai.com"}:
            match = _CIVITAI_COLLECTION_PATH_RE.match(parsed.path or "")
            if match:
                collection_id = int(match.group("collection_id"))
                return ("collection", collection_id)
    except Exception:
        pass
    
    # If neither pattern matched
    raise HTTPException(
        status_code=400,
        detail="Invalid CivitAI URL. Please provide a valid CivitAI image or collection URL.",
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

    return {
        source: sorted(names)
        for source, names in by_source.items()
    }


def _gallery_tag_names_by_source_db_only(db: Session) -> dict[str, list[str]]:
    """Fast path for filter option hydration using DB metadata only.

    This intentionally avoids sidecar reads to keep startup filters responsive.
    """
    by_source: dict[str, set[str]] = {
        "civitai": set(),
        "danbooru": set(),
        "prompt": set(),
        "user": set(),
    }

    rows = (
        db.query(ImageModel.json_metadata, ImageModel.exif_data)
        .filter(_active_image_filter())
        .all()
    )
    for json_metadata, exif_data in rows:
        payload: dict[str, Any] = {}
        if isinstance(json_metadata, dict):
            payload.update(json_metadata)
        if isinstance(exif_data, dict):
            payload["exif_data"] = exif_data
        extracted = _extract_image_scope_tag_names(payload)
        for source, names in extracted.items():
            by_source[source].update(names)

    return {
        source: sorted(names)
        for source, names in by_source.items()
    }


def _gallery_tag_usage_counts_by_source(db: Session) -> dict[str, dict[str, int]]:
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
        source: {
            name: counts[name]
            for name in sorted(counts)
        }
        for source, counts in by_source.items()
    }


def _gallery_tag_usage_counts_by_source_db_only(db: Session) -> dict[str, dict[str, int]]:
    """Fast path for tag usage counts using DB metadata only, avoiding sidecar reads.

    This provides approximate counts from json_metadata fields for responsive tree loads.
    """
    by_source: dict[str, dict[str, int]] = {
        "civitai": {},
        "danbooru": {},
        "prompt": {},
        "user": {},
    }

    rows = (
        db.query(ImageModel.json_metadata, ImageModel.exif_data)
        .filter(_active_image_filter())
        .all()
    )
    for json_metadata, exif_data in rows:
        payload: dict[str, Any] = {}
        if isinstance(json_metadata, dict):
            payload.update(json_metadata)
        if isinstance(exif_data, dict):
            payload["exif_data"] = exif_data
        extracted = _extract_image_scope_tag_names(payload)
        for source, names in extracted.items():
            bucket = by_source.setdefault(source, {})
            for name in names:
                normalized_name = _normalize_gallery_tag_text(name)
                if not normalized_name:
                    continue
                bucket[normalized_name] = int(bucket.get(normalized_name, 0)) + 1

    return {
        source: {
            name: counts[name]
            for name in sorted(counts)
        }
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
    external_tag_id: Optional[str] = None,
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
                    if str(term.external_tag_id or "") != external_tag_id:
                        term.external_tag_id = external_tag_id
                        changed = True
                    if str(term.external_name or "") != raw_name:
                        term.external_name = raw_name
                        changed = True
                    if str(term.normalized_external_name or "") != normalized_name:
                        term.normalized_external_name = normalized_name
                        changed = True
                    if int(term.concept_id or 0) != int(concept.id):
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
        current_id = int(current.id)
        if current_id in seen:
            break
        seen.add(current_id)
        if int(current.parent_concept_id) == ancestor_id:
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

    civitai_uuid = str(target.get("civitai_uuid") or "").strip() or _extract_civitai_uuid_from_url_hash(url_hash)
    if civitai_uuid:
        urls.append(f"https://image-b2.civitai.com/file/civitai-media-cache/{civitai_uuid}/original")

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

    candidate_urls = _build_civitai_video_candidate_urls(target) if declared_video else [str(target.get("image_url") or "").strip()]
    candidate_urls = [url for url in candidate_urls if url]
    if not candidate_urls:
        raise HTTPException(status_code=502, detail=f"CivitAI image {image_id} did not include a downloadable URL.")

    mismatch_temp_path: Optional[Path] = None
    mismatch_source_url: Optional[str] = None
    mismatch_mime_type: Optional[str] = None
    mismatch_file_hash: Optional[str] = None

    for image_url in candidate_urls:
        temp_path = _download_civitai_image(
            image_url=image_url,
            image_id=image_id,
            mime_type=target.get("mime_type"),
            declared_file_size=declared_file_size,
        )
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
        f"https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA/"
        f"{clean_hash}/{transform_segment}/{safe_name}"
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


def _get_civitai_source_variant_path(image_id: int, actual_path: Path, actual_mime_type: Optional[str]) -> Path:
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
    declared_video_like = declared_mime_type.startswith("video/") or _url_looks_like_video(prepared.image_url)
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
        variant_path = _get_civitai_source_variant_path(prepared.image_id, actual_path, actual_mime_type)
        if not variant_path.exists() or actual_path.stat().st_mtime_ns > variant_path.stat().st_mtime_ns:
            shutil.copy2(actual_path, variant_path)
        variant_file_hash = _sha256_file(variant_path)
        variant_metadata = {
            "image_id": prepared.image_id,
            "image_db_id": image_db_id,
            "declared_mimetype": prepared.mime_type,
            "actual_mimetype": image.mimetype,
            "declared_filename": prepared.original_filename,
            "library_file_path": str(image.file_path),
            "library_file_hash": image.file_hash,
            "variant_file_path": str(variant_path.relative_to(Path(IMAGE_RESOURCES_PATH))),
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
            preview_mime_type = _normalize_mime_type(response.headers.get("Content-Type"))
            if preview_mime_type.startswith("image/"):
                preview_extension = _guess_suffix(preview_mime_type)
                variant_root = Path(IMAGE_RESOURCES_PATH) / _CIVITAI_SOURCE_VARIANT_DIRNAME
                variant_root.mkdir(parents=True, exist_ok=True)
                temp_preview_path = variant_root / f"temp_preview_{prepared.image_id}{preview_extension}"
                with open(temp_preview_path, "wb") as handle:
                    for chunk in response.iter_content(chunk_size=1024 * 256):
                        if chunk:
                            handle.write(chunk)

                variant_file_hash = _sha256_file(temp_preview_path)
                variant_path = variant_root / f"{variant_file_hash}{preview_extension}"
                if variant_path.exists():
                    temp_preview_path.unlink(missing_ok=True)
                else:
                    temp_preview_path.rename(variant_path)

                variant_metadata = {
                    "image_id": prepared.image_id,
                    "image_db_id": image_db_id,
                    "declared_mimetype": prepared.mime_type,
                    "actual_mimetype": preview_mime_type,
                    "declared_filename": prepared.original_filename,
                    "library_file_path": str(image.file_path),
                    "library_file_hash": image.file_hash,
                    "variant_file_path": str(variant_path.relative_to(Path(IMAGE_RESOURCES_PATH))),
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

    merged_json = dict(image.json_metadata) if isinstance(image.json_metadata, dict) else {}
    merged_json["civitai_source_variant"] = variant_metadata
    image.json_metadata = merged_json

    processor = ImageProcessor(str(actual_path), db, IMAGE_LIBRARY_PATH)
    processor.save_json_metadata(
        actual_path,
        image,
        additional_data={"civitai_source_variant": variant_metadata},
    )


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

    variant_path = _get_civitai_source_variant_path(prepared.image_id, mismatch_path, detected_mime)
    if not variant_path.exists() or mismatch_path.stat().st_mtime_ns > variant_path.stat().st_mtime_ns:
        shutil.copy2(mismatch_path, variant_path)

    variant_file_hash = _sha256_file(variant_path)
    variant_metadata = {
        "image_id": prepared.image_id,
        "image_db_id": image_db_id,
        "declared_mimetype": prepared.mime_type,
        "actual_mimetype": detected_mime or "image/unknown",
        "declared_filename": prepared.original_filename,
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

    merged_json = dict(image.json_metadata) if isinstance(image.json_metadata, dict) else {}
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
    """Save raw CivitAI API responses to disk for debugging and audit.
    
    Returns dict with keys like 'raw_basic_info_path', 'raw_generation_data_path', 'raw_infinite_path'.
    """
    saved_paths: dict[str, str] = {}
    
    if not civitai_uuid:
        return saved_paths
    
    try:
        api_response_dir = Path(IMAGE_RESOURCES_PATH) / "civitai_api_responses"
        api_response_dir.mkdir(parents=True, exist_ok=True)
        
        if isinstance(raw_basic_info, dict):
            basic_info_path = api_response_dir / f"civitai_image_get_{civitai_uuid}.json"
            with open(basic_info_path, "w", encoding="utf-8") as f:
                json.dump(raw_basic_info, f, indent=2)
            saved_paths["raw_basic_info_path"] = str(basic_info_path.relative_to(Path(IMAGE_RESOURCES_PATH)))
        
        if isinstance(raw_generation_data, dict):
            gen_data_path = api_response_dir / f"civitai_image_getGenerationData_{civitai_uuid}.json"
            with open(gen_data_path, "w", encoding="utf-8") as f:
                json.dump(raw_generation_data, f, indent=2)
            saved_paths["raw_generation_data_path"] = str(gen_data_path.relative_to(Path(IMAGE_RESOURCES_PATH)))
        
        if isinstance(raw_infinite, dict):
            infinite_path = api_response_dir / f"civitai_image_getInfinite_{civitai_uuid}.json"
            with open(infinite_path, "w", encoding="utf-8") as f:
                json.dump(raw_infinite, f, indent=2)
            saved_paths["raw_infinite_path"] = str(infinite_path.relative_to(Path(IMAGE_RESOURCES_PATH)))
    except Exception as exc:
        # If saving fails, log but don't crash the import
        print(f"[ERROR] Failed to save CivitAI API responses for UUID {civitai_uuid}: {exc}")
    
    return saved_paths


def _archive_civitai_collection_items(items: list[dict[str, Any]]) -> None:
    """Persist get.Infinite item payloads for every scraped collection image."""
    if not items:
        return

    api_response_dir = Path(IMAGE_RESOURCES_PATH) / "civitai_api_responses"
    api_response_dir.mkdir(parents=True, exist_ok=True)

    for item in items:
        if not isinstance(item, dict):
            continue
        raw_url = item.get("url")
        uuid_value = _extract_civitai_uuid_from_url_hash(raw_url if isinstance(raw_url, str) else None)
        key = uuid_value
        if not key:
            raw_id = item.get("id")
            try:
                key = f"imageid_{int(raw_id)}"
            except (TypeError, ValueError):
                key = None
        if not key:
            continue

        out_path = api_response_dir / f"civitai_image_getInfinite_{key}.json"
        try:
            with open(out_path, "w", encoding="utf-8") as handle:
                json.dump(item, handle, indent=2)
        except Exception:
            # Best-effort archival only.
            continue


def _resolve_civitai_image_target(api: CivitaiAPI, image_id: int, *, strict: bool = False) -> dict:
    try:
        basic_info = api.fetch_basic_info(image_id, strict=strict)
    except CivitaiRequestError as exc:
        if exc.status_code == 404:
            raise _CivitaiImageUnavailableError(
                image_id=image_id,
                endpoint="image.get",
                reason=str(exc),
                status_code=exc.status_code,
            ) from exc
        raise

    try:
        generation_data = api.fetch_generation_data(image_id, strict=strict)
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

    image = CivitaiImage.from_single_image(
        basic_info=basic_info or {"id": image_id},
        generation_data=generation_data or {},
        api=api,
    )
    image_data = image.to_dict(include_full_url=True)

    mime_type = (basic_info or {}).get("mimeType") if isinstance(basic_info, dict) else None
    declared_file_size = None
    if isinstance(basic_info, dict):
        metadata = basic_info.get("metadata")
        if isinstance(metadata, dict):
            raw_size = metadata.get("size")
            try:
                declared_file_size = int(raw_size) if raw_size is not None else None
            except (TypeError, ValueError):
                declared_file_size = None
    preferred_name = (basic_info or {}).get("name") if isinstance(basic_info, dict) else None
    original_filename = _build_civitai_original_filename(
        image_id=image_id,
        preferred_name=preferred_name,
        image_url=image_data.get("url") or "",
        mime_type=mime_type,
    )
    url_hash = (basic_info or {}).get("url") if isinstance(basic_info, dict) else None
    perceptual_hash = (basic_info or {}).get("hash") if isinstance(basic_info, dict) else None
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
    preview_image_url = _build_civitai_media_url(
        url_hash,
        original_filename,
        mime_type,
        use_video_transcode=False,
    ) if _normalize_mime_type(mime_type).startswith("video/") else None

    basic_user = basic_info.get("user", {}) if isinstance(basic_info, dict) else {}
    author_name = image_data.get("author")
    if not author_name and isinstance(basic_user, dict):
        author_name = basic_user.get("username")

    return {
        "image_id": image_id,
        "image_url": image_url,
        "mime_type": mime_type,
        "declared_file_size": declared_file_size,
        "preview_image_url": preview_image_url,
        "original_filename": original_filename,
        "artist_name": author_name,
        "source_url": f"https://civitai.com/images/{image_id}",
        "civitai_url_hash": url_hash,
        "civitai_uuid": civitai_uuid,
        "civitai_hash": perceptual_hash,
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


def _normalize_civitai_platform_name(generation_data: dict, image_data: dict, basic_info: dict, meta: dict) -> str:
    process_value = str(_first_meaningful_civitai_value(generation_data.get("process"), image_data.get("process")) or "").strip().lower()
    engine_value = _first_meaningful_civitai_value(image_data.get("engine"), meta.get("engine"))
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


def _normalize_civitai_method_family(generation_data: dict, image_data: dict, meta: dict) -> Optional[str]:
    process_value = str(_first_meaningful_civitai_value(generation_data.get("process"), image_data.get("process")) or "").strip().lower()
    workflow_value = _first_meaningful_civitai_value(meta.get("workflow"), image_data.get("workflow"))
    techniques = _list_payload(generation_data.get("techniques"))
    first_technique = techniques[0] if techniques and isinstance(techniques[0], dict) else {}
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
    return _first_meaningful_civitai_value(meta.get("workflow"), image_data.get("workflow"), image_data.get("process"))


def _resolve_civitai_generation_dimensions(meta: dict, image_data: dict, generation_data: dict, basic_info: dict) -> tuple[Optional[int], Optional[int], Optional[int], Optional[int]]:
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
    normalized_name = display_name.lower() if isinstance(display_name, str) and display_name.strip() else None
    resolved_model_id = _coerce_optional_int(civitai_model_id)
    resolved_model_version_id = _coerce_optional_int(civitai_model_version_id)
    resolved_identifier = str(source_identifier or resolved_model_id or "").strip() or None
    resolved_is_primary = normalized_type == "checkpoint" if is_primary is None else bool(is_primary)
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
        candidate_payloads.extend([comfy_payload.get("prompt"), comfy_payload.get("workflow"), comfy_payload])
    candidate_payloads.extend([
        workflow_payload,
        workflow_payload.get("prompt") if isinstance(workflow_payload, dict) else None,
        workflow_payload.get("workflow") if isinstance(workflow_payload, dict) else None,
    ])
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


def _collect_comfy_upstream_node_ids(graph: dict, start_node_id: str, input_names: Optional[set[str]] = None) -> set[str]:
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


def _count_upstream_comfy_sampler_nodes(graph: dict, node_id: str, memo: dict[str, int]) -> int:
    normalized_id = str(node_id)
    if normalized_id in memo:
        return memo[normalized_id]
    total = 0
    for upstream_id in _collect_comfy_upstream_node_ids(graph, normalized_id):
        upstream_node = _get_comfy_node(graph, upstream_id)
        if _is_comfy_generation_stage_node_class(_get_comfy_node_class(upstream_node).lower()):
            total += 1 + _count_upstream_comfy_sampler_nodes(graph, upstream_id, memo)
    memo[normalized_id] = total
    return total


def _is_comfy_generation_stage_node_class(node_class_lower: str) -> bool:
    return "ksampler" in node_class_lower or node_class_lower == "ultimatesdupscale"


def _unwrap_workflow_resource_identifier(value: Any) -> Any:
    if isinstance(value, dict):
        for key in ("content", "name", "model_name", "ckpt_name", "vae_name", "lora_name"):
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
        if is_empty_value(merged.get(field_name)) and not is_empty_value(incoming.get(field_name)):
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
        source_identifier=air_details.get("source_identifier") or (str(resolved_identifier).strip() if resolved_identifier is not None else None),
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
        if "cliptextencode" in node_class.lower() and str(inputs.get("text") or "").strip():
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


def _resolve_comfy_image_dimensions(graph: dict, reference: Any, fallback_width: Optional[int], fallback_height: Optional[int], visited: Optional[set[str]] = None) -> tuple[Optional[int], Optional[int]]:
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
        return _coerce_optional_int(inputs.get("width")), _coerce_optional_int(inputs.get("height"))
    if node_class == "loadimage":
        return fallback_width, fallback_height
    if node_class == "imageupscalewithmodel":
        return _resolve_comfy_image_dimensions(graph, inputs.get("image"), fallback_width, fallback_height, visited)
    if node_class == "vaedecode":
        return _resolve_comfy_latent_dimensions(graph, inputs.get("samples"), fallback_width, fallback_height, visited)
    return fallback_width, fallback_height


def _resolve_comfy_latent_dimensions(graph: dict, reference: Any, fallback_width: Optional[int], fallback_height: Optional[int], visited: Optional[set[str]] = None) -> tuple[Optional[int], Optional[int]]:
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
    if node_class in {"emptylatentimage", "emptysd3latentimage", "emptyhunyuanlatentvideo"}:
        return _coerce_optional_int(inputs.get("width")), _coerce_optional_int(inputs.get("height"))
    if node_class == "latentupscale":
        return _coerce_optional_int(inputs.get("width")), _coerce_optional_int(inputs.get("height"))
    if node_class == "latentupscaleby":
        source_width, source_height = _resolve_comfy_latent_dimensions(graph, inputs.get("samples"), fallback_width, fallback_height, visited)
        scale_by = _coerce_optional_float(inputs.get("scale_by"))
        if source_width is not None and source_height is not None and scale_by:
            return int(round(source_width * scale_by)), int(round(source_height * scale_by))
        return source_width, source_height
    if "ksampler" in node_class:
        return _resolve_comfy_latent_dimensions(graph, inputs.get("latent_image"), fallback_width, fallback_height, visited)
    if node_class == "vaeencode":
        return _resolve_comfy_image_dimensions(graph, inputs.get("pixels"), fallback_width, fallback_height, visited)
    return fallback_width, fallback_height


def _collect_comfy_model_resources(graph: dict, reference: Any, inherited_weight: Any = None, visited: Optional[set[str]] = None) -> list[dict]:
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

    if node_class_lower.startswith("checkpointloader") or "checkpointloader" in node_class_lower:
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
        model1_weight = _first_meaningful_civitai_value(inputs.get("model1_weight"), inputs.get("weight1"), allow_zero=True)
        model2_weight = _first_meaningful_civitai_value(inputs.get("model2_weight"), inputs.get("weight2"), allow_zero=True)
        merged_resources = []
        merged_resources.extend(_collect_comfy_model_resources(graph, inputs.get("model1"), model1_weight, visited))
        merged_resources.extend(_collect_comfy_model_resources(graph, inputs.get("model2"), model2_weight, visited))
        return merged_resources

    if node_class_lower in {"cr apply lora stack", "reroute"}:
        for input_name in ("model", "input", "", "source"):
            if input_name in inputs:
                nested_resources = _collect_comfy_model_resources(graph, inputs.get(input_name), inherited_weight, visited)
                if nested_resources:
                    return nested_resources
        for input_value in inputs.values():
            nested_resources = _collect_comfy_model_resources(graph, input_value, inherited_weight, visited)
            if nested_resources:
                return nested_resources
        return []

    return []


def _collect_comfy_stage_resources(graph: dict, sampler_node_id: str, prompt_nodes: list[dict]) -> list[dict]:
    resource_nodes: list[dict] = []
    sampler_node = _get_comfy_node(graph, sampler_node_id)
    sampler_inputs = _get_comfy_node_inputs(sampler_node)
    resource_nodes.extend(_collect_comfy_model_resources(graph, sampler_inputs.get("model")))
    upstream_ids = _collect_comfy_upstream_node_ids(graph, sampler_node_id)
    for upstream_id in upstream_ids:
        node = _get_comfy_node(graph, upstream_id)
        node_class = _get_comfy_node_class(node)
        node_class_lower = node_class.lower()
        inputs = _get_comfy_node_inputs(node)
        title = _get_comfy_node_title(node)
        if node_class_lower.startswith("checkpointloader") or "checkpointloader" in node_class_lower:
            resource_nodes.append(
                _build_workflow_resource_preview(
                    "checkpoint",
                    inputs.get("ckpt_name") or inputs.get("model_name"),
                    {"node_id": upstream_id, "node_class": node_class, "node_title": title, "inputs": inputs},
                )
            )
        elif node_class_lower == "loraloader":
            resource_nodes.append(
                _build_workflow_resource_preview(
                    "lora",
                    inputs.get("lora_name"),
                    {"node_id": upstream_id, "node_class": node_class, "node_title": title, "inputs": inputs},
                    strength_model=inputs.get("strength_model"),
                    strength_clip=inputs.get("strength_clip"),
                )
            )
        elif node_class_lower == "vaeloader":
            resource_nodes.append(
                _build_workflow_resource_preview(
                    "vae",
                    inputs.get("vae_name"),
                    {"node_id": upstream_id, "node_class": node_class, "node_title": title, "inputs": inputs},
                )
            )
        elif node_class_lower == "upscalemodelloader":
            resource_nodes.append(
                _build_workflow_resource_preview(
                    "upscaler",
                    inputs.get("model_name"),
                    {"node_id": upstream_id, "node_class": node_class, "node_title": title, "inputs": inputs},
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
        for embedding_token in _extract_embedding_tokens_from_prompt(prompt_node.get("text") or ""):
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
            _coerce_optional_int(item) if _coerce_optional_int(item) is not None else 10**9,
            str(item),
        )
    )

    stages: list[dict] = []
    for stage_index, sampler_id in enumerate(sampler_ids):
        sampler_node = _get_comfy_node(graph, sampler_id)
        sampler_class_lower = _get_comfy_node_class(sampler_node).lower()
        sampler_inputs = _get_comfy_node_inputs(sampler_node)
        prompt_nodes_positive = _collect_comfy_prompt_texts(graph, sampler_inputs.get("positive"))
        prompt_nodes_negative = _collect_comfy_prompt_texts(graph, sampler_inputs.get("negative"))
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
        upstream_classes = {_get_comfy_node_class(_get_comfy_node(graph, upstream_id)).lower() for upstream_id in upstream_ids}
        has_loaded_input = bool(source_assets)
        has_latent_upscale = any(node_class.startswith("latentupscale") for node_class in upstream_classes)
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
        for prompt_role, prompt_nodes in (("positive", prompt_nodes_positive), ("negative", prompt_nodes_negative)):
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

        stage_resources = _collect_comfy_stage_resources(graph, sampler_id, prompt_nodes_all)
        output_dimensions = None
        if stage_index == len(sampler_ids) - 1 and output_width and output_height:
            output_dimensions = {"output_width": output_width, "output_height": output_height}

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
                "input_asset_ref": source_assets[0]["encoded_payload_ref"] if source_assets else None,
                "width": stage_width,
                "height": stage_height,
                "base_width": generation_width if stage_index == 0 else stage_width,
                "base_height": generation_height if stage_index == 0 else stage_height,
                "sampler_name": str(sampler_inputs.get("sampler_name") or "").strip() or None,
                "scheduler_name": str(sampler_inputs.get("scheduler") or "").strip() or None,
                "steps": _first_meaningful_civitai_value(sampler_inputs.get("steps")),
                "cfg_scale": _round_preview_float(_first_meaningful_civitai_value(sampler_inputs.get("cfg"), sampler_inputs.get("cfg_scale"))),
                "seed": None if sampler_inputs.get("seed") in {None, ""} else str(sampler_inputs.get("seed")),
                "clip_skip": sampler_inputs.get("clip_skip"),
                "strength": _round_preview_float(sampler_inputs.get("strength")),
                "denoise_strength": _round_preview_float(denoise_strength),
                "guidance_notes": None,
                "compatibility_json": {
                    "node_id": sampler_id,
                    "node_class": _get_comfy_node_class(sampler_node),
                    "node_title": _get_comfy_node_title(sampler_node),
                    "upstream_sampler_count": _count_upstream_comfy_sampler_nodes(graph, sampler_id, sampler_depths),
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
    sidecar_path = (Path(IMAGE_LIBRARY_PATH) / str(image.file_path)).with_suffix(".json")
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
    average_hash_builder = getattr(ih, "average_hash", None) or getattr(ih, "ahash", None)
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


def _compute_bit_distance(local_int: int, local_bits: int, candidate_int: int, candidate_bits: int) -> tuple[int, int, bool]:
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
    return {"base16": b16, "base32": b32, "base64": b64, "uuencode": "\n".join(uu_lines)}


def _extract_primary_civitai_image_hash(image: ImageModel, civitai_payload: dict[str, Any]) -> Optional[str]:
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


def _suggest_blurhash_component_pairs(target_hash: Optional[str]) -> list[tuple[int, int]]:
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
        lambda: encode_fn(image_rgb, x_components=x_components, y_components=y_components),
        lambda: encode_fn(image_rgb, components_x=x_components, components_y=y_components),
        lambda: encode_fn(pixel_rows, x_components=x_components, y_components=y_components),
        lambda: encode_fn(pixel_rows, components_x=x_components, components_y=y_components),
    )

    for attempt in attempts:
        try:
            encoded = attempt()
        except Exception:
            continue
        if isinstance(encoded, str) and encoded.strip():
            return encoded.strip()
    return None


def _decode_blurhash_pixels(blurhash_value: Optional[str], *, width: int = 32, height: int = 32) -> Optional[list[list[list[int]]]]:
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


def _blurhash_preview_data_url(blurhash_value: Optional[str], *, width: int = 32, height: int = 32) -> Optional[str]:
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


def _blurhash_preview_distance(candidate_hash: Optional[str], reference_hash: Optional[str], *, width: int = 32, height: int = 32) -> Optional[dict[str, Any]]:
    candidate_pixels = _decode_blurhash_pixels(candidate_hash, width=width, height=height)
    reference_pixels = _decode_blurhash_pixels(reference_hash, width=width, height=height)
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
                delta = float(candidate_pixel[channel_index]) - float(reference_pixel[channel_index])
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


def _build_blurhash_report(image_path: Path, *, civitai_hash: Optional[str]) -> dict[str, Any]:
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
        report["reason"] = "BlurHash package is not installed. Install blurhash to enable this comparison."
        return report

    target_preview = _blurhash_preview_data_url(civitai_hash_text, width=preview_width, height=preview_height)
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
            resized_image.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
            resized_size = resized_image.size
            width, height = resized_image.size
            flat_pixels = list(resized_image.getdata())
            pixel_rows = [
                [tuple(pixel) for pixel in flat_pixels[row_index * width : (row_index + 1) * width]]
                for row_index in range(height)
            ]
            report["analysis_size"] = {"width": resized_size[0], "height": resized_size[1]}
            report["source_size"] = {"width": original_size[0], "height": original_size[1]}
            report["was_resized"] = original_size != resized_size
            run_started = time.monotonic()
            for x_components, y_components in _suggest_blurhash_component_pairs(civitai_hash_text):
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
    report["best_candidate"] = report["candidates"].get(best_candidate_key) if best_candidate_key else None
    return report


def _build_perceptual_hash_suite(image_path: Path, hash_size: int = 8) -> dict[str, dict[str, Any]]:
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
    db_blurhash = db_json.get("blurhash") if isinstance(db_json.get("blurhash"), dict) else None
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


def _compute_blurhash_4x4(image_path: Path, *, max_dimension: int = 128) -> Optional[str]:
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
            [tuple(pixel) for pixel in flat_pixels[row_index * width : (row_index + 1) * width]]
            for row_index in range(height)
        ]
        return _encode_blurhash_with_fallbacks(
            resized,
            pixel_rows,
            4,
            4,
        )


def _resolve_local_blurhash_4x4(image: ImageModel, image_path: Path) -> tuple[Optional[str], str]:
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


def _build_civitai_hash_comparison_payload(local_hashes: dict[str, dict[str, Any]], civitai_payload: dict) -> dict:
    candidates = []
    seen_values: set[str] = set()
    for item in _iter_hash_strings(civitai_payload):
        value_text = str(item.get("value") or "").strip()
        if not value_text or value_text in seen_values:
            continue
        seen_values.add(value_text)
        candidates.append({
            "path": str(item.get("path") or "civitai"),
            "value": value_text,
        })
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
        "source_url": asset.source_url,
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
        "source_assets": [_serialize_generation_source_asset(item) for item in stage.source_assets],
        "field_values": [_serialize_generation_field_value(item) for item in stage.field_values],
        "provenance_records": [_serialize_generation_provenance(item) for item in stage.provenance_records],
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
        "prompts": [_serialize_generation_prompt(item) for item in process.prompts if item.stage_id is None],
        "resources": [_serialize_generation_resource(item) for item in process.resources if item.stage_id is None],
        "source_assets": [_serialize_generation_source_asset(item) for item in process.source_assets if item.stage_id is None],
        "field_values": [_serialize_generation_field_value(item) for item in process.field_values if item.stage_id is None],
        "provenance_records": [_serialize_generation_provenance(item) for item in process.provenance_records if item.stage_id is None],
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
    resource_type = str(resource.get("modelType") or resource.get("type") or "other").strip().lower() or "other"
    display_name = str(resource.get("modelName") or resource.get("name") or "").strip() or None
    version_name = str(resource.get("versionName") or "").strip() or None
    base_model_name = str(resource.get("baseModel") or "").strip() or None
    model_id = _first_meaningful_civitai_value(resource.get("modelId"), resource.get("id"))
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


def _build_civitai_normalized_preview(
    image_id: int,
    basic_info: dict,
    generation_data: dict,
    image_data: dict,
) -> dict:
    meta = generation_data.get("meta") if isinstance(generation_data, dict) else {}
    if not isinstance(meta, dict):
        meta = {}
    raw_resources = generation_data.get("resources") if isinstance(generation_data, dict) else []
    if not isinstance(raw_resources, list):
        raw_resources = []
    method_family = _normalize_civitai_method_family(generation_data, image_data, meta)
    platform_name = _normalize_civitai_platform_name(generation_data, image_data, basic_info, meta)
    workflow_value = _resolve_civitai_workflow_payload(meta, image_data)
    comfy_prompt_graph = _extract_comfy_prompt_graph(workflow_value, meta)
    generation_width, generation_height, output_width, output_height = _resolve_civitai_generation_dimensions(
        meta,
        image_data,
        generation_data,
        basic_info,
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

    raw_stage_resources = [_normalize_civitai_resource_preview(item) for item in raw_resources if isinstance(item, dict)]
    compatibility_json = {"draft": image_data.get("draft")} if image_data.get("draft") is not None else None
    has_generation_payload = bool(meta or raw_resources or positive_prompt or negative_prompt)
    normalized_seed = _first_meaningful_civitai_value(meta.get("seed"), image_data.get("seed"))
    stages = _build_comfy_workflow_stages(
        comfy_prompt_graph,
        generation_data,
        generation_width,
        generation_height,
        output_width,
        output_height,
    ) if comfy_prompt_graph else []

    if stages:
        stages = [dict(stage) for stage in stages]
        stages[0]["resources"] = _dedupe_resource_previews(raw_stage_resources + _list_payload(stages[0].get("resources")))
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
                "steps": _first_meaningful_civitai_value(meta.get("steps"), image_data.get("steps")),
                "cfg_scale": _round_preview_float(_first_meaningful_civitai_value(meta.get("cfgScale"), image_data.get("cfg_scale"))),
                "seed": None if normalized_seed is None else str(normalized_seed),
                "clip_skip": meta.get("clipSkip") if meta.get("clipSkip") is not None else image_data.get("clip_skip"),
                "strength": _round_preview_float(meta.get("strength")),
                "denoise_strength": _round_preview_float(_first_meaningful_civitai_value(meta.get("denoise"), meta.get("denoiseStrength"), allow_zero=True)),
                "guidance_notes": None,
                "compatibility_json": {
                    **(compatibility_json or {}),
                    **({"output_width": output_width, "output_height": output_height} if output_width and output_height else {}),
                } or None,
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
    stage_source_assets = any(_list_payload(stage.get("source_assets")) for stage in stages)

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
                "has_refiners": len(stages) > 1 or any(
                    item.get("resource_type") == "refiner"
                    for stage in stages
                    for item in _list_payload(stage.get("resources"))
                ),
                "has_video_generation": str(basic_info.get("mimeType") or "").lower().startswith("video/"),
                "raw_payload_json": {
                    "basic_info": basic_info,
                    "generation_data": generation_data,
                },
                "workflow_json": workflow_value,
                "compatibility_json": {
                    **(compatibility_json or {}),
                    **({"workflow_stage_count": len(stages)} if stages else {}),
                } or None,
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


def _build_civitai_validation_payload(basic_info: dict, generation_data: dict, normalized: dict) -> dict:
    warnings: list[str] = []
    errors: list[str] = []
    meta = generation_data.get("meta") if isinstance(generation_data, dict) else {}
    if not isinstance(meta, dict):
        meta = {}
    processes = _list_payload(normalized.get("processes") if isinstance(normalized, dict) else [])
    first_process = processes[0] if processes else {}
    stages = _list_payload(first_process.get("stages"))
    first_stage = stages[0] if stages else {}
    compatibility_json = {}
    for stage in reversed(stages):
        stage_compatibility = _dict_payload(stage.get("compatibility_json"))
        if stage_compatibility.get("output_width") and stage_compatibility.get("output_height"):
            compatibility_json = stage_compatibility
            break
    if not basic_info:
        errors.append("The image.get endpoint returned no payload.")
    if not generation_data:
        warnings.append("The image.getGenerationData endpoint returned no payload.")
    if not str(meta.get("prompt") or "").strip():
        warnings.append("Positive prompt is missing from the fetched generation metadata.")
    if meta.get("seed") in {None, ""} and not first_stage.get("seed"):
        warnings.append("Seed is missing from the fetched generation metadata.")
    has_generation_dimensions = bool(first_stage.get("width") and first_stage.get("height"))
    has_output_dimensions = bool(compatibility_json.get("output_width") and compatibility_json.get("output_height"))
    if not has_generation_dimensions and not has_output_dimensions:
        warnings.append("Width and height are incomplete in the fetched generation metadata.")
    raw_resources = generation_data.get("resources") if isinstance(generation_data, dict) else []
    if not isinstance(raw_resources, list) or not raw_resources:
        warnings.append("No generation resources were returned by CivitAI for this item.")
    if not processes:
        errors.append("The prototype could not build a normalized generation preview.")
    return _summarize_validation(warnings, errors)


def _build_local_generation_validation_payload(
    image: ImageModel,
    sidecar_payload: dict,
    merged_payload: dict,
    serialized_processes: list[dict],
) -> dict:
    warnings: list[str] = []
    errors: list[str] = []
    db_json = _dict_payload(image.json_metadata)
    exif_payload = _dict_payload(image.exif_data)
    source_url = str(image.source_url or "").strip()
    source_variant = sidecar_payload.get("civitai_source_variant") or db_json.get("civitai_source_variant")

    if not serialized_processes:
        warnings.append("No normalized generation process records have been stored for this image yet.")
    if not sidecar_payload and not db_json and not exif_payload:
        errors.append("No sidecar JSON, json_metadata, or EXIF payload is available for this image.")
    if source_url and "civitai.com/images/" in source_url and not isinstance(sidecar_payload.get("civitai") or db_json.get("civitai"), dict):
        warnings.append("Image has a CivitAI source URL but no cached CivitAI metadata payload is stored locally.")
    if not str(_read_generation_software_for_image(image) or "").strip():
        warnings.append("No generation_software summary is currently available for this image.")
    if isinstance(source_variant, dict):
        variant_reason = str(source_variant.get("reason") or "source_variant_present").strip()
        warnings.append(f"CivitAI source variant metadata is present: {variant_reason}.")
    if not isinstance(merged_payload.get("civitai"), dict) and not serialized_processes:
        warnings.append("Prototype view has no API-derived generation block to compare against persisted records.")

    return _summarize_validation(warnings, errors)


def _build_local_generation_overview(
    image: ImageModel,
    sidecar_payload: dict,
    serialized_processes: list[dict],
) -> dict:
    stage_count = sum(len(process.get("stages", [])) for process in serialized_processes)
    return {
        "image_db_id": image.id,
        "file_hash": image.file_hash,
        "file_name": image.file_name,
        "file_path": image.file_path,
        "mimetype": image.mimetype,
        "source_site": image.source_site,
        "source_url": image.source_url,
        "generation_software": _read_generation_software_for_image(image),
        "has_sidecar": bool(sidecar_payload),
        "has_exif_data": bool(_dict_payload(image.exif_data)),
        "has_json_metadata": bool(_dict_payload(image.json_metadata)),
        "process_count": len(serialized_processes),
        "stage_count": stage_count,
        "image_status": image.image_status,
    }


def _build_civitai_generation_overview(image_id: int, prepared: dict, normalized: dict) -> dict:
    processes = _list_payload(normalized.get("processes") if isinstance(normalized, dict) else [])
    first_process = processes[0] if processes else {}
    stages = _list_payload(first_process.get("stages"))
    first_stage = stages[0] if stages else {}
    compatibility_json = {}
    for stage in reversed(stages):
        stage_compatibility = _dict_payload(stage.get("compatibility_json"))
        if stage_compatibility.get("output_width") and stage_compatibility.get("output_height"):
            compatibility_json = stage_compatibility
            break
    stage_count = 0
    for process in processes:
        stages = process.get("stages")
        if isinstance(stages, list):
            stage_count += len(stages)
    return {
        "image_id": image_id,
        "source_url": prepared.get("source_url"),
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
            if compatibility_json.get("output_width") and compatibility_json.get("output_height")
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
            warnings=[f"CivitAI could not resolve image {image_id} via {exc.endpoint}."],
            errors=[exc.reason],
        )
        return {
            "ok": False,
            "mode": "civitai",
            "target": {
                "image_id": image_id,
                "source_url": exc.source_url,
            },
            "overview": {
                "image_id": image_id,
                "source_url": exc.source_url,
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
                "source_url": exc.source_url,
            },
        }

    basic_info = api.fetch_basic_info(image_id, strict=False) or {}
    generation_data = api.fetch_generation_data(image_id, strict=False) or {}
    image_data = CivitaiImage.from_single_image(
        basic_info=basic_info or {"id": image_id},
        generation_data=generation_data,
        api=api,
    ).to_dict(include_full_url=True)
    normalized = _build_civitai_normalized_preview(image_id, basic_info, generation_data, image_data)
    validation = _build_civitai_validation_payload(basic_info, generation_data, normalized)

    return {
        "ok": validation.get("status") != "error",
        "mode": "civitai",
        "target": {
            "image_id": image_id,
            "source_url": prepared.get("source_url"),
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
    serialized_processes = [_serialize_generation_process(item) for item in image.generation_processes]
    validation = _build_local_generation_validation_payload(
        image,
        sidecar_payload,
        merged_payload,
        serialized_processes,
    )
    return {
        "ok": validation.get("status") != "error",
        "mode": "local",
        "target": {
            "file_hash": image.file_hash,
            "image_db_id": image.id,
            "source_url": image.source_url,
        },
        "overview": _build_local_generation_overview(image, sidecar_payload, serialized_processes),
        "raw": {
            "db": db_payload,
            "merged": merged_payload,
            "sidecar": sidecar_payload,
            "json_metadata": image.json_metadata,
            "exif_data": image.exif_data,
        },
        "normalized": {
            "processes": serialized_processes,
        },
        "validation": validation,
        "error": None,
    }


def _import_single_civitai_image(
    api: CivitaiAPI,
    db: Session,
    image_id: int,
    *,
    force_reimport_on_missing_metadata: bool = False,
) -> dict:
    source_url = f"https://civitai.com/images/{image_id}"
    recovered_existing = False

    # Fast path: if this exact CivitAI source URL is already in library,
    # skip download and only attempt metadata repair.
    existing_by_source = _find_existing_image_by_source_url(db, source_url)
    if existing_by_source is not None:
        existing_status = (getattr(existing_by_source, "image_status", None) or "active").lower()
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
    mismatch_static_temp_path: Optional[Path] = None
    try:
        target = _resolve_civitai_image_target(api, image_id)
        download_result = _download_civitai_image_with_validation(
            image_id=image_id,
            target=target,
        )
        temp_path = download_result.temp_path
        mismatch_static_temp_path = download_result.mismatch_static_temp_path

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


def _find_existing_image_by_source_url(db: Session, source_url: str) -> Optional[ImageModel]:
    """Find existing image by DB source_url, then by sidecar source_url fallback."""
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
    if not current_source_site and is_civitai_image_url(str(image.source_url or "").strip()):
        image.source_site = "civitai"
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
    if sidecar_has_civitai and db_has_civitai and sidecar_has_nsfw_level and db_has_nsfw_level:
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
                ImageModel.civitai_uuid: civitai_uuid,
                ImageModel.civitai_hash: civitai_hash,
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


def _collection_name_exists(
    db: Session, name: str, exclude_collection_id: Optional[int] = None
) -> bool:
    query = db.query(CollectionModel).filter(CollectionModel.name == name)
    if exclude_collection_id is not None:
        query = query.filter(CollectionModel.id != exclude_collection_id)
    return query.first() is not None


def _serialize_collection(collection: CollectionModel) -> dict:
    return {
        "id": collection.id,
        "name": collection.name,
        "source": collection.source,
        "civitai_collection_id": collection.civitai_collection_id,
        "civitai_last_synced_at": _isoformat_or_none(collection.civitai_last_synced_at),
        "civitai_last_full_scan_at": _isoformat_or_none(collection.civitai_last_full_scan_at),
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


def _fetch_civitai_user_image_collections(api: CivitaiAPI) -> list[dict]:
    try:
        response = api._make_request(
            endpoint="collection.getAllUser",
            payload_data={"authed": True},
        )
    except Exception as e:
        raise HTTPException(
            status_code=502,
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
        if collection_type != "image":
            continue

        try:
            collection_id = int(row.get("id"))
        except (TypeError, ValueError):
            continue

        if collection_id in seen_ids:
            continue

        seen_ids.add(collection_id)
        name = str(row.get("name") or "").strip() or f"CivitAI Collection {collection_id}"
        collections.append(
            {
                "id": collection_id,
                "name": name,
                "type": collection_type,
            }
        )

    collections.sort(key=lambda item: str(item.get("name") or "").lower())
    return collections


def _build_civitai_image_source_url(image_id: int) -> str:
    return f"https://civitai.com/images/{image_id}"


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
        raise RuntimeError(f"Could not fetch CivitAI collection {collection_id} head page.")

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
        if image_status != "active":
            return membership_count, True
        if not _is_local_media_usable(image_path, image.mimetype):
            return membership_count, True

    return membership_count, False


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
    last_full_item_count = int(raw_last_full_item_count) if isinstance(raw_last_full_item_count, int) else None
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

    if last_full_item_count <= _CIVITAI_COLLECTION_HEAD_PROBE_SIZE and not probe.has_more:
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


def _summarize_civitai_collection_item_stub(collection_item: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not isinstance(collection_item, dict):
        return None

    item: dict[str, Any] = collection_item
    metadata: dict[str, Any] = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    user: dict[str, Any] = item.get("user") if isinstance(item.get("user"), dict) else {}
    account: dict[str, Any] = item.get("account") if isinstance(item.get("account"), dict) else {}
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
        "username": item.get("username") or user.get("username") or account.get("username"),
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


def _probe_civitai_endpoint_status(api: CivitaiAPI, endpoint: str, payload_data: dict[str, Any]) -> dict[str, Any]:
    try:
        response = api._make_request(endpoint=endpoint, payload_data=payload_data, strict=True)
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
    collection_stub: dict[str, Any] = diagnostics.get("collection_stub") if isinstance(diagnostics.get("collection_stub"), dict) else {}
    endpoint_status: dict[str, Any] = diagnostics.get("endpoint_status") if isinstance(diagnostics.get("endpoint_status"), dict) else {}
    image_get: dict[str, Any] = endpoint_status.get("image.get") if isinstance(endpoint_status.get("image.get"), dict) else {}
    generation_get: dict[str, Any] = endpoint_status.get("image.getGenerationData") if isinstance(endpoint_status.get("image.getGenerationData"), dict) else {}
    media_probe: dict[str, Any] = diagnostics.get("media_probe") if isinstance(diagnostics.get("media_probe"), dict) else {}
    image_get_status = image_get.get("status_code")
    generation_status = generation_get.get("status_code")
    media_status = media_probe.get("status_code")

    ingestion = str(collection_stub.get("ingestion") or "").strip().lower()
    blocked_for = str(collection_stub.get("blocked_for") or "").strip()
    if blocked_for or ingestion == "blocked" or bool(collection_stub.get("tos_violation")):
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
        diagnostics["classification"] = _classify_civitai_unavailable_diagnostics(diagnostics)
        return diagnostics

    diagnostics["endpoint_status"] = {
        "image.get": _probe_civitai_endpoint_status(api, "image.get", {"id": int(image_id), "authed": True}),
        "image.getGenerationData": _probe_civitai_endpoint_status(
            api,
            "image.getGenerationData",
            {"id": int(image_id), "authed": True},
        ),
    }

    if isinstance(collection_item, dict):
        preferred_name = collection_item.get("name") if isinstance(collection_item.get("name"), str) else None
        url_hash = collection_item.get("url")
        mime_type = collection_item.get("mimeType") if isinstance(collection_item.get("mimeType"), str) else None
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

    diagnostics["classification"] = _classify_civitai_unavailable_diagnostics(diagnostics)
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

    if civitai_collection_id is not None:
        existing_by_remote_id = (
            db.query(CollectionModel)
            .filter(CollectionModel.civitai_collection_id == civitai_collection_id)
            .first()
        )
        if existing_by_remote_id is not None:
            if not _collection_name_exists(
                db,
                normalized_name,
                exclude_collection_id=existing_by_remote_id.id,
            ):
                existing_by_remote_id.name = normalized_name
            if source == "civitai" and str(existing_by_remote_id.source or "") != "civitai":
                existing_by_remote_id.source = "civitai"
            db.flush()
            return existing_by_remote_id

    existing = db.query(CollectionModel).filter(CollectionModel.name == normalized_name).first()
    if existing:
        if civitai_collection_id is not None and existing.civitai_collection_id is None:
            existing.civitai_collection_id = civitai_collection_id
        elif (
            civitai_collection_id is not None
            and existing.civitai_collection_id is not None
            and int(existing.civitai_collection_id) != int(civitai_collection_id)
        ):
            normalized_name = f"{normalized_name} (CivitAI {civitai_collection_id})"
            existing = None
        if existing is not None and source == "civitai" and str(existing.source or "") == "user":
            existing.source = "civitai"
        if existing is not None:
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
    if len(parts) == 4 and parts[0] == "retry" and parts[1] == "standalone" and parts[2] == "image":
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


def _get_retry_failed_items_from_task(task_payload: dict[str, Any]) -> tuple[list[_RetryFailedItem], list[str]]:
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
        _commit_with_lock_retry(db, context=f"Collection setup commit for retry {civitai_collection_id}")
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
        prefix = f"Remote CivitAI image {image_id} is unavailable ({', '.join(qualifiers)})"
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
    diagnostics: dict[str, Any] = detail.get("diagnostics") if isinstance(detail.get("diagnostics"), dict) else {}
    collection_stub: dict[str, Any] = diagnostics.get("collection_stub") if isinstance(diagnostics.get("collection_stub"), dict) else {}
    media_probe: dict[str, Any] = diagnostics.get("media_probe") if isinstance(diagnostics.get("media_probe"), dict) else {}

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
        source_url = str(detail.get("source_url") or _build_civitai_image_source_url(image_id)).strip()
        existing = _find_existing_image_by_source_url(db, source_url)
        existing_status = (getattr(existing, "image_status", None) or "active").lower() if existing is not None else ""

        if existing is None or existing_status == "placeholder":
            placeholder_hash = hashlib.sha256(f"civitai-placeholder:{source_url}".encode("utf-8")).hexdigest()
            placeholder_path = f"placeholders/{placeholder_hash[:2]}/{placeholder_hash}.placeholder"
            placeholder_reason = str(detail.get("classification") or "civitai_remote_unavailable").strip() or "civitai_remote_unavailable"

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
                    exif_data={},
                    json_metadata={"civitai": civitai_unavailable_payload},
                )
                db.add(placeholder)
                db.flush()
                placeholder_image_id = int(placeholder.id)
                placeholder_created = True
            else:
                merged_json = dict(existing.json_metadata) if isinstance(existing.json_metadata, dict) else {}
                civitai_json = merged_json.get("civitai") if isinstance(merged_json.get("civitai"), dict) else {}
                civitai_json.update(civitai_unavailable_payload)
                merged_json["civitai"] = civitai_json

                existing.image_status = "placeholder"
                existing.status_reason = placeholder_reason
                existing.replaced_by_image_id = None
                existing.source_url = source_url
                existing.source_site = "civitai"
                existing.mimetype = existing.mimetype or "application/x-civitai-placeholder"
                existing.json_metadata = merged_json
                existing.date_modified = datetime.utcnow()
                placeholder_image_id = int(existing.id)

            if attach_collection_id is not None and placeholder_image_id is not None:
                _ensure_image_in_collection(db, placeholder_image_id, attach_collection_id)

            _commit_with_lock_retry(db, context=f"Unavailable placeholder commit for CivitAI image {image_id}")

    result["unavailable_detail"] = detail
    result["placeholder_image_id"] = placeholder_image_id
    result["placeholder_created"] = placeholder_created
    _log_civitai_unavailable_item(detail)
    return result


def _collect_civitai_unavailable_items(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
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
        task_context.mark_item(item_key, "failed", str(result["error"]))
        task_context.advance()
        return

    for key in ("images_added", "images_skipped", "images_recovered", "json_files_created"):
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
            task_context.add_error(_format_civitai_unavailable_skip_message(unavailable_detail))
        task_context.mark_item(
            item_key,
            "skipped",
            str(result.get("skip_message") or result.get("skip_reason") or "Already present"),
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
        for key, value in (by_status_raw.items() if isinstance(by_status_raw, dict) else [])
        if int(value or 0) >= 0
    }
    by_endpoint = {
        str(key): int(value or 0)
        for key, value in (by_endpoint_raw.items() if isinstance(by_endpoint_raw, dict) else [])
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


def _record_civitai_payload_retry_metrics(task_context: TaskContext, metrics: dict[str, Any]) -> None:
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
        normalized_endpoint = re.sub(r"[^0-9a-zA-Z_]+", "_", str(endpoint or "")).strip("_").lower()
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
        "images_recovered": sum(int(r.get("images_recovered", 0) or 0) for r in results),
        "images_cancelled": sum(1 for r in results if r.get("cancelled")),
        "json_files_created": sum(int(r.get("json_files_created", 0) or 0) for r in results),
        "errors": [
            f"Image {r.get('image_id')}: {r['error']}"
            for r in results
            if r.get("error")
        ],
        "unavailable_items": unavailable_items,
        "warnings": _get_runtime_warnings(),
        "results": results,
        "civitai_payload_retry_metrics": civitai_payload_retry_metrics or {
            "total": 0,
            "by_status": {},
            "by_endpoint": {},
        },
    }


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

    existing_status = (getattr(existing_by_source, "image_status", None) or "active").lower()
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
    task_context.mark_item(item_key, "fetching_metadata", "Fetching CivitAI metadata")
    target = _resolve_civitai_image_target(api, image_id, strict=True)
    task_context.mark_item(item_key, "downloading", "Downloading media")
    download_result = _download_civitai_image_with_validation(
        image_id=image_id,
        target=target,
    )
    temp_path = download_result.temp_path
    
    # Extract collection item (get.Infinite metadata) if available
    collection_item = _collection_context_item(collection_context, image_id)
    
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

    resolved_image_db_id = ingest_result.get("image_id") or ingest_result.get("existing_image_id")
    image_db_id = resolved_image_db_id if isinstance(resolved_image_db_id, int) else None
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

            # Add pre-saved API response file paths (includes basic_info, generation_data, and infinite)
            if prepared.api_response_paths:
                civitai_metadata_info.update(prepared.api_response_paths)
            
            # Update image metadata with UUID and paths
            if civitai_metadata_info:
                merged_json = dict(image.json_metadata) if isinstance(image.json_metadata, dict) else {}
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
            
            is_existing_hash_match = str(ingest_result.get("skip_reason") or "").strip() == "existing_file_hash"
            if is_existing_hash_match:
                effective_source_url = str(image.source_url or prepared.source_url or "").strip()
                if effective_source_url and is_civitai_image_url(effective_source_url):
                    metadata_backfilled = _ensure_civitai_metadata_for_existing_image(
                        db,
                        image,
                        effective_source_url,
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
        or (_is_sha256_hex(candidate_stem) and candidate_stem.lower() == normalized_hash)
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
    current_name = sanitize_display_filename(image.file_name, fallback_ext=actual_extension) or ""
    if civitai_target and isinstance(civitai_target.get("original_filename"), str):
        candidate = sanitize_display_filename(
            str(civitai_target["original_filename"]),
            fallback_ext=actual_extension,
        )
        if candidate:
            return candidate

    source_name = sanitize_display_filename(image.source_url, fallback_ext=actual_extension) or ""
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

    db_file_name = sanitize_display_filename(db_payload.get("file_name"), fallback_ext=fallback_ext)
    merged_file_name = normalized.get("file_name")
    file_path = normalized.get("file_path") or db_payload.get("file_path") or image.file_path

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

    if isinstance(normalized.get("civitai"), dict) and isinstance(db_payload.get("civitai"), dict):
        merged_civitai = dict(normalized.get("civitai") or {})
        db_civitai = dict(db_payload.get("civitai") or {})
        for key in ("uuid", "hash"):
            if merged_civitai.get(key) in (None, "") and db_civitai.get(key):
                merged_civitai[key] = db_civitai.get(key)
        normalized["civitai"] = merged_civitai

    return normalized


def _variant_group_key_for_image(image: ImageModel, merged_payload: dict[str, Any]) -> str:
    explicit_key = str(
        getattr(image, "variant_group_key", None)
        or merged_payload.get("variant_group_key")
        or ""
    ).strip()
    if explicit_key:
        return explicit_key
    return str(image.file_hash or image.id or image.file_path or "").strip()


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


def _first_positive_int(payloads: list[Any], paths: list[tuple[str, ...]]) -> Optional[int]:
    for payload in payloads:
        for path in paths:
            parsed = _coerce_positive_int(_get_nested_value(payload, path))
            if parsed is not None:
                return parsed
    return None


def _extract_civitai_variant_payloads(merged_payload: dict[str, Any], *, include_merged: bool = True) -> list[Any]:
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


def _extract_civitai_media_name(payloads: list[Any], *, image_id: int, media_url: str, mime_type: Optional[str]) -> str:
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
    return _build_civitai_original_filename(image_id, preferred_name, media_url, mime_type)


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


def _extract_civitai_media_dimensions(payloads: list[Any], fallback_width: Any, fallback_height: Any) -> tuple[Optional[int], Optional[int]]:
    width = _first_positive_int(payloads, [("width",), ("meta", "width"), ("image", "width")])
    height = _first_positive_int(payloads, [("height",), ("meta", "height"), ("image", "height")])
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

    return f"https://image-b2.civitai.com/file/civitai-media-cache/{media_uuid}/original"


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


def _get_local_asset_category(local_file_path: Optional[str], db_mime_type: Optional[str]) -> str:
    """
    Determine asset category (video/image/unknown) using MIME type first, then extension.
    
    Returns 'video', 'image', or 'unknown'.
    """
    category_from_mime = _get_asset_category_from_mime(db_mime_type)
    if category_from_mime != "unknown":
        return category_from_mime
    return _get_asset_category_from_path(local_file_path)


def _get_civitai_asset_category(civitai_url: Optional[str], civitai_mime_type: Optional[str]) -> str:
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
                if header[:8] != b'\x89PNG\r\n\x1a\n':
                    return False, "PNG file has invalid magic bytes (corrupted)"
            
            elif suffix in {".jpg", ".jpeg"}:
                # JPEG files start with FFD8
                if header[:2] != b'\xff\xd8':
                    return False, "JPEG file has invalid magic bytes (corrupted)"
            
            elif suffix == ".webp":
                # WEBP files start with RIFF...WEBP
                if not header.startswith(b'RIFF') or header[8:12] != b'WEBP':
                    return False, "WEBP file has invalid structure (corrupted)"
            
            elif suffix == ".mp4":
                # MP4 is more complex - just check it's not empty
                if len(header) == 0:
                    return False, "MP4 file appears empty"
    
    except IOError as e:
        return False, f"Cannot read file: {e}"
    
    return True, None


def _build_local_image_variant(image: ImageModel, merged_payload: dict[str, Any], *, group_key: str) -> dict[str, Any]:
    variant_role = str(getattr(image, "variant_role", None) or merged_payload.get("variant_role") or "library").strip() or "library"
    variant = {
        "variant_key": f"variant:local:{group_key}:{image.file_hash}",
        "variant_label": "Library Asset",
        "variant_role": variant_role,
        "variant_sort_index": _variant_sort_index_for_image(image, 100),
        "file_name": merged_payload.get("file_name"),
        "file_hash": image.file_hash,
        "file_size": merged_payload.get("file_size"),
        "width": merged_payload.get("width"),
        "height": merged_payload.get("height"),
        "mimetype": merged_payload.get("mimetype"),
        "file_path": merged_payload.get("file_path"),
        "display_url": None,
        "poster_url": merged_payload.get("video_poster_url") or merged_payload.get("poster_url"),
        "video_poster_url": merged_payload.get("video_poster_url"),
        "video_thumbnail_url": merged_payload.get("video_thumbnail_url"),
        "preview_image_url": merged_payload.get("preview_image_url") or merged_payload.get("video_poster_url"),
        "source_url": merged_payload.get("source_url"),
        "civitai_uuid": merged_payload.get("civitai_uuid") or image.civitai_uuid,
        "civitai_hash": merged_payload.get("civitai_hash") or image.civitai_hash,
        "resource_origin": "library",
        "resource_status": "available",
        "is_remote": False,
        "is_local": True,
        "editable_file_hash": image.file_hash,
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
    mime_type = extracted_mime_type if extracted_mime_type.startswith("video/") else guessed_mime_type
    image_id = extract_civitai_image_id(str(image.source_url or "")) or int(image.id)
    file_name = _extract_civitai_media_name(payloads, image_id=image_id, media_url=playable_url, mime_type=mime_type)
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
        "civitai_uuid": merged_payload.get("civitai_uuid") or image.civitai_uuid or media_uuid,
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
    resource_path = Path(IMAGE_RESOURCES_PATH) / relative_path
    resources_root = Path(IMAGE_RESOURCES_PATH)
    metadata_mime = _normalize_mime_type(variant_metadata.get("actual_mimetype") or variant_metadata.get("declared_mimetype"))
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
            candidate = resources_root / "civitai_source_variants" / f"{normalized_hash}{suffix}"
            if candidate.exists() and candidate.is_file():
                resource_path = candidate
                relative_path = str(resource_path.relative_to(resources_root))
                file_name = resource_path.name
                break
        else:
            glob_pattern = str(resources_root / "civitai_source_variants" / f"{normalized_hash}.*")
            for candidate_path in glob.glob(glob_pattern):
                candidate = Path(candidate_path)
                if candidate.suffix.lower() == ".json":
                    continue
                if candidate.exists() and candidate.is_file():
                    resource_path = candidate
                    relative_path = str(resource_path.relative_to(resources_root))
                    file_name = resource_path.name
                    break

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

    display_url = f"/image_resources/{_encode_relative_static_path(relative_path)}"

    return {
        "variant_key": f"variant:{variant_key_suffix}:{group_key}:{file_name}",
        "variant_label": variant_label,
        "variant_role": variant_role,
        "variant_sort_index": variant_sort_index,
        "file_name": file_name,
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
        "source_url": str(variant_metadata.get("source_url") or image.source_url or "").strip() or None,
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
    for variant in sorted(variants, key=lambda item: (int(item.get("variant_sort_index") or 0), str(item.get("variant_key") or ""))):
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


def _build_image_variants(image: ImageModel, merged_payload: dict[str, Any]) -> list[dict[str, Any]]:
    group_key = _variant_group_key_for_image(image, merged_payload)
    local_variant = _build_local_image_variant(image, merged_payload, group_key=group_key)
    variants: list[dict[str, Any]] = []

    # Determine local asset category to help decide which variants to create
    local_asset_category = _get_local_asset_category(
        merged_payload.get("file_path"),
        merged_payload.get("mimetype")
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
        poster_url=local_variant.get("poster_url") or local_variant.get("preview_image_url"),
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

    if static_variant_meta is None and isinstance(merged_payload.get("civitai_source_variant_static"), dict):
        static_variant_meta = merged_payload.get("civitai_source_variant_static")
    if archived_variant_meta is None and isinstance(merged_payload.get("civitai_source_variant"), dict):
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



def _merge_display_item_variant(base_payload: dict[str, Any], variant: dict[str, Any]) -> dict[str, Any]:
    display_item = dict(base_payload)
    for field_name in (
        "file_name",
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
    display_item["editable_file_hash"] = variant.get("editable_file_hash") or base_payload.get("editable_file_hash")
    return display_item


def _build_grouped_display_item(image: ImageModel, merged_payload: dict[str, Any], variants: list[dict[str, Any]]) -> dict[str, Any]:
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
    base_payload["gallery_item_key"] = f"group:{group_key}"
    base_payload["display_mode"] = "grouped"
    base_payload["variant_index"] = 0
    return _merge_display_item_variant(base_payload, default_variant)


def _build_flat_variant_display_items(image: ImageModel, merged_payload: dict[str, Any], variants: list[dict[str, Any]]) -> list[dict[str, Any]]:
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


def _build_display_items_for_image(image: ImageModel, merged_payload: dict[str, Any], *, group_variants: bool) -> list[dict[str, Any]]:
    variants = _build_image_variants(image, merged_payload)
    if not variants:
        return []
    if group_variants:
        return [_build_grouped_display_item(image, merged_payload, variants)]
    return _build_flat_variant_display_items(image, merged_payload, variants)


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
    group_variants: bool,
) -> list[dict[str, Any]]:
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
            "group_variants": bool(group_variants),
        },
    )
    cached_items = _search_cache_get(display_cache_key)
    if isinstance(cached_items, list):
        return cached_items

    images_query = db.query(ImageModel).options(
        joinedload(ImageModel.artist),
        joinedload(ImageModel.license),
        joinedload(ImageModel.collections),
    ).filter(_active_image_filter())
    images_query = _apply_image_list_filters(
        images_query,
        search=search,
        source_sites=source_site,
        mimetypes=mimetype,
        artist_names=artist_name,
        collection_names=collection_name,
    )

    generation_filtered_ids = _filter_image_ids_by_generation_software(images_query, generation_software)
    nsfw_filtered_ids = _filter_image_ids_by_nsfw_ratings(images_query, nsfw_rating)
    nsfw_safety_filtered_ids = _filter_image_ids_by_nsfw_safety_classes(images_query, nsfw_safety)

    constrained_ids: Optional[set[int]] = None
    for filtered_ids in (generation_filtered_ids, nsfw_filtered_ids, nsfw_safety_filtered_ids):
        if filtered_ids is None:
            continue
        filtered_set = set(filtered_ids)
        constrained_ids = filtered_set if constrained_ids is None else constrained_ids.intersection(filtered_set)

    if constrained_ids is not None:
        if constrained_ids:
            images_query = images_query.filter(ImageModel.id.in_(list(constrained_ids)))
        else:
            images_query = images_query.filter(text("1 = 0"))

    if sort_by == "last_added":
        images_query = images_query.order_by(ImageModel.id.desc())
    else:
        images_query = images_query.order_by(ImageModel.id.asc())

    images = images_query.all()
    display_items: list[dict[str, Any]] = []
    for image in images:
        db_dict = ImageData.from_db_record(image).to_dict()

        sidecar_path = (Path(IMAGE_LIBRARY_PATH) / str(image.file_path)).with_suffix(".json")
        sidecar_dict: dict[str, Any] = {}
        if sidecar_path.exists():
            try:
                with open(sidecar_path, "r", encoding="utf-8") as handle:
                    loaded = json.load(handle)
                if isinstance(loaded, dict):
                    sidecar_dict = loaded
            except (OSError, json.JSONDecodeError):
                sidecar_dict = {}

        merged = {**db_dict, **sidecar_dict}
        merged = _normalize_merged_image_payload(
            image,
            db_payload=db_dict,
            merged_payload=merged,
        )
        if (image.mimetype or "").lower().startswith("video/"):
            image_path = Path(IMAGE_LIBRARY_PATH) / str(image.file_path)
            poster_path = get_video_poster_path(image_path, IMAGE_RESOURCES_PATH)
            thumbnail_path = get_video_thumbnail_path(image_path, IMAGE_RESOURCES_PATH)
            if poster_path.exists() and poster_path.is_file():
                merged["video_poster_url"] = f"/images/{image.file_hash}/video_poster"
            if thumbnail_path.exists() and thumbnail_path.is_file():
                merged["video_thumbnail_url"] = f"/images/{image.file_hash}/video_thumbnail"
        merged["collection_names"] = [c.name for c in image.collections]
        merged["collection_ids"] = [c.id for c in image.collections]
        nsfw_ratings = _read_nsfw_ratings_for_image(image)
        merged["nsfw_ratings"] = nsfw_ratings
        merged["nsfw_rating"] = nsfw_ratings[0] if nsfw_ratings else None
        merged["user_nsfw_rating"] = image.user_nsfw_rating
        merged["user_nsfw_safety_class"] = image.user_nsfw_safety_class
        display_items.extend(_build_display_items_for_image(image, merged, group_variants=group_variants))

    _search_cache_put(display_cache_key, display_items)
    return display_items


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

    repaired_image_id = ingest_result.get("image_id") or ingest_result.get("existing_image_id")
    repaired_image = None
    if isinstance(repaired_image_id, int):
        repaired_image = db.query(ImageModel).filter(ImageModel.id == repaired_image_id).first()

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
            task_context.mark_item(item_key, "checking_existing", "Checking local library")
            try:
                result, recovered_existing = _handle_existing_civitai_image(
                    db,
                    image_id=image_id,
                    attach_collection_id=attach_collection_id,
                    backfill_metadata=False,
                )
                if result is not None:
                    _commit_with_lock_retry(db, context=f"Existing image update for {image_id}")
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
                    _commit_with_lock_retry(db, context=f"Stale record cleanup for {image_id}")
                download_candidates.append((image_id, recovered_existing))
                task_context.mark_item(item_key, "queued", "Queued for remote fetch")
            except Exception as exc:
                db.rollback()
                if _is_civitai_remote_not_found_error(exc):
                    result = _build_civitai_unavailable_result(
                        image_id,
                        exc,
                        api=api,
                        db=db,
                        attach_collection_id=attach_collection_id,
                        collection_id=(collection_context or {}).get("collection_id"),
                        collection_name=(collection_context or {}).get("collection_name"),
                        collection_item=_collection_context_item(collection_context, image_id),
                    )
                else:
                    result = _build_failed_civitai_import_result(image_id, str(exc))
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

    max_workers = max(1, min(_CIVITAI_IMPORT_NETWORK_CONCURRENCY, len(download_candidates)))
    next_index = 0
    futures: dict[Any, tuple[int, bool, str]] = {}
    last_heartbeat_at = time.monotonic()
    total_count = len(image_ids)

    def submit_available(executor: ThreadPoolExecutor) -> None:
        nonlocal next_index
        while next_index < len(download_candidates) and len(futures) < max_workers and not task_context.cancel_requested:
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

    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="civitai-fetch") as executor:
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

            done, _ = wait(list(futures.keys()), timeout=0.25, return_when=FIRST_COMPLETED)
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
                        task_context.mark_item(item_key, "ingesting", "Importing into local library")
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
                    if _is_civitai_remote_not_found_error(exc):
                        with SessionLocal() as db:
                            result = _build_civitai_unavailable_result(
                                image_id,
                                exc,
                                api=api,
                                db=db,
                                attach_collection_id=attach_collection_id,
                                collection_id=(collection_context or {}).get("collection_id"),
                                collection_name=(collection_context or {}).get("collection_name"),
                                collection_item=_collection_context_item(collection_context, image_id),
                            )
                    else:
                        result = _build_failed_civitai_import_result(image_id, str(exc))
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
            return f"Collection {collection_index}/{collection_count}: {collection_label}"
        return f"Collection: {collection_label}"

    def _on_collection_page(page_number: int, page_items: int, discovered_count: int) -> None:
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
        task_context.set_message(
            f"{_format_collection_prefix()} | Discovered {discovered_count} items | All discovered {overall_discovered}"
        )

    task_context.set_message(f"Fetching collection items for {collection_label}")
    scraper = CivitaiPrivateScraper(auto_authenticate=True)
    collection_items = scraper.fetch_collection_items(
        collection_id=collection_id,
        limit=limit,
        progress_callback=_on_collection_page,
    )
    if isinstance(collection_items, list):
        normalized_items = [item for item in collection_items if isinstance(item, dict)]
        _archive_civitai_collection_items(normalized_items)

    if not collection_items:
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
        _commit_with_lock_retry(db, context=f"Collection setup commit for {collection_id}")
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
        local_collection = db.query(CollectionModel).filter(CollectionModel.id == local_collection_id).first()
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
                full_item_count=len(image_ids),
                mark_full_scan=True,
            )
            local_collection_snapshot = _serialize_collection(local_collection)
        _commit_with_lock_retry(
            db,
            context=f"Collection membership sync commit for {collection_id}",
        )

    task_context.increment_counter("collections_synced", 1)
    task_context.increment_counter("memberships_removed", memberships_removed)
    return {
        "civitai_collection_id": collection_id,
        "civitai_collection_name": collection_name,
        "local_collection": local_collection_snapshot,
        "requested": len(image_ids),
        "images_added": sum(int(r.get("images_added", 0) or 0) for r in results),
        "images_skipped": sum(int(r.get("images_skipped", 0) or 0) for r in results),
        "images_recovered": sum(int(r.get("images_recovered", 0) or 0) for r in results),
        "images_cancelled": sum(1 for r in results if r.get("cancelled")),
        "json_files_created": sum(int(r.get("json_files_created", 0) or 0) for r in results),
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


def _run_civitai_collection_sync_job(task_context: TaskContext, *, limit: Optional[int]) -> dict:
    api = CivitaiAPI.get_instance()
    retry_metrics_before = _snapshot_civitai_payload_retry_metrics(api)
    task_context.set_total(0)
    task_context.set_message("Fetching your CivitAI collections")
    remote_collections = _fetch_civitai_user_image_collections(api)
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

    collection_summaries: list[dict] = []
    overall_processed = 0
    overall_discovered = 0
    for index, remote in enumerate(remote_collections, start=1):
        collection_id = int(remote["id"])
        collection_name = str(remote["name"])
        collection_item_key = f"collection:{collection_id}"
        task_context.mark_item(collection_item_key, "running", f"Syncing {collection_name}")
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
                    raise RuntimeError(f"Local collection {local_collection_id} disappeared during sync.")

                local_membership_count, local_media_incomplete = _inspect_local_civitai_collection_health(
                    db,
                    local_collection_id=local_collection_id,
                )
                needs_full_verify, sync_state = _civitai_collection_requires_full_verify(
                    collection_row,
                    probe=probe,
                    local_membership_count=local_membership_count,
                    local_media_incomplete=local_media_incomplete,
                    force_full_verify=limit is not None,
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
                    task_context.increment_counter("collections_synced", 1)
                    task_context.increment_counter("memberships_removed", memberships_removed)
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
                    task_context.increment_counter("collections_synced", 1)
                    summary = _build_civitai_collection_skip_summary(
                        collection_id=collection_id,
                        collection_name=collection_name,
                        local_collection_snapshot=local_snapshot,
                        sync_state=sync_state,
                    )

            if summary is None:
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
                )
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
        "collections_synced": len([item for item in collection_summaries if not item.get("errors")]),
        "images_added": sum(int(item.get("images_added", 0) or 0) for item in collection_summaries),
        "images_skipped": sum(int(item.get("images_skipped", 0) or 0) for item in collection_summaries),
        "images_recovered": sum(int(item.get("images_recovered", 0) or 0) for item in collection_summaries),
        "images_cancelled": sum(int(item.get("images_cancelled", 0) or 0) for item in collection_summaries),
        "json_files_created": sum(int(item.get("json_files_created", 0) or 0) for item in collection_summaries),
        "memberships_removed": sum(int(item.get("memberships_removed", 0) or 0) for item in collection_summaries),
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
    sidecar_civitai_payload = sidecar_payload.get("civitai") if isinstance(sidecar_payload, dict) else None
    return _payload_has_nsfw_level(db_civitai_payload) or _payload_has_nsfw_level(sidecar_civitai_payload)


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
            .filter(func.lower(ImageModel.source_url).like("%civitai.com/images/%"))
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
                task_context.mark_item(f"nsfw-backfill:{image_db_id}", "skipped", "Image no longer exists")
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
                task_context.mark_item(item_key, "skipped", "Not a CivitAI image source")
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
                    _commit_with_lock_retry(db, context=f"NSFW metadata backfill commit for image {image.id}")
                    task_context.mark_item(item_key, "completed", "Backfilled nsfwLevel metadata")
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
                        task_context.mark_item(item_key, "failed", "Could not parse CivitAI image id for reimport")
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
                        task_context.mark_item(item_key, "failed", str(reimport_result.get("error")))
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

                    _commit_with_lock_retry(db, context=f"NSFW reimport commit for image {parsed_image_id}")
                    refreshed = _find_existing_image_by_source_url(db, source_url)
                    if refreshed is not None and _image_has_civitai_nsfw_level(refreshed):
                        task_context.mark_item(item_key, "completed", "Recovered via reimport")
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

                task_context.mark_item(item_key, "skipped", "Remote nsfwLevel unavailable")
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
        raise RuntimeError("No retryable failed CivitAI items were found in the selected job.")

    api = CivitaiAPI.get_instance()
    retry_metrics_before = _snapshot_civitai_payload_retry_metrics(api)
    grouped_collection_items: dict[int, list[int]] = {}
    standalone_image_ids: list[int] = []
    for entry in retry_items:
        if entry.civitai_collection_id is None:
            standalone_image_ids.append(entry.image_id)
            continue
        grouped_collection_items.setdefault(entry.civitai_collection_id, []).append(entry.image_id)

    task_context.set_total(len(retry_items))
    task_context.set_metadata("source_task_id", source_task.get("id"))
    task_context.set_metadata("retry_requested", len(retry_items))
    if skipped_items:
        task_context.set_metadata("skipped_item_keys", skipped_items)

    all_results: list[dict] = []
    group_summaries: list[dict] = []

    if standalone_image_ids:
        task_context.set_message(f"Retrying {len(standalone_image_ids)} standalone failed image(s)")
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
        collection_snapshot, local_collection_id = _ensure_local_civitai_collection_for_retry(
            api,
            civitai_collection_id=civitai_collection_id,
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
        "images_skipped": sum(int(r.get("images_skipped", 0) or 0) for r in all_results),
        "images_recovered": sum(int(r.get("images_recovered", 0) or 0) for r in all_results),
        "images_cancelled": sum(1 for r in all_results if r.get("cancelled")),
        "json_files_created": sum(int(r.get("json_files_created", 0) or 0) for r in all_results),
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
                _commit_with_lock_retry(db, context=f"Import commit for image {image_id}")
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


def create_initial_data():
    """Populate initial tools/licenses/authorities through bootstrap module."""
    populate_initial_data(SessionLocal)


# Define the lifespan context manager
@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_uvicorn_access_logging(
        suppress_status_get_logs=_read_env_flag("ATELIER_SUPPRESS_STATUS_GET_LOGS", False)
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
    _ensure_collection_sync_columns()
    _ensure_user_nsfw_columns()
    _ensure_civitai_uuid_column()
    _ensure_civitai_hash_column()
    _ensure_image_variant_columns()

    print("AtelierAI API is ready to go!")

    yield

    print("Shutting down AtelierAI API...")
    task_manager.shutdown()


# Pass the lifespan manager to the FastAPI app
app = FastAPI(title="AtelierAI API", version="0.1.0", lifespan=lifespan)

# Compress larger JSON responses (e.g., taxonomy tree state payloads).
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)

# Mount the static files directory
# This will serve files from the 'frontend' directory under the '/frontend/' URL path
app.mount("/frontend", StaticFiles(directory="frontend"), name="frontend")
# Expose processed images so the gallery can render thumbnails/full previews.
app.mount("/image_library", StaticFiles(directory=IMAGE_LIBRARY_PATH), name="image_library")
app.mount("/image_resources", StaticFiles(directory=IMAGE_RESOURCES_PATH), name="image_resources")


# Define a root endpoint to serve the main index.html file
@app.get("/")
async def read_index():
    return FileResponse("frontend/index.html")


@app.get("/tree")
async def read_tree_prototype():
    return FileResponse("frontend/tree.html")


@app.get("/generation-lab")
async def read_generation_lab():
    return FileResponse("frontend/generation-lab.html")


@app.get("/model-lab")
async def read_model_lab():
    return FileResponse("frontend/model-lab.html")


@app.get("/folder-lab")
async def read_folder_lab():
    return FileResponse("frontend/folder-lab.html")


@app.get("/perceptual-lab")
async def read_perceptual_lab():
    return FileResponse("frontend/perceptual-lab.html")


@app.get("/tasks/", response_model=list[dict])
def list_background_tasks(limit: int = 20):
    capped_limit = max(1, min(int(limit), 50))
    return task_manager.list_tasks(limit=capped_limit)


@app.get("/tasks/{task_id}", response_model=dict)
def get_background_task(task_id: str):
    try:
        return task_manager.get_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found")


@app.post("/tasks/{task_id}/cancel", response_model=dict)
def cancel_background_task(task_id: str):
    try:
        return task_manager.cancel_task(task_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Task not found")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/generation-prototype/civitai/{image_id}", response_model=dict)
def get_civitai_generation_prototype(image_id: int):
    return _build_generation_prototype_civitai_payload(image_id)


@app.get("/images/{file_hash}/generation-prototype", response_model=dict)
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


@app.get("/images/{file_hash}/perceptual-lab/analyze", response_model=dict)
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
        raise HTTPException(status_code=400, detail=f"Unable to open image for hashing: {exc}")

    civitai_payload = _read_civitai_payload_for_image(image)
    civitai_image_hash = _extract_primary_civitai_image_hash(image, civitai_payload)
    civitai_comparison = _build_civitai_hash_comparison_payload(local_hashes, civitai_payload)
    blurhash_report = _build_blurhash_report(image_path, civitai_hash=civitai_image_hash)
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


@app.get("/images/{file_hash}/perceptual-lab/similarity", response_model=dict)
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
        raise HTTPException(status_code=404, detail="Target image file not found on disk.")

    if str(target_image.mimetype or "").lower().startswith("video/"):
        raise HTTPException(status_code=400, detail="Perceptual similarity search currently supports still images only.")

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
            raise HTTPException(status_code=400, detail=f"Unable to open target image for hashing: {exc}")
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
            candidate_blurhash, blurhash_source = _resolve_local_blurhash_4x4(candidate, candidate_path)
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

            matches.append({
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
            })
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

            matches.append({
                "file_hash": candidate.file_hash,
                "civitai_uuid": candidate.civitai_uuid,
                "file_name": candidate.file_name,
                "file_path": candidate.file_path,
                "mimetype": candidate.mimetype,
                "source_url": candidate.source_url,
                "distance": distance,
                "distance_type": "hamming",
                "image_url": f"/image_library/{_normalize_media_url_path(str(candidate.file_path))}",
            })

    matches.sort(key=lambda item: (float(item.get("distance") or 0.0), str(item.get("file_name") or "")))
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
            "distance_type": "blurhash_mae" if selected_algorithm == "blurhash" else "hamming",
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


@app.get("/model-prototype/civitai/{image_id}", response_model=dict)
def get_civitai_model_prototype(
    image_id: int,
    catalog_url: Optional[str] = Query(default=None),
    checkpoints_url: Optional[str] = Query(default=None),
    loras_url: Optional[str] = Query(default=None),
):
    local_catalog = model_reference_service.fetch_local_catalog(
        catalog_url=catalog_url,
        checkpoints_url=checkpoints_url,
        loras_url=loras_url,
    )
    generation_payload = _build_generation_prototype_civitai_payload(image_id)
    return model_reference_service.build_item_payload(
        generation_payload,
        local_catalog=local_catalog,
    )


@app.get("/images/{file_hash}/model-prototype", response_model=dict)
def get_local_model_prototype(
    file_hash: str,
    catalog_url: Optional[str] = Query(default=None),
    checkpoints_url: Optional[str] = Query(default=None),
    loras_url: Optional[str] = Query(default=None),
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
    )
    generation_payload = _build_generation_prototype_local_payload(image)
    return model_reference_service.build_item_payload(
        generation_payload,
        local_catalog=local_catalog,
    )


@app.get("/model-prototype/catalog", response_model=dict)
def get_model_catalog_prototype(
    image_limit: int = Query(default=250, ge=1, le=2000),
    catalog_url: Optional[str] = Query(default=None),
    checkpoints_url: Optional[str] = Query(default=None),
    loras_url: Optional[str] = Query(default=None),
    db: Session = Depends(get_db),
):
    local_catalog = model_reference_service.fetch_local_catalog(
        catalog_url=catalog_url,
        checkpoints_url=checkpoints_url,
        loras_url=loras_url,
    )
    return model_reference_service.build_library_catalog_payload(
        db,
        image_limit=image_limit,
        local_catalog=local_catalog,
    )


@app.post("/tasks/{task_id}/retry_failed", response_model=dict, status_code=status.HTTP_202_ACCEPTED)
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
            extra = "" if len(skipped_items) <= 3 else f" (+{len(skipped_items) - 3} more)"
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
        runner=lambda context: _run_retry_failed_items_job(context, source_task=source_task),
    )
    return {
        "message": "Retry failed items task queued.",
        "task": task,
    }


@app.get("/images/", response_model=list[dict])
def read_images(
    request: Request,
    response: Response,
    skip: int = 0,
    limit: int = 10,
    group_variants: bool = Query(default=True),
    sort_by: Literal["first_added", "last_added"] = "first_added",
    search: Optional[str] = None,
    generation_software: Optional[list[str]] = Query(default=None),
    source_site: Optional[list[str]] = Query(default=None),
    mimetype: Optional[list[str]] = Query(default=None),
    nsfw_rating: Optional[list[str]] = Query(default=None),
    nsfw_safety: Optional[list[str]] = Query(default=None),
    artist_name: Optional[list[str]] = Query(default=None),
    collection_name: Optional[list[str]] = Query(default=None),
    db: Session = Depends(get_db),
):
    """
    Returns a list of images with their associated artist and license info.
    Uses ImageData class to encapsulate and display image metadata.
    """
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
        },
    )
    cache_headers = _build_json_cache_headers(cache_key)
    for header_name, header_value in cache_headers.items():
        response.headers[header_name] = header_value
    if _should_return_json_not_modified(request, cache_headers):
        return Response(status_code=304, headers=cache_headers)

    cached_payload = _search_cache_get(cache_key)
    if isinstance(cached_payload, dict):
        response.headers["X-Filtered-Count"] = str(int(cached_payload.get("filtered_count") or 0))
        items = cached_payload.get("items")
        if isinstance(items, list):
            return items

    display_items = _load_display_image_items(
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
        group_variants=group_variants,
    )
    filtered_count = len(display_items)
    response.headers["X-Filtered-Count"] = str(filtered_count)
    response_payload = display_items[skip:skip + limit]

    _search_cache_put(
        cache_key,
        {
            "filtered_count": filtered_count,
            "items": response_payload,
        },
    )

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
        "warnings": _get_runtime_warnings(),
        "capabilities": _get_media_capabilities(),
    }


@app.get("/images/keys", response_model=list[str])
def read_image_keys(
    request: Request,
    response: Response,
    group_variants: bool = Query(default=True),
    search: Optional[str] = None,
    generation_software: Optional[list[str]] = Query(default=None),
    source_site: Optional[list[str]] = Query(default=None),
    mimetype: Optional[list[str]] = Query(default=None),
    nsfw_rating: Optional[list[str]] = Query(default=None),
    nsfw_safety: Optional[list[str]] = Query(default=None),
    artist_name: Optional[list[str]] = Query(default=None),
    collection_name: Optional[list[str]] = Query(default=None),
    db: Session = Depends(get_db),
):
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
        },
    )
    cache_headers = _build_json_cache_headers(cache_key)
    for header_name, header_value in cache_headers.items():
        response.headers[header_name] = header_value
    if _should_return_json_not_modified(request, cache_headers):
        return Response(status_code=304, headers=cache_headers)

    cached_keys = _search_cache_get(cache_key)
    if isinstance(cached_keys, list):
        return [str(file_hash) for file_hash in cached_keys if file_hash]

    display_items = _load_display_image_items(
        db,
        sort_by="first_added",
        search=search,
        generation_software=generation_software,
        source_site=source_site,
        mimetype=mimetype,
        nsfw_rating=nsfw_rating,
        nsfw_safety=nsfw_safety,
        artist_name=artist_name,
        collection_name=collection_name,
        group_variants=group_variants,
    )
    keys = [str(item.get("gallery_item_key") or "") for item in display_items if item.get("gallery_item_key")]
    _search_cache_put(cache_key, keys)
    return keys


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
        sidecar_user_negative_tags: list[str] = []
        sidecar_path = (Path(IMAGE_LIBRARY_PATH) / str(image.file_path)).with_suffix(".json")
        if sidecar_path.exists():
            try:
                with open(sidecar_path, "r", encoding="utf-8") as f:
                    sidecar_data = json.load(f)
                if isinstance(sidecar_data, dict):
                    sidecar_artist_profile = sidecar_data.get("artist_profile")
                    raw_negative_tags = sidecar_data.get("user_negative_tags")
                    if isinstance(raw_negative_tags, list):
                        sidecar_user_negative_tags = [
                            str(item).strip().lower()
                            for item in raw_negative_tags
                            if str(item or "").strip()
                        ]
            except (OSError, json.JSONDecodeError):
                sidecar_artist_profile = None
                sidecar_user_negative_tags = []
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
        "user_negative_tags": sidecar_user_negative_tags,
        "user_nsfw_rating": image.user_nsfw_rating,
        "user_nsfw_safety_class": image.user_nsfw_safety_class,
    }


@app.get("/images/{file_hash}/video_poster")
def get_image_video_poster(file_hash: str, request: Request, db: Session = Depends(get_db)):
    image = db.query(ImageModel).filter(ImageModel.file_hash == file_hash).first()
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found")

    image_path = Path(IMAGE_LIBRARY_PATH) / str(image.file_path)
    if not image_path.exists() or not image_path.is_file():
        raise HTTPException(status_code=404, detail="Image file not found on disk")

    mimetype = (image.mimetype or "").lower()
    is_video = mimetype.startswith("video/") or image_path.suffix.lower() in _VIDEO_FILE_SUFFIXES
    if not is_video:
        raise HTTPException(status_code=400, detail="Image is not a video asset")

    poster_path = ensure_video_poster(image_path, IMAGE_RESOURCES_PATH)
    if poster_path is None or not poster_path.exists():
        raise HTTPException(status_code=404, detail="Video poster unavailable")

    cache_headers = _build_media_cache_headers(poster_path)
    if _should_return_not_modified(request, poster_path, cache_headers):
        return Response(status_code=304, headers=cache_headers)

    return FileResponse(str(poster_path), media_type="image/jpeg", headers=cache_headers)


@app.get("/images/{file_hash}/video_thumbnail")
def get_image_video_thumbnail(file_hash: str, request: Request, db: Session = Depends(get_db)):
    image = db.query(ImageModel).filter(ImageModel.file_hash == file_hash).first()
    if image is None:
        raise HTTPException(status_code=404, detail="Image not found")

    image_path = Path(IMAGE_LIBRARY_PATH) / str(image.file_path)
    if not image_path.exists() or not image_path.is_file():
        raise HTTPException(status_code=404, detail="Image file not found on disk")

    mimetype = (image.mimetype or "").lower()
    is_video = mimetype.startswith("video/") or image_path.suffix.lower() in _VIDEO_FILE_SUFFIXES
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


@app.post("/images/{file_hash}/repair", response_model=dict)
@app.post("/images/{file_hash}/repair_png", response_model=dict)
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

                _actions.append("Re-downloaded missing file from CivitAI source and restored to library.")
                _commit_with_lock_retry(db, context=f"Repair commit for missing image {file_hash}")
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
                    declared_file_size=_civitai_target_missing.get("declared_file_size"),
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
                        _poster = ensure_video_poster(_repaired_path, IMAGE_RESOURCES_PATH)
                        _thumb = ensure_video_thumbnail(_repaired_path, IMAGE_RESOURCES_PATH)
                        if _poster is not None:
                            _actions.append(f"Rebuilt video poster {_poster.name}.")
                        if _thumb is not None:
                            _actions.append(f"Rebuilt video thumbnail {_thumb.name}.")
                _actions.append(
                    "Re-downloaded missing file from CivitAI (content changed); created replacement record."
                )
                _commit_with_lock_retry(db, context=f"Repair commit for missing image {file_hash}")
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
            _cleanup_temp_file(mismatch_temp_path)

    actions_taken: list[str] = []
    issues_found: list[str] = []
    warnings: list[str] = []
    repaired_image: Optional[ImageModel] = None
    created_new_image = False
    png_inspection: Optional[dict[str, Any]] = None

    try:
        processor = ImageProcessor(str(image_path), db, IMAGE_LIBRARY_PATH)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not inspect image file: {e}")

    actual_mime = _normalize_mime_type(processor.mimetype)
    actual_extension = ImageProcessor.mime_to_extension(processor.mimetype) or image_path.suffix.lower() or ".jpg"
    db_mime = _normalize_mime_type(image.mimetype)
    sidecar_before = _load_image_sidecar_payload(image)
    sidecar_mime = _normalize_mime_type(sidecar_before.get("mimetype"))

    civitai_target: Optional[dict[str, Any]] = None
    civitai_image_id: Optional[int] = None
    if isinstance(image.source_url, str) and is_civitai_image_url(image.source_url):
        civitai_image_id = extract_civitai_image_id(image.source_url)
        if civitai_image_id is not None:
            try:
                civitai_target = _resolve_civitai_image_target(CivitaiAPI.get_instance(), civitai_image_id, strict=False)
            except Exception as exc:
                warnings.append(f"Could not refresh CivitAI metadata during repair: {exc}")

    declared_civitai_mime = _normalize_mime_type(civitai_target.get("mime_type")) if civitai_target else ""
    expected_library_name = f"{image.file_hash}{actual_extension}"
    preferred_file_name = _derive_preferred_file_name(
        image,
        actual_extension=actual_extension,
        civitai_target=civitai_target,
    )

    if db_mime and db_mime != actual_mime:
        issues_found.append(f"Database MIME type {db_mime} did not match actual file type {actual_mime}.")
    if sidecar_mime and sidecar_mime != actual_mime:
        issues_found.append(f"Sidecar MIME type {sidecar_mime} did not match actual file type {actual_mime}.")
    if image_path.name != expected_library_name:
        issues_found.append(f"Library filename {image_path.name} did not match expected normalized name {expected_library_name}.")
    if _looks_like_hashed_display_name(image.file_name, file_hash=image.file_hash, file_path=image.file_path):
        issues_found.append("Display filename looked like the library hash/path rather than an original source filename.")
    if declared_civitai_mime and declared_civitai_mime != actual_mime:
        issues_found.append(f"CivitAI declares {declared_civitai_mime}, but the local file is {actual_mime}.")

    if declared_civitai_mime and declared_civitai_mime != actual_mime and civitai_target and civitai_image_id is not None:
        temp_path = None
        mismatch_temp_path = None
        try:
            declared_video_like = declared_civitai_mime.startswith("video/") or _url_looks_like_video(
                civitai_target.get("image_url") if isinstance(civitai_target, dict) else None
            )
            archived_variant = None
            if declared_video_like:
                archived_variant = _archive_static_civitai_source_variant(
                    image=image,
                    civitai_image_id=civitai_image_id,
                    expected_source_url=str(civitai_target.get("source_url") or "") if isinstance(civitai_target, dict) else None,
                    declared_mime_type=civitai_target.get("mime_type") if isinstance(civitai_target, dict) else None,
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
                merged_json = dict(repaired_image.json_metadata) if isinstance(repaired_image.json_metadata, dict) else {}
                merged_json["civitai_source_variant_static"] = archived_variant
                repaired_image.json_metadata = merged_json
                repaired_path_for_sidecar = Path(IMAGE_LIBRARY_PATH) / str(repaired_image.file_path)
                if repaired_path_for_sidecar.exists():
                    repaired_processor = ImageProcessor(str(repaired_path_for_sidecar), db, IMAGE_LIBRARY_PATH)
                    repaired_processor.save_json_metadata(
                        repaired_path_for_sidecar,
                        repaired_image,
                        additional_data={"civitai_source_variant_static": archived_variant},
                    )
            if repaired_image is not None:
                repaired_path = Path(IMAGE_LIBRARY_PATH) / str(repaired_image.file_path)
                if _normalize_mime_type(repaired_image.mimetype).startswith("video/"):
                    poster_path = ensure_video_poster(repaired_path, IMAGE_RESOURCES_PATH)
                    thumbnail_path = ensure_video_thumbnail(repaired_path, IMAGE_RESOURCES_PATH)
                    if poster_path is not None:
                        actions_taken.append(f"Rebuilt video poster {poster_path.name}.")
                    if thumbnail_path is not None:
                        actions_taken.append(f"Rebuilt video thumbnail {thumbnail_path.name}.")
            actions_taken.append("Downloaded and ingested the canonical source asset to replace the mismatched local media.")
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
            raise HTTPException(status_code=500, detail=f"Failed to replace mismatched CivitAI media: {exc}")
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
                ),
                image_db_id=image.id,
            )
            after_variant = image.json_metadata.get("civitai_source_variant") if isinstance(image.json_metadata, dict) else None
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
                    original_filename=preferred_file_name or image.file_name or current_path.name,
                    replacement_reason="replaced_by_media_repair_png",
                    artist_name=image.artist.name if image.artist is not None else None,
                    source_url=image.source_url,
                    license_id=image.license_id,
                )
                repaired_image = replacement.get("repaired_image")
                created_new_image = bool(replacement.get("created_new_image"))
                actions_taken.append("Repacked damaged PNG payload into a repaired library item.")
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


@app.post("/images/{file_hash}/rescan", response_model=dict)
def rescan_image_metadata(file_hash: str, db: Session = Depends(get_db)):
    """Rescan one media file and rerun metadata hydration/backfill steps."""
    image = (
        db.query(ImageModel)
        .filter(ImageModel.file_hash == file_hash)
        .first()
    )
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
        raise HTTPException(status_code=500, detail=f"Could not rescan image metadata: {exc}")


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
    placeholder = db.query(ImageModel).filter(ImageModel.image_status == "placeholder").count()
    return {
        "active": active,
        "deleted": deleted,
        "tombstoned": tombstoned,
        "placeholder": placeholder,
    }


@app.get("/utilities/inactive_images", response_model=List[dict])
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


@app.get("/utilities/placeholders", response_model=List[dict])
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
        civitai_payload = row.json_metadata.get("civitai") if isinstance(row.json_metadata, dict) else {}
        unavailable_detail = civitai_payload.get("unavailable_detail") if isinstance(civitai_payload, dict) else {}
        if not isinstance(unavailable_detail, dict):
            unavailable_detail = {}

        item_classification = str(unavailable_detail.get("classification") or "").strip().lower()
        if normalized_classification and item_classification != normalized_classification:
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
                    row.date_modified.isoformat() if row.date_modified is not None else None
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


@app.get("/utilities/placeholders/summary", response_model=dict)
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

        civitai_payload = row.json_metadata.get("civitai") if isinstance(row.json_metadata, dict) else {}
        unavailable_detail = civitai_payload.get("unavailable_detail") if isinstance(civitai_payload, dict) else {}
        if not isinstance(unavailable_detail, dict):
            unavailable_detail = {}

        classification = str(unavailable_detail.get("classification") or "unknown").strip().lower() or "unknown"
        endpoint = str(unavailable_detail.get("endpoint") or "unknown").strip() or "unknown"
        raw_status = unavailable_detail.get("status_code")
        status_code = str(raw_status) if raw_status is not None else "unknown"

        total += 1
        by_classification[classification] = by_classification.get(classification, 0) + 1
        by_endpoint[endpoint] = by_endpoint.get(endpoint, 0) + 1
        by_status_code[status_code] = by_status_code.get(status_code, 0) + 1

    return {
        "total": total,
        "collection_id": collection_id,
        "by_classification": dict(sorted(by_classification.items(), key=lambda item: item[0])),
        "by_endpoint": dict(sorted(by_endpoint.items(), key=lambda item: item[0])),
        "by_status_code": dict(sorted(by_status_code.items(), key=lambda item: item[0])),
    }


@app.post("/utilities/images/{file_hash}/restore", response_model=dict)
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
        {"id": artist.id, "name": artist.name, "nickname": artist.nickname}
        for artist in artists
    ]


@app.get("/filters/options", response_model=dict)
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

    def _collect_nsfw_tokens_from_value(value: Any, rating_tokens: set[str], safety_tokens: set[str]) -> None:
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
                    _collect_nsfw_tokens_from_value(parsed, rating_tokens, safety_tokens)
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

    tag_names_by_source = _gallery_tag_names_by_source_db_only(db)
    tag_names = sorted({
        name
        for names in tag_names_by_source.values()
        for name in names
    })

    source_site_rows = (
        db.query(ImageModel.source_site)
        .filter(_active_image_filter())
        .distinct()
        .all()
    )
    mimetype_rows = (
        db.query(ImageModel.mimetype)
        .filter(_active_image_filter())
        .distinct()
        .all()
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
        .join(ImageCollectionMembership, ImageCollectionMembership.collection_id == CollectionModel.id)
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
        "nsfw_ratings": sorted(rating_tokens, key=lambda item: ["PG", "PG13", "R", "X", "XXX", "N/A"].index(item) if item in {"PG", "PG13", "R", "X", "XXX", "N/A"} else 999),
        "nsfw_safety": sorted(safety_tokens, key=lambda item: ["Safe", "Mature", "Explicit", "N/A"].index(item) if item in {"Safe", "Mature", "Explicit", "N/A"} else 999),
        "artist_names": _sorted_unique_text([row[0] for row in artist_name_rows]),
        "collection_names": _sorted_unique_text([row[0] for row in collection_name_rows]),
    }
    _search_cache_put(cache_key, payload, ttl_seconds=_FILTER_OPTIONS_CACHE_TTL_SECONDS)
    return payload


@app.get("/collections/", response_model=List[dict])
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


@app.post("/collections/{collection_id}/images", response_model=dict)
def add_images_to_collection(
    collection_id: int,
    payload: CollectionBulkMembershipRequest,
    db: Session = Depends(get_db),
):
    collection = db.query(CollectionModel).filter(CollectionModel.id == collection_id).first()
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
        raise HTTPException(status_code=400, detail="At least one file hash is required.")

    images = (
        db.query(ImageModel)
        .filter(ImageModel.file_hash.in_(normalized_hashes))
        .all()
    )
    images_by_hash = {str(image.file_hash): image for image in images}
    missing_hashes = [file_hash for file_hash in normalized_hashes if file_hash not in images_by_hash]

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


@app.api_route("/collections/{collection_id}/images", methods=["DELETE"], response_model=dict)
def remove_images_from_collection(
    collection_id: int,
    payload: CollectionBulkMembershipRequest,
    db: Session = Depends(get_db),
):
    collection = db.query(CollectionModel).filter(CollectionModel.id == collection_id).first()
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
        raise HTTPException(status_code=400, detail="At least one file hash is required.")

    images = (
        db.query(ImageModel)
        .filter(ImageModel.file_hash.in_(normalized_hashes))
        .all()
    )
    images_by_hash = {str(image.file_hash): image for image in images}
    missing_hashes = [file_hash for file_hash in normalized_hashes if file_hash not in images_by_hash]

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


@app.get("/licenses/", response_model=List[dict])
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


@app.post("/import_civitai/", status_code=status.HTTP_202_ACCEPTED)
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


@app.post("/collections/sync/civitai", response_model=dict, status_code=status.HTTP_202_ACCEPTED)
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
        runner=lambda context: _run_civitai_collection_sync_job(context, limit=payload.limit),
    )

    return {
        "message": "CivitAI collection sync task queued.",
        "task": task,
    }


@app.post("/civitai/backfill/nsfw-levels", response_model=dict, status_code=status.HTTP_202_ACCEPTED)
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


@app.post("/taxonomy/utils/purge_root_concepts", response_model=dict)
def taxonomy_purge_root_concepts(payload: TaxonomyPurgeRootsRequest, db: Session = Depends(get_db)):
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
        children = db.query(Concept.id).filter(Concept.parent_concept_id == current).all()
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
        "message": "Dry-run purge preview." if payload.dry_run else "Root concept branches purged.",
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
    db.query(Concept).filter(Concept.id.in_(branch_id_list)).delete(synchronize_session=False)
    db.commit()

    return response


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
def taxonomy_tree_state(
    request: Request,
    response: Response,
    include_tag_details: bool = True,
    include_tags: bool = True,
    db: Session = Depends(get_db),
):
    cache_key = _build_search_cache_key(
        "taxonomy_tree_state",
        payload={"include_tag_details": bool(include_tag_details), "include_tags": bool(include_tags)},
    )
    cache_headers = _build_json_cache_headers(cache_key, max_age_seconds=30)
    for header_name, header_value in cache_headers.items():
        response.headers[header_name] = header_value
    if _should_return_json_not_modified(request, cache_headers):
        return Response(status_code=304, headers=cache_headers)

    cached_state = _search_cache_get(cache_key)
    if isinstance(cached_state, dict):
        return cached_state

    gallery_tag_names_by_source = _gallery_tag_names_by_source_db_only(db)
    gallery_tag_usage_counts_by_source = _gallery_tag_usage_counts_by_source_db_only(db)
    gallery_tag_name_sets_by_source = {
        source: set(names)
        for source, names in gallery_tag_names_by_source.items()
    }
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
            bucket = alias_data_by_concept.setdefault(concept_id, {"aliases": [], "implies": []})
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

    danbooru_name_by_external_tag_id: dict[str, str] = {}
    for term, authority, _ in term_rows:
        authority_name = str(authority.name or "").strip().lower()
        if authority_name != "danbooru":
            continue
        external_tag_id = str(term.external_tag_id or "").strip()
        external_name = str(term.external_name or "").strip()
        if external_tag_id and external_name:
            danbooru_name_by_external_tag_id[external_tag_id] = external_name

    tags: list[dict] = []
    normalized_term_names: set[str] = set()
    referenced_concept_ids: set[int] = set()
    for term, authority, concept in term_rows:
        taxonomy_normalized_term_name = _normalize_taxonomy_text(term.external_name or "")
        if taxonomy_normalized_term_name:
            normalized_term_names.add(taxonomy_normalized_term_name)
        gallery_normalized_term_name = _normalize_gallery_tag_text(term.external_name or "")

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
            raw_examples = metadata.get("examples") if isinstance(metadata, dict) else []
            if isinstance(raw_examples, list):
                examples = [str(item) for item in raw_examples if str(item).strip()]
        post_count = None
        if source_name in {"danbooru", "civitai"}:
            raw_post_count = metadata.get("post_count") if isinstance(metadata, dict) else None
            try:
                parsed_post_count = int(raw_post_count) if raw_post_count is not None else None
            except (TypeError, ValueError):
                parsed_post_count = None
            if parsed_post_count is not None and parsed_post_count > 0:
                post_count = parsed_post_count

        mapped_danbooru_tag_id = None
        mapped_danbooru_name = None
        external_tag_id = str(term.external_tag_id or "").strip() or None
        if source_name == "prompt":
            raw_mapped_danbooru_tag_id = metadata.get("mapped_danbooru_tag_id") if isinstance(metadata, dict) else None
            if raw_mapped_danbooru_tag_id not in (None, ""):
                mapped_danbooru_tag_id = str(raw_mapped_danbooru_tag_id).strip() or None
            elif external_tag_id and external_tag_id.isdigit():
                # Prompt terms created during rescan can reuse mapped Danbooru IDs as external_tag_id.
                mapped_danbooru_tag_id = external_tag_id

            if mapped_danbooru_tag_id:
                mapped_danbooru_name = danbooru_name_by_external_tag_id.get(mapped_danbooru_tag_id)

        tag_payload = {
            "id": f"term:{term.id}",
            "authority_term_id": int(term.id),
            "name": term.external_name,
            "external_tag_id": external_tag_id,
            "source": source_name,
            "scope": (
                "gallery"
                if gallery_normalized_term_name and gallery_normalized_term_name in gallery_scope_names
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

    payload = {
        "concepts": [
            {
                "id": int(c.id),
                "canonical_name": c.canonical_name,
                "parent_concept_id": int(c.parent_concept_id) if c.parent_concept_id is not None else None,
            }
            for c in filtered_concepts
        ],
        "tags": tags,
        "gallery_tag_names_by_source": gallery_tag_names_by_source,
        "tag_usage_by_scope": {
            "gallery": gallery_tag_usage_counts_by_source,
            "selected": {source: {} for source in gallery_tag_usage_counts_by_source},
            "all": {source: {} for source in gallery_tag_usage_counts_by_source},
        },
    }
    _search_cache_put(cache_key, payload, ttl_seconds=_FILTER_OPTIONS_CACHE_TTL_SECONDS)
    return payload


# Columnar tag columns for the per-source tag endpoint.
_TAG_SOURCE_COLS = ["id", "name", "ext_id", "scope", "post_count", "concept_id", "mdtag_id", "mdtag_name"]


@app.get("/taxonomy/tree/tags/{source}", response_model=dict)
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

    cache_key = _build_search_cache_key("taxonomy_tags_for_source", payload={"source": source_lower})
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
        gallery_names_all = _gallery_tag_names_by_source_db_only(db)
        _search_cache_put(gallery_names_cache_key, gallery_names_all, ttl_seconds=_FILTER_OPTIONS_CACHE_TTL_SECONDS)
    gallery_scope_names: set[str] = {
        _normalize_gallery_tag_text(n)
        for n in gallery_names_all.get(source_lower, [])
        if n
    }

    # Reuse a shared cache for the danbooru name-by-ext-id lookup used by the
    # prompt source; avoids re-scanning 100k rows per cold prompt request.
    danbooru_name_by_ext_id: dict[str, str] = {}
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
                eid = str(ext_id or "").strip()
                ename = str(ext_name or "").strip()
                if eid and ename:
                    danbooru_name_by_ext_id[eid] = ename
            _search_cache_put(danbooru_names_cache_key, danbooru_name_by_ext_id, ttl_seconds=_FILTER_OPTIONS_CACHE_TTL_SECONDS)
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
        scope = "gallery" if (gallery_norm and gallery_norm in gallery_scope_names) else "image"

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

        external_tag_id = str(term.external_tag_id or "").strip() or None

        mdtag_id = None
        mdtag_name = None
        if source_lower == "prompt":
            raw_mapped = metadata.get("mapped_danbooru_tag_id") if isinstance(metadata, dict) else None
            if raw_mapped not in (None, ""):
                mdtag_id = str(raw_mapped).strip() or None
            elif external_tag_id and external_tag_id.isdigit():
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

    payload = {"source": source_lower, "cols": _TAG_SOURCE_COLS, "rows": rows}
    _search_cache_put(cache_key, payload, ttl_seconds=_FILTER_OPTIONS_CACHE_TTL_SECONDS)
    return payload


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


@app.delete("/taxonomy/tree/tag/{authority_term_id}", response_model=dict)
def taxonomy_tree_delete_tag(authority_term_id: int, db: Session = Depends(get_db)):
    term = db.query(AuthorityTerm).filter(AuthorityTerm.id == authority_term_id).first()
    if term is None:
        raise HTTPException(status_code=404, detail="Authority term not found")

    authority_name = str(term.authority.name or "").strip().lower() if term.authority is not None else ""
    if authority_name != "prompt":
        raise HTTPException(status_code=409, detail="Only prompt tags can be deleted from tree edit mode")

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


def _build_default_example_url(authority_name: str, external_name: str, metadata: dict[str, Any]) -> str:
    normalized_authority = str(authority_name or "").strip().lower()
    name = str(external_name or "").strip()
    if not name:
        return ""

    if normalized_authority == "civitai":
        encoded = quote(name, safe="")
        return f"https://civitai.com/search/images?tags={encoded}&sortBy=images_v6"

    if normalized_authority == "danbooru":
        wiki_url = str(metadata.get("wiki_url") or "").strip() if isinstance(metadata, dict) else ""
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
    default_url = str(_build_default_example_url(authority_name, external_name, metadata) or "").strip()
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


@app.get("/taxonomy/tree/tag/{authority_term_id}/details", response_model=dict)
def taxonomy_tree_tag_details(authority_term_id: int, db: Session = Depends(get_db)):
    term = db.query(AuthorityTerm).filter(AuthorityTerm.id == authority_term_id).first()
    if term is None:
        raise HTTPException(status_code=404, detail="Authority term not found")

    authority_name = str(term.authority.name or "").strip().lower() if term.authority is not None else ""

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
        "examples": _with_source_default_example_first(
            authority_name,
            str(term.external_name or ""),
            metadata,
            examples,
        ),
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

    authority_name = str(term.authority.name or "").strip().lower() if term.authority is not None else ""

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


if __name__ == "__main__":
    _main()
