# main.py
import os
import json
from pathlib import Path
from fastapi import FastAPI, Depends, HTTPException, File, UploadFile, Form
from typing import List, Optional
from contextlib import asynccontextmanager
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
    Tool,
    License,
    Artist,
    SchemaVersion,
)  # Import the specific classes we need

from image_collection import ImageCollection
from image_data import ImageData
from image_processor import ImageProcessor
from civitai_enrichment import is_civitai_image_url


class ScanRequest(BaseModel):
    folder_path: str


class ImageUpdateRequest(BaseModel):
    source_url: Optional[str] = None
    artist_name: Optional[str] = None
    artist_profile: Optional[str] = None


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
def read_images(skip: int = 0, limit: int = 10, db: Session = Depends(get_db)):
    """
    Returns a list of images with their associated artist and license info.
    Uses ImageData class to encapsulate and display image metadata.
    """
    # Use joinedload to efficiently fetch the related objects
    images_query = db.query(ImageModel).options(
        joinedload(ImageModel.artist), joinedload(ImageModel.license)
    )
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
        response_payload.append({**db_dict, **sidecar_dict})

    return response_payload


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
    return {
        "message": "Upload complete.",
        "images_added": images_added,
        "images_skipped": images_skipped,
        "json_files_created": json_files_created,
        "errors": errors,
    }
