# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/civitai-integration.md
# 📄 docs: app/docs/memories/image-api.md
# ──────────────────────────────────────────────────────────────────────────────
"""Collection management routes.

Extracted from main.py:
  - Lines ~19518–19799: collection CRUD + image membership
  - Lines ~20135–20157: POST /collections/sync/civitai
  - Lines ~22629–23384: sync-lab (SSE) routes
  - Lines ~19821–19982: scan_library / upload_images / import_civitai

Helper functions co-located here:
  _isoformat_or_none, _normalize_collection_name, _serialize_collection,
  _ensure_image_in_collection

TODO: Move sync-lab helpers (_resolve_civitai_image_target, _download_*,
      _ingest_*, _archive_*, etc.) from main.py into services/civitai_service.py
      and remove the lazy main-module imports below.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi import status as http_status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, joinedload

from core.lifespan import task_manager
from database import get_db
from models import CollectionModel, ImageCollectionMembership, ImageModel
from schemas import (
    CivitaiCollectionSyncRequest,
    CivitaiImportRequest,
    CollectionBulkMembershipRequest,
    CollectionCreateRequest,
    CollectionRenameRequest,
    SyncLabAnalyzeRequest,
    SyncSessionCreateRequest,
    SyncSessionStepUpdateRequest,
)
from utils.cache import (
    _build_json_cache_headers,
    _build_search_cache_key,
    _should_return_json_not_modified,
)

router = APIRouter(tags=["collections"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _isoformat_or_none(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


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
        "civitai_last_synced_at": _isoformat_or_none(collection.civitai_last_synced_at),
        "civitai_last_full_scan_at": _isoformat_or_none(
            collection.civitai_last_full_scan_at
        ),
        "civitai_last_full_item_count": collection.civitai_last_full_item_count,
    }


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


# ---------------------------------------------------------------------------
# Collection CRUD
# ---------------------------------------------------------------------------


@router.get("/collections/", response_model=List[dict])
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


@router.post("/collections/", response_model=dict)
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


@router.patch("/collections/{collection_id}", response_model=dict)
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


@router.delete("/collections/{collection_id}", response_model=dict)
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


@router.get("/images/{file_hash}/collections", response_model=List[dict])
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


@router.post("/images/{file_hash}/collections/{collection_id}", response_model=dict)
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


@router.post("/collections/{collection_id}/images", response_model=dict)
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


@router.delete("/images/{file_hash}/collections/{collection_id}", response_model=dict)
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


@router.api_route(
    "/collections/{collection_id}/images", methods=["DELETE"], response_model=dict
)
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


# ---------------------------------------------------------------------------
# CivitAI collection sync (belongs here: modifies collection membership)
# ---------------------------------------------------------------------------


@router.post(
    "/collections/sync/civitai",
    response_model=dict,
    status_code=http_status.HTTP_202_ACCEPTED,
)
def sync_civitai_collections(
    payload: CivitaiCollectionSyncRequest, db: Session = Depends(get_db)
):
    if payload.limit is not None and payload.limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be greater than 0.")

    # _run_civitai_collection_sync_job is still defined in main.py; imported
    # lazily until it is moved to services/civitai_service.py.
    from main import _run_civitai_collection_sync_job  # noqa: PLC0415

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


# ---------------------------------------------------------------------------
# Licenses
# ---------------------------------------------------------------------------


@router.get("/licenses/", response_model=List[dict])
def get_licenses(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    """Returns a list of all available licenses."""
    from models import License  # noqa: PLC0415 – avoid circular at module level

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


# ---------------------------------------------------------------------------
# Library ingestion (scan / upload / CivitAI import)
# ---------------------------------------------------------------------------


@router.post("/scan_library/")
def scan_library(db: Session = Depends(get_db)):
    """Scans the library, imports new files, and removes duplicates/orphaned records."""
    from image_collection import ImageCollection  # noqa: PLC0415

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


@router.post("/upload_images/")
async def upload_images(
    files: List[UploadFile] = File(...),
    artist_name: Optional[str] = Form(None),
    source_url: Optional[str] = Form(None),
    license_id: Optional[int] = Form(None),
    db: Session = Depends(get_db),
):
    """Uploads one or more images, saves them to the library, and adds them to the database."""
    from main import IMAGE_LIBRARY_PATH, _commit_with_lock_retry, _get_runtime_warnings  # noqa: PLC0415
    from image_collection import ImageCollection  # noqa: PLC0415

    images_added = 0
    images_skipped = 0
    json_files_created = 0
    errors = []

    for file in files:
        temp_path = None
        try:
            contents = await file.read()
            temp_path = Path(IMAGE_LIBRARY_PATH) / f"temp_{file.filename}"
            temp_path.write_bytes(contents)

            collection = ImageCollection(db)
            ingest_result = collection.ingest_uploaded_file(
                uploaded_file_path=temp_path,
                original_filename=file.filename or temp_path.name,
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
            if temp_path and temp_path.exists():
                temp_path.unlink(missing_ok=True)

    runtime_warnings = _get_runtime_warnings()
    return {
        "message": "Upload complete.",
        "images_added": images_added,
        "images_skipped": images_skipped,
        "json_files_created": json_files_created,
        "errors": errors,
        "warnings": runtime_warnings,
    }


@router.post("/import_civitai/", status_code=http_status.HTTP_202_ACCEPTED)
def import_civitai_images(payload: CivitaiImportRequest, db: Session = Depends(get_db)):
    """Import CivitAI images by image URL/ID or collection URL/ID."""
    from main import (  # noqa: PLC0415
        _detect_civitai_url_type,
        _parse_civitai_collection_id,
        _parse_civitai_image_id,
        _parse_civitai_post_id,
        _run_civitai_collection_import_job,
        _run_civitai_image_import_job,
        _run_civitai_post_import_job,
    )

    value = (payload.value or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="Import value is required.")

    if payload.limit is not None and payload.limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be greater than 0.")

    detected_type = None
    detected_id = None
    type_mismatch_warning = None

    try:
        detected_type, detected_id = _detect_civitai_url_type(value)
        if detected_type != payload.import_type:
            type_mismatch_warning = (
                f"URL contains a {detected_type}, not a {payload.import_type}. "
                f"Importing as {detected_type}."
            )
    except HTTPException:
        pass

    if detected_type:
        import_type = detected_type
        import_id = detected_id
    else:
        import_type = payload.import_type
        if import_type == "image":
            import_id = _parse_civitai_image_id(value)
        elif import_type == "post":
            import_id = _parse_civitai_post_id(value)
        else:
            import_id = _parse_civitai_collection_id(value)

    response_data: dict[str, Any] = {}
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
    elif import_type == "post":
        task = task_manager.create_task(
            kind="civitai-post-import",
            title=f"Import CivitAI post {import_id}",
            metadata={
                "import_type": "post",
                "post_id": import_id,
                "requested_value": value,
                "limit": payload.limit,
            },
            runner=lambda context: _run_civitai_post_import_job(
                context,
                post_id=import_id,
                limit=payload.limit,
            ),
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
# Sync-lab routes (SSE streaming)
# ---------------------------------------------------------------------------
# These routes have heavy dependencies on civitai download/ingest helpers
# still located in main.py.  They are extracted verbatim here; the lazy
# imports from `main` will be replaced once civitai_service.py is populated.


# ---------------------------------------------------------------------------
# Sync Session CRUD routes
# ---------------------------------------------------------------------------

@router.post("/sync-lab/sessions", response_model=dict)
def create_sync_session(payload: "SyncSessionCreateRequest", db: Session = Depends(get_db)):
    """Create a new resumable sync session."""
    from main import sync_session_create as _impl  # noqa: PLC0415
    return _impl(payload, db)


@router.get("/sync-lab/sessions", response_model=dict)
def list_sync_sessions(
    include_complete: bool = Query(False, description="Include completed sessions"),
    db: Session = Depends(get_db),
):
    """List sync sessions."""
    from main import sync_session_list as _impl  # noqa: PLC0415
    return _impl(db, include_complete=include_complete)


@router.get("/sync-lab/sessions/{session_id}", response_model=dict)
def get_sync_session(session_id: str, db: Session = Depends(get_db)):
    """Get a single sync session."""
    from main import sync_session_get as _impl  # noqa: PLC0415
    return _impl(session_id, db)


@router.patch("/sync-lab/sessions/{session_id}/step", response_model=dict)
def update_sync_session_step(
    session_id: str, payload: "SyncSessionStepUpdateRequest", db: Session = Depends(get_db)
):
    """Update a step's status and data for a sync session."""
    from main import sync_session_update_step as _impl  # noqa: PLC0415
    return _impl(session_id, payload, db)


@router.delete("/sync-lab/sessions/{session_id}", response_model=dict)
def delete_sync_session(session_id: str, db: Session = Depends(get_db)):
    """Delete a sync session."""
    from main import sync_session_delete as _impl  # noqa: PLC0415
    return _impl(session_id, db)


# ---------------------------------------------------------------------------
# Sync Lab step routes
# ---------------------------------------------------------------------------

@router.get("/sync-lab/collections", response_model=dict)
def sync_lab_list_collections():
    """Step 1: Fetch the authenticated user's CivitAI image collections."""
    import time  # noqa: PLC0415

    from atelierai.civitai.civitai_api import CivitaiAPI  # noqa: PLC0415
    from main import (  # noqa: PLC0415
        _fetch_civitai_user_image_collections,
    )

    t0 = time.monotonic()
    api = CivitaiAPI.get_instance()
    try:
        collections = _fetch_civitai_user_image_collections(api)
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


@router.get("/sync-lab/collection-items/{collection_id}")
def sync_lab_fetch_collection_items(
    collection_id: int,
    limit: int = Query(default=None, ge=1),
    collection_type: str = Query(default="image", pattern="^(image|post)$"),
):
    """Step 3: Fetch all items for a specific CivitAI collection (SSE streaming).

    Supports both image-type collections (via image.getInfinite) and
    post-type collections (via post.getInfinite → image.getInfinite per post).
    """
    import queue  # noqa: PLC0415
    import threading  # noqa: PLC0415

    from atelierai.civitai.civitai import CivitaiPrivateScraper  # noqa: PLC0415
    from atelierai.civitai.civitai_api import CivitaiAPI  # noqa: PLC0415
    from atelierai.civitai.http_client import CivitaiRequestError  # noqa: PLC0415
    from main import (  # noqa: PLC0415
        _archive_civitai_collection_items,
        _classify_civitai_upstream_error,
    )

    collection_type = collection_type.strip().lower()

    def _sse(data: dict) -> str:
        return f"data: {json.dumps(data)}\n\n"

    def event_stream():
        q: queue.Queue = queue.Queue()
        import time  # noqa: PLC0415

        t0 = time.monotonic()

        def on_progress(page: int, page_items: int, total: int):
            q.put(("progress", page, page_items, total))

        def _fetch_post_collection_items(
            api: CivitaiAPI,
            cid: int,
            max_items: int | None,
        ) -> list[dict]:
            """Fetch images from a post-type collection.

            Uses post.getInfinite to get posts, then image.getInfinite
            with postId to get images from each post.
            """
            all_images: list[dict] = []
            posts = api.fetch_collection_posts(collection_id=cid)
            total_posts = len(posts)

            for i, post in enumerate(posts, 1):
                post_id = post.get("id")
                if not post_id:
                    continue

                post_images = api.fetch_post_images(int(post_id))
                for idx, img in enumerate(post_images):
                    if isinstance(img, dict):
                        # Attach post metadata for context
                        img["_post_id"] = int(post_id)
                        img["_post_title"] = post.get("title", "")
                        img["_post_index"] = idx
                        all_images.append(img)

                # Report per-post progress with cumulative image count
                q.put(("progress", {
                    "post_number": i,
                    "total_posts": total_posts,
                    "post_images": len(post_images),
                    "total_images": len(all_images),
                }))

                if max_items is not None and len(all_images) >= max_items:
                    all_images = all_images[:max_items]
                    break

            return all_images

        def worker():
            try:
                if collection_type == "post":
                    api = CivitaiAPI.get_instance()
                    items = _fetch_post_collection_items(api, collection_id, limit)
                else:
                    scraper = CivitaiPrivateScraper(auto_authenticate=True)
                    items = scraper.fetch_collection_items(
                        collection_id=collection_id,
                        limit=limit,
                        progress_callback=on_progress,
                        collection_type=collection_type.capitalize() if collection_type else None,
                    )
                q.put(("result", items))
            except CivitaiRequestError as exc:
                classified = _classify_civitai_upstream_error(exc)
                q.put(("error", classified.status_code, classified.detail))
            except Exception as exc:
                q.put(("error", 503, f"Failed to fetch collection items: {exc}"))

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()

        items = []
        while thread.is_alive() or not q.empty():
            try:
                msg = q.get(timeout=0.2)
            except queue.Empty:
                continue

            kind = msg[0]
            if kind == "progress":
                payload = msg[1]
                # Image-type collections use 3-tuple (page, page_items, total)
                # Post-type collections use dict with post/image details
                if isinstance(payload, dict):
                    yield _sse({
                        "type": "progress",
                        "collection_type": "post",
                        "page": payload["post_number"],
                        "page_items": payload["post_images"],
                        "total": payload["total_posts"],
                        "post_number": payload["post_number"],
                        "total_posts": payload["total_posts"],
                        "post_images": payload["post_images"],
                        "total_images": payload["total_images"],
                    })
                else:
                    page, page_items, total = msg[1], msg[2], msg[3]
                    yield _sse({
                        "type": "progress",
                        "collection_type": "image",
                        "page": page,
                        "page_items": page_items,
                        "total": total,
                    })
            elif kind == "error":
                _, status_code, detail = msg
                yield _sse({"type": "error", "status_code": status_code, "detail": detail})
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

        normalized = [item for item in items if isinstance(item, dict)]
        _archive_civitai_collection_items(normalized)


        seen: set[int] = set()
        image_ids: list[int] = []
        item_index: dict[int, dict] = {}
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


@router.post("/sync-lab/analyze-local", response_model=dict)
def sync_lab_analyze_local(
    payload: SyncLabAnalyzeRequest,
    session_id: Optional[str] = Query(None, description="Sync session ID for resumability"),
):
    """Step 4: Check which CivitAI image IDs exist in the local DB."""
    # Re-dispatch to main implementation to avoid duplicating complex logic.
    from main import sync_lab_analyze_local as _impl  # noqa: PLC0415

    return _impl(payload, session_id=session_id)


@router.get("/sync-lab/fetch-metadata")
def sync_lab_fetch_metadata(
    image_ids: str = Query(..., description="Comma-separated CivitAI image IDs"),
    session_id: Optional[str] = Query(None, description="Sync session ID for resumability"),
):
    """Step 5: Fetch CivitAI API metadata for specified image IDs (SSE streaming)."""
    from main import sync_lab_fetch_metadata as _impl  # noqa: PLC0415

    return _impl(image_ids=image_ids, session_id=session_id)


@router.get("/sync-lab/download")
def sync_lab_download(
    image_ids: str = Query(..., description="Comma-separated CivitAI image IDs"),
    session_id: Optional[str] = Query(None, description="Sync session ID for resumability"),
):
    """Step 6: Download images for specified CivitAI image IDs (SSE streaming)."""
    from main import sync_lab_download as _impl  # noqa: PLC0415

    return _impl(image_ids=image_ids, session_id=session_id)


@router.get("/sync-lab/ingest")
def sync_lab_ingest(
    image_ids: str = Query(..., description="Comma-separated CivitAI image IDs"),
    collection_id: Optional[int] = Query(None, description="CivitAI collection ID to attach"),
    session_id: Optional[str] = Query(None, description="Sync session ID for resumability"),
):
    """Step 7: Ingest previously downloaded images into the library (SSE streaming)."""
    from main import sync_lab_ingest as _impl  # noqa: PLC0415

    return _impl(image_ids=image_ids, collection_id=collection_id, session_id=session_id)


@router.get("/sync-lab/collection-status/{collection_id}", response_model=dict)
def sync_lab_collection_status(collection_id: int, db: Session = Depends(get_db)):
    """Get the current local DB state for a CivitAI collection."""
    collection = (
        db.query(CollectionModel)
        .filter(CollectionModel.civitai_collection_id == collection_id)
        .first()
    )
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

    memberships = (
        db.query(ImageCollectionMembership, ImageModel)
        .join(ImageModel, ImageCollectionMembership.image_id == ImageModel.id)
        .filter(ImageCollectionMembership.collection_id == collection.id)
        .all()
    )

    images = []
    for _membership, image in memberships:
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


@router.get("/sync-lab")
def sync_lab_page():
    """Serve the Sync Lab page."""
    from fastapi.responses import FileResponse  # noqa: PLC0415

    return FileResponse("frontend/sync-lab.html")
