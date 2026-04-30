# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/image-api.md
# ──────────────────────────────────────────────────────────────────────────────
"""Image management routes.

Extracted from main.py:
  - Lines ~17287–19517: images CRUD, utilities, artists, search, filters, variant groups

Complex handlers (GET /images/, GET /images/keys, GET /images/state,
PATCH /images/{file_hash}, POST /images/{file_hash}/repair, GET /search/suggest,
GET /filters/options) delegate to the corresponding main.py function via lazy
import.  These will be extracted to services/image_service.py in a future phase.

Self-contained handlers (utilities, artists, variant groups, soft-delete, rescan,
video poster/thumbnail) are implemented directly.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, List, Literal, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models import (
    Artist,
    ImageCollectionMembership,
    ImageModel,
    ImageTag,
    ImageVariantGroupMembership,
    VariantGroup,
)
from schemas import (
    ImageUpdateRequest,
    VariantGroupAddMembersRequest,
    VariantGroupCreateRequest,
    VariantGroupUpdateRequest,
)
from utils.cache import (
    _build_json_cache_headers,
    _build_search_cache_key,
    _should_return_json_not_modified,
)

router = APIRouter(tags=["images"])


# ---------------------------------------------------------------------------
# Helpers
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


# ---------------------------------------------------------------------------
# Main image listing/state — delegates to main.py (complex helpers)
# ---------------------------------------------------------------------------


@router.get("/images/", response_model=list[dict])
def read_images(
    request: Request,
    response: Response,
    skip: int = 0,
    limit: int = 10,
    group_variants: bool = Query(default=True),
    sort_by: Literal["first_added", "last_added"] = "first_added",
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
    cursor: Optional[int] = None,
    missing_data: Optional[list[str]] = Query(default=None),
    missing_source: Optional[list[str]] = Query(default=None),
    db: Session = Depends(get_db),
):
    from main import read_images as _impl  # noqa: PLC0415

    return _impl(
        request=request,
        response=response,
        skip=skip,
        limit=limit,
        group_variants=group_variants,
        sort_by=sort_by,
        search=search,
        included=included,
        excluded=excluded,
        hidden=hidden,
        missing=missing,
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
        cursor=cursor,
        missing_data=missing_data,
        missing_source=missing_source,
        db=db,
    )


@router.get("/images/state", response_model=dict)
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
    from main import read_images_state as _impl  # noqa: PLC0415

    return _impl(
        included=included,
        excluded=excluded,
        hidden=hidden,
        missing=missing,
        nsfw_rating=nsfw_rating,
        db=db,
    )


@router.get("/images/keys", response_model=list[str])
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
    from main import read_image_keys as _impl  # noqa: PLC0415

    return _impl(
        request=request,
        response=response,
        group_variants=group_variants,
        search=search,
        included=included,
        excluded=excluded,
        hidden=hidden,
        missing=missing,
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
        missing_data=missing_data,
        missing_source=missing_source,
        db=db,
    )


@router.get("/images/{image_id}", response_model=dict)
def get_image_detail(image_id: int, db: Session = Depends(get_db)):
    from main import get_image_detail as _impl  # noqa: PLC0415

    return _impl(image_id=image_id, db=db)


@router.patch("/images/{file_hash}", response_model=dict)
def update_image(
    file_hash: str, payload: ImageUpdateRequest, db: Session = Depends(get_db)
):
    from main import update_image as _impl  # noqa: PLC0415

    return _impl(file_hash=file_hash, payload=payload, db=db)


# ---------------------------------------------------------------------------
# Unified query endpoint
# ---------------------------------------------------------------------------


@router.post("/query")
def execute_gallery_query(
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
):
    """Execute a unified gallery query (POST /api/query).

    Accepts a structured JSON body with optional filter, summary, images,
    and tags sections.  Each section is computed only when present in the
    request, allowing clients to fetch exactly the data they need.

    Responses are cached in memory for up to 30s (or the value of
    ``ATELIER_SEARCH_CACHE_TTL_SECONDS``).  The cache is keyed on the
    full request body and is automatically invalidated on any DB commit.
    """
    from services.query_model import GalleryQueryRequest  # noqa: PLC0415

    # Validate the raw dict into the Pydantic model.
    req_model = GalleryQueryRequest.model_validate(payload)

    # ── Response cache ───────────────────────────────────────────────────
    import json  # noqa: PLC0415
    from utils.cache import (  # noqa: PLC0415
        _build_json_cache_headers,
        _build_search_cache_key,
        _search_cache_get,
        _search_cache_put,
        _should_return_json_not_modified,
    )

    cache_key = _build_search_cache_key(
        "post_query", payload=payload
    )

    # ETag / 304 support
    cache_headers = _build_json_cache_headers(
        cache_key, max_age_seconds=15, gallery=True
    )
    if _should_return_json_not_modified(request, cache_headers):
        return Response(status_code=304, headers=cache_headers)

    # In-memory response cache
    cached_response = _search_cache_get(cache_key)
    if cached_response is not None:
        return Response(
            content=json.dumps(cached_response),
            media_type="application/json",
            headers=cache_headers,
        )

    # ── Execute query ────────────────────────────────────────────────────
    import main as _main_mod  # noqa: PLC0415

    from services.gallery_query import GalleryQuery  # noqa: PLC0415

    gq = GalleryQuery(
        db=db,
        query_service=_main_mod.image_query_service,
        image_library_path=_main_mod.IMAGE_LIBRARY_PATH,
        image_resources_path=_main_mod.IMAGE_RESOURCES_PATH,
        active_image_filter=_main_mod._active_image_filter,
        apply_image_list_filters=_main_mod._apply_image_list_filters,
        build_display_items_for_image=_main_mod._build_display_items_for_image,
        merge_duplicate_grouped_items=_main_mod._merge_duplicate_grouped_items,
        read_nsfw_ratings_for_image=_main_mod._read_nsfw_ratings_for_image,
        get_video_poster_path=_main_mod.get_video_poster_path,
        get_video_thumbnail_path=_main_mod.get_video_thumbnail_path,
        image_data_from_db=_main_mod.ImageData.from_db_record,
    )
    response = gq.execute(req_model)
    result = response.model_dump(exclude_none=True)

    # Store in cache
    _search_cache_put(cache_key, result)

    return Response(
        content=json.dumps(result),
        media_type="application/json",
        headers=cache_headers,
    )


# ---------------------------------------------------------------------------
# Video poster / thumbnail
# ---------------------------------------------------------------------------


@router.get("/images/{file_hash}/video_poster")
def get_image_video_poster(
    file_hash: str, request: Request, db: Session = Depends(get_db)
):
    from main import get_image_video_poster as _impl  # noqa: PLC0415

    return _impl(file_hash=file_hash, request=request, db=db)


@router.get("/images/{file_hash}/video_thumbnail")
def get_image_video_thumbnail(
    file_hash: str, request: Request, db: Session = Depends(get_db)
):
    from main import get_image_video_thumbnail as _impl  # noqa: PLC0415

    return _impl(file_hash=file_hash, request=request, db=db)


# ---------------------------------------------------------------------------
# Image repair / rescan / delete
# ---------------------------------------------------------------------------


@router.post("/images/{file_hash}/repair", response_model=dict)
@router.post("/images/{file_hash}/repair_png", response_model=dict)
def repair_image_file(file_hash: str, db: Session = Depends(get_db)):
    from main import repair_image_file as _impl  # noqa: PLC0415

    return _impl(file_hash=file_hash, db=db)


@router.post("/images/{file_hash}/rescan", response_model=dict)
def rescan_image_metadata(file_hash: str, db: Session = Depends(get_db)):
    from image_collection import ImageCollection  # noqa: PLC0415

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


@router.delete("/images/{file_hash}/file", response_model=dict)
def delete_image_file(file_hash: str, db: Session = Depends(get_db)):
    """Soft-delete image record while preserving file and sidecar on disk."""
    from sqlalchemy.exc import OperationalError  # noqa: PLC0415

    max_attempts = 4
    image = None
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
        "image_status": image.image_status if image else "deleted",
        "status_reason": image.status_reason if image else "user_deleted",
    }


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------


@router.get("/utilities/image_status_counts", response_model=dict)
def get_image_status_counts(db: Session = Depends(get_db)):
    from main import _active_image_filter  # noqa: PLC0415

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


@router.get("/utilities/inactive_images", response_model=List[dict])
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


@router.get("/utilities/placeholders", response_model=List[dict])
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


@router.get("/utilities/placeholders/summary", response_model=dict)
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


@router.post("/utilities/images/{file_hash}/restore", response_model=dict)
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


@router.post("/utilities/purge_deleted_files", response_model=dict)
def purge_deleted_files(db: Session = Depends(get_db)):
    """Permanently remove deleted records and their on-disk files/sidecars."""
    import atelierai.config as app_config  # noqa: PLC0415
    from models import AnalysisData, DatasetImage  # noqa: PLC0415

    IMAGE_LIBRARY_PATH = getattr(app_config, "IMAGE_LIBRARY_PATH", "image_library")
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


# ---------------------------------------------------------------------------
# Artists
# ---------------------------------------------------------------------------


@router.get("/artists/", response_model=List[dict])
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


# ---------------------------------------------------------------------------
# Search + filter options (delegate — large complex implementations)
# ---------------------------------------------------------------------------


@router.get("/search/suggest", response_model=dict)
def search_suggest(
    q: str = Query(default="", min_length=1, max_length=200),
    limit: int = Query(default=15, ge=1, le=50),
    # Unified filter params (preferred).
    included: Optional[list[str]] = Query(default=None),
    excluded: Optional[list[str]] = Query(default=None),
    hidden: Optional[list[str]] = Query(default=None),
    missing: Optional[list[str]] = Query(default=None),
    # Legacy filter params (deprecated).
    nsfw_rating: Optional[list[str]] = Query(default=None),
    generation_software: Optional[list[str]] = Query(default=None),
    source_site: Optional[list[str]] = Query(default=None),
    mimetype: Optional[list[str]] = Query(default=None),
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
    missing_data: Optional[list[str]] = Query(default=None),
    missing_source: Optional[list[str]] = Query(default=None),
    db: Session = Depends(get_db),
):
    from main import search_suggest as _impl  # noqa: PLC0415

    return _impl(
        q=q,
        limit=limit,
        included=included,
        excluded=excluded,
        hidden=hidden,
        missing=missing,
        nsfw_rating=nsfw_rating,
        generation_software=generation_software,
        source_site=source_site,
        mimetype=mimetype,
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
        missing_data=missing_data,
        missing_source=missing_source,
        db=db,
    )


@router.get("/filters/options", response_model=dict)
def get_filter_options(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    from main import get_filter_options as _impl  # noqa: PLC0415

    return _impl(request=request, response=response, db=db)


# ---------------------------------------------------------------------------
# Variant groups
# ---------------------------------------------------------------------------


@router.get("/variant-groups/", response_model=List[dict])
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


@router.get("/variant-groups/{group_id}", response_model=dict)
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


@router.post("/variant-groups/", response_model=dict, status_code=201)
def create_variant_group(
    payload: VariantGroupCreateRequest, db: Session = Depends(get_db)
):
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


@router.patch("/variant-groups/{group_id}", response_model=dict)
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


@router.delete("/variant-groups/{group_id}", response_model=dict)
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


@router.post("/variant-groups/{group_id}/members", response_model=dict)
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

        new_membership = ImageVariantGroupMembership(
            image_id=image_id,
            group_id=group_id,
            role_in_group=payload.role_in_group or "member",
            sort_index=max_sort + idx + 1,
            source="manual",
        )
        db.add(new_membership)
        added += 1

    db.commit()
    return {
        "message": f"Added {added} image(s) to variant group.",
        "group_id": group_id,
        "added": added,
    }


@router.delete("/variant-groups/{group_id}/members/{image_id}", response_model=dict)
def remove_member_from_variant_group(
    group_id: int, image_id: int, db: Session = Depends(get_db)
):
    """Remove an image from a variant group. Deletes the group if last member."""
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
