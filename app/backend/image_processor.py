import os
import shutil
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, Any

from PIL import Image
from PIL.ExifTags import TAGS
from sqlalchemy.orm import Session

from models import ImageModel, Artist
from image_data import ImageData

# A set of allowed image extensions for easy lookup
ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".jfif"}
MIME_MAPPING = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".jfif": "image/jpeg",
}

UUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)
UUID_ANYWHERE_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}"
)


class ImageProcessor:
    """A class to handle all operations for a single image file."""

    def __init__(self, file_path: str, db: Session, library_path: str):
        self.db = db
        self.library_path = library_path
        # self.original_path = Path(file_path)
        # self.original_filename = self.original_path.name

        # --- Initialize state using ImageData ---
        self.metadata: ImageData = ImageData()
        self.db_record: Optional[ImageModel] = None
        self.exif_tags: dict[str, Any] = {}

        # --- Perform initial processing ---
        self.metadata.file_path = str(Path(file_path))
        self.metadata.file_name = Path(file_path).name
        if not Path(file_path).is_file() or not self._is_valid_image():
            print(f"File is not a valid image: {self.metadata.file_name}")
            raise ValueError(f"File is not a valid image: {self.metadata.file_name}")

        self._process_file()

        # Add file-derived data to ImageData after processing
        self.metadata.file_hash = self.file_hash
        self.metadata.width = self.width
        self.metadata.height = self.height
        self.metadata.mimetype = self.mimetype
        self.metadata.file_size = self.file_size
        self.metadata.exif_data = self.exif_data
        self.metadata.date_created = self.date_created.isoformat()
        self.metadata.date_modified = self.date_modified.isoformat()

    # --- Properties for backward compatibility ---
    @property
    def file_hash(self) -> str:
        return self.metadata.file_hash or ""

    @property
    def width(self) -> int:
        return self.metadata.width or 0

    @property
    def height(self) -> int:
        return self.metadata.height or 0

    @property
    def mimetype(self) -> Optional[str]:
        return self.metadata.mimetype

    @property
    def exif_data(self) -> dict:
        return self.metadata.exif_data or {}

    @property
    def file_size(self) -> int:
        return self.metadata.file_size or 0

    @property
    def date_created(self) -> datetime:
        """Returns date_created as datetime object."""
        if self.metadata.date_created:
            return datetime.fromisoformat(self.metadata.date_created)
        return datetime.now()

    @property
    def extension(self) -> Optional[str]:
        """Returns the file extension based on MIME type or filename."""
        if self.mimetype:
            ext = self.mime_to_extension(self.mimetype)
            if ext:
                return ext
        if self.metadata.file_name:
            return Path(self.metadata.file_name).suffix.lower()
        return None

    @property
    def date_modified(self) -> datetime:
        """Returns date_modified as datetime object."""
        if self.metadata.date_modified:
            return datetime.fromisoformat(self.metadata.date_modified)
        return datetime.now()

    def _is_valid_image(self) -> bool:
        """Checks if the file has a valid image extension."""
        if self.metadata.file_path is None:
            return False
        return self.extension in ALLOWED_EXTENSIONS

    def _process_file(self):
        """Calculates hash, gets metadata, and size."""
        self.metadata.file_hash = self._calculate_hash()
        (
            self.metadata.width,
            self.metadata.height,
            self.metadata.mimetype,
            self.metadata.exif_data,
            self.exif_tags,
        ) = self._get_metadata()
        if self.metadata.file_path:
            self.metadata.file_size = Path(self.metadata.file_path).stat().st_size

    def _calculate_hash(self) -> str:
        """Calculates the SHA256 hash of the image file."""
        if self.metadata.file_path is None:
            raise ValueError("File path is None")
        h = hashlib.sha256()
        with open(self.metadata.file_path, "rb") as f:
            while chunk := f.read(8192):
                h.update(chunk)
        return h.hexdigest()

    @staticmethod
    def mime_to_extension(mime_type: str | None) -> Optional[str]:
        """Converts a MIME type to a file extension."""
        if mime_type is None:
            return None
        for ext, mime in MIME_MAPPING.items():
            if mime == mime_type:
                return ext
        return None

    def _extract_mimetype(self, img: Image.Image) -> Optional[str]:
        """Extracts the MIME type from a PIL Image object."""
        mimetype = Image.MIME.get(img.format) if img.format else None
        if not mimetype and img.format:
            ext = self.mime_to_extension(img.format)
            mimetype = MIME_MAPPING.get(ext) if ext else None
        if not mimetype and self.metadata.file_path:
            ext = self.extension
            mimetype = MIME_MAPPING.get(ext) if ext else None
        return mimetype

    @staticmethod
    def decode_exif_value(value):
        """Decodes an EXIF value."""
        if not isinstance(value, bytes):
            return value

        # EXIF UserComment often has "UNICODE\x00\x00" as first 8 bytes
        # Then the actual UTF-16BE data starts at byte 8
        if len(value) > 8 and value[:7] == b"UNICODE":
            # Skip to byte 8, then decode as UTF-16BE
            try:
                decoded = value[8:].decode("utf-16be")
                decoded = decoded.rstrip("\x00")
                if decoded:
                    return decoded
            except (UnicodeDecodeError, AttributeError):
                pass

        # Try standard decodings (UTF-16BE first, then UTF-8, then ASCII)
        for encoding in ["utf-16be", "utf-8", "ascii", "latin-1"]:
            try:
                decoded = value.decode(encoding)
                decoded = decoded.rstrip("\x00")
                if decoded:
                    return decoded
            except (UnicodeDecodeError, AttributeError):
                continue

        # If all decodings fail, return the raw bytes as-is
        return value

    @classmethod
    def _to_json_safe(cls, value: Any) -> Any:
        """Recursively convert EXIF values into JSON-safe primitives."""
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        if isinstance(value, bytes):
            decoded = cls.decode_exif_value(value)
            if isinstance(decoded, bytes):
                return decoded.hex()
            return cls._to_json_safe(decoded)
        if isinstance(value, tuple):
            return [cls._to_json_safe(v) for v in value]
        if isinstance(value, list):
            return [cls._to_json_safe(v) for v in value]
        if isinstance(value, dict):
            return {str(k): cls._to_json_safe(v) for k, v in value.items()}

        # Fallback for Rational/IFDRational and other Pillow-specific types.
        return str(value)

    def _extract_standard_exif_tags(self, exif_data_raw) -> dict:
        """Extracts standard EXIF tags from raw EXIF data."""
        exif_data = {}
        for tag, value in exif_data_raw.items():
            tag_name = TAGS.get(tag, tag)
            if isinstance(value, (str, int, float, list, tuple, dict)):
                exif_data[tag_name] = value
            elif isinstance(value, bytes):
                decoded_value = self.decode_exif_value(value)
                exif_data[tag_name] = decoded_value
        return exif_data

    def _extract_ifd_exif_tags(
        self,
        exif_data_raw,
        exif_data: dict,
        exif_tags: Optional[dict[str, Any]] = None,
    ) -> None:
        """Extracts IFD (Image File Directory) data from raw EXIF data."""
        try:
            for ifd_id in [
                0x8825,  # GPSInfo IFD
                0x8827,  # Interop IFD
                0x927C,  # MakerNote IFD
                0x8769,  # ExifOffset IFD
            ]:
                try:
                    ifd_data = exif_data_raw.get_ifd(ifd_id)
                    for tag, value in ifd_data.items() if ifd_data else []:
                        # Decode the value before storing
                        decoded_value = self.decode_exif_value(value)
                        tag_name = TAGS.get(tag, tag)
                        if isinstance(
                            decoded_value,
                            (str, int, float, list, tuple, dict),
                        ):
                            exif_data[f"{tag_name}"] = decoded_value

                        if exif_tags is not None:
                            ifd_key = f"ifd:{ifd_id}:{tag_name}"
                            exif_tags[ifd_key] = self._to_json_safe(decoded_value)
                except KeyError:
                    pass
        except Exception:
            pass

    def _print_exif_data(self, exif_data: dict) -> None:
        """Prints all decoded EXIF data."""
        print(f"Second pass: Extracted EXIF data from {self.metadata.file_name}:")
        for key, value in exif_data.items():
            # print(f"  {key}: {value[:100] if isinstance(value, str) else value}")
            pass

    def _extract_generation_text_fields(
        self,
        img: Image.Image,
        exif_data: dict,
        exif_tags: Optional[dict[str, Any]] = None,
    ) -> None:
        """Extract generation text fields often stored outside classic EXIF.

        Many AI-generated images (especially PNG/WebP) store prompt metadata in
        image info text chunks under keys like `parameters`, `comment`, or
        `comments`. These values may not appear in `img.getexif()`.
        """
        info = getattr(img, "info", {}) or {}
        if not isinstance(info, dict):
            return

        # Build case-insensitive lookup of textual keys in image info.
        info_lookup: dict[str, Any] = {
            str(k).strip().lower(): self.decode_exif_value(v)
            for k, v in info.items()
            if isinstance(k, str)
        }

        if exif_tags is not None:
            for key, value in info_lookup.items():
                exif_tags[f"info:{key}"] = self._to_json_safe(value)

        # Preserve common generation fields and textual metadata from PNG chunks.
        for source_key, target_key in (
            ("parameters", "parameters"),
            ("prompt", "Prompt"),
            ("workflow", "Workflow"),
            ("title", "Title"),
            ("description", "Description"),
            ("software", "Software"),
            ("source", "Source"),
            ("generation time", "Generation Time"),
        ):
            value = info_lookup.get(source_key)
            if isinstance(value, str):
                cleaned = value.strip()
                if cleaned:
                    exif_data[target_key] = cleaned

        # Canonicalize free-form generation payload under UserComment only.
        # Some images expose this under comment/comments text chunks.
        user_comment = exif_data.get("UserComment")
        if not isinstance(user_comment, str) or not user_comment.strip():
            for key in ("comment", "comments"):
                value = info_lookup.get(key)
                if isinstance(value, str):
                    cleaned = value.strip()
                    if cleaned:
                        exif_data["UserComment"] = cleaned
                        break

        # Avoid duplicate payload copies once UserComment is present.
        exif_data.pop("comment", None)
        exif_data.pop("comments", None)

    @staticmethod
    def _looks_like_comfyui_workflow_tag(workflow_value: str) -> bool:
        """Return True when workflow text looks like ComfyUI workflow metadata.

        Common pattern: starts with an `id` field and includes a UUID-like value,
        e.g. JSON-ish strings like {"id":"<uuid>", ...} or id:<uuid>.
        """
        text = workflow_value.strip()
        if not text:
            return False

        lowered = text.lower().lstrip("{\n\r\t ")
        starts_with_id = lowered.startswith('"id"') or lowered.startswith("id")
        has_uuid = bool(UUID_ANYWHERE_RE.search(text))
        return starts_with_id and has_uuid

    @staticmethod
    def _prune_ifd_pointer_tags(exif_data: dict) -> None:
        """Remove low-value IFD pointer tags once nested data has been extracted."""
        pointer_keys = {
            "ExifOffset",
            "GPSInfo",
            "InteroperabilityOffset",
            "InteropOffset",
        }
        for key in pointer_keys:
            exif_data.pop(key, None)

        # Keep UserComment as the canonical free-form generation payload key.
        if "UserComment" in exif_data:
            exif_data.pop("comment", None)
            exif_data.pop("comments", None)

    def infer_generation_software(
        self,
        source_url: Optional[str] = None,
        existing_sidecar: Optional[dict[str, Any]] = None,
        db_json_metadata: Optional[dict[str, Any]] = None,
    ) -> Optional[str]:
        """Infer generator software while preserving original EXIF fields.

        This returns a normalized tool/source name suitable for a dedicated
        sidecar field (e.g. `generation_software`).
        """
        sidecar = existing_sidecar if isinstance(existing_sidecar, dict) else {}
        json_meta = db_json_metadata if isinstance(db_json_metadata, dict) else {}

        # Note: Hosting platform is not the same as generation tool.
        # We intentionally do NOT infer generation software from source_url.

        software_raw = self.exif_data.get("Software") if isinstance(self.exif_data, dict) else None
        software_value = software_raw.strip() if isinstance(software_raw, str) else ""
        software_lower = software_value.lower()

        if "adobe" in software_lower or "photoshop" in software_lower:
            return "Photoshop"
        if "comfyui" in software_lower or "comfy" in software_lower:
            return "ComfyUI"
        if "automatic1111" in software_lower or "stable diffusion webui" in software_lower:
            return "AUTOMATIC1111"
        if "invoke" in software_lower:
            return "InvokeAI"
        if "fooocus" in software_lower:
            return "Fooocus"
        if "swarm" in software_lower:
            return "SwarmUI"
        if "civitai" in software_lower:
            return "CivitAI"
        if "novelai" in software_lower:
            return "NovelAI"

        # Inspect generation text fields for tool fingerprints.
        generation_text_sources: list[str] = []
        if isinstance(self.exif_data, dict):
            for key in (
                "parameters",
                "prompt",
                "Prompt",
                "workflow",
                "Workflow",
                "comment",
                "comments",
                "UserComment",
            ):
                value = self.exif_data.get(key)
                if isinstance(value, str) and value.strip():
                    generation_text_sources.append(value)

            workflow_candidate = self.exif_data.get("Workflow") or self.exif_data.get(
                "workflow"
            )
            if isinstance(workflow_candidate, str) and self._looks_like_comfyui_workflow_tag(
                workflow_candidate
            ):
                return "ComfyUI"

        sidecar_exif = sidecar.get("exif_data")
        if isinstance(sidecar_exif, dict):
            for key in (
                "parameters",
                "prompt",
                "Prompt",
                "workflow",
                "Workflow",
                "comment",
                "comments",
                "UserComment",
            ):
                value = sidecar_exif.get(key)
                if isinstance(value, str) and value.strip():
                    generation_text_sources.append(value)

            workflow_candidate = sidecar_exif.get("Workflow") or sidecar_exif.get(
                "workflow"
            )
            if isinstance(workflow_candidate, str) and self._looks_like_comfyui_workflow_tag(
                workflow_candidate
            ):
                return "ComfyUI"

        generation_text = "\n".join(generation_text_sources).lower()

        if "comfyui" in generation_text:
            return "ComfyUI"
        if "automatic1111" in generation_text or "a1111" in generation_text or "stable diffusion webui" in generation_text:
            return "AUTOMATIC1111"
        if "invokeai" in generation_text:
            return "InvokeAI"
        if "fooocus" in generation_text:
            return "Fooocus"
        if "swarmui" in generation_text or "swarm ui" in generation_text:
            return "SwarmUI"

        # CivitAI-specific marker often appears in generation metadata export.
        if "civitai resources:" in generation_text:
            return "CivitAI"

        # If CivitAI metadata exists and tool still unknown, use process/engine hints.
        civitai_payload = None
        if isinstance(sidecar.get("civitai"), dict):
            civitai_payload = sidecar.get("civitai")
        elif isinstance(json_meta.get("civitai"), dict):
            civitai_payload = json_meta.get("civitai")

        if isinstance(civitai_payload, dict):
            process = str(civitai_payload.get("process", "")).lower()
            engine = str(civitai_payload.get("engine", "")).lower()
            merged_hint = f"{process} {engine}"
            if "comfy" in merged_hint:
                return "ComfyUI"
            if "a1111" in merged_hint or "automatic" in merged_hint or "webui" in merged_hint:
                return "AUTOMATIC1111"

        # CivitAI often writes opaque UUID-like software values.
        if software_value and UUID_RE.match(software_value):
            if "civitai resources:" in generation_text:
                return "CivitAI"

        return None

    def _get_metadata(self) -> tuple[int, int, Optional[str], dict, dict[str, Any]]:
        """Extracts metadata from the image file."""
        try:
            if self.metadata.file_path is None:
                return 0, 0, None, {}, {}
            with Image.open(self.metadata.file_path) as img:
                width, height = img.size
                mimetype = self._extract_mimetype(img)
                exif_data_raw = img.getexif()

                exif_data = {}
                exif_tags: dict[str, Any] = {}
                if exif_data_raw:
                    for tag, value in exif_data_raw.items():
                        tag_name = TAGS.get(tag, f"tag_{tag}")
                        exif_tags[f"exif:{tag_name}"] = self._to_json_safe(value)

                    exif_data = self._extract_standard_exif_tags(exif_data_raw)
                    self._extract_ifd_exif_tags(exif_data_raw, exif_data, exif_tags)

                # Also inspect text chunks (PNG/WebP/etc.) for generation metadata.
                self._extract_generation_text_fields(img, exif_data, exif_tags)
                self._prune_ifd_pointer_tags(exif_data)
                self._print_exif_data(exif_data)

            return width, height, mimetype, exif_data, exif_tags
        except Exception:
            return 0, 0, None, {}, {}

    def find_in_database(self) -> Optional[ImageModel]:
        """Finds the image in the database using its hash."""
        self.db_record = (
            self.db.query(ImageModel)
            .filter(ImageModel.file_hash == self.file_hash)
            .first()
        )
        return self.db_record

    def save_to_library(self) -> str:
        """Renames/moves the image to the library using its hash as the filename."""
        final_filename = f"{self.file_hash}{self.extension or ''}"
        final_absolute_path = Path(self.library_path) / final_filename
        print(f"Saving image to library: {final_absolute_path}")

        # If the file is already in the right place with the right name, do nothing
        if (
            self.metadata.file_path
            and Path(self.metadata.file_path).resolve() == final_absolute_path.resolve()
        ):
            return final_filename

        # Move any existing JSON metadata file along with the image
        original_json_path = (
            self._get_json_path(Path(self.metadata.file_path))
            if self.metadata.file_path
            else None
        )
        final_json_path = self._get_json_path(final_absolute_path)

        # Move the image file to its final destination
        if self.metadata.file_path is None:
            raise ValueError("File path is None, cannot save to library")
        shutil.move(self.metadata.file_path, final_absolute_path)

        # Move the JSON file if it exists
        if original_json_path and original_json_path.exists():
            try:
                shutil.move(original_json_path, final_json_path)
                print(f"Moved JSON metadata to: {final_json_path}")
            except Exception as e:
                print(f"Warning: Could not move JSON metadata file: {e}")

        # Update the original_path to point to the new location
        self.original_path = final_absolute_path

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
        source_site: Optional[str] = None,
        license_id: Optional[int] = None,
        json_metadata: Optional[dict[str, Any]] = None,
    ) -> ImageModel:
        """Creates a new ImageModel record."""
        absolute_path = Path(self.library_path) / relative_filepath
        stat = os.stat(absolute_path)

        display_name = original_filename or self.metadata.file_name or relative_filepath

        self.db_record = ImageModel(
            file_path=relative_filepath,
            file_name=display_name,
            file_hash=self.file_hash,
            file_size=stat.st_size,
            width=self.width,
            height=self.height,
            mimetype=self.mimetype,
            date_created=datetime.fromtimestamp(stat.st_ctime),
            date_modified=datetime.fromtimestamp(stat.st_mtime),
            artist_id=artist_obj.id if artist_obj else None,
            source_url=source_url,
            source_site=source_site,
            license_id=license_id if license_id else None,
            exif_data=self.exif_data,
            json_metadata=json_metadata,
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

    def _get_json_path(self, image_path: Path) -> Path:
        """Returns the path to the JSON metadata file for a given image path."""
        return image_path.with_suffix(".json")

    def save_json_metadata(
        self,
        image_path: Path,
        db_record: Optional[ImageModel] = None,
        additional_data: Optional[dict] = None,
    ) -> None:
        """
        Public API to save/update sidecar JSON metadata for an image.

        This method is the stable entry point used by other modules.
        It intentionally delegates to the internal writer so callers do not
        depend on implementation details and future refactors stay localized.
        """
        self._write_json_sidecar(image_path, db_record, additional_data)

    def _write_json_sidecar(
        self,
        image_path: Path,
        db_record: Optional[ImageModel] = None,
        additional_data: Optional[dict] = None,
    ) -> None:
        """
        Private/internal implementation that writes metadata to a JSON sidecar file.

        This method is subject to change without notice.
        External callers should use ``save_json_metadata`` instead.

        The JSON file serves as the source of authority for metadata, except for
        fields derived directly from the file content (hash, size, date_modified).

        Args:
            image_path: Path to the image file
            db_record: Optional ImageModel record to include data from
            additional_data: Optional additional metadata not in the database model
        """
        json_path = self._get_json_path(image_path)
        stat = image_path.stat()

        # Build the data structure from all available sources
        data: dict[str, Any] = {
            # Core file metadata (from actual file)
            "file_hash": self.file_hash,
            "file_size": stat.st_size,
            "date_modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            # Image metadata
            "width": self.width,
            "height": self.height,
            "mimetype": self.mimetype,
            "exif_data": self.exif_data,
            "exif_tags": self.exif_tags,
        }

        # Load existing JSON data to preserve fields not in current state
        existing_data = {}
        if json_path.exists():
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    existing_data = json.load(f)
            except (json.JSONDecodeError, IOError):
                pass

        # Preserve date_created from JSON if it exists (file creation time)
        # The JSON file is the authority for this
        if "date_created" in existing_data:
            data["date_created"] = existing_data["date_created"]
        else:
            # No existing JSON, use file's creation time
            data["date_created"] = datetime.fromtimestamp(stat.st_ctime).isoformat()

        # Merge additional fields from existing JSON (fields not in database)
        json_only_fields = {
            k: v
            for k, v in existing_data.items()
            if k
            not in {
                "file_hash",
                "file_size",
                "date_modified",
                "date_created",
                "width",
                "height",
                "mimetype",
                "exif_data",
                "exif_tags",
                "file_path",
                "file_name",
                "artist_id",
                "artist_name",
                "source_url",
                "license_id",
                "source_site",
                "json_metadata",
                "json_data",
            }
        }
        data.update(json_only_fields)

        # Add database fields if a record is provided
        if db_record:
            data.update(
                {
                    "file_path": db_record.file_path,
                    "file_name": db_record.file_name,
                    "artist_id": db_record.artist_id,
                    "source_url": db_record.source_url,
                    "source_site": db_record.source_site,
                    "license_id": db_record.license_id,
                }
            )
            # Include artist name if available
            if db_record.artist_id is not None:
                artist = (
                    self.db.query(Artist)
                    .filter(Artist.id == db_record.artist_id)
                    .first()
                )
                if artist:
                    data["artist_name"] = artist.name

        # Add any additional custom data provided
        if additional_data:
            data.update(additional_data)

        # Keep original Software EXIF raw value, but provide normalized generator field.
        source_url_value = (
            db_record.source_url
            if db_record is not None and isinstance(db_record.source_url, str)
            else data.get("source_url")
        )
        db_json_metadata_value = (
            db_record.json_metadata
            if db_record is not None and isinstance(db_record.json_metadata, dict)
            else data.get("json_metadata")
        )

        inferred_software = self.infer_generation_software(
            source_url=source_url_value,
            existing_sidecar=existing_data,
            db_json_metadata=(
                db_json_metadata_value
                if isinstance(db_json_metadata_value, dict)
                else None
            ),
        )
        if inferred_software:
            data["generation_software"] = inferred_software
        elif "generation_software" in existing_data:
            data["generation_software"] = existing_data["generation_software"]

        # Write the JSON file
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)

    def _load_json(self, image_path: Path) -> dict[str, Any]:
        """
        Loads metadata from a JSON file alongside the image file.

        Returns a dictionary containing metadata that can be used to populate
        the database. File-derived fields (hash, size, date_modified, dimensions)
        from the actual file take precedence over JSON values.

        Args:
            image_path: Path to the image file

        Returns:
            Dictionary containing metadata from the JSON file, with file-derived
            fields overridden by actual file data
        """
        json_path = self._get_json_path(image_path)

        # Default empty structure
        metadata: dict[str, Any] = {}

        if not json_path.exists():
            return metadata

        try:
            with open(json_path, "r", encoding="utf-8") as f:
                loaded_data = json.load(f)

            # Get actual file stats
            stat = image_path.stat()

            # Start with loaded JSON data
            metadata = loaded_data.copy()

            # Override with actual file data (file is authority for these)
            metadata["file_hash"] = self.file_hash
            metadata["file_size"] = stat.st_size
            metadata["date_modified"] = datetime.fromtimestamp(
                stat.st_mtime
            ).isoformat()
            metadata["width"] = self.width
            metadata["height"] = self.height

            # Keep date_created from JSON (it's the authority for file creation time)
            # unless it doesn't exist, then use file creation time
            if "date_created" not in metadata:
                metadata["date_created"] = datetime.fromtimestamp(
                    stat.st_ctime
                ).isoformat()

        except (json.JSONDecodeError, IOError) as e:
            print(f"Warning: Failed to load JSON metadata for {image_path}: {e}")

        return metadata
