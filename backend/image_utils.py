import os
import hashlib
from datetime import datetime
from typing import Optional
from PIL import Image
from PIL.ExifTags import TAGS
from sqlalchemy.orm import Session

from models import ImageModel, Artist

# A set of allowed image extensions for easy lookup
ALLOWED_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp'}

def is_valid_image(file_path: Optional[str]) -> bool:
    """Checks if a file has a valid image extension."""
    return os.path.splitext(file_path or "")[1].lower() in ALLOWED_EXTENSIONS

def calculate_file_hash(file_path: str) -> str:
    """Calculates the SHA256 hash of a file."""
    h = hashlib.sha256()
    with open(file_path, "rb") as file:
        while chunk := file.read(8192):
            h.update(chunk)
    return h.hexdigest()

def get_image_metadata(file_path: str) -> tuple:
    """Extracts metadata from an image file.

    Returns:
        A tuple containing (width, height, exif_data_dict).
    """
    try:
        with Image.open(file_path) as img:
            width, height = img.size
            exif_data_raw = img.getexif()
            if exif_data_raw:
                exif_data = {TAGS.get(tag, tag): value for tag, value in exif_data_raw.items() if isinstance(value, (str, int, float, list, tuple, dict))}
            else:
                exif_data = {}
        return width, height, exif_data
    except Exception as e:
        print(f"Could not read metadata from {file_path}: {e}")
        return 0, 0, {}

def find_image_by_hash(db: Session, file_hash: str) -> ImageModel | None:
    """Finds an image in the database by its hash."""
    return db.query(ImageModel).filter(ImageModel.file_hash == file_hash).first()

def create_image_record(
    db: Session,
    file_hash: str,
    original_filename: str,
    final_filepath: str,
    file_size: int,
    width: int,
    height: int,
    exif_data: dict,
    artist_obj: Artist | None = None,
    source_url: str | None = None,
    license_id: int | None = None,
) -> ImageModel:
    """Creates and returns a new ImageModel record."""
    # Get file stats for dates
    stat = os.stat(final_filepath)

    new_image = ImageModel(
        file_path=final_filepath,
        file_name=original_filename,
        file_hash=file_hash,
        file_size=file_size,
        width=width,
        height=height,
        date_created=datetime.fromtimestamp(stat.st_ctime),
        date_modified=datetime.fromtimestamp(stat.st_mtime),
        artist_id=artist_obj.id if artist_obj else None,
        source_url=source_url,
        license_id=license_id if license_id else None,
        exif_data=exif_data
    )
    return new_image

def find_or_create_artist(db: Session, artist_name: str) -> Artist:
    """Finds an artist by name or creates a new one if it doesn't exist."""
    artist_obj = db.query(Artist).filter(Artist.name == artist_name).first()
    if not artist_obj:
        artist_obj = Artist(name=artist_name)
        db.add(artist_obj)
        db.commit()
        db.refresh(artist_obj)
    return artist_obj
