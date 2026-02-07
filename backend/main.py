# main.py
import os
import hashlib
from fastapi import FastAPI, Depends, HTTPException, File, UploadFile, Form
from typing import List, Optional
from PIL import Image
from PIL.ExifTags import TAGS
from contextlib import asynccontextmanager
from sqlalchemy.orm import Session, joinedload
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

from config import IMAGE_LIBRARY_PATH, CURRENT_SCHEMA_VERSION

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
    SchemaVersion
)   # Import the specific classes we need

from image_processor import ImageProcessor
from image_collection import ImageCollection


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
    """
    Returns a list of images with their associated artist and license info.
    """
    # Use joinedload to efficiently fetch the related objects
    images_query = db.query(ImageModel).options(
        joinedload(ImageModel.artist),
        joinedload(ImageModel.license)
    )
    images = images_query.offset(skip).limit(limit).all()

    # The logic is now just a simple list comprehension
    return [image.to_dict() for image in images]


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
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {e}")


@app.post("/upload_images/")
async def upload_images(
    # This will be a list of uploaded files

    files: List[UploadFile] = File(...),
    # These are the optional batch metadata fields

    artist_name: Optional[str] = Form(None),
    source_url: Optional[str] = Form(None),
    license_id: Optional[int] = Form(None),
    db: Session = Depends(get_db)
):
    """
    Uploads one or more images, saves them to the library, and adds them to the database.
    This version is robust against filesystem/database mismatches.
    """

    images_added = 0
    images_skipped = 0
    errors = []

    # Ensure the library directory exists
    os.makedirs(IMAGE_LIBRARY_PATH, exist_ok=True)

    for file in files:
        temp_path = None
        try:
            # 1. Save uploaded content to a temporary file
            contents = await file.read()
            temp_path = os.path.join(IMAGE_LIBRARY_PATH, f"temp_{file.filename}")
            with open(temp_path, "wb") as f:
                f.write(contents)

            # 2. Create a processor for the image
            processor = ImageProcessor(temp_path, db, IMAGE_LIBRARY_PATH)

            # 3. Check for duplicates
            if processor.find_in_database():
                images_skipped += 1
                continue

            # 4. Handle the artist
            artist_obj = None
            if artist_name:
                artist_obj = ImageProcessor.find_or_create_artist(db, artist_name)

            # 5. Save to library and create DB record
            final_path = processor.save_to_library()
            new_image = processor.create_database_record(
                str(final_path), file.filename, artist_obj, source_url, license_id
            )
            db.add(new_image)
            images_added += 1

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
        "errors": errors
    }
