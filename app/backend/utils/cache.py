"""In-memory search/gallery cache, ETag helpers, and access-log filtering.

These utilities are extracted from the original monolithic main.py so they
can be shared by router modules without circular imports.  The module-level
SQLAlchemy event listener is registered on import, so any module that uses
the cache must import (or transitively import) this module.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Optional

from fastapi import Request
from sqlalchemy import event
from sqlalchemy.orm import Session

# ---------------------------------------------------------------------------
# Environment-configurable cache parameters
# ---------------------------------------------------------------------------

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

# ---------------------------------------------------------------------------
# Thread-safe in-memory search cache
# ---------------------------------------------------------------------------


@dataclass
class _SearchCacheEntry:
    value: Any
    expires_at_monotonic: float
    version: int


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


def _current_search_cache_version() -> int:
    with _search_cache_lock:
        return int(_search_cache_version)


def _current_gallery_cache_version() -> int:
    with _search_cache_lock:
        return int(_gallery_cache_version)


# ---------------------------------------------------------------------------
# ETag / HTTP caching helpers
# ---------------------------------------------------------------------------


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


def _should_return_json_not_modified(
    request: Request, headers: dict[str, str]
) -> bool:
    if_none_match = request.headers.get("if-none-match")
    if not if_none_match:
        return False
    return _if_none_match_contains(if_none_match, headers.get("ETag", ""))


# ---------------------------------------------------------------------------
# Uvicorn access-log filtering
# ---------------------------------------------------------------------------


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
