# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/civitai-cache.md
# ──────────────────────────────────────────────────────────────────────────────
"""Persistence layer for the CivitAI API response cache.

Design principles:
- append-on-change: a new row is written only when response_hash differs from
  the current latest; identical re-fetches update fetched_at in place.
- is_latest flag: only one row per (endpoint, request_key) has is_latest=True.
- prev_id chain: each new append points back at the previous latest row so
  history can be traversed in order.
- failure-safe writes: callers (especially _record_to_db_cache in CivitaiAPI)
  must wrap calls to record_response() in try/except — this module itself
  raises on unexpected DB errors but does not swallow them.
- endpoint exclusions: signals.getToken and multi-search are never cached;
  callers are responsible for not passing those endpoints here, but
  record_response() also guards against them defensively.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from models import CivitaiApiCacheEntry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Endpoints that must never be cached (transient / auth tokens).
# ---------------------------------------------------------------------------

_EXCLUDED_ENDPOINTS: frozenset[str] = frozenset(
    {"signals.getToken", "multi-search"}
)

# ---------------------------------------------------------------------------
# Request-key canonicalization
# ---------------------------------------------------------------------------

# Fields used to build the request key, in declaration order, per endpoint.
# Fields absent from the payload are silently omitted.
_KEY_FIELDS: dict[str, list[str]] = {
    "image.get": ["id"],
    "image.getGenerationData": ["id"],
    "tag.getVotableTags": ["id", "type"],
    "tag.getById": ["id"],
    "model.getById": ["id"],
    "modelVersion.getById": ["id"],
    "post.get": ["id"],
    "model.getAll": ["username", "cursor", "sort", "period", "limit"],
    "image.getInfinite": [
        "collectionId",
        "postId",
        "modelId",
        "modelVersionId",
        "username",
        "sort",
        "period",
        "browsingLevel",
        "types",
        "cursor",
    ],
    "post.getInfinite": ["cursor", "collectionId"],
    "collection.getAllUser": ["userId"],
}


def build_request_key(endpoint: str, payload: dict[str, Any] | None) -> str:
    """Return a stable, human-readable cache key for the given endpoint call.

    Builds a ``field=value&field=value`` string from a per-endpoint whitelist
    of payload fields.  Fields absent from the payload are omitted.  If the
    endpoint has no declared key fields (or the payload is empty/None), the
    empty string is returned — those calls are still cached but keyed under
    ``""``.

    The key is intentionally human-readable rather than opaque so that
    ``SELECT * FROM civitai_api_cache WHERE endpoint='image.get' AND
    request_key='id=12345'`` just works.
    """
    if not payload:
        return ""
    fields = _KEY_FIELDS.get(endpoint, [])
    if not fields:
        # Unknown endpoint — fall back to sorted full payload key
        return "&".join(
            f"{k}={payload[k]}" for k in sorted(payload) if payload[k] is not None
        )
    parts = [
        f"{f}={payload[f]}"
        for f in fields
        if f in payload and payload[f] is not None
    ]
    return "&".join(parts)


# ---------------------------------------------------------------------------
# Response hashing
# ---------------------------------------------------------------------------


def canonical_hash(obj: Any) -> str:
    """Return a SHA-256 hex digest of the sorted-keys JSON serialization of obj.

    None/null responses hash as the empty-object ``{}`` serialization so that
    tombstone rows (404s with no body) still have a stable, comparable hash.
    """
    if obj is None:
        obj = {}
    serialized = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------


def record_response(
    db: Session,
    *,
    endpoint: str,
    payload: dict[str, Any] | None,
    response_json: Any,
    http_status: int = 200,
) -> CivitaiApiCacheEntry | None:
    """Persist one API response to the cache.

    Returns the (new or updated) cache row, or None if the endpoint is
    excluded from caching.

    Append-on-change semantics:
    - If no row exists yet → insert a new row with is_latest=True.
    - If a latest row exists and the hash matches → update fetched_at only.
    - If a latest row exists but the hash differs → flip old is_latest=False,
      insert new row with is_latest=True and prev_id=old.id.

    This function commits the session.  It is the caller's responsibility to
    ensure the session is independent of any ongoing transaction they care
    about (use a dedicated SessionLocal() session).
    """
    if endpoint in _EXCLUDED_ENDPOINTS:
        return None

    request_key = build_request_key(endpoint, payload)
    new_hash = canonical_hash(response_json)
    now = datetime.now(UTC).replace(tzinfo=None)  # store naive UTC

    existing = (
        db.query(CivitaiApiCacheEntry)
        .filter(
            CivitaiApiCacheEntry.endpoint == endpoint,
            CivitaiApiCacheEntry.request_key == request_key,
            CivitaiApiCacheEntry.is_latest.is_(True),
        )
        .first()
    )

    if existing is None:
        # First time we've seen this (endpoint, request_key)
        entry = CivitaiApiCacheEntry(
            endpoint=endpoint,
            request_key=request_key,
            request_payload=payload,
            response_json=response_json,
            response_hash=new_hash,
            http_status=http_status,
            fetched_at=now,
            is_latest=True,
            prev_id=None,
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)
        return entry

    if existing.response_hash == new_hash:
        # Identical response — just refresh the timestamp
        existing.fetched_at = now
        existing.http_status = http_status
        db.commit()
        db.refresh(existing)
        return existing

    # Response changed — append a new row
    existing.is_latest = False
    old_id = existing.id

    new_entry = CivitaiApiCacheEntry(
        endpoint=endpoint,
        request_key=request_key,
        request_payload=payload,
        response_json=response_json,
        response_hash=new_hash,
        http_status=http_status,
        fetched_at=now,
        is_latest=True,
        prev_id=old_id,
    )
    db.add(new_entry)
    db.commit()
    db.refresh(new_entry)
    return new_entry


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------


def get_latest(
    db: Session,
    *,
    endpoint: str,
    request_key: str,
) -> CivitaiApiCacheEntry | None:
    """Return the most recent cache row for the given (endpoint, request_key).

    Returns None if no cached row exists.
    """
    return (
        db.query(CivitaiApiCacheEntry)
        .filter(
            CivitaiApiCacheEntry.endpoint == endpoint,
            CivitaiApiCacheEntry.request_key == request_key,
            CivitaiApiCacheEntry.is_latest.is_(True),
        )
        .first()
    )


def get_history(
    db: Session,
    *,
    endpoint: str,
    request_key: str,
    limit: int | None = None,
) -> list[CivitaiApiCacheEntry]:
    """Return all cache rows for (endpoint, request_key) newest-first.

    Includes both latest and historical rows.  Pass limit to cap results.
    """
    q = (
        db.query(CivitaiApiCacheEntry)
        .filter(
            CivitaiApiCacheEntry.endpoint == endpoint,
            CivitaiApiCacheEntry.request_key == request_key,
        )
        .order_by(CivitaiApiCacheEntry.fetched_at.desc())
    )
    if limit is not None:
        q = q.limit(limit)
    return q.all()
