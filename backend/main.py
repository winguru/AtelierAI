# main.py
import os
import hashlib
import json
from pathlib import Path
from datetime import datetime
from fastapi import FastAPI, Depends, HTTPException, File, UploadFile, Form
from typing import List, Optional
from PIL import Image
from PIL.ExifTags import TAGS
from contextlib import asynccontextmanager
from sqlalchemy.orm import Session, joinedload
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

# Use absolute imports for consistency with our project structure
from database import (
    SessionLocal,
    Base,
    engine,
    get_db,
    test_db_connection,
    IMAGE_LIBRARY_PATH,
)
from models import (
    ImageModel,
    Tool,
    License,
    AnalysisData,
    Dataset,
    Artist,
    SchemaVersion
)   # Import the specific classes we need

class ScanRequest(BaseModel):
    folder_path: str


def get_file_hash(filepath: str) -> str:
    """Returns the SHA256 hash of a file."""
    h = hashlib.sha256()
    with open(filepath, "rb") as file:
        while chunk := file.read(8192):
            h.update(chunk)
    return h.hexdigest()


def get_exif_data(image_path: str) -> dict:
    """Extracts EXIF data from an image and returns it as a JSON-serializable dict."""
    try:
        image = Image.open(image_path)
        exif_data_raw = image.getexif()
        if exif_data_raw:
            # Filter out non-serializable data and convert tag IDs to names
            return {
                TAGS.get(tag, tag): value
                for tag, value in exif_data_raw.items()
                if isinstance(value, (str, int, float, list, tuple, dict))
            }
    except Exception:
        # If any error occurs during EXIF extraction, return an empty dict
        pass
    return {}


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


# --- DEFINE YOUR CURRENT SCHEMA VERSION ---
CURRENT_SCHEMA_VERSION = "1.1"  # Increment this when you make schema changes


# Define the lifespan context manager
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("Starting AtelierAI API...")

    # --- NEW SCHEMA VERSIONING LOGIC ---
    # Check if the database file exists at all
    db_file_path = os.getenv("DATABASE_URL", "sqlite:///./data/image_db.sqlite").replace("sqlite:///", "")
    db_exists = os.path.exists(db_file_path)

    if db_exists:
        # Check the version
        try:
            with engine.connect() as connection:
                version_result = connection.execute(SchemaVersion.__table__.select()).scalar_one_or_none()
                if version_result != CURRENT_SCHEMA_VERSION:
                    print(f"⚠️ Schema version mismatch. Found {version_result}, expected {CURRENT_SCHEMA_VERSION}.")
                    print("   Recreating database...")
                    os.remove(db_file_path)
                    db_exists = False
                else:
                    print("✅ Database schema is up to date.")
        except Exception as e:
            print(f"⚠️ Could not check schema version (table might not exist): {e}")
            print("   Recreating database to be safe...")
            os.remove(db_file_path)
            db_exists = False

    # Create tables and initial data if the DB doesn't exist
    if not db_exists:
        print("Creating new database and initial data...")
        Base.metadata.create_all(bind=engine, checkfirst=True)

        # Create a record for the new schema version
        with SessionLocal() as db:
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


# Define a root endpoint to serve the main index.html file
@app.get("/")
async def read_index():
    return FileResponse("frontend/index.html")


@app.get("/images/", response_model=list[dict])
def read_images(skip: int = 0, limit: int = 10, db: Session = Depends(get_db)):
    # Use joinedload for both artist and license
    images_query = db.query(ImageModel).options(joinedload(ImageModel.artist), joinedload(ImageModel.license))
    images = images_query.offset(skip).limit(limit).all()

    result = []
    for img in images:
        # Safely access the artist information
        artist_info = None
        if img.artist:
            artist_info = {
                "id": img.artist.id,
                "name": img.artist.name,
                "nickname": img.artist.nickname
            }

        # Safely access the license information
        license_info = None
        if img.license:
            license_info = {
                "id": img.license.id,
                "short_name": img.license.short_name,
                "name": img.license.name
            }

        result.append({
            "id": img.id,
            "file_name": img.file_name,
            "file_hash": img.file_hash,
            "artist": artist_info,        # Changed from author_name
            "license": license_info
        })
    return result


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

@app.post("/scan_folder/")
def scan_folder(request: ScanRequest, db: Session = Depends(get_db)):
    """
    Scans a given folder for images, adds them to the database if they are new.
    """
    folder_path = request.folder_path
    if not os.path.isdir(folder_path):
        raise HTTPException(
            status_code=400, detail=f"Invalid folder path: {folder_path}"
        )

    images_added = 0
    images_skipped = 0

    # Use pathlib for easier path manipulation
    path = Path(folder_path)
    for image_file in path.rglob("*"):  # rglob for recursive search
        if image_file.is_file() and image_file.suffix.lower() in [
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
        ]:
            try:
                file_hash = get_file_hash(str(image_file))

                # Check for duplicates by hash
                if (
                    db.query(ImageModel)
                    .filter(ImageModel.file_hash == file_hash)
                    .first()
                ):
                    images_skipped += 1
                    continue

                # Get image metadata
                with Image.open(image_file) as img:
                    width, height = img.size

                stat = image_file.stat()

                # Create new image record
                new_image = ImageModel(
                    file_path=str(image_file),
                    file_name=image_file.name,
                    file_hash=file_hash,
                    file_size=stat.st_size,
                    width=width,
                    height=height,
                    date_created=datetime.fromtimestamp(stat.st_ctime),
                    date_modified=datetime.fromtimestamp(stat.st_mtime),
                    exif_data=get_exif_data(str(image_file)),
                )
                db.add(new_image)
                images_added += 1
            except Exception as e:
                print(f"Could not process {image_file.name}: {e}")

    db.commit()

    return {
        "message": f"Scan complete. Processed {images_added + images_skipped} images.",
        "images_added": images_added,
        "images_skipped": images_skipped,
    }


@app.post("/rescan_library/")
def rescan_library(db: Session = Depends(get_db)):
    """
    Scans the pre-configured image library folder for new images.
    """
    if not os.path.isdir(IMAGE_LIBRARY_PATH):
        raise HTTPException(
            status_code=500,
            detail=f"Image library directory not found on server: {IMAGE_LIBRARY_PATH}",
        )

    images_added = 0
    images_skipped = 0

    path = Path(IMAGE_LIBRARY_PATH)
    # We don't need rglob here, just the top-level of the library folder
    for image_file in path.iterdir():
        if image_file.is_file() and image_file.suffix.lower() in [
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
        ]:
            try:
                file_hash = get_file_hash(str(image_file))

                if (
                    db.query(ImageModel)
                    .filter(ImageModel.file_hash == file_hash)
                    .first()
                ):
                    images_skipped += 1
                    continue

                with Image.open(image_file) as img:
                    width, height = img.size

                stat = image_file.stat()

                new_image = ImageModel(
                    file_path=str(image_file),
                    file_name=image_file.name,
                    file_hash=file_hash,
                    file_size=stat.st_size,
                    width=width,
                    height=height,
                    date_created=datetime.fromtimestamp(stat.st_ctime),
                    date_modified=datetime.fromtimestamp(stat.st_mtime),
                    exif_data=get_exif_data(str(image_file)),
                )
                db.add(new_image)
                images_added += 1
            except Exception as e:
                print(f"Could not process {image_file.name}: {e}")

    db.commit()

    return {
        "message": f"Library scan complete. Processed {images_added + images_skipped} images.",
        "images_added": images_added,
        "images_skipped": images_skipped,
        "library_path": IMAGE_LIBRARY_PATH,
    }


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
    """
    images_added = 0
    images_skipped = 0
    errors = []

    # Ensure the library directory exists
    os.makedirs(IMAGE_LIBRARY_PATH, exist_ok=True)

    for file in files:
        try:
            # Read file content into memory
            contents = await file.read()

            # 1. Get the file hash to check for duplicates and use as a filename
            file_hash = hashlib.sha256(contents).hexdigest()

            # Check if this image (by hash) already exists in the database
            if db.query(ImageModel).filter(ImageModel.file_hash == file_hash).first():
                images_skipped += 1
                continue

            # 2. Determine the file extension and create a safe filename
            filename = file.filename or "file"
            file_extension = os.path.splitext(filename)[1].lower()
            if file_extension not in [".png", ".jpg", ".jpeg", ".webp"]:
                errors.append(f"Skipped {file.filename}: Unsupported file type.")
                continue

            # Use the hash as the filename to ensure uniqueness and prevent overwrites
            safe_filename = f"{file_hash}{file_extension}"
            destination_path = os.path.join(IMAGE_LIBRARY_PATH, safe_filename)

            # 3. Save the file to the image library
            with open(destination_path, "wb") as f:
                f.write(contents)

            # 4. Get image metadata (width, height, EXIF)
            with Image.open(destination_path) as img:
                width, height = img.size
                exif_data = get_exif_data(destination_path)

            # 5. Handle the artist
            artist_obj = None
            if artist_name:
                # Check if the artist already exists
                artist_obj = db.query(Artist).filter(Artist.name == artist_name).first()
                if not artist_obj:
                    # If not, create a new one
                    artist_obj = Artist(name=artist_name)
                    db.add(artist_obj)
                    db.commit()  # Commit to get the new artist's ID
                    db.refresh(artist_obj)  # Refresh to get the new ID from the DB

            # 6. Create the database record
            new_image = ImageModel(
                file_path=destination_path,
                file_name=file.filename,  # Keep original filename
                file_hash=file_hash,
                file_size=len(contents),
                width=width,
                height=height,
                date_created=datetime.now(),
                date_modified=datetime.now(),
                # Populate with batch metadata
                artist_id=artist_obj.id if artist_obj else None,
                license_id=license_id if license_id else None,
            )
            db.add(new_image)
            # Note: We commit once at the end of the loop for performance
            images_added += 1

        except Exception as e:
            errors.append(f"Could not process {file.filename}: {e}")

    # Commit all new image records to the database
    db.commit()

    return {
        "message": "Upload complete.",
        "images_added": images_added,
        "images_skipped": images_skipped,
        "errors": errors,
    }
