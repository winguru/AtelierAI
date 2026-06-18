"""CivitAI Meilisearch proxy routes.

Extracted from main.py (lines ~20135–20327).

Routes:
  POST /civitai-search
  GET  /civitai-search/auth-status
"""

from __future__ import annotations

import logging
import threading
from typing import Any

import atelierai.config as app_config
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Text as sa_Text, func as sa_func, or_ as sa_or
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

logger = logging.getLogger(__name__)

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

# B2-backed direct-media endpoint used for video content.  The standard
# image CDN serves the raw MP4 even when image URL patterns are used, so
# videos must use this endpoint for playable URLs.
_CIVITAI_B2_MEDIA = "https://image-b2.civitai.com/file/civitai-media-cache"


def _build_cdn_urls(
    uuid: str, *, orig_width: int | None = None
) -> tuple[str | None, str | None, str | None]:
    """Build (thumbnail, mid_res, original) URLs for a CivitAI CDN UUID.

    The mid-res tier is intentionally set to the original image.  CivitAI's
    CDN rounds requested widths UP to the nearest standard tier (450, 650,
    800, 1000, 1260, 1600, 2000, ...), and typical AI-generated images are
    512-1216px wide.  Requesting ``width=1260`` almost always causes the CDN
    to serve a 1600px-tier image that has been upscaled from the original,
    introducing interpolation artifacts (blockiness).  Serving the original
    directly avoids this entirely while the thumbnail tier (450px) still
    handles fast tile rendering.
    """
    if not uuid or "/" in uuid:
        return None, None, None

    thumb = f"{_CIVITAI_IMAGE_CDN}/{uuid}/width=450/{uuid}"
    full = f"{_CIVITAI_IMAGE_CDN}/{uuid}/original=true/{uuid}"

    return thumb, full, full


def _build_video_url(uuid: str) -> str | None:
    """Build a directly-playable URL for a CivitAI video asset.

    Video content (``type: video``) cannot use the image CDN — that endpoint
    serves the raw MP4 regardless of the width/quality parameters.  The B2
    media cache serves the original file with correct ``video/mp4`` headers
    that a ``<video>`` element can play.
    """
    if not uuid or "/" in uuid:
        return None
    return f"{_CIVITAI_B2_MEDIA}/{uuid}/original"


def _is_video_hit(raw: dict) -> bool:
    """Return True if a raw CivitAI hit represents video content.

    Checks the ``type`` field (``'video'``) and the ``mimeType`` field
    (``video/*``) for robustness across Meilisearch and tRPC payloads.
    """
    hit_type = str(raw.get("type", "") or "").strip().lower()
    if hit_type == "video":
        return True
    mime = str(raw.get("mimeType", "") or raw.get("mime_type", "") or "").strip().lower()
    return mime.startswith("video/")


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


def _maybe_lazy_fetch_missing_metadata(hits: list[dict]) -> None:
    """Lazily fetch tags / generation data for hits missing them.

    Runs in a background thread.  For each hit whose ``tagNames`` / ``prompt``
    is still null/empty after the DB enrichment pass, this fires off the
    batched tRPC call (``fetch_batch_for_image``) which checks the DB cache
    first and only makes a live API call on a cache miss.  Fetched data is
    persisted to ``civitai_search_images`` so it's available immediately on
    the next search or page refresh.

    This is best-effort and fail-open: any error is logged and swallowed.
    """
    missing_ids = [
        h["id"]
        for h in hits
        if isinstance(h.get("id"), int)
        and (
            not h.get("tagNames")
            or all(t is None for t in h.get("tagNames"))
            or not h.get("prompt")
        )
    ]
    if not missing_ids:
        return

    # Cap to avoid hammering the API on large result sets.
    ids_to_fetch = missing_ids[:20]

    def _worker() -> None:
        try:
            from atelierai.civitai.civitai_api import CivitaiAPI

            api = CivitaiAPI.get_instance()
        except Exception:
            logger.debug("CivitAI API unavailable for lazy fetch", exc_info=True)
            return

        from database import SessionLocal

        for image_id in ids_to_fetch:
            try:
                batch = api.fetch_batch_for_image(
                    image_id,
                    need_generation_data=True,
                    need_tag_records=True,
                )
                tag_records = batch.get("tag_records") or []
                gen_data = batch.get("generation_data") or {}
                meta = gen_data.get("meta") if isinstance(gen_data, dict) else {}
                if not isinstance(meta, dict):
                    meta = {}

                tag_names = [
                    t.get("name")
                    for t in tag_records
                    if isinstance(t, dict) and t.get("name")
                ] or None
                prompt = meta.get("prompt") or None
                resources = gen_data.get("resources") if isinstance(gen_data, dict) else None
                models = resources if isinstance(resources, list) and resources else None

                if not tag_names and not prompt and not models:
                    continue

                db = SessionLocal()
                try:
                    _persist_search_image(
                        db,
                        civitai_image_id=image_id,
                        tags=tag_names,
                        generation_prompt=prompt,
                        generation_models=models,
                    )
                    db.commit()
                except Exception:
                    db.rollback()
                    logger.debug(
                        "Lazy persist failed for image %s", image_id, exc_info=True
                    )
                finally:
                    db.close()
            except Exception:
                logger.debug(
                    "Lazy fetch failed for image %s", image_id, exc_info=True
                )

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()


def _enrich_hits_from_db(db: Session, hits: list[dict]) -> list[dict]:
    """Merge stored tags / generation data into hits that lack them.

    Meilisearch frequently returns ``null`` for ``tagNames`` / ``tagIds``,
    and may omit generation metadata entirely.  When a user has previously
    refreshed an image (via the ``r`` key) or rated it, we already have the
    full metadata stored in ``civitai_search_images``.  This function fills
    in any missing fields from that stored copy so the user doesn't lose
    their manually-refreshed data on page reload.

    Only fills fields that are null / empty in the Meilisearch hit — it
    never overwrites data already present from the live search.
    """
    if not hits:
        return hits

    # Collect the civitai image IDs present in this result set.
    civitai_ids: list[int] = []
    for h in hits:
        cid = h.get("id")
        if isinstance(cid, int):
            civitai_ids.append(cid)

    if not civitai_ids:
        return hits

    # Fetch stored metadata for any of these images we've seen before.
    stored_rows = (
        db.query(CivitaiSearchImage)
        .filter(CivitaiSearchImage.civitai_image_id.in_(civitai_ids))
        .all()
    )
    stored_by_id: dict[int, CivitaiSearchImage] = {
        r.civitai_image_id: r for r in stored_rows
    }

    enriched_count = 0
    for h in hits:
        cid = h.get("id")
        if not isinstance(cid, int):
            continue
        row = stored_by_id.get(cid)
        if row is None:
            continue

        filled = False

        # ── Tags ──
        # Meilisearch sometimes returns ``tagNames: [None, None, ...]`` —
        # a non-empty list filled entirely with nulls.  ``bool([None, ...])``
        # is True, so a simple truthiness check skips enrichment.  We need
        # to detect this "effectively empty" case explicitly.
        existing_tags = h.get("tagNames")
        tags_effectively_empty = not existing_tags or all(
            t is None for t in existing_tags
        )
        if tags_effectively_empty and row.tags:
            h["tagNames"] = list(row.tags)
            filled = True

        # ── Generation prompt ──
        if (not h.get("prompt")) and row.generation_prompt:
            h["prompt"] = row.generation_prompt
            filled = True

        # ── Generation models / resources ──
        if (not h.get("models")) and row.generation_models:
            h["models"] = row.generation_models
            filled = True

        if filled:
            enriched_count += 1

    logger.info(
        "search-lab enrichment: %d/%d hits have stored metadata, %d enriched",
        len(stored_rows),
        len(hits),
        enriched_count,
    )
    return hits


def _normalize_meili_hit(hit: dict) -> dict:
    """Transform a raw Meilisearch hit into a frontend-friendly dict.

    Raw Meilisearch hits use internal field names (e.g. ``url`` is just a
    UUID, stats use ``*AllTime`` suffixes).  This function:

    * Builds ``thumbnail_url`` and full ``url`` from the UUID.
    * Passes through the BlurHash ``hash`` for progressive loading.
    * Normalises stats keys to match what the Search Lab frontend expects.
    """
    out = dict(hit)

    # ── Sanitise tagNames ──
    # Meilisearch sometimes returns ``tagNames: [None, None, ...]`` — a
    # non-empty list filled entirely with nulls.  Strip those nulls here so
    # that all downstream code (enrichment, lazy-fetch, frontend) sees an
    # accurate picture of whether tags are actually present.
    raw_tags = out.get("tagNames")
    if isinstance(raw_tags, list):
        out["tagNames"] = [t for t in raw_tags if t is not None]
    elif raw_tags is not None:
        out["tagNames"] = []

    # ── Image URLs ──
    # Three tiers: thumbnail (tiles), mid-res (details/fullscreen), original.
    # When the original is smaller than the mid-res tier, mid-res falls back
    # to original to avoid CDN upscaling artifacts.
    uuid = hit.get("url", "")
    orig_w = hit.get("width")

    # Video content uses B2-backed URLs — the image CDN serves raw MP4.
    if _is_video_hit(hit):
        video_url = _build_video_url(uuid)
        if video_url:
            out["is_video"] = True
            out["video_url"] = video_url
            out["thumbnail_url"] = video_url
            out["mid_res_url"] = video_url
            out["url"] = video_url
    else:
        thumb, mid, full = _build_cdn_urls(
            uuid, orig_width=orig_w if isinstance(orig_w, int) else None
        )
        if thumb:
            out["thumbnail_url"] = thumb
            out["mid_res_url"] = mid
            out["url"] = full

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
    orig_w = meta.get("width") or basic_info.get("width")

    # ── Image / video URLs ──
    # Video content uses B2-backed URLs — the image CDN serves raw MP4.
    is_video = _is_video_hit(basic_info)
    if is_video:
        video_url = _build_video_url(uuid) or uuid
        thumbnail_url = mid_res_url = full_url = video_url
    else:
        thumbnail_url, mid_res_url, full_url = _build_cdn_urls(
            uuid, orig_width=orig_w if isinstance(orig_w, int) else None
        )
        # Fall back to the raw uuid if CDN URL construction failed
        if not thumbnail_url:
            thumbnail_url = mid_res_url = full_url = uuid

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
        "type": basic_info.get("type", "image"),
        "is_video": is_video,
        "video_url": video_url if is_video else None,
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
        # Even on cache hit, merge stored tags / generation data from the
        # DB — the user may have refreshed metadata since the cache was
        # populated, and the cached hits may have null tagNames.
        cached_hits = cached.get("hits", [])
        if cached_hits:
            ids = [h.get("id") for h in cached_hits if isinstance(h.get("id"), int)]
            logger.info(
                "search-lab CACHE HIT: %d hits, ids sample=%s",
                len(cached_hits),
                ids[:5],
            )
            cached["hits"] = _enrich_hits_from_db(db, cached_hits)
            _maybe_lazy_fetch_missing_metadata(cached_hits)
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

    # Merge stored tags / generation data from images we've previously
    # refreshed or rated, so the user doesn't lose manually-fetched tags
    # on page reload (Meilisearch returns null tagNames for many images).
    normalized_hits = _enrich_hits_from_db(db, normalized_hits)

    # Lazily fetch missing tags / generation data for hits that still lack
    # them after the DB merge.  This runs in a background thread so the
    # search response is not delayed; results land in the DB cache and are
    # available on the next search or page refresh.
    _maybe_lazy_fetch_missing_metadata(normalized_hits)

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
def civitai_search_single_image(image_id: int, db: Session = Depends(get_db)):
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
    hit = _build_hit_from_trpc(basic_info, generation_data, tag_records)

    # ── Persist fetched metadata so it survives page refreshes ──
    try:
        _persist_search_image(
            db,
            civitai_image_id=image_id,
            post_id=hit.get("postId"),
            artist_id=(hit.get("user") or {}).get("id") if isinstance(hit.get("user"), dict) else None,
            artist_name=hit.get("username"),
            blurhash=hit.get("blurhash"),
            uuid=hit.get("url"),
            image_url=hit.get("url"),
            tags=hit.get("tagNames") or None,
            generation_prompt=hit.get("prompt") or None,
            generation_models=hit.get("resources") or None,
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("Failed to persist search image %s", image_id, exc_info=True)

    return {"hit": hit}


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


def _persist_search_image(db: Session, civitai_image_id: int, **fields) -> None:
    """Insert or update a CivitaiSearchImage row from keyword fields.

    Unlike ``_upsert_search_image`` (which takes a ``CivitaiImageRatingRequest``),
    this accepts individual keyword arguments so it can be used from the
    single-image reload endpoint which works with a normalised hit dict.

    Only updates fields that are explicitly provided (not None) — existing
    values are preserved otherwise.
    """
    img = (
        db.query(CivitaiSearchImage)
        .filter(CivitaiSearchImage.civitai_image_id == civitai_image_id)
        .first()
    )
    if img is None:
        img = CivitaiSearchImage(civitai_image_id=civitai_image_id)
        db.add(img)
        db.flush()

    if fields.get("post_id") is not None:
        img.post_id = fields["post_id"]
    if fields.get("artist_id") is not None:
        img.artist_id = fields["artist_id"]
    if fields.get("artist_name") is not None:
        img.artist_name = fields["artist_name"]
    if fields.get("blurhash") is not None:
        img.blurhash = fields["blurhash"]
    if fields.get("uuid") is not None:
        img.uuid = fields["uuid"]
    if fields.get("image_url") is not None:
        img.image_url = fields["image_url"]
    if fields.get("tags") is not None:
        img.tags = fields["tags"]
    if fields.get("generation_prompt") is not None:
        img.generation_prompt = fields["generation_prompt"]
    if fields.get("generation_models") is not None:
        img.generation_models = fields["generation_models"]
    if fields.get("reactions") is not None:
        img.reactions = fields["reactions"]
    if fields.get("likes") is not None:
        img.likes = fields["likes"]

    db.flush()


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


@router.get("/debug/{image_id}", response_model=dict)
def civitai_search_debug_image(image_id: int, db: Session = Depends(get_db)):
    """Debug endpoint showing what metadata is stored in the DB for an image.

    Useful for verifying that tag / generation data persisted correctly
    after a manual refresh or rating.
    """
    img = (
        db.query(CivitaiSearchImage)
        .filter(CivitaiSearchImage.civitai_image_id == image_id)
        .first()
    )
    if img is None:
        return {"image_id": image_id, "stored": False}

    return {
        "image_id": image_id,
        "stored": True,
        "tags": list(img.tags) if img.tags else None,
        "generation_prompt": img.generation_prompt,
        "generation_models": img.generation_models,
        "post_id": img.post_id,
        "artist_name": img.artist_name,
        "blurhash": img.blurhash,
        "image_url": img.image_url,
    }


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


# ---------------------------------------------------------------------------
# Review mode: browse previously-rated images
# ---------------------------------------------------------------------------


def _build_hit_from_search_image(img: CivitaiSearchImage) -> dict[str, Any]:
    """Build a normalised hit dict (same shape as ``_normalize_meili_hit``)
    from a stored :class:`CivitaiSearchImage` row.

    This lets review-mode results reuse the exact same frontend rendering
    path (tiles, details pane, fullscreen) as live search results without
    any client-side branching.
    """
    uuid = img.uuid or ""
    is_video = (img.file_name or "").lower().endswith((".mp4", ".webm", ".mov"))

    if is_video:
        video_url = _build_video_url(uuid) or img.image_url or uuid
        thumbnail_url = mid_res_url = full_url = video_url
    else:
        thumbnail_url, mid_res_url, full_url = _build_cdn_urls(uuid)
        # Fall back to the stored image_url if CDN construction failed.
        if not thumbnail_url:
            thumbnail_url = mid_res_url = full_url = img.image_url or uuid

    tags = img.tags if isinstance(img.tags, list) else []
    # Stored tags may be raw strings or dicts with a "name" key — normalise
    # to a flat list of names for the ``tagNames`` field the frontend expects.
    tag_names: list[Any] = []
    for t in tags:
        if isinstance(t, str):
            tag_names.append(t)
        elif isinstance(t, dict) and t.get("name"):
            tag_names.append(t["name"])

    hit: dict[str, Any] = {
        "id": img.civitai_image_id,
        "url": full_url,
        "thumbnail_url": thumbnail_url,
        "mid_res_url": mid_res_url,
        "name": img.file_name or "",
        "is_video": is_video,
        "video_url": video_url if is_video else None,
        "postId": img.post_id,
        "tagNames": tag_names,
        "prompt": img.generation_prompt or "",
        "resources": img.generation_models if isinstance(img.generation_models, list) else None,
    }

    if img.blurhash:
        hit["hash"] = img.blurhash
        hit["blurhash"] = img.blurhash

    if img.artist_name:
        hit["user"] = {"username": img.artist_name}
        hit["username"] = img.artist_name

    if img.reactions is not None or img.likes is not None:
        stats: dict[str, Any] = {}
        if img.reactions is not None:
            stats["reactionCount"] = img.reactions
        if img.likes is not None:
            stats["collectedCount"] = img.likes
        hit["stats"] = stats

    return hit


# Sortable columns for review mode (maps the API ``sort`` value to an
# order-by expression).  Values are chosen to be human-friendly rather than
# mirroring the raw column names so the frontend dropdowns stay simple.
_RATED_SORT_MAP: dict[str, tuple[Any, Any]] = {
    # rated_at comes from the link's created_at (aliased in the subquery)
    "recent": ("rated_at", CivitaiSearchImageLink.created_at),
    "reactions": ("reactions", CivitaiSearchImage.reactions),
    "likes": ("likes", CivitaiSearchImage.likes),
    "artist": ("artist", CivitaiSearchImage.artist_name),
}


@router.get("/rated", response_model=dict)
def get_rated_images(
    rating: str = Query(
        ...,
        description="Rating filter: 'keep', 'skip', 'discard', or 'any'.",
    ),
    q: str | None = Query(
        None,
        description=(
            "Optional text filter — matches against tags, generation prompt, "
            "and artist name.  Multiple terms are AND-ed."
        ),
    ),
    sort: str = Query(
        "recent",
        description="Sort key: 'recent', 'reactions', 'likes', or 'artist'.",
    ),
    order: str = Query(
        "desc",
        description="Sort direction: 'asc' or 'desc'.",
    ),
    artists: str | None = Query(
        None,
        description=(
            "Comma-separated artist names to filter by.  Only images "
            "by at least one of the listed artists are returned."
        ),
    ),
    limit: int = Query(51, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    """Return images the user has previously rated (review mode).

    Queries stored :class:`CivitaiSearchImage` rows joined to their latest
    :class:`CivitaiSearchImageLink` rating, filtered by the requested
    rating value.  Supports optional text search (``q``) across tags /
    prompt / artist, filtering by artist names (``artists``), and sorting
    by recency, reactions, likes, or artist.

    The response shape mirrors the live search endpoint so the frontend
    can render results without mode-specific branching.
    """
    valid_ratings = {"keep", "skip", "discard", "any"}
    if rating not in valid_ratings:
        raise HTTPException(
            status_code=400,
            detail=f"rating must be one of {sorted(valid_ratings)}, got '{rating}'.",
        )
    if sort not in _RATED_SORT_MAP:
        raise HTTPException(
            status_code=400,
            detail=f"sort must be one of {sorted(_RATED_SORT_MAP)}, got '{sort}'.",
        )
    if order not in ("asc", "desc"):
        raise HTTPException(
            status_code=400,
            detail=f"order must be 'asc' or 'desc', got '{order}'.",
        )

    # Rank link rows per image by created_at DESC so we only consider the
    # most recent rating for each image (an image may have been rated in
    # multiple sessions).
    ranked = (
        db.query(
            CivitaiSearchImageLink.image_id.label("img_id"),
            CivitaiSearchImageLink.rating.label("rating"),
            CivitaiSearchImageLink.created_at.label("rated_at"),
            sa_func.row_number()
            .over(
                partition_by=CivitaiSearchImageLink.image_id,
                order_by=CivitaiSearchImageLink.created_at.desc(),
            )
            .label("rn"),
        )
        .filter(CivitaiSearchImageLink.rating.isnot(None))
        .subquery()
    )

    # Apply rating filter on the ranked subquery (latest rating per image).
    rating_filter = ranked.c.rating == rating if rating != "any" else None

    # ── Text search (q): AND-match all whitespace-separated terms ──
    # Each term is matched (case-insensitive) against the tags JSON,
    # generation prompt, and artist name.  All terms must match for an
    # image to be included (AND semantics).

    terms = [t for t in (q or "").split() if t]
    text_filters: list[Any] = []
    for term in terms:
        pattern = f"%{term.lower()}%"
        tag_match = CivitaiSearchImage.tags.cast(sa_Text).like(f"%{term}%")
        prompt_match = sa_func.lower(
            sa_func.coalesce(CivitaiSearchImage.generation_prompt, "")
        ).like(pattern)
        artist_match = sa_func.lower(
            sa_func.coalesce(CivitaiSearchImage.artist_name, "")
        ).like(pattern)
        text_filters.append(sa_or(tag_match, prompt_match, artist_match))

    # ── Artist filter (comma-separated names) ──
    # Exact (case-insensitive) match against artist_name.  When multiple
    # names are supplied they are OR-ed — any matching artist is included.
    selected_artists = [
        a.strip()
        for a in (artists or "").split(",")
        if a.strip()
    ]
    artist_filter: Any | None = None
    if selected_artists:
        lowered = [a.lower() for a in selected_artists]
        artist_filter = sa_func.lower(
            sa_func.coalesce(CivitaiSearchImage.artist_name, "")
        ).in_(lowered)

    # ── Total count ──
    count_q = db.query(ranked.c.img_id).filter(ranked.c.rn == 1)
    if rating_filter is not None:
        count_q = count_q.filter(rating_filter)

    # For text/artist search on the count, we need the image join because
    # the filter columns live on CivitaiSearchImage.
    if text_filters or artist_filter is not None:
        count_q = (
            count_q.join(CivitaiSearchImage, CivitaiSearchImage.id == ranked.c.img_id)
        )
        for tf in text_filters:
            count_q = count_q.filter(tf)
        if artist_filter is not None:
            count_q = count_q.filter(artist_filter)
    total = count_q.distinct().count()

    # ── Page query ──
    page_q = (
        db.query(
            CivitaiSearchImage,
            ranked.c.rating.label("rating"),
            ranked.c.rated_at.label("rated_at"),
        )
        .join(ranked, ranked.c.img_id == CivitaiSearchImage.id)
        .filter(ranked.c.rn == 1)
    )
    if rating_filter is not None:
        page_q = page_q.filter(rating_filter)
    for tf in text_filters:
        page_q = page_q.filter(tf)
    if artist_filter is not None:
        page_q = page_q.filter(artist_filter)

    # Determine sort column.  For "recent" we sort by rated_at (from the
    # subquery); for the others we sort on the image table column.
    _, sort_col = _RATED_SORT_MAP[sort]
    sort_expr = sort_col if sort != "recent" else ranked.c.rated_at
    page_q = page_q.order_by(
        sort_expr.desc() if order == "desc" else sort_expr.asc(),
        # Secondary tiebreaker so pagination is stable
        CivitaiSearchImage.id.asc(),
    )
    page_q = page_q.offset(offset).limit(limit)

    rows = page_q.all()

    hits = []
    ratings_map: dict[str, str] = {}
    for img, r, rated_at in rows:
        hit = _build_hit_from_search_image(img)
        hit["_rating"] = r
        hit["_rated_at"] = rated_at.isoformat() if rated_at else None
        hits.append(hit)
        ratings_map[str(img.civitai_image_id)] = r

    return {
        "hits": hits,
        "total": total,
        "offset": offset,
        "limit": limit,
        "rating": rating,
        "ratings": ratings_map,
        "facets": None,
        "backend": "review",
        "sort": sort,
        "order": order,
        "q": q,
        "artists": selected_artists,
    }


@router.get("/rated/artists", response_model=list[dict])
def get_rated_artist_facets(
    rating: str = Query(
        ...,
        description="Rating filter: 'keep', 'skip', 'discard', or 'any'.",
    ),
    q: str | None = Query(
        None,
        description=(
            "Optional text filter — must match the ``q`` parameter used in "
            "the corresponding /rated request so facet counts stay in sync."
        ),
    ),
    db: Session = Depends(get_db),
):
    """Return per-artist counts of rated images for the artist facets panel.

    Returns a list of ``{artist, count}`` objects sorted by count
    descending.  The rating (and optional text-search ``q``) filters must
    match those used in the corresponding :http:get:`/rated` request so
    the facet counts stay consistent with the displayed gallery.

    Artists with a NULL/empty name are grouped under ``"(Unknown)"``.
    """
    valid_ratings = {"keep", "skip", "discard", "any"}
    if rating not in valid_ratings:
        raise HTTPException(
            status_code=400,
            detail=f"rating must be one of {sorted(valid_ratings)}, got '{rating}'.",
        )

    # Reuse the same ranked-link subquery pattern as /rated so we only
    # count each image's most recent rating.
    ranked = (
        db.query(
            CivitaiSearchImageLink.image_id.label("img_id"),
            CivitaiSearchImageLink.rating.label("rating"),
            sa_func.row_number()
            .over(
                partition_by=CivitaiSearchImageLink.image_id,
                order_by=CivitaiSearchImageLink.created_at.desc(),
            )
            .label("rn"),
        )
        .filter(CivitaiSearchImageLink.rating.isnot(None))
        .subquery()
    )

    rating_filter = ranked.c.rating == rating if rating != "any" else None

    terms = [t for t in (q or "").split() if t]
    text_filters: list[Any] = []
    for term in terms:
        pattern = f"%{term.lower()}%"
        tag_match = CivitaiSearchImage.tags.cast(sa_Text).like(f"%{term}%")
        prompt_match = sa_func.lower(
            sa_func.coalesce(CivitaiSearchImage.generation_prompt, "")
        ).like(pattern)
        artist_match = sa_func.lower(
            sa_func.coalesce(CivitaiSearchImage.artist_name, "")
        ).like(pattern)
        text_filters.append(sa_or(tag_match, prompt_match, artist_match))

    # Use COALESCE to give NULL/empty artists a label so they can be
    # grouped and counted consistently.
    artist_label = sa_func.coalesce(
        sa_func.nullif(CivitaiSearchImage.artist_name, ""),
        "(Unknown)",
    ).label("artist")

    facet_q = (
        db.query(
            artist_label,
            sa_func.count(ranked.c.img_id).label("count"),
        )
        .join(ranked, ranked.c.img_id == CivitaiSearchImage.id)
        .filter(ranked.c.rn == 1)
    )
    if rating_filter is not None:
        facet_q = facet_q.filter(rating_filter)
    for tf in text_filters:
        facet_q = facet_q.filter(tf)
    facet_q = facet_q.group_by(artist_label)
    facet_q = facet_q.order_by(sa_func.count(ranked.c.img_id).desc(), artist_label.asc())

    rows = facet_q.all()
    return [{"artist": name, "count": cnt} for name, cnt in rows]


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
