from pathlib import Path
from typing import List, Dict, Any

from sqlalchemy.orm import Session
from models import ImageModel
from image_processor import ImageProcessor
from config import IMAGE_LIBRARY_PATH

class ImageCollection:
    """A class to manage and process a collection of images in the library."""

    def __init__(self, db: Session):
        self.db = db
        self.library_path = Path(IMAGE_LIBRARY_PATH)
        self.results: Dict[str, int] = {
            "images_scanned": 0,
            "images_added": 0,
            "files_renamed": 0,
            "files_removed": 0,
            "records_removed": 0,
            "errors": 0
        }
        self.error_messages: List[str] = []

    def _cleanup_orphaned_records(self):
        """Finds and removes database records for files that no longer exist on the filesystem."""
        print("Starting cleanup of orphaned database records...")
        all_db_images = self.db.query(ImageModel.file_path, ImageModel.id).all()
        existing_files_on_disk = {str(p) for p in self.library_path.iterdir() if p.is_file()}

        orphaned_ids = []
        for relative_path, image_id in all_db_images:
            absolute_path_from_db = str(self.library_path / relative_path)
            if absolute_path_from_db not in existing_files_on_disk:
                orphaned_ids.append(image_id)

        if orphaned_ids:
            print(f"Found {len(orphaned_ids)} orphaned records. Deleting them...")
            self.db.query(ImageModel).filter(ImageModel.id.in_(orphaned_ids)).delete(synchronize_session=False)
            self.results["records_removed"] = len(orphaned_ids)
            self.db.commit()
            print("Cleanup complete.")
        else:
            print("No orphaned records found.")

    def _process_library_files(self):
        """Scans the library, imports new files, and removes duplicates."""
        print("Starting filesystem scan...")
        for image_file in self.library_path.iterdir():
            if not image_file.is_file():
                continue

            try:
                processor = ImageProcessor(str(image_file), self.db, str(self.library_path))
                self.results["images_scanned"] += 1
                file_hash = processor.file_hash
                expected_filename = f"{file_hash}{image_file.suffix.lower()}"
                # expected_filepath = self.library_path / expected_filename
                db_record = processor.find_in_database()

                if db_record:
                    # File is a known entity.
                    if image_file.name != expected_filename:
                        # It's a duplicate.
                        print(f"Removing duplicate file: {image_file.name}")
                        processor.delete_from_filesystem()
                        self.results["files_removed"] += 1
                else:
                    # File is new and needs to be imported.
                    if image_file.name != expected_filename:
                        print(f"Renaming new file '{image_file.name}' to '{expected_filename}'")
                        processor.save_to_library()
                        self.results["files_renamed"] += 1
                        relative_path = expected_filename
                    else:
                        relative_path = image_file.name

                    new_image = processor.create_database_record(
                        relative_filepath=relative_path,
                        original_filename=processor.original_filename
                    )
                    self.db.add(new_image)
                    self.results["images_added"] += 1

            except Exception as e:
                self.error_messages.append(f"Could not process {image_file.name}: {e}")
                self.results["errors"] += 1

    def scan(self) -> Dict[str, Any]:
        """
        Performs a full scan and synchronization of the image library.
        This is the main public method for the class.
        """
        if not self.library_path.is_dir():
            raise FileNotFoundError(f"Image library directory not found: {self.library_path}")

        self._cleanup_orphaned_records()
        self._process_library_files()

        # Final commit for any new records added during processing
        self.db.commit()

        return {
            "message": "Library scan and synchronization complete.",
            **self.results,
            "errors": self.error_messages
        }

    # --- Magic methods for a more Pythonic feel ---

    def __len__(self) -> int:
        """Returns the total number of images in the database."""
        return self.db.query(ImageModel).count()

    def __iter__(self):
        """Allows iteration over all ImageModel objects in the database."""
        return (img for img in self.db.query(ImageModel).all())
