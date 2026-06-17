"""CivitAI Meilisearch proxy routes.

Extracted from main.py (lines ~20135–20327).

Routes:
  POST /civitai-search
  GET  /civitai-search/auth-status
"""

from __future__ import annotations

import threading
from typing import Any

import atelierai.config as app_config
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
from models import (
    CivitaiArtistPreference,
    CivitaiSearchImage,
    CivitaiSearchImageLink,
    CivitaiSearchRecord,
    ImageModel,
)
from schemas import (
    CivitaiArtistBlockRequest,
    CivitaiArtistSummaryItem,
    CivitaiImageRatingRequest,
    CivitaiImageRatingResponse,
    CivitaiSearchRecordRequest,
    CivitaiSearchRequest,
)
from utils.cache import (
    _build_search_cache_key,
    _search_cache_get,
    _search_cache_put,
)

router = APIRouter(prefix="/civitai-search", tags=["civitai-search"])


# ---------------------------------------------------------------------------
# Module-level singletons
# ---------------------------------------------------------------------------

_civitai_search_client = None
_civitai_search_client_lock = threading.Lock()

# CivitAI image CDN base for constructing URLs from Meilisearch UUIDs.
_CIVITAI_IMAGE_CDN = getattr(
    app_config,
    "CIVITAI_CDN_BASE_URL",
    "https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
    # Three tiers: thumbnail (tiles), mid-res (details/fullscreen), original
    uuid = hit.get("url", "")
    if uuid and "/" not in uuid:
        # Meilisearch stores just the UUID slug.
        out["thumbnail_url"] = f"{_CIVITAI_IMAGE_CDN}/{uuid}/width=450/{uuid}"
        out["mid_res_url"] = f"{_CIVITAI_IMAGE_CDN}/{uuid}/width=1260/{uuid}"
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


def _build_hit_from_trpc(
    basic_info: dict, generation_data: dict, tag_records: list[dict]
) -> dict:
    """Build a normalised hit dict from CivitAI tRPC endpoint responses.

    Produces the same shape as ``_normalize_meili_hit`` so the frontend can
    consume it without any changes.

    Sources:
        basic_info      → ``image.get``
        generation_data → ``image.getGenerationData``
        tag_records     → ``tag.getVotableTags``
    """
    meta = generation_data.get("meta") if generation_data else None
    if not isinstance(meta, dict):
        meta = {}

    uuid = basic_info.get("url", "") or ""

    # ── Image URLs (same construction as _normalize_meili_hit) ──
    thumbnail_url = mid_res_url = full_url = uuid
    if uuid and "/" not in uuid:
        thumbnail_url = f"{_CIVITAI_IMAGE_CDN}/{uuid}/width=450/{uuid}"
        mid_res_url = f"{_CIVITAI_IMAGE_CDN}/{uuid}/width=1260/{uuid}"
        full_url = f"{_CIVITAI_IMAGE_CDN}/{uuid}/original=true/{uuid}"

    # ── Tags ──
    tag_names = [
        t.get("name") for t in tag_records if isinstance(t, dict) and t.get("name")
    ]
    tag_ids = [
        t.get("id")
        for t in tag_records
        if isinstance(t, dict) and t.get("id") is not None
    ]

    # ── Author ──
    user = basic_info.get("user")
    if not isinstance(user, dict):
        user = None

    # ── Build the hit ──
    hit: dict[str, Any] = {
        "id": basic_info.get("id", 0),
        "url": full_url,
        "thumbnail_url": thumbnail_url,
        "mid_res_url": mid_res_url,
        "name": basic_info.get("name", ""),
        "mimeType": basic_info.get("mimeType", "image/jpeg"),
        "nsfwLevel": basic_info.get("nsfwLevel"),
        "createdAt": basic_info.get("createdAt"),
        "publishedAt": basic_info.get("publishedAt"),
        "postId": basic_info.get("postId"),
        # Tags from tag.getVotableTags
        "tagNames": tag_names,
        "tagIds": tag_ids,
        # Generation metadata from image.getGenerationData
        "prompt": meta.get("prompt", ""),
        "negativePrompt": meta.get("negativePrompt", ""),
        "baseModel": meta.get("baseModel"),
        "sampler": meta.get("sampler"),
        "steps": meta.get("steps"),
        "cfgScale": meta.get("cfgScale"),
        "seed": meta.get("seed"),
        "clipSkip": meta.get("clipSkip"),
    }

    # Dimensions: prefer generation_data meta, fall back to basic_info
    hit["width"] = meta.get("width") or basic_info.get("width")
    hit["height"] = meta.get("height") or basic_info.get("height")

    # BlurHash
    if basic_info.get("hash"):
        hit["hash"] = basic_info["hash"]
        hit["blurhash"] = basic_info["hash"]

    # Author info in Meilisearch-like format
    if user:
        hit["user"] = {
            "username": user.get("username", ""),
            "image": user.get("image"),
        }
        hit["username"] = user.get("username", "")

    # Stats: pass through if present (tRPC image.get may include them)
    stats = basic_info.get("stats")
    if isinstance(stats, dict):
        normalised = {}
        for key, value in stats.items():
            short = key.replace("AllTime", "")
            normalised[short] = value
        hit["stats"] = normalised

    # Resources (LoRAs, models, embeddings)
    resources = generation_data.get("resources") if generation_data else None
    if isinstance(resources, list) and resources:
        hit["resources"] = resources
        # Extract baseModel from meta if not already set
        if not hit.get("baseModel") and meta.get("baseModel"):
            hit["baseModel"] = meta["baseModel"]

    # Meta dict for frontend fallbacks (tags, etc.)
    if meta:
        hit["meta"] = meta

    return hit


def _classify_civitai_upstream_error(exc: Any) -> HTTPException:
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


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("", response_model=dict)
def civitai_search_proxy(payload: CivitaiSearchRequest, db: Session = Depends(get_db)):
    """Proxy a search request to the CivitAI Meilisearch host.

    Handles bearer-token acquisition automatically from the cached session
    cookie. Returns the raw Meilisearch result envelope.
    """
    from atelierai.civitai.http_client import CivitaiRequestError

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
            "matching_strategy": payload.matching_strategy,
        },
    )
    cached = _search_cache_get(cache_key)
    if cached is not None:
        return cached

    client = _get_civitai_search_client()

    # Split comma-separated usernames into a list for multi-user search.
    # Meilisearch accepts repeated ``users`` CGI params (e.g.
    # ``users=alice&users=bob``) to scope results to any listed artist.
    users_list: list[str] = []
    if payload.username:
        users_list = [
            u.strip() for u in payload.username.split(",") if u.strip()
        ]

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
            username=users_list[0] if len(users_list) == 1 else None,
            facets=payload.facets,
            extra_filters=payload.extra_filters,
            matching_strategy=payload.matching_strategy,
            users=users_list if len(users_list) > 1 else None,
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

    # Filter out images the user has discarded in previous sessions.
    excluded_ids = _get_excluded_civitai_image_ids(db)
    if excluded_ids:
        normalized_hits = [
            h for h in normalized_hits if h.get("id") not in excluded_ids
        ]

    # Filter out images from blocked artists.
    blocked_names = _get_blocked_artist_names(db)
    if blocked_names:
        normalized_hits = [
            h for h in normalized_hits
            if h.get("user", {}).get("username") not in blocked_names
        ]

    response = {
        "hits": normalized_hits,
        "total": result.get("estimatedTotalHits") or 0,
        "offset": result.get("offset", payload.offset),
        "limit": result.get("limit", payload.limit),
        "processing_time_ms": result.get("processingTimeMs", 0),
        "facets": {
            "distribution": result.get("facetDistribution"),
            "stats": result.get("facetStats"),
        },
        "backend": result.get("backend", "unknown"),
    }

    # Cache with a shorter TTL for search results.
    _search_cache_put(cache_key, response, ttl_seconds=60)

    return response


@router.get("/image/{image_id}", response_model=dict)
def civitai_search_single_image(image_id: int):
    """Fetch fresh metadata for a single CivitAI image via tRPC endpoints.

    Used by the frontend 'r' (reload) action to re-fetch tags, models,
    prompt, and other metadata that may have been missing or stale in the
    original search response.

    Uses the CivitAI tRPC endpoints (``image.get``, ``image.getGenerationData``,
    ``tag.getVotableTags``) instead of Meilisearch, because Meilisearch
    returns null ``tagNames`` / ``tagIds`` for id-filtered queries.

    Returns a normalised hit dict (same shape as ``_normalize_meili_hit``
    output) or 404 if the image is not found.
    """
    from atelierai.civitai.civitai_api import CivitaiAPI
    from atelierai.civitai.http_client import CivitaiRequestError

    try:
        api = CivitaiAPI.get_instance()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"CivitAI API initialisation error: {exc}",
        )

    # ── Fetch from all three tRPC endpoints ──
    basic_info: dict = {}
    generation_data: dict = {}
    tag_records: list[dict] = []

    try:
        basic_info = api.fetch_basic_info(image_id) or {}
    except CivitaiRequestError as exc:
        raise _classify_civitai_upstream_error(exc)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"CivitAI image.get error: {exc}",
        )

    if not basic_info:
        raise HTTPException(
            status_code=404,
            detail=f"Image {image_id} not found on CivitAI.",
        )

    # Generation data and tags are best-effort (fail-open per AGENTS.md).
    try:
        generation_data = api.fetch_generation_data(image_id) or {}
    except CivitaiRequestError as exc:
        raise _classify_civitai_upstream_error(exc)
    except Exception:
        pass  # fail-open

    try:
        tag_records = api.fetch_image_tag_records(image_id) or []
    except Exception:
        pass  # fail-open

    # ── Build normalised hit from tRPC responses ──
    return {"hit": _build_hit_from_trpc(basic_info, generation_data, tag_records)}


@router.get("/library-status", response_model=dict)
def civitai_search_library_status(
    civitai_image_ids: str = Query("", description="Comma-separated CivitAI image IDs"),
    db: Session = Depends(get_db),
):
    """Check which CivitAI image IDs are already in the local library.

    Returns a dict mapping each found CivitAI ID to its local file info.
    IDs not present in the library are simply absent from the response.
    """
    if not civitai_image_ids:
        return {"imported": {}}

    try:
        ids = [int(x.strip()) for x in civitai_image_ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="civitai_image_ids must be comma-separated integers.",
        )

    if not ids:
        return {"imported": {}}

    # Cap to prevent abuse
    if len(ids) > 200:
        raise HTTPException(
            status_code=400,
            detail=f"Too many IDs ({len(ids)}). Maximum 200 per request.",
        )

    rows = (
        db.query(
            ImageModel.civitai_image_id,
            ImageModel.file_hash,
            ImageModel.file_name,
        )
        .filter(
            ImageModel.civitai_image_id.in_(ids),
            ImageModel.image_status.in_(["active", "placeholder"]),
        )
        .all()
    )

    imported = {
        str(row.civitai_image_id): {"file_hash": row.file_hash, "file_name": row.file_name}
        for row in rows
        if row.civitai_image_id is not None
    }

    return {"imported": imported}


@router.get("/auth-status", response_model=dict)
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


# ---------------------------------------------------------------------------
# Image preference tracking (keep / discard / skip)
# ---------------------------------------------------------------------------


def _get_excluded_civitai_image_ids(db: Session) -> set[int]:
    """Return the set of CivitAI image IDs the user has discarded.

    An image is considered excluded when *any* link record has
    ``is_excluded = True``.  Once excluded the image stays hidden across all
    future searches until the user changes the rating.
    """
    rows = (
        db.query(CivitaiSearchImage.civitai_image_id)
        .join(
            CivitaiSearchImageLink,
            CivitaiSearchImageLink.image_id == CivitaiSearchImage.id,
        )
        .filter(CivitaiSearchImageLink.is_excluded.is_(True))
        .distinct()
        .all()
    )
    return {r[0] for r in rows}


def _upsert_search_image(db: Session, hit: CivitaiImageRatingRequest) -> CivitaiSearchImage:
    """Insert or update a CivitaiSearchImage row from the request data."""
    img = (
        db.query(CivitaiSearchImage)
        .filter(CivitaiSearchImage.civitai_image_id == hit.civitai_image_id)
        .first()
    )
    if img is None:
        img = CivitaiSearchImage(civitai_image_id=hit.civitai_image_id)
        db.add(img)
        db.flush()

    # Update mutable metadata fields.
    img.post_id = hit.post_id
    img.artist_id = hit.artist_id
    img.artist_name = hit.artist_name
    img.file_name = hit.file_name
    img.blurhash = hit.blurhash
    img.uuid = hit.uuid
    img.file_size = hit.file_size
    img.image_url = hit.image_url
    img.tags = hit.tags
    img.generation_prompt = hit.generation_prompt
    img.generation_models = hit.generation_models
    img.reactions = hit.reactions
    img.likes = hit.likes
    db.flush()
    return img


def _update_artist_preference(
    db: Session,
    artist_id: int | None,
    artist_name: str | None,
    *,
    is_keep: bool,
) -> None:
    """Increment keep/discard counter for the artist, if known."""
    if not artist_name and artist_id is None:
        return

    pref = (
        db.query(CivitaiArtistPreference)
        .filter(
            CivitaiArtistPreference.artist_id == artist_id,
            CivitaiArtistPreference.artist_name == artist_name,
        )
        .first()
    )
    if pref is None:
        pref = CivitaiArtistPreference(
            artist_id=artist_id,
            artist_name=artist_name or f"artist-{artist_id}",
            keeps=0,
            discards=0,
        )
        db.add(pref)

    if is_keep:
        pref.keeps = (pref.keeps or 0) + 1
    else:
        pref.discards = (pref.discards or 0) + 1


@router.post("/rate", response_model=CivitaiImageRatingResponse)
def rate_civitai_image(
    payload: CivitaiImageRatingRequest, db: Session = Depends(get_db)
):
    """Record a keep / discard / skip rating for a CivitAI search image.

    * **discard** marks the image as excluded — it will be hidden from all
      future search results.
    * **keep** clears any previous exclusion.
    * **skip** advances without changing the exclusion state.
    """
    img = _upsert_search_image(db, payload)

    is_excluded = payload.rating == "discard"

    # Find or create the link row for this search+image pair.
    # When search_id is None we look for a standalone link (search_id IS NULL)
    # so we don't create duplicates on repeated ratings.
    if payload.search_id is not None:
        link = (
            db.query(CivitaiSearchImageLink)
            .filter(
                CivitaiSearchImageLink.search_id == payload.search_id,
                CivitaiSearchImageLink.image_id == img.id,
            )
            .first()
        )
    else:
        link = (
            db.query(CivitaiSearchImageLink)
            .filter(
                CivitaiSearchImageLink.search_id.is_(None),
                CivitaiSearchImageLink.image_id == img.id,
            )
            .first()
        )

    if link is None:
        link = CivitaiSearchImageLink(
            image_id=img.id,
            search_id=payload.search_id,
            position=payload.position,
        )
        db.add(link)

    link.rating = payload.rating
    link.is_excluded = is_excluded
    if payload.position is not None:
        link.position = payload.position

    # Update artist preference counters (only for keep/discard, not skip).
    if payload.rating in ("keep", "discard"):
        _update_artist_preference(
            db,
            artist_id=payload.artist_id,
            artist_name=payload.artist_name,
            is_keep=(payload.rating == "keep"),
        )

    db.commit()
    return CivitaiImageRatingResponse(
        status="ok", rating=payload.rating, is_excluded=is_excluded
    )


@router.get("/excluded-ids", response_model=dict)
def get_excluded_image_ids(db: Session = Depends(get_db)):
    """Return CivitAI image IDs the user has discarded (for client-side filtering)."""
    return {"excluded_ids": sorted(_get_excluded_civitai_image_ids(db))}


@router.get("/ratings", response_model=dict)
def get_image_ratings(
    civitai_image_ids: str = Query("", description="Comma-separated CivitAI image IDs"),
    db: Session = Depends(get_db),
):
    """Return the user's keep/discard/skip ratings for the given CivitAI image IDs.

    Returns a dict mapping each rated CivitAI image ID (as string) to its
    most recent rating (``"keep"``, ``"discard"``, or ``"skip"``).  IDs
    with no rating are simply absent from the response.

    When an image has multiple link rows (from different searches), the
    most recently created link wins.
    """
    if not civitai_image_ids:
        return {"ratings": {}}

    try:
        ids = [int(x.strip()) for x in civitai_image_ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="civitai_image_ids must be comma-separated integers.",
        )

    if not ids:
        return {"ratings": {}}

    if len(ids) > 200:
        raise HTTPException(
            status_code=400,
            detail=f"Too many IDs ({len(ids)}). Maximum 200 per request.",
        )

    # Join CivitaiSearchImage → CivitaiSearchImageLink, pick the latest rating
    # per civitai_image_id using a window function.
    from sqlalchemy import func as sa_func

    # Subquery: rank link rows per image by created_at DESC.
    ranked = (
        db.query(
            CivitaiSearchImage.civitai_image_id.label("cid"),
            CivitaiSearchImageLink.rating.label("rating"),
            sa_func.row_number()
            .over(
                partition_by=CivitaiSearchImage.civitai_image_id,
                order_by=CivitaiSearchImageLink.created_at.desc(),
            )
            .label("rn"),
        )
        .join(
            CivitaiSearchImageLink,
            CivitaiSearchImageLink.image_id == CivitaiSearchImage.id,
        )
        .filter(
            CivitaiSearchImage.civitai_image_id.in_(ids),
            CivitaiSearchImageLink.rating.isnot(None),
        )
        .subquery()
    )

    rows = db.query(ranked.c.cid, ranked.c.rating).filter(ranked.c.rn == 1).all()

    ratings = {str(row.cid): row.rating for row in rows if row.cid is not None}
    return {"ratings": ratings}


@router.post("/search-record", response_model=dict)
def create_search_record(
    payload: CivitaiSearchRecordRequest, db: Session = Depends(get_db)
):
    """Persist a search query for history/tracking.  Returns the new record ID."""
    record = CivitaiSearchRecord(
        search_text=payload.search_text,
        search_terms=payload.search_terms,
        search_rating=payload.search_rating,
        result_count=payload.result_count,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return {"id": record.id}


# ---------------------------------------------------------------------------
# Artist preference summary & blocking
# ---------------------------------------------------------------------------


def _get_blocked_artist_names(db: Session) -> set[str]:
    """Return the set of artist names the user has blocked."""
    rows = (
        db.query(CivitaiArtistPreference.artist_name)
        .filter(CivitaiArtistPreference.is_blocked.is_(True))
        .all()
    )
    return {r[0] for r in rows}


@router.get("/artist-summary", response_model=list[CivitaiArtistSummaryItem])
def get_artist_summary(db: Session = Depends(get_db)):
    """Return aggregated keep/discard scores for all rated artists.

    Sorted by score descending (score = keeps − discards).  Artists with a
    positive score appear first; blocked artists are pushed to the bottom.
    """
    rows = (
        db.query(CivitaiArtistPreference)
        .order_by(CivitaiArtistPreference.is_blocked.asc())
        .all()
    )
    result = []
    for r in rows:
        score = (r.keeps or 0) - (r.discards or 0)
        result.append(
            CivitaiArtistSummaryItem(
                artist_id=r.artist_id,
                artist_name=r.artist_name,
                keeps=r.keeps or 0,
                discards=r.discards or 0,
                score=score,
                is_blocked=r.is_blocked,
            )
        )
    # Sort by score descending, then by artist name.
    result.sort(key=lambda x: (-x.score, x.artist_name.lower()))
    return result


@router.post("/artist-block", response_model=CivitaiArtistSummaryItem)
def toggle_artist_block(
    payload: CivitaiArtistBlockRequest, db: Session = Depends(get_db)
):
    """Set the blocked status for a specific artist."""
    pref = (
        db.query(CivitaiArtistPreference)
        .filter(
            CivitaiArtistPreference.artist_id == payload.artist_id,
            CivitaiArtistPreference.artist_name == payload.artist_name,
        )
        .first()
    )
    if pref is None:
        pref = CivitaiArtistPreference(
            artist_id=payload.artist_id,
            artist_name=payload.artist_name,
            keeps=0,
            discards=0,
        )
        db.add(pref)

    pref.is_blocked = payload.is_blocked
    db.commit()
    db.refresh(pref)

    return CivitaiArtistSummaryItem(
        artist_id=pref.artist_id,
        artist_name=pref.artist_name,
        keeps=pref.keeps or 0,
        discards=pref.discards or 0,
        score=(pref.keeps or 0) - (pref.discards or 0),
        is_blocked=pref.is_blocked,
    )
