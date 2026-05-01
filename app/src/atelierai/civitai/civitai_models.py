#!/usr/bin/env python3
"""
CivitAI Model Catalog scraping and syncing.

Provides functions to fetch and store CivitAI model data (Checkpoints, LoRAs, etc.)
into the local database. Uses model.getAll (with cursor pagination) for discovery
and model.getById for full model detail.
"""

import logging
import sys
from datetime import datetime
from typing import Callable, Dict, List, Optional, Any, Tuple, TYPE_CHECKING
from importlib import import_module

from sqlalchemy.orm import Session
from sqlalchemy import text

from .civitai_api import CivitaiAPI
from .http_client import CivitaiRequestError

if TYPE_CHECKING:
    from backend.models import CivitaiModel

logger = logging.getLogger(__name__)


def _load_model_classes():
    """Dynamically load model classes to avoid circular imports.

    Tries multiple import paths to handle different deployment scenarios.
    """
    module_paths = [
        "models",
        "backend.models",
        "atelierai.models",
        "app.backend.models",
    ]

    # Prefer an already-loaded module to avoid importing the same models file
    # under a different module name (which would re-register SQLAlchemy tables).
    for module_path in module_paths:
        mod = sys.modules.get(module_path)
        if mod is None:
            continue
        try:
            return {
                'CivitaiModel': mod.CivitaiModel,
                'CivitaiModelVersion': mod.CivitaiModelVersion,
                'CivitaiModelVersionFile': mod.CivitaiModelVersionFile,
                'CivitaiModelFileHash': mod.CivitaiModelFileHash,
                'CivitaiModelRank': mod.CivitaiModelRank,
                'CivitaiModelTag': mod.CivitaiModelTag,
                'CivitaiUser': mod.CivitaiUser,
                'CivitaiBaseModel': mod.CivitaiBaseModel,
                'ModelObservation': mod.ModelObservation,
                'GenerationResource': mod.GenerationResource,
            }
        except AttributeError:
            continue

    for module_path in module_paths:
        try:
            mod = import_module(module_path)
            return {
                'CivitaiModel': mod.CivitaiModel,
                'CivitaiModelVersion': mod.CivitaiModelVersion,
                'CivitaiModelVersionFile': mod.CivitaiModelVersionFile,
                'CivitaiModelFileHash': mod.CivitaiModelFileHash,
                'CivitaiModelRank': mod.CivitaiModelRank,
                'CivitaiModelTag': mod.CivitaiModelTag,
                'CivitaiUser': mod.CivitaiUser,
                'CivitaiBaseModel': mod.CivitaiBaseModel,
                'ModelObservation': mod.ModelObservation,
                'GenerationResource': mod.GenerationResource,
            }
        except (ModuleNotFoundError, AttributeError):
            continue

    raise RuntimeError(
        "Could not load CivitaiModel classes from any import path"
    )


class CivitaiModelSyncError(Exception):
    """Raised when model sync operations fail."""
    pass


def fetch_model_list(
    api: CivitaiAPI,
    username: str,
    cursor: Optional[str] = None,
    limit: int = 50,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Fetch a page of models from model.getAll endpoint.

    Args:
        api: CivitaiAPI instance
        username: CivitAI username to fetch models for
        cursor: Pagination cursor (opaque string); None for first page
        limit: Number of items to fetch per page

    Returns:
        Tuple of (items, nextCursor) where nextCursor is None if no more pages
    """
    payload_data = {
        "username": username,
        "sort": "Newest",
        "period": "AllTime",
        "cursor": cursor,
        "limit": limit,
        "authed": True,
    }

    response = api._make_request(
        endpoint="model.getAll",
        payload_data=payload_data,
    )

    if not response:
        return [], None

    items = response.get("items", [])
    next_cursor = response.get("nextCursor")

    return items, next_cursor


def fetch_model_detail(
    api: CivitaiAPI,
    model_id: int,
    *,
    raise_on_not_found: bool = False,
) -> Optional[Dict[str, Any]]:
    """Fetch full model detail from model.getById endpoint.

    Args:
        api: CivitaiAPI instance
        model_id: CivitAI model ID
        raise_on_not_found: If True, raise CivitaiRequestError on HTTP 404
            so the caller can distinguish deleted models from transient failures.

    Returns:
        Model detail dict, or None if not found (when raise_on_not_found=False)

    Raises:
        CivitaiRequestError: When raise_on_not_found=True and the model returns 404
    """
    payload_data = {
        "id": model_id,
        "authed": True,
    }

    return api._make_request(
        endpoint="model.getById",
        payload_data=payload_data,
        strict=raise_on_not_found,
    )


def _mark_model_removed(
    db: Session,
    model_id: int,
    name: Optional[str] = None,
) -> Optional[Any]:
    """Create or update a CivitaiModel tombstone with status='Removed'.

    When CivitAI returns 404 for a model, we store a minimal row so that future
    scans skip it instead of repeatedly trying to fetch a deleted model.

    Args:
        db: Database session
        model_id: CivitAI model ID that was deleted
        name: Model name if known (for easier debugging)

    Returns:
        The upserted CivitaiModel, or None on error
    """
    models = _load_model_classes()
    CivitaiModel = models.get("CivitaiModel")
    if CivitaiModel is None:
        return None

    existing = (
        db.query(CivitaiModel)
        .filter(CivitaiModel.civitai_model_id == model_id)
        .first()
    )
    if existing is not None:
        if existing.status != "Removed":
            existing.status = "Removed"
            logger.info(
                "Marked existing model %d (%s) as Removed",
                model_id,
                existing.name,
            )
        return existing

    # Create minimal tombstone
    tombstone = CivitaiModel(
        civitai_model_id=model_id,
        name=name or f"RemovedModel_{model_id}",
        type="Unknown",
        status="Removed",
        civitai_user_id=0,
        civitai_username="unknown",
    )
    db.add(tombstone)
    logger.info(
        "Created tombstone for removed model %d%s",
        model_id,
        f" ({name})" if name else "",
    )
    return tombstone


def upsert_model_list_item(
    db: Session,
    item: Dict[str, Any],
) -> Optional["CivitaiModel"]:
    """Upsert a model from model.getAll list item (partial data).

    This populates the top-level model metadata but not versions/files.
    The model will have scraped_at=None until a full getById is done.

    Args:
        db: Database session
        item: Model item from model.getAll response

    Returns:
        Created or updated CivitaiModel, or None if item is invalid
    """
    # Dynamically load model classes to avoid circular imports
    models = _load_model_classes()
    CivitaiModel = models['CivitaiModel']
    CivitaiUser = models['CivitaiUser']

    model_id = item.get("id")
    if not model_id:
        logger.warning("Model item missing 'id': %s", item)
        return None

    # Find or create model
    model = db.query(CivitaiModel).filter(
        CivitaiModel.civitai_model_id == model_id
    ).first()

    if not model:
        model = CivitaiModel(civitai_model_id=model_id)
        db.add(model)

    # Update from list item
    model.name = item.get("name", "")
    model.type = item.get("type", "Unknown")
    model.nsfw = item.get("nsfw", False)
    model.nsfw_level = item.get("nsfwLevel", 1)
    model.sfw_only = item.get("sfwOnly", False)
    model.poi = item.get("poi", False)
    model.minor = item.get("minor", False)
    model.status = item.get("status", "Published")
    model.availability = item.get("availability", "Public")
    model.locked = item.get("locked", False)

    # Author info — write to both legacy columns and CivitaiUser table
    user = item.get("user", {})
    user_id = user.get("id", 0)
    username = user.get("username", "")
    user_deleted = user.get("deleted", False)

    model.civitai_user_id = user_id
    model.civitai_username = username
    model.civitai_user_deleted = user_deleted

    # Upsert into normalized CivitaiUser table
    if user_id:
        civitai_user = db.query(CivitaiUser).filter(
            CivitaiUser.civitai_user_id == user_id
        ).first()
        if not civitai_user:
            civitai_user = CivitaiUser(civitai_user_id=user_id)
            db.add(civitai_user)
        civitai_user.name = username
        civitai_user.scraped_at = datetime.utcnow()
        if user_deleted and civitai_user.deleted_at is None:
            civitai_user.deleted_at = datetime.utcnow()
            civitai_user.original_name = civitai_user.original_name or username
        civitai_user.updated_at = datetime.utcnow()
        model.creator_id = user_id

    # Timestamps
    if item.get("createdAt"):
        model.created_at = datetime.fromisoformat(item["createdAt"].replace("Z", "+00:00"))
    if item.get("publishedAt"):
        model.published_at = datetime.fromisoformat(item["publishedAt"].replace("Z", "+00:00"))
    if item.get("lastVersionAt"):
        model.last_version_at = datetime.fromisoformat(item["lastVersionAt"].replace("Z", "+00:00"))
    if item.get("earlyAccessDeadline"):
        model.early_access_deadline = datetime.fromisoformat(item["earlyAccessDeadline"].replace("Z", "+00:00"))

    # Track when we saw this in the list
    model.list_scraped_at = datetime.utcnow()

    db.flush()
    return model


def upsert_model_detail(
    db: Session,
    detail: Dict[str, Any],
) -> Optional["CivitaiModel"]:
    """Upsert full model detail from model.getById (comprehensive data).

    Creates or updates the model and all its versions, files, hashes, tags, and rank.

    Args:
        db: Database session
        detail: Full model detail from model.getById response

    Returns:
        Created or updated CivitaiModel, or None if detail is invalid
    """
    # Dynamically load model classes to avoid circular imports
    models = _load_model_classes()
    CivitaiModel = models['CivitaiModel']
    CivitaiModelVersion = models['CivitaiModelVersion']
    CivitaiModelVersionFile = models['CivitaiModelVersionFile']
    CivitaiModelFileHash = models['CivitaiModelFileHash']
    CivitaiModelRank = models['CivitaiModelRank']
    CivitaiModelTag = models['CivitaiModelTag']
    CivitaiUser = models['CivitaiUser']

    model_id = detail.get("id")
    if not model_id:
        logger.warning("Model detail missing 'id'")
        return None

    # Find or create model
    model = db.query(CivitaiModel).filter(
        CivitaiModel.civitai_model_id == model_id
    ).first()

    if not model:
        model = CivitaiModel(civitai_model_id=model_id)
        db.add(model)

    # Update from detail
    model.name = detail.get("name", "")
    model.type = detail.get("type", "Unknown")
    model.description = detail.get("description")
    model.checkpoint_type = detail.get("checkpointType")
    model.nsfw = detail.get("nsfw", False)
    model.nsfw_level = detail.get("nsfwLevel", 1)
    model.sfw_only = detail.get("sfwOnly", False)
    model.poi = detail.get("poi", False)
    model.minor = detail.get("minor", False)
    model.status = detail.get("status", "Published")
    model.availability = detail.get("availability", "Public")
    model.allow_no_credit = detail.get("allowNoCredit")
    model.allow_commercial_use = detail.get("allowCommercialUse")
    model.allow_derivatives = detail.get("allowDerivatives")
    model.allow_different_license = detail.get("allowDifferentLicense")
    model.locked = detail.get("locked", False)

    # Author info — write to both legacy columns and CivitaiUser table
    user = detail.get("user", {})
    user_id = user.get("id", 0)
    username = user.get("username", "")
    user_deleted = user.get("deleted", False)

    model.civitai_user_id = user_id
    model.civitai_username = username
    model.civitai_user_deleted = user_deleted

    # Upsert into normalized CivitaiUser table
    if user_id:
        civitai_user = db.query(CivitaiUser).filter(
            CivitaiUser.civitai_user_id == user_id
        ).first()
        if not civitai_user:
            civitai_user = CivitaiUser(civitai_user_id=user_id)
            db.add(civitai_user)
        civitai_user.name = username
        civitai_user.scraped_at = datetime.utcnow()
        if user_deleted and civitai_user.deleted_at is None:
            civitai_user.deleted_at = datetime.utcnow()
            civitai_user.original_name = civitai_user.original_name or username
        civitai_user.updated_at = datetime.utcnow()
        model.creator_id = user_id

    # Timestamps
    if detail.get("createdAt"):
        model.created_at = datetime.fromisoformat(detail["createdAt"].replace("Z", "+00:00"))
    if detail.get("publishedAt"):
        model.published_at = datetime.fromisoformat(detail["publishedAt"].replace("Z", "+00:00"))
    if detail.get("updatedAt"):
        model.updated_at = datetime.fromisoformat(detail["updatedAt"].replace("Z", "+00:00"))
    if detail.get("lastVersionAt"):
        model.last_version_at = datetime.fromisoformat(detail["lastVersionAt"].replace("Z", "+00:00"))
    if detail.get("earlyAccessDeadline"):
        model.early_access_deadline = datetime.fromisoformat(detail["earlyAccessDeadline"].replace("Z", "+00:00"))

    # Latest version ID — prefer API field, fall back to max version ID
    if detail.get("latestVersionId"):
        model.latest_version_id = detail["latestVersionId"]

    model.scraped_at = datetime.utcnow()

    # Upsert versions
    versions_data = detail.get("modelVersions", [])
    incoming_version_ids = {v.get("id") for v in versions_data if v.get("id")}

    # Remove versions not in incoming data
    for version in model.versions[:]:
        if version.civitai_version_id not in incoming_version_ids:
            db.delete(version)

    # Upsert versions from detail
    for version_data in versions_data:
        version_id = version_data.get("id")
        if not version_id:
            continue

        version = db.query(CivitaiModelVersion).filter(
            CivitaiModelVersion.civitai_version_id == version_id
        ).first()

        if not version:
            version = CivitaiModelVersion(civitai_version_id=version_id)
            version.civitai_model_id = model_id
            db.add(version)

        version.name = version_data.get("name", "")
        version.description = version_data.get("description")
        version.base_model = version_data.get("baseModel", "Unknown")
        version.base_model_type = version_data.get("baseModelType")
        version.nsfw_level = version_data.get("nsfwLevel", 1)
        version.status = version_data.get("status", "Published")
        version.availability = version_data.get("availability", "Public")
        version.clip_skip = version_data.get("clipSkip")
        version.steps = version_data.get("steps")
        version.epochs = version_data.get("epochs")
        version.trained_words = version_data.get("trainedWords")
        version.training_status = version_data.get("trainingStatus")
        version.require_auth = version_data.get("requireAuth", False)
        version.usage_control = version_data.get("usageControl")
        version.early_access_config = version_data.get("earlyAccessConfig")

        # Phase 2: Fall back to model-level data for null version fields
        if version.description is None and model.description:
            version.description = model.description
        if version.nsfw_level is None:
            version.nsfw_level = model.nsfw_level or 1
        if version.status is None:
            version.status = model.status or "Published"

        if version_data.get("publishedAt"):
            version.published_at = datetime.fromisoformat(
                version_data["publishedAt"].replace("Z", "+00:00")
            )
        if version_data.get("createdAt"):
            version.created_at = datetime.fromisoformat(
                version_data["createdAt"].replace("Z", "+00:00")
            )
        if version_data.get("updatedAt"):
            version.updated_at = datetime.fromisoformat(
                version_data["updatedAt"].replace("Z", "+00:00")
            )

        version.scraped_at = datetime.utcnow()

        db.flush()

        # Upsert files and hashes
        files_data = version_data.get("files", [])
        incoming_file_ids = {f.get("id") for f in files_data if f.get("id")}

        # Remove files not in incoming data
        for file in version.files[:]:
            if file.civitai_file_id not in incoming_file_ids:
                db.delete(file)

        # Upsert files
        for file_data in files_data:
            file_id = file_data.get("id")
            if not file_id:
                continue

            file = db.query(CivitaiModelVersionFile).filter(
                CivitaiModelVersionFile.civitai_file_id == file_id
            ).first()

            if not file:
                file = CivitaiModelVersionFile(civitai_file_id=file_id)
                file.civitai_version_id = version_id
                db.add(file)

            file.name = file_data.get("name", "")
            file.type = file_data.get("type", "Model")
            file.size_kb = file_data.get("sizeKB", 0)
            file.download_url = file_data.get("downloadUrl")
            file.visibility = file_data.get("visibility")
            file.pickle_scan_result = file_data.get("pickleScanResult")
            file.virus_scan_result = file_data.get("virusScanResult")

            if file_data.get("scannedAt"):
                file.scanned_at = datetime.fromisoformat(
                    file_data["scannedAt"].replace("Z", "+00:00")
                )

            # Metadata
            metadata = file_data.get("metadata", {})
            if metadata:
                file.format = metadata.get("format")
                file.fp = metadata.get("fp")
                file.size_label = metadata.get("size")

            db.flush()

            # Upsert hashes
            hashes_data = file_data.get("hashes", [])
            incoming_hash_types = {h.get("type") for h in hashes_data if h.get("type")}

            # Remove hashes not in incoming data
            for hash_rec in file.hashes[:]:
                if hash_rec.hash_type not in incoming_hash_types:
                    db.delete(hash_rec)

            # Upsert hashes
            for hash_data in hashes_data:
                hash_type = hash_data.get("type")
                hash_value = hash_data.get("hash")
                if not hash_type or not hash_value:
                    continue

                hash_rec = db.query(CivitaiModelFileHash).filter(
                    CivitaiModelFileHash.file_id == file_id,
                    CivitaiModelFileHash.hash_type == hash_type,
                ).first()

                if not hash_rec:
                    hash_rec = CivitaiModelFileHash(
                        file_id=file_id,
                        hash_type=hash_type,
                        hash_value=hash_value,
                    )
                    db.add(hash_rec)
                else:
                    hash_rec.hash_value = hash_value

                db.flush()

        # Upsert version rank
        version_rank_data = version_data.get("rank", {})
        if version_rank_data:
            version_rank = db.query(CivitaiModelRank).filter(
                CivitaiModelRank.version_id == version_id,
                CivitaiModelRank.scope == "allTime",
            ).first()

            if not version_rank:
                version_rank = CivitaiModelRank(
                    version_id=version_id,
                    scope="allTime",
                )
                db.add(version_rank)

            version_rank.download_count = version_rank_data.get("downloadCountAllTime", 0)
            version_rank.thumbs_up_count = version_rank_data.get("thumbsUpCountAllTime", 0)
            version_rank.thumbs_down_count = version_rank_data.get("thumbsDownCountAllTime", 0)
            version_rank.generation_count = version_rank_data.get("generationCountAllTime")
            version_rank.earned_amount = version_rank_data.get("earnedAmountAllTime")
            version_rank.scraped_at = datetime.utcnow()

            db.flush()

    # Upsert model rank
    model_rank_data = detail.get("rank", {})
    if model_rank_data:
        model_rank = db.query(CivitaiModelRank).filter(
            CivitaiModelRank.model_id == model_id,
            CivitaiModelRank.scope == "allTime",
        ).first()

        if not model_rank:
            model_rank = CivitaiModelRank(
                model_id=model_id,
                scope="allTime",
            )
            db.add(model_rank)

        model_rank.download_count = model_rank_data.get("downloadCountAllTime", 0)
        model_rank.thumbs_up_count = model_rank_data.get("thumbsUpCountAllTime", 0)
        model_rank.thumbs_down_count = model_rank_data.get("thumbsDownCountAllTime", 0)
        model_rank.comment_count = model_rank_data.get("commentCountAllTime")
        model_rank.collected_count = model_rank_data.get("collectedCountAllTime")
        model_rank.tipped_amount_count = model_rank_data.get("tippedAmountCountAllTime")
        model_rank.scraped_at = datetime.utcnow()

        db.flush()

    # Upsert tags
    tags_data = detail.get("tagsOnModels", [])
    incoming_tag_ids = {t.get("id") for t in tags_data if t.get("id")}

    # Remove tags not in incoming data
    for tag in model.tags[:]:
        if tag.civitai_tag_id not in incoming_tag_ids:
            db.delete(tag)

    # Upsert tags
    for tag_data in tags_data:
        tag_id = tag_data.get("id")
        if not tag_id:
            continue

        tag = db.query(CivitaiModelTag).filter(
            CivitaiModelTag.civitai_model_id == model_id,
            CivitaiModelTag.civitai_tag_id == tag_id,
        ).first()

        if not tag:
            tag = CivitaiModelTag(
                civitai_model_id=model_id,
                civitai_tag_id=tag_id,
            )
            db.add(tag)

        tag.tag_name = tag_data.get("name", "")
        tag.is_category = tag_data.get("isCategory", False)

        db.flush()

    # Derive latest_version_id from stored versions if not already set
    if model.latest_version_id is None and model.versions:
        max_vid = max(v.civitai_version_id for v in model.versions)
        model.latest_version_id = max_vid

    return model


def sync_user_models(
    db: Session,
    username: str,
    limit: Optional[int] = None,
    fetch_details: bool = True,
) -> Tuple[int, int]:
    """Synchronize all models for a CivitAI user.

    This orchestrates a full sync:
    1. Paginate through model.getAll for the user (cursor pagination)
    2. Upsert each list item (partial data)
    3. If fetch_details=True, also fetch model.getById for full detail

    Args:
        db: Database session
        username: CivitAI username
        limit: Maximum number of models to sync (None for all)
        fetch_details: If True, fetch full getById for each model

    Returns:
        Tuple of (total_models_synced, total_details_fetched)
    """
    api = CivitaiAPI.get_instance()
    total_synced = 0
    total_details = 0
    cursor = None

    while True:
        items, next_cursor = fetch_model_list(api, username, cursor=cursor)
        if not items:
            break

        for item in items:
            if limit and total_synced >= limit:
                break

            # Upsert list item
            model = upsert_model_list_item(db, item)
            if model:
                total_synced += 1

                # Fetch and upsert full detail if requested
                if fetch_details:
                    detail = fetch_model_detail(api, model.civitai_model_id)
                    if detail:
                        upsert_model_detail(db, detail)
                        total_details += 1
                    else:
                        logger.warning(
                            "Failed to fetch detail for model %s (%s)",
                            model.civitai_model_id,
                            model.name,
                        )

        if limit and total_synced >= limit:
            break

        # Check for more pages
        if next_cursor:
            cursor = next_cursor
        else:
            break

    db.commit()
    logger.info(
        "Synced %d models for %s; fetched details for %d",
        total_synced,
        username,
        total_details,
    )

    return total_synced, total_details


def _resolve_version_to_model_id(
    api: "CivitaiAPI",
    version_id: int,
) -> Optional[int]:
    """Resolve a CivitAI model version ID to its parent model ID.

    Uses the modelVersion.getById endpoint which returns the parent model
    in the ``model`` key.

    Args:
        api: CivitaiAPI instance
        version_id: CivitAI model version ID

    Returns:
        Parent CivitAI model ID, or None if not found
    """
    resp = api._make_request(
        endpoint="modelVersion.getById",
        payload_data={"id": int(version_id), "authed": True},
    )
    if resp and isinstance(resp, dict):
        model_info = resp.get("model")
        if isinstance(model_info, dict):
            mid = model_info.get("id")
            if mid:
                return int(mid)
    return None


def rescan_gallery_models(
    db: Session,
    *,
    model_type: str,
    limit: Optional[int] = None,
    fetch_details: bool = True,
    missing_only: bool = False,
    progress_callback: Optional[Callable[[dict[str, Any]], None]] = None,
) -> dict[str, Any]:
    """Scan gallery ``images.json_metadata`` for model refs and backfill catalog rows.

    The model references live in ``json_metadata → civitai.models`` (checkpoints)
    and ``json_metadata → civitai.loras`` (LoRAs).  Each entry carries
    ``modelVersionId`` (a real CivitAI version ID) but ``modelId`` is typically
    the string ``"Unknown"``.  We therefore:

    1. Collect distinct ``(name, modelVersionId, baseModel)`` tuples via
       SQLite ``json_each``.
    2. For each unique ``modelVersionId``, resolve the parent CivitAI model ID
       via ``modelVersion.getById``, then optionally fetch full details with
       ``model.getById`` and upsert into the local catalog tables.
    Args:
        db: Database session
        model_type: "checkpoint" or "lora"
        limit: Maximum number of new detail fetches (fetch_budget)
        fetch_details: If True, fetch full getById for each model
        missing_only: If True, skip refs whose parent model is already catalogued
        progress_callback: Optional callable receiving a progress dict at key
            points during the scan.  Dict contains keys: ``type`` ("progress"
            or "complete"), plus counters matching the return dict.

    Returns:
        Summary dict with keys: model_type, scanned, found_version_refs,
        skipped_by_prefilter, resolved_model_ids, upserted, fetched_details,
        skipped_already_cached, marked_removed, failed.    """
    models = _load_model_classes()
    CivitaiModel = models.get("CivitaiModel")
    if CivitaiModel is None:
        raise RuntimeError("CivitaiModel class not available for rescan")

    normalized_type = str(model_type or "").strip().lower()
    if normalized_type not in {"checkpoint", "lora"}:
        raise ValueError("model_type must be one of: checkpoint, lora")

    # Choose the JSON key: checkpoints → civitai.models, loras → civitai.loras
    json_key = "models" if normalized_type == "checkpoint" else "loras"

    # Collect distinct (name, version_id, base_model) from json_metadata
    raw_sql = text("""
        SELECT DISTINCT
            json_extract(elem.value, '$.name')          AS name,
            json_extract(elem.value, '$.modelVersionId') AS version_id,
            json_extract(elem.value, '$.baseModel')      AS base_model
        FROM images, json_each(json_extract(images.json_metadata, '$.civitai.{key}')) AS elem
        WHERE images.json_metadata IS NOT NULL
          AND json_extract(images.json_metadata, '$.civitai.{key}') IS NOT NULL
        ORDER BY version_id ASC
    """.format(key=json_key))

    result = db.execute(raw_sql)
    rows = result.fetchall()

    # Deduplicate by version_id (rows may have same version with slightly
    # different names across images).
    seen_versions: dict[int, dict[str, Any]] = {}
    for row in rows:
        name, vid, base = row[0], row[1], row[2]
        if vid is None:
            continue
        vid = int(vid)
        if vid not in seen_versions:
            seen_versions[vid] = {"name": name, "version_id": vid, "base_model": base}

    version_refs = list(seen_versions.values())

    CivitaiModelVersion = models.get("CivitaiModelVersion")

    # When missing_only, bulk-filter out version refs whose parent model is
    # already in the catalog — avoids per-ref API round-trips for cached models.
    skipped_by_prefilter = 0
    if missing_only and CivitaiModelVersion is not None:
        all_vids = [r["version_id"] for r in version_refs]
        # Find version IDs already tracked locally
        existing_vers = (
            db.query(CivitaiModelVersion.civitai_version_id, CivitaiModelVersion.civitai_model_id)
            .filter(CivitaiModelVersion.civitai_version_id.in_(all_vids))
            .all()
        )
        known_model_ids = {
            mv_id for _, mv_id in existing_vers if mv_id is not None
        }
        # Among those, determine which model IDs already exist in CivitaiModel
        # and build status lookup so we can skip Removed tombstones too.
        cached_model_ids: set[int] = set()
        removed_model_ids: set[int] = set()
        if known_model_ids:
            cached_rows = (
                db.query(
                    CivitaiModel.civitai_model_id,
                    CivitaiModel.status,
                )
                .filter(CivitaiModel.civitai_model_id.in_(known_model_ids))
                .all()
            )
            for row in cached_rows:
                cached_model_ids.add(row[0])
                if row[1] == "Removed":
                    removed_model_ids.add(row[0])

        # Build lookup: version_id → model_id for known versions
        ver_to_model: dict[int, Optional[int]] = {
            vid: mid for vid, mid in existing_vers
        }

        filtered: list[dict[str, Any]] = []
        for ref in version_refs:
            mid = ver_to_model.get(ref["version_id"])
            if mid is None:
                # Version unknown to local DB → may be new, keep it
                filtered.append(ref)
                continue
            # If parent model was Removed → skip (tombstone)
            if mid in removed_model_ids:
                skipped_by_prefilter += 1
                continue
            # If parent model is cached and not Removed → skip
            if mid in cached_model_ids:
                skipped_by_prefilter += 1
                continue
            filtered.append(ref)
        version_refs = filtered
        if skipped_by_prefilter:
            logger.info(
                "missing_only: pre-filtered %d / %d version refs "
                "(parent model already catalogued)",
                skipped_by_prefilter,
                len(version_refs) + skipped_by_prefilter,
            )

    # limit caps the number of *new model fetches*, not the number of refs
    # scanned.  We iterate through refs and stop once we've upserted N models.
    fetch_budget = int(limit) if limit is not None and int(limit) > 0 else None

    api = CivitaiAPI.get_instance()

    scanned = 0
    resolved_model_ids: list[int] = []
    created_or_updated = 0
    fetched_details = 0
    failed = 0
    skipped_already_cached = 0
    marked_removed = 0

    def _emit_progress() -> None:
        """Fire progress callback (if configured) with current counters."""
        if progress_callback is None:
            return
        progress_callback({
            "type": "progress",
            "model_type": normalized_type,
            "scanned": scanned,
            "upserted": created_or_updated,
            "failed": failed,
            "marked_removed": marked_removed,
            "total_refs": len(version_refs),
            "resolved_models": len(set(resolved_model_ids)),
            "current_ref": ref.get("name"),
            "current_version_id": version_id,
        })

    for ref in version_refs:
        # Stop once we've fetched enough new models
        if fetch_budget is not None and fetched_details >= fetch_budget:
            break

        scanned += 1
        version_id = ref["version_id"]

        # Before resolving via API, check if we already have a CivitaiModelVersion
        # whose civitai_version_id matches — then we can skip the API call.
        CivitaiModelVersion = models.get("CivitaiModelVersion")
        existing_model_id: Optional[int] = None
        if CivitaiModelVersion is not None:
            existing_ver = db.query(CivitaiModelVersion).filter(
                CivitaiModelVersion.civitai_version_id == version_id
            ).first()
            if existing_ver and existing_ver.civitai_model_id:
                existing_model_id = existing_ver.civitai_model_id

        if existing_model_id:
            model_id = existing_model_id
        else:
            # Resolve version → parent model ID via CivitAI API
            model_id = _resolve_version_to_model_id(api, version_id)
            if not model_id:
                logger.warning(
                    "Could not resolve version %d to model ID", version_id
                )
                failed += 1
                _emit_progress()
                continue

        resolved_model_ids.append(model_id)

        existing = (
            db.query(CivitaiModel)
            .filter(CivitaiModel.civitai_model_id == model_id)
            .first()
        )

        # Skip models previously marked as Removed (deleted from CivitAI)
        if existing is not None and existing.status == "Removed":
            skipped_already_cached += 1
            _emit_progress()
            continue

        # Skip detail fetch if already cached and fetch_details=False
        if existing is not None and not fetch_details:
            skipped_already_cached += 1
            _emit_progress()
            continue

        # Skip models already present in the catalog when missing_only=True
        if existing is not None and missing_only:
            skipped_already_cached += 1
            _emit_progress()
            continue

        # Fetch full model details and upsert
        try:
            detail = fetch_model_detail(
                api, model_id, raise_on_not_found=True
            )
        except CivitaiRequestError as exc:
            if exc.status_code == 404:
                # Model was deleted from CivitAI — create tombstone
                _mark_model_removed(db, model_id, name=ref.get("name"))
                marked_removed += 1
                _emit_progress()
                continue
            # Other API errors — just count as failed
            logger.warning(
                "API error fetching model %d: %s", model_id, exc
            )
            failed += 1
            _emit_progress()
            continue

        if not detail:
            failed += 1
            _emit_progress()
            continue

        upserted = upsert_model_detail(db, detail)
        if upserted is not None:
            created_or_updated += 1
            fetched_details += 1
        else:
            failed += 1

        _emit_progress()

    db.commit()

    result = {
        "model_type": normalized_type,
        "scanned": scanned,
        "found_version_refs": len(seen_versions),
        "skipped_by_prefilter": skipped_by_prefilter,
        "resolved_model_ids": len(set(resolved_model_ids)),
        "upserted": created_or_updated,
        "fetched_details": fetched_details,
        "skipped_already_cached": skipped_already_cached,
        "marked_removed": marked_removed,
        "failed": failed,
    }

    if progress_callback:
        result["type"] = "complete"
        progress_callback(result)

    return result
