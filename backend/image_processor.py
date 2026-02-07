import os
import shutil
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Optional

from PIL import Image
from PIL.ExifTags import TAGS
from sqlalchemy.orm import Session

from models import ImageModel, Artist

# A set of allowed image extensions for easy lookup
ALLOWED_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp', '.jfif'}

class ImageProcessor:
    """A class to handle all operations for a single image file."""

    def __init__(self, file_path: str, db: Session, library_path: str):
        self.db = db
        self.library_path = library_path  # <-- Store the injected dependency
        self.original_path = Path(file_path)
        self.original_filename = self.original_path.name

        # --- Initialize state ---
        self.file_hash: Optional[str] = None
        self.width: int = 0
        self.height: int = 0
        self.date_created = datetime.now()
        self.date_modified = datetime.now()
        self.exif_data: dict = {}
        self.file_size: int = 0
        self.db_record: Optional[ImageModel] = None

        # --- Perform initial processing ---
        if not self.original_path.is_file() or not self._is_valid_image():
            print(f"File is not a valid image: {self.original_filename}")
            raise ValueError(f"File is not a valid image: {self.original_filename}")

        self._process_file()

    def _is_valid_image(self) -> bool:
        """Checks if the file has a valid image extension."""
        return self.original_path.suffix.lower() in ALLOWED_EXTENSIONS

    def _process_file(self):
        """Calculates hash, gets metadata, and size."""
        self.file_hash = self._calculate_hash()
        self.width, self.height, self.exif_data = self._get_metadata()
        self.file_size = self.original_path.stat().st_size

    def _calculate_hash(self) -> str:
        """Calculates the SHA256 hash of the image file."""
        h = hashlib.sha256()
        with open(self.original_path, "rb") as f:
            while chunk := f.read(8192):
                h.update(chunk)
        return h.hexdigest()

    def _get_metadata(self) -> tuple[int, int, dict]:
        """Extracts metadata from the image file."""
        try:
            with Image.open(self.original_path) as img:
                width, height = img.size
                exif_data_raw = img.getexif()
                if exif_data_raw:
                    exif_data = {TAGS.get(tag, tag): value for tag, value in exif_data_raw.items() if isinstance(value, (str, int, float, list, tuple, dict))}
                else:
                    exif_data = {}
            return width, height, exif_data
        except Exception:
            return 0, 0, {}

    def find_in_database(self) -> Optional[ImageModel]:
        """Finds the image in the database using its hash."""
        self.db_record = self.db.query(ImageModel).filter(ImageModel.file_hash == self.file_hash).first()
        return self.db_record

    def save_to_library(self) -> str:
        """Renames/moves the image to the library using its hash as the filename."""
        final_filename = f"{self.file_hash}{self.original_path.suffix.lower()}"
        final_absolute_path = Path(self.library_path) / final_filename

        # If the file is already in the right place with the right name, do nothing
        if self.original_path.resolve() == final_absolute_path.resolve():
            return final_filename

        # Move the file to its final destination
        shutil.move(self.original_path, final_absolute_path)
        return final_filename

    def delete_from_filesystem(self):
        """Deletes the image file from the filesystem."""
        if self.original_path.exists():
            os.remove(self.original_path)

    def create_database_record(
        self,
        relative_filepath: str,
        original_filename: Optional[str] = None,
        artist_obj: Optional[Artist] = None,
        source_url: Optional[str] = None,
        license_id: Optional[int] = None,
    ) -> ImageModel:
        """Creates a new ImageModel record."""
        absolute_path = Path(self.library_path) / relative_filepath
        stat = os.stat(absolute_path)

        display_name = original_filename or self.original_filename

        self.db_record = ImageModel(
            file_path=relative_filepath,
            file_name=display_name,
            file_hash=self.file_hash,
            file_size=stat.st_size,
            width=self.width,
            height=self.height,
            date_created=datetime.fromtimestamp(stat.st_ctime),
            date_modified=datetime.fromtimestamp(stat.st_mtime),
            artist_id=artist_obj.id if artist_obj else None,
            source_url=source_url,
            license_id=license_id if license_id else None,
            exif_data=self.exif_data
        )
        return self.db_record

    @staticmethod
    def find_or_create_artist(db: Session, artist_name: str) -> Artist:
        """Finds an artist by name or creates a new one if it doesn't exist."""
        artist_obj = db.query(Artist).filter(Artist.name == artist_name).first()
        if not artist_obj:
            artist_obj = Artist(name=artist_name)
            db.add(artist_obj)
            db.commit()
            db.refresh(artist_obj)
        return artist_obj
