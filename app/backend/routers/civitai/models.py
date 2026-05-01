# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/model-reference.md
# ──────────────────────────────────────────────────────────────────────────────
"""CivitAI Model Catalog API routes.

Provides endpoints for:
- Listing and filtering models
- Retrieving model/version/file details
- Looking up models by hash
- Triggering model syncs

All endpoints are under /civitai/models prefix when registered in main router.
"""

from __future__ import annotations

import json
import queue
import threading
from datetime import datetime
from typing import Any, Optional

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Body
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session, joinedload

from database import SessionLocal, get_db, engine
from models import (
    CivitaiModel,
    CivitaiModelVersion,
    CivitaiModelVersionFile,
    CivitaiModelFileHash,
    CivitaiModelRank,
    CivitaiModelTag,
    ImageModel,
    ModelObservation,
)
from atelierai.civitai.civitai_models import (
    sync_user_models,
    upsert_model_detail,
    rescan_gallery_models,
)
from services.model_reference_service import ModelReferenceService

router = APIRouter(prefix="/civitai/models", tags=["civitai-models"])


def _normalize_model_type(value: Optional[str]) -> str:
    text = str(value or "").strip().lower()
    if text in {"checkpoint", "checkpoints", "model", "ckpt"}:
        return "Checkpoint"
    if text in {"lora", "loras", "lycoris", "locon", "loha"}:
        return "LORA"
    raise HTTPException(status_code=400, detail="model_type must be one of checkpoint|lora")


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def _isoformat_or_none(value: Any) -> Optional[str]:
    """Convert datetime to ISO format string or return None."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _serialize_file_hash(hash_rec: CivitaiModelFileHash) -> dict:
    """Serialize a file hash record."""
    return {
        "type": hash_rec.hash_type,
        "value": hash_rec.hash_value,
    }


def _serialize_file(file: CivitaiModelVersionFile) -> dict:
    """Serialize a model version file record."""
    return {
        "id": file.civitai_file_id,
        "name": file.name,
        "type": file.type,
        "size_kb": file.size_kb,
        "download_url": file.download_url,
        "visibility": file.visibility,
        "format": file.format,
        "fp": file.fp,
        "size_label": file.size_label,
        "hashes": [_serialize_file_hash(h) for h in file.hashes],
        "scanned_at": _isoformat_or_none(file.scanned_at),
    }


def _serialize_version_rank(rank: Optional[CivitaiModelRank]) -> Optional[dict]:
    """Serialize version rank data."""
    if not rank:
        return None
    return {
        "download_count": rank.download_count,
        "thumbs_up_count": rank.thumbs_up_count,
        "thumbs_down_count": rank.thumbs_down_count,
        "generation_count": rank.generation_count,
        "earned_amount": rank.earned_amount,
        "scraped_at": _isoformat_or_none(rank.scraped_at),
    }


def _serialize_model_rank(rank: Optional[CivitaiModelRank]) -> Optional[dict]:
    """Serialize model rank data."""
    if not rank:
        return None
    return {
        "download_count": rank.download_count,
        "thumbs_up_count": rank.thumbs_up_count,
        "thumbs_down_count": rank.thumbs_down_count,
        "comment_count": rank.comment_count,
        "collected_count": rank.collected_count,
        "tipped_amount_count": rank.tipped_amount_count,
        "scraped_at": _isoformat_or_none(rank.scraped_at),
    }


def _serialize_tag(tag: CivitaiModelTag) -> dict:
    """Serialize a model tag."""
    return {
        "id": tag.civitai_tag_id,
        "name": tag.tag_name,
        "is_category": tag.is_category,
    }


def _serialize_version(version: CivitaiModelVersion, include_files: bool = True) -> dict:
    """Serialize a model version."""
    data = {
        "id": version.civitai_version_id,
        "model_id": version.civitai_model_id,
        "name": version.name,
        "description": version.description,
        "base_model": version.base_model,
        "base_model_type": version.base_model_type,
        "nsfw_level": version.nsfw_level,
        "status": version.status,
        "availability": version.availability,
        "clip_skip": version.clip_skip,
        "steps": version.steps,
        "epochs": version.epochs,
        "trained_words": version.trained_words,
        "training_status": version.training_status,
        "require_auth": version.require_auth,
        "usage_control": version.usage_control,
        "published_at": _isoformat_or_none(version.published_at),
        "created_at": _isoformat_or_none(version.created_at),
        "updated_at": _isoformat_or_none(version.updated_at),
        "scraped_at": _isoformat_or_none(version.scraped_at),
        "rank": _serialize_version_rank(version.rank),
    }

    if include_files:
        data["files"] = [_serialize_file(f) for f in version.files]

    return data


def _serialize_model(
    model: CivitaiModel,
    include_versions: bool = False,
) -> dict:
    """Serialize a model record."""
    data = {
        "id": model.civitai_model_id,
        "name": model.name,
        "type": model.type,
        "description": model.description,
        "checkpoint_type": model.checkpoint_type,
        "nsfw": model.nsfw,
        "nsfw_level": model.nsfw_level,
        "sfw_only": model.sfw_only,
        "poi": model.poi,
        "minor": model.minor,
        "status": model.status,
        "availability": model.availability,
        "locked": model.locked,
        "allow_no_credit": model.allow_no_credit,
        "allow_commercial_use": model.allow_commercial_use,
        "allow_derivatives": model.allow_derivatives,
        "allow_different_license": model.allow_different_license,
        "author": {
            "id": model.civitai_user_id,
            "username": model.civitai_username,
            "deleted": model.civitai_user_deleted,
        },
        "timestamps": {
            "created_at": _isoformat_or_none(model.created_at),
            "published_at": _isoformat_or_none(model.published_at),
            "updated_at": _isoformat_or_none(model.updated_at),
            "last_version_at": _isoformat_or_none(model.last_version_at),
            "early_access_deadline": _isoformat_or_none(model.early_access_deadline),
            "scraped_at": _isoformat_or_none(model.scraped_at),
            "list_scraped_at": _isoformat_or_none(model.list_scraped_at),
        },
        "latest_version_id": model.latest_version_id,
        "version_count": len(model.versions) if model.versions else 0,
        "rank": _serialize_model_rank(model.rank),
        "tags": [_serialize_tag(t) for t in model.tags],
    }

    if include_versions:
        data["versions"] = [_serialize_version(v, include_files=True) for v in model.versions]

    return data


def _to_detail_payload(model: CivitaiModel) -> dict[str, Any]:
    """Convert ORM model row into a model.getById-like payload for import/export."""
    model_rank = model.rank
    return {
        "id": model.civitai_model_id,
        "name": model.name,
        "description": model.description,
        "type": model.type,
        "checkpointType": model.checkpoint_type,
        "nsfw": model.nsfw,
        "nsfwLevel": model.nsfw_level,
        "sfwOnly": model.sfw_only,
        "poi": model.poi,
        "minor": model.minor,
        "status": model.status,
        "availability": model.availability,
        "uploadType": model.upload_type,
        "locked": model.locked,
        "allowNoCredit": model.allow_no_credit,
        "allowCommercialUse": model.allow_commercial_use,
        "allowDerivatives": model.allow_derivatives,
        "allowDifferentLicense": model.allow_different_license,
        "createdAt": _isoformat_or_none(model.created_at),
        "publishedAt": _isoformat_or_none(model.published_at),
        "updatedAt": _isoformat_or_none(model.updated_at),
        "lastVersionAt": _isoformat_or_none(model.last_version_at),
        "earlyAccessDeadline": _isoformat_or_none(model.early_access_deadline),
        "user": {
            "id": model.civitai_user_id,
            "username": model.civitai_username,
            "deleted": model.civitai_user_deleted,
        },
        "rank": {
            "downloadCountAllTime": model_rank.download_count if model_rank else 0,
            "thumbsUpCountAllTime": model_rank.thumbs_up_count if model_rank else 0,
            "thumbsDownCountAllTime": model_rank.thumbs_down_count if model_rank else 0,
            "commentCountAllTime": model_rank.comment_count if model_rank else 0,
            "collectedCountAllTime": model_rank.collected_count if model_rank else 0,
            "tippedAmountCountAllTime": model_rank.tipped_amount_count if model_rank else 0,
        },
        "tagsOnModels": [
            {
                "id": tag.civitai_tag_id,
                "name": tag.tag_name,
                "isCategory": tag.is_category,
            }
            for tag in model.tags
        ],
        "modelVersions": [
            {
                "id": version.civitai_version_id,
                "modelId": version.civitai_model_id,
                "name": version.name,
                "description": version.description,
                "baseModel": version.base_model,
                "baseModelType": version.base_model_type,
                "nsfwLevel": version.nsfw_level,
                "status": version.status,
                "availability": version.availability,
                "uploadType": version.upload_type,
                "clipSkip": version.clip_skip,
                "steps": version.steps,
                "epochs": version.epochs,
                "trainedWords": version.trained_words,
                "trainingStatus": version.training_status,
                "requireAuth": version.require_auth,
                "usageControl": version.usage_control,
                "earlyAccessConfig": version.early_access_config,
                "publishedAt": _isoformat_or_none(version.published_at),
                "createdAt": _isoformat_or_none(version.created_at),
                "updatedAt": _isoformat_or_none(version.updated_at),
                "rank": {
                    "generationCountAllTime": version.rank.generation_count if version.rank else 0,
                    "downloadCountAllTime": version.rank.download_count if version.rank else 0,
                    "thumbsUpCountAllTime": version.rank.thumbs_up_count if version.rank else 0,
                    "thumbsDownCountAllTime": version.rank.thumbs_down_count if version.rank else 0,
                    "earnedAmountAllTime": version.rank.earned_amount if version.rank else 0,
                },
                "files": [
                    {
                        "id": file.civitai_file_id,
                        "name": file.name,
                        "type": file.type,
                        "sizeKB": file.size_kb,
                        "downloadUrl": file.download_url,
                        "visibility": file.visibility,
                        "pickleScanResult": file.pickle_scan_result,
                        "virusScanResult": file.virus_scan_result,
                        "scannedAt": _isoformat_or_none(file.scanned_at),
                        "metadata": {
                            "format": file.format,
                            "fp": file.fp,
                            "size": file.size_label,
                        },
                        "hashes": [
                            {
                                "type": hash_row.hash_type,
                                "hash": hash_row.hash_value,
                            }
                            for hash_row in file.hashes
                        ],
                    }
                    for file in version.files
                ],
            }
            for version in model.versions
        ],
    }


# ---------------------------------------------------------------------------
# List endpoint with filters
# ---------------------------------------------------------------------------


@router.get("/")
def list_models(
    model_type: Optional[str] = Query(None, description="Filter by model type (Checkpoint, LORA, etc.)"),
    base_model: Optional[str] = Query(None, description="Filter by base model"),
    username: Optional[str] = Query(None, description="Filter by author username"),
    search: Optional[str] = Query(None, description="Search in model names"),
    expand_versions: bool = Query(False, description="Include full version details for each model"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    db: Session = Depends(get_db),
) -> dict:
    """List models with optional filters.

    Query parameters:
    - model_type: Filter by type (Checkpoint, LORA, etc.)
    - base_model: Filter by base model (Illustrious, SDXL, etc.)
    - username: Filter by author username
    - search: Search in model names (case-insensitive)
    - skip: Number of results to skip (default: 0)
    - limit: Number of results to return (default: 50, max: 500)

    Returns:
        Dictionary with total count and items array
    """
    query = db.query(CivitaiModel)

    # Apply filters
    if model_type:
        query = query.filter(CivitaiModel.type == model_type)

    if base_model:
        # Filter versions by base_model
        query = query.join(CivitaiModelVersion).filter(
            CivitaiModelVersion.base_model == base_model
        )

    if username:
        query = query.filter(CivitaiModel.civitai_username.ilike(f"%{username}%"))

    if search:
        query = query.filter(CivitaiModel.name.ilike(f"%{search}%"))

    # Get total count
    total = query.count()

    # Apply pagination
    opts = [
        joinedload(CivitaiModel.rank),
        joinedload(CivitaiModel.tags),
    ]
    if expand_versions:
        opts.append(
            joinedload(CivitaiModel.versions)
            .joinedload(CivitaiModelVersion.rank)
        )

    items = query.options(*opts).offset(skip).limit(limit).all()

    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "items": [_serialize_model(m, include_versions=expand_versions) for m in items],
    }


# ---------------------------------------------------------------------------
# Model detail endpoint
# ---------------------------------------------------------------------------


@router.get("/{model_id}")
def get_model_detail(
    model_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """Get full model detail including all versions and files.

    Parameters:
    - model_id: CivitAI model ID

    Returns:
        Model detail dict with versions and files
    """
    model = db.query(CivitaiModel).filter(
        CivitaiModel.civitai_model_id == model_id
    ).options(
        joinedload(CivitaiModel.versions).joinedload(CivitaiModelVersion.files).joinedload(CivitaiModelVersionFile.hashes),
        joinedload(CivitaiModel.rank),
        joinedload(CivitaiModel.tags),
    ).first()

    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    return _serialize_model(model, include_versions=True)


# ---------------------------------------------------------------------------
# Version detail endpoint
# ---------------------------------------------------------------------------


@router.get("/{model_id}/versions/{version_id}")
def get_version_detail(
    model_id: int,
    version_id: int,
    db: Session = Depends(get_db),
) -> dict:
    """Get version detail including all files and hashes.

    Parameters:
    - model_id: CivitAI model ID (for validation)
    - version_id: CivitAI version ID

    Returns:
        Version detail dict with files and hashes
    """
    version = db.query(CivitaiModelVersion).filter(
        CivitaiModelVersion.civitai_version_id == version_id,
        CivitaiModelVersion.civitai_model_id == model_id,
    ).options(
        joinedload(CivitaiModelVersion.files).joinedload(CivitaiModelVersionFile.hashes),
        joinedload(CivitaiModelVersion.rank),
    ).first()

    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    return _serialize_version(version, include_files=True)


# ---------------------------------------------------------------------------
# Hash lookup endpoint
# ---------------------------------------------------------------------------


@router.get("/lookup-by-hash")
def lookup_by_hash(
    sha256: Optional[str] = Query(None, description="SHA256 hash to look up"),
    db: Session = Depends(get_db),
) -> dict:
    """Lookup model/version/file by SHA256 hash.

    Parameters:
    - sha256: SHA256 hash (case-insensitive)

    Returns:
        Dictionary with file, version, and model details, or error if not found
    """
    if not sha256:
        raise HTTPException(status_code=400, detail="sha256 parameter is required")

    # Normalize hash to uppercase
    hash_value = sha256.upper().strip()

    # Look up hash
    hash_rec = db.query(CivitaiModelFileHash).filter(
        CivitaiModelFileHash.hash_type == "SHA256",
        CivitaiModelFileHash.hash_value == hash_value,
    ).first()

    if not hash_rec:
        raise HTTPException(status_code=404, detail="Hash not found")

    # Get file, version, model
    file = db.query(CivitaiModelVersionFile).filter(
        CivitaiModelVersionFile.civitai_file_id == hash_rec.file_id
    ).first()

    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    version = db.query(CivitaiModelVersion).filter(
        CivitaiModelVersion.civitai_version_id == file.civitai_version_id
    ).options(
        joinedload(CivitaiModelVersion.rank),
    ).first()

    if not version:
        raise HTTPException(status_code=404, detail="Version not found")

    model = db.query(CivitaiModel).filter(
        CivitaiModel.civitai_model_id == version.civitai_model_id
    ).options(
        joinedload(CivitaiModel.rank),
        joinedload(CivitaiModel.tags),
    ).first()

    if not model:
        raise HTTPException(status_code=404, detail="Model not found")

    return {
        "hash": {
            "type": hash_rec.hash_type,
            "value": hash_rec.hash_value,
        },
        "file": _serialize_file(file),
        "version": _serialize_version(version, include_files=False),
        "model": _serialize_model(model, include_versions=False),
    }


# ---------------------------------------------------------------------------
# Sync endpoint
# ---------------------------------------------------------------------------


@router.post("/sync")
def sync_models(
    username: str = Query(..., description="CivitAI username to sync"),
    limit: Optional[int] = Query(None, description="Maximum models to fetch"),
    fetch_details: bool = Query(True, description="Fetch full model details"),
    db: Session = Depends(get_db),
) -> dict:
    """Trigger a model sync for a CivitAI user.

    This will paginate through model.getAll and optionally fetch full
    model.getById detail for each model.

    Parameters:
    - username: CivitAI username (required)
    - limit: Maximum number of models to sync (None for all)
    - fetch_details: If true, fetch full detail for each model

    Returns:
        Summary with counts of models synced
    """
    try:
        total_synced, total_details = sync_user_models(
            db,
            username,
            limit=limit,
            fetch_details=fetch_details,
        )
        return {
            "username": username,
            "total_synced": total_synced,
            "total_details_fetched": total_details,
            "completed_at": datetime.utcnow().isoformat(),
        }
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Sync failed: {str(e)}",
        )


@router.get("/maintenance/export")
def export_models(
    model_type: str = Query(..., description="checkpoint or lora"),
    db: Session = Depends(get_db),
) -> dict:
    """Export models for a specific type as a JSON archive payload."""
    normalized = _normalize_model_type(model_type)
    rows = (
        db.query(CivitaiModel)
        .filter(CivitaiModel.type == normalized)
        .options(
            joinedload(CivitaiModel.versions)
            .joinedload(CivitaiModelVersion.files)
            .joinedload(CivitaiModelVersionFile.hashes),
            joinedload(CivitaiModel.versions).joinedload(CivitaiModelVersion.rank),
            joinedload(CivitaiModel.rank),
            joinedload(CivitaiModel.tags),
        )
        .order_by(CivitaiModel.civitai_model_id.asc())
        .all()
    )
    payloads = [_to_detail_payload(row) for row in rows]
    return {
        "model_type": normalized,
        "total": len(payloads),
        "exported_at": datetime.utcnow().isoformat(),
        "items": payloads,
    }


@router.post("/maintenance/import")
def import_models(
    payload: dict = Body(...),
    model_type: str = Query(..., description="checkpoint or lora"),
    dry_run: bool = Query(True),
    db: Session = Depends(get_db),
) -> dict:
    """Import models from an export payload (`items` list of detail payloads)."""
    normalized = _normalize_model_type(model_type)
    items = payload.get("items")
    if not isinstance(items, list):
        raise HTTPException(status_code=400, detail="payload.items must be a list")

    imported = 0
    skipped = 0
    errors: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            skipped += 1
            continue
        item_type = str(item.get("type") or "").strip()
        if item_type and item_type != normalized:
            skipped += 1
            continue
        try:
            if not dry_run:
                upsert_model_detail(db, item)
            imported += 1
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))

    if dry_run:
        db.rollback()
    else:
        db.commit()

    return {
        "model_type": normalized,
        "dry_run": bool(dry_run),
        "imported": imported,
        "skipped": skipped,
        "errors": errors,
    }


@router.post("/maintenance/purge")
def purge_models(
    model_type: str = Query(..., description="checkpoint or lora"),
    dry_run: bool = Query(True),
    db: Session = Depends(get_db),
) -> dict:
    """Purge model catalog rows for a selected type."""
    normalized = _normalize_model_type(model_type)
    rows = db.query(CivitaiModel).filter(CivitaiModel.type == normalized).all()
    count = len(rows)
    if not dry_run:
        for row in rows:
            db.delete(row)
        db.commit()
    else:
        db.rollback()

    return {
        "model_type": normalized,
        "dry_run": bool(dry_run),
        "deleted": count,
    }


@router.post("/maintenance/rescan-gallery")
def maintenance_rescan_gallery(
    model_type: str = Query(..., description="checkpoint or lora"),
    limit: Optional[int] = Query(None, ge=1),
    fetch_details: bool = Query(True),
    missing_only: bool = Query(False),
    db: Session = Depends(get_db),
) -> dict:
    """Rescan generation_resources and backfill model catalog rows by type."""
    normalized = _normalize_model_type(model_type).lower()
    summary = rescan_gallery_models(
        db,
        model_type=normalized,
        limit=limit,
        fetch_details=fetch_details,
        missing_only=missing_only,
    )
    summary["completed_at"] = datetime.utcnow().isoformat()
    return summary


@router.get("/maintenance/rescan-gallery/stream")
def maintenance_rescan_gallery_stream(
    model_type: str = Query(..., description="checkpoint or lora"),
    limit: Optional[int] = Query(None, ge=1),
    fetch_details: bool = Query(True),
    missing_only: bool = Query(False),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """SSE streaming variant of rescan-gallery with real-time progress events.

    Event types:
      - ``progress``: emitted after each version ref is processed
      - ``complete``: final event with full summary
    """
    normalized = _normalize_model_type(model_type).lower()

    def _sse(data: dict) -> str:
        return f"data: {json.dumps(data)}\n\n"

    def event_stream():  # type: ignore[no-untyped-def]
        q: queue.Queue[Optional[dict]] = queue.Queue()

        def on_progress(payload: dict[str, Any]) -> None:
            q.put(payload)

        def worker() -> None:
            try:
                rescan_gallery_models(
                    db,
                    model_type=normalized,
                    limit=limit,
                    fetch_details=fetch_details,
                    missing_only=missing_only,
                    progress_callback=on_progress,
                )
            except Exception:
                # Propagate unexpected errors to the SSE stream
                q.put({"type": "error", "detail": "Rescan failed unexpectedly"})
            finally:
                q.put(None)  # sentinel

        t = threading.Thread(target=worker, daemon=True)
        t.start()

        while True:
            item = q.get()
            if item is None:
                break
            if item.get("type") == "complete":
                item["completed_at"] = datetime.utcnow().isoformat()
            yield _sse(item)

        t.join(timeout=5)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Model Observation Rescan (populate model_observations from sidecar JSON)
# ---------------------------------------------------------------------------

def _resource_type_from_civitai(model_type_str: str | None) -> str:
    """Normalise a CivitAI modelType string to a canonical resource_type."""
    raw = str(model_type_str or "").strip().lower()
    if raw in {"checkpoint", "model"}:
        return "checkpoint"
    if raw in {"lora", "locon", "lycoris"}:
        return "lora"
    if raw in {"vae"}:
        return "vae"
    if raw in {"upscaler", "upsampler"}:
        return "upscaler"
    if raw in {"controlnet"}:
        return "controlnet"
    if raw in {"embedding", "textualinversion"}:
        return "embedding"
    return "unknown"


def _rescan_model_observations_inner(
    db: Session,
    model_type: str,
    dry_run: bool,
    emit: Any,
) -> Any:
    """Generator: scan sidecar JSON files and populate ``model_observations``.

    Follows the same SSE generator pattern as
    ``_rescan_civitai_observations_inner`` in the taxonomy router.

    Metrics emitted:
        models_processed, unique_models, pre_existing, new_models,
        observations_created, observations_skipped
    """
    from atelierai.config import IMAGE_LIBRARY_PATH

    library_path = Path(IMAGE_LIBRARY_PATH)
    if not library_path.is_dir():
        yield emit("error_event", {"error": "Image library path not found.", "current_image": 0})
        yield emit("complete", {
            "models_processed": 0, "unique_models": 0,
            "pre_existing": 0, "new_models": 0,
            "observations_created": 0, "observations_skipped": 0,
            "errors": 1, "dry_run": dry_run,
        })
        return

    sidecar_files = sorted(library_path.glob("*.json"))
    total_images = len(sidecar_files)
    models_processed = 0
    unique_model_version_ids: set[int] = set()
    pre_existing = 0
    new_models = 0
    observations_created = 0
    observations_skipped = 0
    error_count = 0

    now = datetime.utcnow()

    # Pre-load all known version_id → model_id mappings for fast lookup
    version_to_model: dict[int, int] = {}
    rows = db.query(
        CivitaiModelVersion.civitai_version_id,
        CivitaiModelVersion.civitai_model_id,
    ).all()
    for vid, mid in rows:
        if vid is not None and mid is not None:
            version_to_model[vid] = mid

    # Pre-load existing model_observations to detect duplicates efficiently
    existing_obs: set[tuple[int, int, str | None]] = set()
    obs_rows = db.query(
        ModelObservation.image_id,
        ModelObservation.civitai_version_id,
        ModelObservation.generation_stage,
    ).all()
    for oid, vid, stage in obs_rows:
        existing_obs.add((oid, vid, stage))

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
                "models_processed": models_processed,
                "unique_models": len(unique_model_version_ids),
                "pre_existing": pre_existing, "new_models": new_models,
                "observations_created": observations_created,
                "observations_skipped": observations_skipped,
            })
            continue

        if not isinstance(data, dict):
            yield emit("progress", {
                "current_image": idx, "total_images": total_images,
                "models_processed": models_processed,
                "unique_models": len(unique_model_version_ids),
                "pre_existing": pre_existing, "new_models": new_models,
                "observations_created": observations_created,
                "observations_skipped": observations_skipped,
            })
            continue

        civitai = data.get("civitai")
        if not isinstance(civitai, dict):
            yield emit("progress", {
                "current_image": idx, "total_images": total_images,
                "models_processed": models_processed,
                "unique_models": len(unique_model_version_ids),
                "pre_existing": pre_existing, "new_models": new_models,
                "observations_created": observations_created,
                "observations_skipped": observations_skipped,
            })
            continue

        # Resolve image_id from DB via file stem
        image_stem = json_file.stem
        image_row = (
            db.query(ImageModel.id)
            .filter(ImageModel.file_path.like(f"{image_stem}.%"))
            .first()
        )
        if image_row is None:
            yield emit("progress", {
                "current_image": idx, "total_images": total_images,
                "models_processed": models_processed,
                "unique_models": len(unique_model_version_ids),
                "pre_existing": pre_existing, "new_models": new_models,
                "observations_created": observations_created,
                "observations_skipped": observations_skipped,
            })
            continue

        image_id = image_row[0]

        # Collect model references from both 'models' and 'loras' arrays
        refs: list[dict] = []

        # Determine which keys to scan based on model_type filter
        keys_to_scan = []
        if model_type in {"checkpoint", "all"}:
            keys_to_scan.append(("models", "checkpoint"))
        if model_type in {"lora", "all"}:
            keys_to_scan.append(("loras", "lora"))
        # If neither specified, scan both
        if not keys_to_scan:
            keys_to_scan = [("models", "checkpoint"), ("loras", "lora")]

        for json_key, default_resource_type in keys_to_scan:
            items = civitai.get(json_key)
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                refs.append({
                    "version_id": item.get("modelVersionId"),
                    "name": item.get("name"),
                    "base_model": item.get("baseModel"),
                    "model_type": item.get("modelType", default_resource_type),
                    "strength": item.get("weight"),
                    "resource_type": default_resource_type,
                })

        if not refs:
            yield emit("progress", {
                "current_image": idx, "total_images": total_images,
                "models_processed": models_processed,
                "unique_models": len(unique_model_version_ids),
                "pre_existing": pre_existing, "new_models": new_models,
                "observations_created": observations_created,
                "observations_skipped": observations_skipped,
            })
            continue

        models_processed += len(refs)

        if not dry_run:
            try:
                # Determine generation_stage from process metadata
                process = civitai.get("process") or data.get("meta", {}).get("process") if isinstance(data.get("meta"), dict) else None
                stage = str(process) if process else None

                for ref_idx, ref in enumerate(refs):
                    version_id = ref.get("version_id")
                    if not version_id:
                        continue
                    version_id = int(version_id)

                    # Track unique models
                    if version_id in unique_model_version_ids:
                        pass  # already seen
                    else:
                        unique_model_version_ids.add(version_id)
                        if version_id in version_to_model:
                            pre_existing += 1
                        else:
                            new_models += 1

                    # Look up civitai_model_id from version cache
                    civitai_model_id = version_to_model.get(version_id)

                    # Determine resource_type
                    resource_type = _resource_type_from_civitai(ref.get("model_type"))

                    # Determine is_primary: first checkpoint is primary
                    is_primary = (
                        ref_idx == 0
                        and resource_type == "checkpoint"
                    )

                    # Check for existing observation (unique constraint)
                    obs_key = (image_id, version_id, stage)
                    if obs_key in existing_obs:
                        observations_skipped += 1
                        continue

                    # Also check DB directly as fallback
                    existing = (
                        db.query(ModelObservation.id)
                        .filter(
                            ModelObservation.image_id == image_id,
                            ModelObservation.civitai_version_id == version_id,
                            ModelObservation.generation_stage == stage,
                        )
                        .first()
                    )
                    if existing is not None:
                        existing_obs.add(obs_key)
                        observations_skipped += 1
                        continue

                    obs = ModelObservation(
                        image_id=image_id,
                        civitai_model_id=civitai_model_id,
                        civitai_version_id=version_id,
                        resource_type=resource_type,
                        generation_stage=stage,
                        is_primary=is_primary,
                        source_type="metadata",
                        confidence=1.0,
                        strength=ref.get("strength"),
                        created_at=now,
                        updated_at=now,
                    )
                    db.add(obs)
                    existing_obs.add(obs_key)
                    observations_created += 1

                if observations_created > 0:
                    db.flush()

            except Exception as exc:
                db.rollback()
                error_count += 1
                yield emit("error_event", {
                    "current_image": idx, "file": json_file.name,
                    "error": f"observations: {exc}",
                })
        else:
            # Dry run: just count without creating rows
            for ref in refs:
                version_id = ref.get("version_id")
                if not version_id:
                    continue
                version_id = int(version_id)
                if version_id not in unique_model_version_ids:
                    unique_model_version_ids.add(version_id)
                    if version_id in version_to_model:
                        pre_existing += 1
                    else:
                        new_models += 1
                # In dry run, assume all would be created
                observations_created += 1

        yield emit("progress", {
            "current_image": idx, "total_images": total_images,
            "models_processed": models_processed,
            "unique_models": len(unique_model_version_ids),
            "pre_existing": pre_existing, "new_models": new_models,
            "observations_created": observations_created,
            "observations_skipped": observations_skipped,
        })

    if not dry_run and observations_created > 0:
        try:
            db.commit()
        except Exception as exc:
            db.rollback()
            yield emit("error_event", {
                "current_image": total_images, "error": f"commit failed: {exc}",
            })

    yield emit("complete", {
        "models_processed": models_processed,
        "unique_models": len(unique_model_version_ids),
        "pre_existing": pre_existing,
        "new_models": new_models,
        "observations_created": observations_created,
        "observations_skipped": observations_skipped,
        "errors": error_count,
        "dry_run": dry_run,
        "total_images": total_images,
    })


@router.get("/maintenance/rescan-observations/stream")
def maintenance_rescan_model_observations_stream(
    model_type: str = Query("all", description="checkpoint, lora, or all"),
    dry_run: bool = Query(False, description="Preview changes without committing"),
) -> StreamingResponse:
    """SSE endpoint: scan sidecar JSON to populate model_observations table.

    Iterates all sidecar files in IMAGE_LIBRARY_PATH, extracts model/lora
    references, resolves to catalog entries, and creates ModelObservation rows.

    Event types:
      - ``progress``: emitted after each sidecar file is processed
      - ``error_event``: emitted on per-file errors
      - ``complete``: final event with full summary
    """
    normalized = str(model_type or "all").strip().lower()
    if normalized not in {"checkpoint", "lora", "all"}:
        raise HTTPException(400, "model_type must be one of: checkpoint, lora, all")

    def _sse_event(event: str, data: dict) -> str:
        return f"event: {event}\ndata: {json.dumps(data)}\n\n"

    def event_stream():
        db = SessionLocal()
        try:
            yield from _rescan_model_observations_inner(
                db, normalized, dry_run, _sse_event,
            )
        finally:
            db.close()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/maintenance/local-catalog")
def maintenance_local_catalog(
    model_type: str = Query(..., description="checkpoint or lora"),
    include_full_raw: bool = Query(False),
) -> dict:
    """Fetch local ComfyUI LoRA Manager catalog and filter by selected type."""
    normalized = _normalize_model_type(model_type).lower()
    service = ModelReferenceService()
    payload = service.fetch_local_catalog(include_full_raw=include_full_raw)
    entries = payload.get("entries") if isinstance(payload, dict) else []
    if not isinstance(entries, list):
        entries = []
    filtered = [
        entry for entry in entries
        if service.parent_resource_type(
            str(entry.get("resource_type") or "").strip().lower()
        ) == normalized
    ]
    payload["entries"] = filtered
    payload["total"] = len(filtered)
    payload["model_type"] = normalized
    return payload


# ---------------------------------------------------------------------------
# Local-version-ids cache (avoids re-fetching ComfyUI on every table page)
# ---------------------------------------------------------------------------
_local_version_cache: dict[str, Any] = {}
_local_version_cache_ts: float = 0.0
_LOCAL_VERSION_CACHE_TTL = 60.0  # seconds


@router.get("/maintenance/local-version-ids")
def maintenance_local_version_ids(
    model_type: str = Query(..., description="checkpoint or lora"),
) -> dict:
    """Return a lightweight mapping of locally-installed CivitAI version IDs.

    Used by the model-maint table to show which versions are available locally
    without transferring the full catalog payload on every page change.
    Response is cached for 60 s to avoid hitting ComfyUI repeatedly.
    """
    import time

    global _local_version_cache, _local_version_cache_ts

    normalized = _normalize_model_type(model_type).lower()
    now = time.time()

    if (now - _local_version_cache_ts) > _LOCAL_VERSION_CACHE_TTL:
        _local_version_cache.clear()
        _local_version_cache_ts = now

    cache_key = normalized
    if cache_key in _local_version_cache:
        return _local_version_cache[cache_key]

    service = ModelReferenceService()
    try:
        payload = service.fetch_local_catalog(include_full_raw=False)
    except Exception as exc:
        result = {
            "configured": False,
            "versions": {},
            "count": 0,
            "model_type": normalized,
            "error": str(exc),
        }
        _local_version_cache[cache_key] = result
        return result

    configured = bool(payload.get("configured"))
    error = payload.get("error")
    entries = payload.get("entries") if isinstance(payload, dict) else []
    if not isinstance(entries, list):
        entries = []

    # Build {version_id: {model_id, file_name, file_path, resource_type}}
    # for entries matching the requested type (including subtypes whose parent
    # category is the requested type, e.g. diffusion_model → checkpoint,
    # dora → lora) that have a CivitAI version ID.
    versions: dict[int, dict[str, Any]] = {}
    for entry in entries:
        rt = str(entry.get("resource_type") or "").strip().lower()
        if service.parent_resource_type(rt) != normalized:
            continue
        vid = entry.get("civitai_model_version_id")
        if vid is None:
            continue
        versions[int(vid)] = {
            "model_id": entry.get("civitai_model_id"),
            "file_name": entry.get("file_name"),
            "file_path": entry.get("file_path"),
            "resource_type": rt,
        }

    result = {
        "configured": configured,
        "versions": versions,
        "count": len(versions),
        "model_type": normalized,
        "error": error,
    }
    _local_version_cache[cache_key] = result
    return result


# ---------------------------------------------------------------------------
# Gallery-usage-counts cache (image counts per model version)
# ---------------------------------------------------------------------------
_gallery_usage_cache: dict[str, Any] = {}
_gallery_usage_cache_ts: float = 0.0
_GALLERY_USAGE_CACHE_TTL = 60.0  # seconds


@router.get("/maintenance/gallery-usage-counts")
def maintenance_gallery_usage_counts(
    model_type: str = Query(..., description="checkpoint or lora"),
    db: Session = Depends(get_db),
) -> dict:
    """Return gallery image usage counts per CivitAI model version and model.

    Counts distinct images from json_metadata.civitai.models[].modelVersionId
    joined through civitai_model_versions → civitai_models, filtered by model
    type (Checkpoint, LORA, etc.).  Response is cached for 60 s.
    """
    import time

    global _gallery_usage_cache, _gallery_usage_cache_ts

    normalized = _normalize_model_type(model_type)  # e.g. "Checkpoint"
    now = time.time()

    if (now - _gallery_usage_cache_ts) > _GALLERY_USAGE_CACHE_TTL:
        _gallery_usage_cache.clear()
        _gallery_usage_cache_ts = now

    cache_key = normalized.lower()
    if cache_key in _gallery_usage_cache:
        return _gallery_usage_cache[cache_key]

    # Use raw SQL to query json_metadata.civitai.models[].modelVersionId
    # and join through civitai_model_versions → civitai_models.
    version_sql = text("""
        SELECT v.civitai_version_id AS vid,
               COUNT(DISTINCT i.id) AS img_count
        FROM images i
        CROSS JOIN json_each(i.json_metadata, '$.civitai.models') j
        JOIN civitai_model_versions v
              ON v.civitai_version_id = CAST(json_extract(j.value, '$.modelVersionId') AS INTEGER)
        JOIN civitai_models m
              ON m.civitai_model_id = v.civitai_model_id
        WHERE json_extract(j.value, '$.modelVersionId') IS NOT NULL
          AND m.type = :model_type
        GROUP BY v.civitai_version_id
    """)

    model_sql = text("""
        SELECT v.civitai_model_id AS mid,
               COUNT(DISTINCT i.id) AS img_count
        FROM images i
        CROSS JOIN json_each(i.json_metadata, '$.civitai.models') j
        JOIN civitai_model_versions v
              ON v.civitai_version_id = CAST(json_extract(j.value, '$.modelVersionId') AS INTEGER)
        JOIN civitai_models m
              ON m.civitai_model_id = v.civitai_model_id
        WHERE json_extract(j.value, '$.modelVersionId') IS NOT NULL
          AND m.type = :model_type
        GROUP BY v.civitai_model_id
    """)

    with engine.connect() as conn:
        version_rows = conn.execute(version_sql, {"model_type": normalized}).fetchall()
        model_rows = conn.execute(model_sql, {"model_type": normalized}).fetchall()

    version_counts: dict[int, int] = {int(row.vid): int(row.img_count) for row in version_rows}
    model_counts: dict[int, int] = {int(row.mid): int(row.img_count) for row in model_rows}

    result = {
        "model_type": cache_key,
        "version_counts": version_counts,
        "model_counts": model_counts,
        "total_versions": len(version_counts),
        "total_models": len(model_counts),
    }
    _gallery_usage_cache[cache_key] = result
    return result
