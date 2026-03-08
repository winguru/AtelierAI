from pathlib import Path
from typing import List, Dict, Any, Optional

from sqlalchemy.orm import Session
from models import ImageModel
from image_processor import ImageProcessor
from config import IMAGE_LIBRARY_PATH
from image_data import ImageData


class ImageCollection:
    """A class to manage and process a collection of images in the library."""

    def __init__(self, db: Session):
        self.db = db
        self.library_path = Path(IMAGE_LIBRARY_PATH)
        self.results: Dict[str, Any] = {
            "images_scanned": 0,
            "images_added": 0,
            "files_renamed": 0,
            "files_removed": 0,
            "records_removed": 0,
            # JSON-related tracking
            "json_files_scanned": 0,
            "json_files_renamed": 0,
            "json_files_merged": 0,
            "json_db_entries_added": 0,
            "json_orphans_removed": 0,
            "json_files_created": 0,
            "json_db_differences": 0,  # Track JSON values differing from DB
            "json_db_records_updated": 0,  # Track DB records updated from JSON
            # Error count (actual error messages in self.error_messages)
            "errors": 0,
        }
        self.error_messages: List[str] = []

    def _cleanup_orphaned_records(self):
        """Finds and removes database records for files that no longer exist on the filesystem."""
        print("Starting cleanup of orphaned database records...")
        all_db_images = self.db.query(ImageModel.file_path, ImageModel.id).all()
        # Get all files on disk, excluding JSON metadata files (we only care about image files)
        existing_files_on_disk = {
            str(p)
            for p in self.library_path.iterdir()
            if p.is_file() and not p.name.endswith(".json")
        }

        orphaned_ids = []
        orphaned_json_paths = []
        for relative_path, image_id in all_db_images:
            absolute_path_from_db = str(self.library_path / relative_path)
            if absolute_path_from_db not in existing_files_on_disk:
                orphaned_ids.append(image_id)
                # Also track the JSON file for cleanup
                json_path = Path(absolute_path_from_db).with_suffix(".json")
                if json_path.exists():
                    orphaned_json_paths.append(json_path)

        if orphaned_ids:
            print(f"Found {len(orphaned_ids)} orphaned records. Deleting them...")
            self.db.query(ImageModel).filter(ImageModel.id.in_(orphaned_ids)).delete(
                synchronize_session=False
            )
            self.results["records_removed"] = len(orphaned_ids)
            self.db.commit()

            # Clean up orphaned JSON files
            for json_path in orphaned_json_paths:
                try:
                    json_path.unlink()
                    print(f"Removed orphaned JSON file: {json_path.name}")
                    self.results["json_orphans_removed"] += 1
                except Exception as e:
                    print(f"Warning: Could not remove JSON file {json_path}: {e}")
                    self.error_messages.append(
                        f"Could not remove JSON file {json_path}: {e}"
                    )

            print("Cleanup complete.")
        else:
            print("No orphaned records found.")

    def _get_file_extension_from_metadata(self, json_metadata: Dict[str, Any]) -> str:
        """Determines file extension from JSON metadata."""
        mimetype = json_metadata.get("mimetype")
        file_extension = None
        if mimetype:
            file_extension = ImageProcessor.mime_to_extension(mimetype)

        if not file_extension:
            file_name = json_metadata.get("file_name", "")
            if "." in file_name:
                file_extension = "." + file_name.rsplit(".", 1)[1].lower()
            else:
                file_extension = ".jpg"

        return file_extension

    def _handle_json_file_rename_or_merge(
        self,
        json_file: Path,
        file_hash: str,
        image_data: ImageData,
        expected_json_path: Path,
        processed_json_files: set,
    ):
        """Handles renaming or merging of misnamed JSON files."""
        expected_json_filename = f"{file_hash}.json"

        if json_file.name != expected_json_filename:
            if expected_json_path.exists():
                print(
                    f"Merging JSON metadata: '{json_file.name}' into '{expected_json_filename}'"
                )
                # Load expected JSON directly as ImageData
                expected_data = ImageData.from_json_file(expected_json_path)

                db_record = (
                    self.db.query(ImageModel)
                    .filter(ImageModel.file_hash == file_hash)
                    .first()
                )

                # Merge in priority order: database -> expected JSON -> new JSON (image_data)
                if db_record:
                    db_data = ImageData.from_db_record(db_record)
                    merged_data = db_data + expected_data + image_data
                else:
                    merged_data = expected_data + image_data

                # Save using ImageData's to_json() method
                with open(expected_json_path, "w", encoding="utf-8") as f:
                    f.write(merged_data.to_json(indent=2))

                json_file.unlink()
                print(f"Removed old JSON file: {json_file.name}")
                self.results["files_removed"] += 1
                self.results["json_files_merged"] += 1
                processed_json_files.add(str(json_file))
            else:
                print(
                    f"Renaming JSON file '{json_file.name}' to '{expected_json_filename}'"
                )
                json_file.rename(expected_json_path)
                self.results["files_renamed"] += 1
                self.results["json_files_renamed"] += 1
                processed_json_files.add(str(json_file))
                processed_json_files.add(str(expected_json_path))

    def _create_db_record_from_json(
        self, file_hash: str, image_data: ImageData, json_file: Path
    ):
        """
        Creates a database record from ImageData and persists it to the database.

        This method takes ImageData for an image and creates an ImageModel database record.
        It handles file extension resolution through two methods:
        1. If a mimetype is provided, converts it to the appropriate file extension
        2. Otherwise, extracts the extension from the file_name or defaults to .jpg

        The method constructs a relative file path using the file hash and determined extension,
        then populates an ImageModel instance with all metadata fields from the ImageData.
        Missing or optional fields are handled with sensible defaults (empty strings, 0, empty dicts).

        Finally, it persists the record to the database via add/flush/commit operations and
        increments the tracking counters for successfully added images and JSON entries.

        Args:
            file_hash (str): The hash identifier for the image file.
            image_data (ImageData): ImageData instance containing image metadata fields such as
                mimetype, file_name, file_size, width, height, date_created, date_modified,
                artist_id, source_url, license_id, and exif_data.
            json_file (Path): The Path object of the JSON metadata file, used as fallback
                for the file_name if not provided in image_data.

        Returns:
            None: Updates internal database state and result counters.
        """
        print(f"Creating database record from ImageData for hash {file_hash}")

        # Determine file extension
        if image_data.mimetype:
            ext = ImageProcessor.mime_to_extension(image_data.mimetype)
        else:
            file_name = image_data.file_name or "image.jpg"
            ext = (
                "." + file_name.rsplit(".", 1)[1].lower()
                if "." in file_name
                else ".jpg"
            )

        relative_filepath = f"{file_hash}{ext}"

        new_image = ImageModel(
            file_path=relative_filepath,
            file_name=image_data.file_name or json_file.stem,
            file_hash=file_hash,
            file_size=image_data.file_size or 0,
            width=image_data.width or 0,
            height=image_data.height or 0,
            mimetype=image_data.mimetype,
            date_created=image_data.date_created,
            date_modified=image_data.date_modified,
            artist_id=image_data.artist_id,
            source_url=image_data.source_url,
            license_id=image_data.license_id,
            exif_data=image_data.exif_data,
        )
        self.db.add(new_image)
        self.db.flush()
        self.db.commit()
        self.results["images_added"] += 1
        self.results["json_db_entries_added"] += 1

    def _process_single_json_file(
        self,
        json_file: Path,
        processed_json_files: set,
    ) -> Optional[tuple]:
        """
        Processes a single JSON file and returns (file_hash, ImageData) or None.

        Handles loading, renaming/merging, and database synchronization for one JSON file.
        """
        self.results["json_files_scanned"] += 1

        # Load JSON directly into ImageData instance
        image_data = ImageData.from_json_file(json_file)
        if not image_data:
            print(f"Warning: Could not load JSON file {json_file.name}, skipping")
            return None

        file_hash = image_data.file_hash
        if not file_hash:
            print(
                f"Warning: JSON file {json_file.name} has no file_hash, skipping"
            )
            return None

        expected_json_path = self.library_path / f"{file_hash}.json"

        # Use ImageData for rename/merge operations
        self._handle_json_file_rename_or_merge(
            json_file, file_hash, image_data, expected_json_path, processed_json_files
        )

        # Reload from final JSON path (in case it was merged)
        final_image_data = None
        final_json_path = expected_json_path
        if final_json_path.exists():
            final_image_data = ImageData.from_json_file(final_json_path)

        # Check database and handle accordingly
        db_record = (
            self.db.query(ImageModel)
            .filter(ImageModel.file_hash == file_hash)
            .first()
        )

        if db_record is not None:
            # Compare JSON values with database values using ImageData
            print(f"Comparing JSON with database for hash {file_hash}")
            has_differences = self._compare_json_with_database(
                final_image_data or image_data,
                db_record
            )
            if has_differences:
                self.results["json_db_differences"] += 1
                print(f"  -> Found differences in JSON vs database for {file_hash}")
        else:
            # Create database record from ImageData
            self._create_db_record_from_json(file_hash, image_data, json_file)

        return (file_hash, final_image_data) if final_image_data else None

    def _process_json_files(self) -> Dict[str, ImageData]:
        """
        Processes all JSON files in the library as the source of authority.

        Returns a dictionary mapping file hashes to their ImageData instances.
        This data will be used when processing image files.
        """
        print("Starting JSON file scan...")

        json_data_by_hash: Dict[str, ImageData] = {}
        processed_json_files: set = set()

        for json_file in self.library_path.iterdir():
            if not json_file.is_file() or not json_file.name.endswith(".json"):
                continue

            if str(json_file) in processed_json_files:
                continue

            try:
                result = self._process_single_json_file(json_file, processed_json_files)
                if result:
                    file_hash, final_image_data = result
                    json_data_by_hash[file_hash] = final_image_data

            except Exception as e:
                self.error_messages.append(
                    f"Could not process JSON file {json_file.name}: {e}"
                )
                self.results["errors"] += 1

        print(
            f"JSON file scan complete. Processed {len(json_data_by_hash)} JSON files."
        )
        return json_data_by_hash

    def _compare_json_with_database(
        self,
        image_data: ImageData,
        db_record: ImageModel,
    ) -> bool:
        """
        Compares ImageData values with database record values.
        Uses ImageData to determine differences.

        Updates the database record with JSON metadata if differences are found.
        Note: Only updates metadata fields, not file-derived fields (hash, size, dimensions).

        Returns True if there were differences (and update was performed), False otherwise.
        """
        db_data = ImageData.from_db_record(db_record)

        # Calculate what merged data would look like (JSON takes precedence)
        merged_data = db_data + image_data

        # Compare database vs merged data
        differences = db_data.diff(merged_data)

        if differences:
            has_differences = True
            print(f"  Found {len(differences)} difference(s) between JSON and database:")
            for field, values in differences.items():
                print(f"    {field}: DB={values['self']} -> JSON={values['other']}")

            # Update database record with JSON metadata
            # Note: Only update metadata fields, not file-derived fields
            # File-derived fields (hash, size, dimensions, mimetype, date_modified)
            # should only be updated when processing the actual image file
            print(f"  Updating database record for hash {db_record.file_hash}")
            self._update_database_from_imagedata(db_record, merged_data, differences)
            self.db.commit()
        else:
            has_differences = False
            print(f"  JSON and database are in sync for hash {db_record.file_hash}")

        return has_differences

    def _update_database_from_imagedata(
        self,
        db_record: ImageModel,
        image_data: ImageData,
        differences: Dict[str, Dict[str, Any]],
    ) -> None:
        """
        Updates database record with ImageData values.

        Only updates metadata fields (file_name, artist_id, source_url, license_id,
        date_created, exif_data), not file-derived fields.

        Args:
            db_record: The database record to update
            image_data: ImageData containing the new values
            differences: Dictionary of fields that changed (from diff() method)
        """
        # Build update dict with only changed metadata fields
        # Skip file-derived fields (updated during image processing)
        update_fields = {}
        metadata_fields = {
            "file_name", "artist_id", "source_url", "license_id",
            "date_created", "exif_data"
        }

        for field in differences.keys():
            if field in metadata_fields:
                # Get the value from merged_data (JSON takes precedence)
                value = getattr(image_data, field, None)
                if value is not None:
                    # Special handling for date_created (convert from ISO string to datetime)
                    if field == "date_created" and isinstance(value, str):
                        from datetime import datetime
                        try:
                            value = datetime.fromisoformat(value)
                        except ValueError:
                            print(f"    Warning: Could not parse date_created: {value}")
                            value = None

                    update_fields[field] = value

        if update_fields:
            print(f"    Updating fields: {list(update_fields.keys())}")
            self.db.query(ImageModel).filter(
                ImageModel.id == db_record.id
            ).update(update_fields, synchronize_session=False)
            self.results["json_db_records_updated"] += 1
        else:
            print("    No metadata fields to update (only file-derived fields changed)")

    def _ensure_json_file_exists(
        self,
        image_file: Path,
        db_record: ImageModel,
        processor: Optional[ImageProcessor] = None,
    ):
        """
        Ensures that a JSON file exists for the given image file.
        Creates one if it doesn't exist.

        Args:
            image_file: Path to the image file
            db_record: Database record for the image
            processor: Optional ImageProcessor instance to use (more efficient than creating new one)
        """
        json_path = image_file.with_suffix(".json")

        if not json_path.exists():
            # Create JSON file if it doesn't exist
            print(f"Creating JSON file for: {image_file.name}")
            if processor:
                processor._save_json(image_file, db_record)
            else:
                new_processor = ImageProcessor(
                    str(image_file), self.db, str(self.library_path)
                )
                new_processor._save_json(image_file, db_record)
            self.results["json_files_created"] += 1
            print(f"Created JSON file: {json_path.name}")

    def _process_library_files(self, json_data_by_hash: Dict[str, ImageData]):
        """
        Processes image files using JSON metadata as source of authority.

        Args:
            json_data_by_hash: Dictionary mapping file hashes to their ImageData instances
        """
        print("Starting image file scan...")
        for image_file in self.library_path.iterdir():
            # Skip JSON files and non-files
            if not image_file.is_file() or image_file.name.endswith(".json"):
                continue

            try:
                processor = ImageProcessor(
                    str(image_file), self.db, str(self.library_path)
                )
                self.results["images_scanned"] += 1
                file_hash = processor.file_hash
                file_extension = (
                    processor.mime_to_extension(processor.mimetype)
                    if processor.mimetype
                    else processor.original_path.suffix.lower()
                )
                expected_filename = f"{file_hash}{file_extension}"
                db_record = processor.find_in_database()

                # Use JSON metadata as source of authority if available
                json_data = json_data_by_hash.get(file_hash) if file_hash else None

                # If no JSON data, try to load from file
                if not json_data:
                    json_data = ImageData.from_json_file(image_file.with_suffix(".json"))

                # Get original filename from JSON or use current filename
                original_filename = (
                    json_data.file_name if json_data and json_data.file_name
                    else (processor.metadata.file_name or image_file.name)
                )

                # Determine the actual file path to use (after potential renaming)
                final_file_path = image_file

                if db_record is not None:
                    # File is a known entity (already exists in database)
                    # Update record with actual file data (file is authority for these fields)

                    # Check if file needs renaming to standardized name
                    if image_file.name != expected_filename:
                        print(
                            f"Renaming file '{image_file.name}' to standardized name '{expected_filename}'"
                        )
                        processor.save_to_library()
                        self.results["files_renamed"] += 1
                        final_file_path = (
                            processor.original_path
                        )  # Updated path after rename

                        # Update database record with actual file data
                        self.db.query(ImageModel).filter(
                            ImageModel.id == db_record.id
                        ).update(
                            {
                                ImageModel.file_path: expected_filename,
                                ImageModel.file_size: processor.file_size,
                                ImageModel.width: processor.width,
                                ImageModel.height: processor.height,
                                ImageModel.mimetype: processor.mimetype,
                                ImageModel.date_modified: processor.date_modified,
                            },
                            synchronize_session=False,
                        )

                        # Save updated JSON with new file path and actual file data
                        processor._save_json(processor.original_path, db_record)
                        self.db.commit()
                    else:
                        # File has correct name, just update with actual file data if needed
                        # JSON is source of authority for metadata, but file overrides for file-derived fields
                        actual_file_stat = image_file.stat()
                        current_file_size = (
                            db_record.file_size
                            if db_record.file_size is not None
                            else 0
                        )
                        current_width = (
                            db_record.width if db_record.width is not None else 0
                        )
                        current_height = (
                            db_record.height if db_record.height is not None else 0
                        )
                        needs_update = bool(
                            current_file_size != actual_file_stat.st_size
                            or current_width != processor.width
                            or current_height != processor.height
                        )
                        if needs_update:
                            self.db.query(ImageModel).filter(
                                ImageModel.id == db_record.id
                            ).update(
                                {
                                    ImageModel.file_size: processor.file_size,
                                    ImageModel.width: processor.width,
                                    ImageModel.height: processor.height,
                                    ImageModel.date_modified: processor.date_modified,
                                },
                                synchronize_session=False,
                            )
                            # Update JSON with current file data
                            processor._save_json(image_file, db_record)
                            self.db.commit()

                        # Check if JSON file exists, create if missing
                        self._ensure_json_file_exists(final_file_path, db_record, processor)
                else:
                    # This shouldn't happen if JSON was processed, but handle it
                    # File is new and needs to be imported
                    print(
                        f"New image file detected (no JSON record): {image_file.name}"
                    )

                    if image_file.name != expected_filename:
                        print(f"Renaming to '{expected_filename}'")
                        processor.save_to_library()
                        self.results["files_renamed"] += 1
                        relative_path = expected_filename
                        final_file_path = processor.original_path
                    else:
                        relative_path = image_file.name
                        final_file_path = image_file

                    # Create database record with preserved original filename
                    new_image = processor.create_database_record(
                        relative_filepath=relative_path,
                        original_filename=original_filename,
                    )
                    self.db.add(new_image)
                    self.db.flush()  # Flush to get the ID

                    # Save JSON metadata using existing processor
                    processor._save_json(processor.original_path, new_image)
                    self.results["json_files_created"] += 1
                    print(f"Created JSON file for: {final_file_path.name}")

                    self.results["images_added"] += 1

            except Exception as e:
                self.error_messages.append(f"Could not process {image_file.name}: {e}")
                self.results["errors"] += 1

    def scan(self) -> Dict[str, Any]:
        """
        Performs a full scan and synchronization of the image library.
        This is the main public method for the class.

        Scan order:
        1. Process all JSON files (source of authority)
        2. Process all image files using JSON metadata
        3. Clean up orphaned records and their JSON files
        """
        if not self.library_path.is_dir():
            raise FileNotFoundError(
                f"Image library directory not found: {self.library_path}"
            )

        # Step 1: Process all JSON files first (source of authority)
        # This ensures JSON data takes precedence and creates database records as needed
        json_data_by_hash = self._process_json_files()

        # Step 2: Process image files using JSON metadata
        # This matches images to JSON data and handles renames/duplicates
        self._process_library_files(json_data_by_hash)

        # Step 3: Clean up truly orphaned records
        # Records where the file still doesn't exist after scanning are truly orphaned
        # Their JSON files will also be removed
        self._cleanup_orphaned_records()

        # Final commit to ensure all changes are persisted
        self.db.commit()

        return {
            "message": "Library scan and synchronization complete.",
            **self.results,
            "errors": self.error_messages,
        }

    # --- Magic methods for a more Pythonic feel ---

    def __len__(self) -> int:
        """Returns the total number of images in the database."""
        return self.db.query(ImageModel).count()

    def __iter__(self):
        """Allows iteration over all ImageModel objects in the database."""
        return (img for img in self.db.query(ImageModel).all())
