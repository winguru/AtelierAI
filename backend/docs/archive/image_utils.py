import os
import hashlib
from datetime import datetime
from typing import Optional
from PIL import Image
from PIL.ExifTags import TAGS
from sqlalchemy.orm import Session

from models import ImageModel, Artist

# A set of allowed image extensions for easy lookup
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


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


def get_image_metadata(file_path: str) -> tuple[int, int, str | None, dict]:
    """Extracts metadata from an image file.

    Returns:
        A tuple containing (width, height, mimetype, exif_data_dict).
    """
    try:
        with Image.open(file_path) as img:
            width, height = img.size
            mimetype = Image.MIME.get(img.format) if img.format else None
            exif_data_raw = img.getexif()
            exif_data = {}

            if exif_data_raw:
                # First pass: get standard EXIF tags
                for tag, value in exif_data_raw.items():
                    tag_name = TAGS.get(tag, tag)
                    # Decode bytes to string if needed
                    if isinstance(value, bytes):
                        try:
                            value = value.decode('utf-8', errors='replace')
                        except:
                            value = str(value)
                    if isinstance(value, (str, int, float, list, tuple, dict)):
                        exif_data[tag_name] = value
                print(f"First pass: Extracted EXIF data from {file_path}:")

                for key, val in exif_data.items():
                    print(f"  {key}: {val}")

                # Second pass: get IFD (Image File Directory) data including ExifOffset
                if hasattr(exif_data_raw, 'get_ifd_list'):
                    for ifd_id in exif_data_raw.get_ifd_list():
                        ifd_data = exif_data_raw.get_ifd(ifd_id)
                        for tag, value in ifd_data.items():
                            tag_name = TAGS.get(tag, tag)
                            # Decode bytes to string if needed
                            if isinstance(value, bytes):
                                try:
                                    value = value.decode('utf-8', errors='replace')
                                except:
                                    value = str(value)
                            if isinstance(value, (str, int, float, list, tuple, dict)):
                                # Prefix with IFD name to avoid collisions
                                exif_data[f"{ifd_id.name}_{tag_name}"] = value
                print(f"Second pass: Extracted EXIF data from {file_path}:")

                for key, val in exif_data.items():
                    print(f"  {key}: {val}")

        return width, height, mimetype, exif_data
    except Exception as e:
        print(f"Could not read metadata from {file_path}: {e}")
        return 0, 0, None, {}


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
    mimetype: str | None,
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
        mimetype=mimetype,
        date_created=datetime.fromtimestamp(stat.st_ctime),
        date_modified=datetime.fromtimestamp(stat.st_mtime),
        artist_id=artist_obj.id if artist_obj else None,
        source_url=source_url,
        license_id=license_id if license_id else None,
        exif_data=exif_data,
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
