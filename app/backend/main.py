# main.py
import os
import json
import re
import tempfile
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, File, UploadFile, Form
from typing import List, Optional, Literal
from contextlib import asynccontextmanager
from urllib.parse import urlparse

import requests
from PIL import Image
from sqlalchemy.orm import Session, joinedload
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from atelierai.config import (
    IMAGE_LIBRARY_PATH,
    CURRENT_SCHEMA_VERSION,
    DATABASE_URL,
    ALLOW_SCHEMA_RESET,
)

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
    Tool,
    License,
    Artist,
    SchemaVersion,
)  # Import the specific classes we need

from image_collection import ImageCollection
from image_data import ImageData
from image_processor import ImageProcessor, is_exiftool_available
from civitai_enrichment import (
    is_civitai_image_url,
    extract_civitai_image_id,
    fetch_civitai_image_data,
)
from atelierai.civitai.civitai_api import CivitaiAPI
from atelierai.civitai.civitai_image import CivitaiImage
from atelierai.civitai.civitai import CivitaiPrivateScraper


class ScanRequest(BaseModel):
    folder_path: str


class ImageUpdateRequest(BaseModel):
    source_url: Optional[str] = None
    artist_name: Optional[str] = None
    artist_profile: Optional[str] = None


class CivitaiImportRequest(BaseModel):
    import_type: Literal["collection", "image"]
    value: str
    limit: Optional[int] = None


class CollectionCreateRequest(BaseModel):
    name: str


class CollectionRenameRequest(BaseModel):
    name: str


_CIVITAI_COLLECTION_PATH_RE = re.compile(r"^/collections/(?P<collection_id>\d+)(?:/.*)?$")


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


def _download_civitai_image(image_url: str, image_id: int, mime_type: Optional[str]) -> Path:
    response = requests.get(image_url, timeout=45)
    response.raise_for_status()

    suffix = _guess_suffix(mime_type)
    with tempfile.NamedTemporaryFile(
        mode="wb",
        prefix=f"temp_civitai_{image_id}_",
        suffix=suffix,
        dir=IMAGE_LIBRARY_PATH,
        delete=False,
    ) as temp_file:
        temp_file.write(response.content)
        return Path(temp_file.name)


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
        safe_name = Path(candidate).name
        if not safe_name:
            continue
        if not Path(safe_name).suffix:
            safe_name = f"{safe_name}{suffix}"
        return safe_name

    return f"civitai_{image_id}{suffix}"


def _resolve_civitai_image_target(api: CivitaiAPI, image_id: int) -> dict:
    basic_info = api.fetch_basic_info(image_id)
    generation_data = api.fetch_generation_data(image_id)

    if not basic_info and not generation_data:
        raise HTTPException(
            status_code=404,
            detail=f"Could not fetch CivitAI data for image {image_id}.",
        )

    image = CivitaiImage.from_single_image(
        basic_info=basic_info or {"id": image_id},
        generation_data=generation_data or {},
        api=api,
    )
    image_data = image.to_dict(include_full_url=True)

    image_url = image_data.get("url")
    if not image_url:
        raise HTTPException(
            status_code=502,
            detail=f"CivitAI image {image_id} did not include a downloadable URL.",
        )

    mime_type = (basic_info or {}).get("mimeType") if isinstance(basic_info, dict) else None
    preferred_name = (basic_info or {}).get("name") if isinstance(basic_info, dict) else None
    original_filename = _build_civitai_original_filename(
        image_id=image_id,
        preferred_name=preferred_name,
        image_url=image_url,
        mime_type=mime_type,
    )

    basic_user = basic_info.get("user", {}) if isinstance(basic_info, dict) else {}
    author_name = image_data.get("author")
    if not author_name and isinstance(basic_user, dict):
        author_name = basic_user.get("username")

    return {
        "image_id": image_id,
        "image_url": image_url,
        "mime_type": mime_type,
        "original_filename": original_filename,
        "artist_name": author_name,
        "source_url": f"https://civitai.com/images/{image_id}",
    }


def _import_single_civitai_image(api: CivitaiAPI, db: Session, image_id: int) -> dict:
    source_url = f"https://civitai.com/images/{image_id}"
    recovered_existing = False

    # Fast path: if this exact CivitAI source URL is already in library,
    # skip download and only attempt metadata repair.
    existing_by_source = (
        db.query(ImageModel)
        .filter(ImageModel.source_url == source_url)
        .order_by(ImageModel.id.desc())
        .first()
    )
    if existing_by_source is not None:
        existing_path = Path(IMAGE_LIBRARY_PATH) / str(existing_by_source.file_path)
        if _is_local_media_usable(existing_path, existing_by_source.mimetype):
            metadata_backfilled = _ensure_civitai_metadata_for_existing_image(
                db=db,
                image=existing_by_source,
                source_url=source_url,
            )
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

            # Existing source-url record is stale/corrupt: remove and re-download.
            _remove_local_image_record(db, existing_by_source)
            recovered_existing = True

    temp_path = None
    try:
        target = _resolve_civitai_image_target(api, image_id)
        temp_path = _download_civitai_image(
            image_url=target["image_url"],
            image_id=image_id,
            mime_type=target["mime_type"],
        )

        ingest_result = ImageCollection(db).ingest_uploaded_file(
            uploaded_file_path=temp_path,
            original_filename=target["original_filename"],
            artist_name=target["artist_name"],
            source_url=target["source_url"],
            license_id=None,
        )
        return {
            "image_id": image_id,
            "image_db_id": ingest_result.get("image_id"),
            "images_added": int(ingest_result.get("images_added", 0)),
            "images_skipped": int(ingest_result.get("images_skipped", 0)),
            "images_recovered": 1 if recovered_existing else 0,
            "json_files_created": int(ingest_result.get("json_files_created", 0)),
            "metadata_backfilled": False,
            "skip_reason": ingest_result.get("skip_reason"),
            "existing_image_id": ingest_result.get("existing_image_id"),
            "existing_file_hash": ingest_result.get("existing_file_hash"),
            "existing_file_path": ingest_result.get("existing_file_path"),
            "existing_source_url": ingest_result.get("existing_source_url"),
            "error": None,
        }
    except HTTPException as e:
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
    return warnings


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
    if mime.startswith("video/") or suffix in {".mp4", ".webm", ".mov", ".mkv"}:
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
    sidecar_has_civitai = isinstance(sidecar_data.get("civitai"), dict)
    db_has_civitai = isinstance(db_json.get("civitai"), dict)
    if sidecar_has_civitai and db_has_civitai:
        return False

    civitai_data = fetch_civitai_image_data(source_url)
    if not civitai_data:
        return False

    merged_json = dict(db_json)
    merged_json["civitai"] = civitai_data

    (
        db.query(ImageModel)
        .filter(ImageModel.id == image.id)
        .update(
            {
                ImageModel.json_metadata: merged_json,
                ImageModel.source_site: "civitai",
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


def _serialize_collection(collection: CollectionModel) -> dict:
    return {
        "id": collection.id,
        "name": collection.name,
        "source": collection.source,
        "civitai_collection_id": collection.civitai_collection_id,
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


def _get_or_create_collection(
    db: Session,
    name: str,
    source: str = "user",
    civitai_collection_id: Optional[int] = None,
) -> CollectionModel:
    normalized_name = _normalize_collection_name(name)
    existing = db.query(CollectionModel).filter(CollectionModel.name == normalized_name).first()
    if existing:
        if civitai_collection_id is not None and existing.civitai_collection_id is None:
            existing.civitai_collection_id = civitai_collection_id
        if source == "civitai" and existing.source == "user":
            existing.source = "civitai"
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


def create_initial_data():
    """
    Populates the database with initial tools and licenses if they don't already exist.
    """
    db = SessionLocal()
    try:
        # --- Create Initial Tools ---
        initial_tools = [
            {
                "name": "WD14 Tagger",
                "description": "A fine-tuned CLIP model for generating Danbooru-style tags.",
                "version": "v1.4",
            },
            {
                "name": "BLIP",
                "description": "A multimodal model for generating natural language image captions.",
                "version": "v1",
            },
            {
                "name": "GPT-4-Vision",
                "description": "OpenAI's multimodal model for advanced image analysis and description.",
                "version": "gpt-4-vision-preview",
            },
            {
                "name": "Custom Caption",
                "description": "A user-curated, manually entered description.",
                "version": "user",
            },
        ]
        for tool_data in initial_tools:
            existing_tool = (
                db.query(Tool).filter(Tool.name == tool_data["name"]).first()
            )
            if not existing_tool:
                db_tool = Tool(**tool_data)
                db.add(db_tool)
                print(f"  - Created tool: {db_tool.name}")

        # --- Create Initial Licenses ---
        initial_licenses = [
            {
                "name": "Creative Commons Attribution 4.0 International",
                "short_name": "CC BY 4.0",
                "url": "https://creativecommons.org/licenses/by/4.0/",
                "allows_commercial_use": True,
                "requires_attribution": True,
            },
            {
                "name": "Creative Commons Attribution-NonCommercial 4.0 International",
                "short_name": "CC BY-NC 4.0",
                "url": "https://creativecommons.org/licenses/by-nc/4.0/",
                "allows_commercial_use": False,
                "requires_attribution": True,
            },
            {
                "name": "Public Domain Dedication (CC0)",
                "short_name": "CC0 1.0",
                "url": "https://creativecommons.org/publicdomain/zero/1.0/",
                "allows_commercial_use": True,
                "requires_attribution": False,
            },
            {
                "name": "All Rights Reserved",
                "short_name": "ARR",
                "url": "",
                "allows_commercial_use": False,
                "requires_attribution": False,
            },
        ]
        for license_data in initial_licenses:
            existing_license = (
                db.query(License)
                .filter(License.short_name == license_data["short_name"])
                .first()
            )
            if not existing_license:
                db_license = License(**license_data)
                db.add(db_license)
                print(f"  - Created license: {db_license.short_name}")

        db.commit()
        print("Initial data population complete.")
    except Exception as e:
        print(f"An error occurred during initial data creation: {e}")
        db.rollback()
    finally:
        db.close()


# Define the lifespan context manager
@asynccontextmanager
async def lifespan(app: FastAPI):
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

    print("AtelierAI API is ready to go!")

    yield

    print("Shutting down AtelierAI API...")


# Pass the lifespan manager to the FastAPI app
app = FastAPI(title="AtelierAI API", version="0.1.0", lifespan=lifespan)

# Mount the static files directory
# This will serve files from the 'frontend' directory under the '/frontend/' URL path
app.mount("/frontend", StaticFiles(directory="frontend"), name="frontend")
# Expose processed images so the gallery can render thumbnails/full previews.
app.mount("/image_library", StaticFiles(directory=IMAGE_LIBRARY_PATH), name="image_library")


# Define a root endpoint to serve the main index.html file
@app.get("/")
async def read_index():
    return FileResponse("frontend/index.html")


@app.get("/images/", response_model=list[dict])
def read_images(
    skip: int = 0,
    limit: int = 10,
    sort_by: Literal["first_added", "last_added"] = "first_added",
    db: Session = Depends(get_db),
):
    """
    Returns a list of images with their associated artist and license info.
    Uses ImageData class to encapsulate and display image metadata.
    """
    # Use joinedload to efficiently fetch the related objects
    images_query = db.query(ImageModel).options(
        joinedload(ImageModel.artist),
        joinedload(ImageModel.license),
        joinedload(ImageModel.collections),
    )
    if sort_by == "last_added":
        images_query = images_query.order_by(ImageModel.id.desc())
    else:
        images_query = images_query.order_by(ImageModel.id.asc())

    images = images_query.offset(skip).limit(limit).all()

    # Build DB payload and merge raw sidecar JSON (source of authority) when present.
    response_payload: list[dict] = []
    for image in images:
        db_dict = ImageData.from_db_record(image).to_dict()

        sidecar_path = (Path(IMAGE_LIBRARY_PATH) / str(image.file_path)).with_suffix(
            ".json"
        )

        sidecar_dict: dict = {}
        if sidecar_path.exists():
            try:
                with open(sidecar_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    sidecar_dict = loaded
            except (OSError, json.JSONDecodeError):
                sidecar_dict = {}

        # Sidecar values override DB where present.
        merged = {**db_dict, **sidecar_dict}
        merged["collection_names"] = [c.name for c in image.collections]
        merged["collection_ids"] = [c.id for c in image.collections]
        response_payload.append(merged)

    return response_payload


@app.get("/images/state", response_model=dict)
def read_images_state(db: Session = Depends(get_db)):
    """Return lightweight image-library state for polling-based UI refresh logic."""
    total_count = db.query(ImageModel).count()
    latest_row = db.query(ImageModel.id).order_by(ImageModel.id.desc()).first()
    latest_id = int(latest_row[0]) if latest_row else 0
    return {
        "count": total_count,
        "latest_id": latest_id,
    }


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
        sidecar_path = (Path(IMAGE_LIBRARY_PATH) / str(image.file_path)).with_suffix(".json")
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
        raise HTTPException(status_code=500, detail=f"Failed to update image metadata: {e}")

    return {
        "message": "Image metadata updated",
        "file_hash": image.file_hash,
        "source_url": image.source_url,
        "source_site": image.source_site,
        "artist_id": image.artist_id,
        "artist_name": image.artist.name if image.artist is not None else None,
        "artist_profile": sidecar_artist_profile,
    }


# Also, update the /artists/ endpoint to return the new artist objects
@app.get("/artists/", response_model=List[dict])
def get_artists(db: Session = Depends(get_db)):
    """Returns a list of all artists."""
    artists = db.query(Artist).all()
    return [
        {"id": artist.id, "name": artist.name, "nickname": artist.nickname}
        for artist in artists
    ]


@app.get("/collections/", response_model=List[dict])
def get_collections(db: Session = Depends(get_db)):
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


@app.get("/licenses/", response_model=List[dict])
def get_licenses(db: Session = Depends(get_db)):
    """Returns a list of all available licenses."""
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

        except ValueError as e:
            errors.append(str(e))
        except Exception as e:
            errors.append(f"Could not process {file.filename}: {e}")
        finally:
            # Clean up the temporary file
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)

    db.commit()
    runtime_warnings = _get_runtime_warnings()
    return {
        "message": "Upload complete.",
        "images_added": images_added,
        "images_skipped": images_skipped,
        "json_files_created": json_files_created,
        "errors": errors,
        "warnings": runtime_warnings,
    }


@app.post("/import_civitai/")
def import_civitai_images(payload: CivitaiImportRequest, db: Session = Depends(get_db)):
    """Import CivitAI images by image URL/ID or collection URL/ID."""
    value = (payload.value or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="Import value is required.")

    if payload.limit is not None and payload.limit <= 0:
        raise HTTPException(status_code=400, detail="limit must be greater than 0.")

    try:
        api = CivitaiAPI.get_instance()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"CivitAI API initialization failed: {e}")

    results: list[dict] = []

    if payload.import_type == "image":
        image_id = _parse_civitai_image_id(value)
        results.append(_import_single_civitai_image(api, db, image_id))
    else:
        collection_id = _parse_civitai_collection_id(value)
        civitai_collection_name = _fetch_civitai_collection_name(api, collection_id)
        local_collection = _get_or_create_collection(
            db,
            name=civitai_collection_name,
            source="civitai",
            civitai_collection_id=collection_id,
        )

        scraper = CivitaiPrivateScraper(auto_authenticate=True)
        collection_items = scraper.fetch_collection_items(
            collection_id=collection_id,
            limit=payload.limit,
        )
        if not collection_items:
            raise HTTPException(
                status_code=404,
                detail=f"No images found for CivitAI collection {collection_id}.",
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

        if payload.limit is not None:
            image_ids = image_ids[: payload.limit]

        for image_id in image_ids:
            result = _import_single_civitai_image(api, db, image_id)
            image_db_id = result.get("image_db_id")
            if isinstance(image_db_id, int):
                _ensure_image_in_collection(db, image_db_id, local_collection.id)
            results.append(result)

    images_added = sum(int(r.get("images_added", 0)) for r in results)
    images_skipped = sum(int(r.get("images_skipped", 0)) for r in results)
    images_recovered = sum(int(r.get("images_recovered", 0)) for r in results)
    json_files_created = sum(int(r.get("json_files_created", 0)) for r in results)
    errors = [
        f"Image {r.get('image_id')}: {r['error']}"
        for r in results
        if r.get("error")
    ]

    db.commit()
    runtime_warnings = _get_runtime_warnings()

    return {
        "message": "CivitAI import complete.",
        "import_type": payload.import_type,
        "local_collection": (
            _serialize_collection(local_collection)
            if payload.import_type == "collection"
            else None
        ),
        "requested": len(results),
        "images_added": images_added,
        "images_skipped": images_skipped,
        "images_recovered": images_recovered,
        "json_files_created": json_files_created,
        "errors": errors,
        "warnings": runtime_warnings,
        "results": results,
    }
