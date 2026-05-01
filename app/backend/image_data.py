# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/image-api.md
# ──────────────────────────────────────────────────────────────────────────────
"""
ImageData class for encapsulating image metadata.

This class provides a clean, object-oriented interface for managing image metadata,
including conversions to/from various formats (dict, JSON, database records) and
merging multiple data sources with priority handling.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Any, Optional
import json

# from datetime import datetime


@dataclass
class ImageData:
    """
    Encapsulates image metadata with conversion and merging capabilities.

    This class stores all image metadata internally and provides methods for
    converting to/from different formats (dict, JSON, database records) and
    merging multiple data sources.

    Example:
        # Create from dictionary
        data1 = ImageData.from_dict({"file_name": "image.jpg", "width": 1920})

        # Create from database record
        data2 = ImageData.from_db_record(db_record)

        # Merge with precedence (data2 values override data1 values)
        merged = data1 + data2

        # Convert to JSON
        json_str = merged.to_json()

        # Print formatted output
        print(merged)
    """

    # File identification
    file_path: Optional[str] = None
    file_name: Optional[str] = None
    original_file_name: Optional[str] = None
    file_hash: Optional[str] = None

    # File properties (file-derived)
    file_size: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    mimetype: Optional[str] = None

    # Metadata
    date_created: Optional[str] = None  # ISO format string
    date_modified: Optional[str] = None  # ISO format string
    artist_id: Optional[str] = None
    source_url: Optional[str] = None
    source_site: Optional[str] = None
    license_id: Optional[str] = None

    # Extended metadata
    exif_data: Dict[str, Any] = field(default_factory=dict)
    json_metadata: Dict[str, Any] = field(default_factory=dict)
    civitai_data: Dict[str, Any] = field(default_factory=dict)

    # CivitAI identifiers
    civitai_uuid: Optional[str] = None
    civitai_hash: Optional[str] = None

    # BlurHash placeholder
    blurhash: Optional[str] = None

    # Variant grouping
    variant_group_key: Optional[str] = None
    variant_role: Optional[str] = None

    def __post_init__(self):
        """Initialize fields after dataclass creation."""
        if self.exif_data is None:
            self.exif_data = {}
        if self.json_metadata is None:
            self.json_metadata = {}
        if self.civitai_data is None:
            self.civitai_data = {}

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> "ImageData":
        """
        Create an ImageData instance from a dictionary.

        Args:
            data: Dictionary containing image metadata fields.
                  Missing fields will use default values.

        Returns:
            A new ImageData instance populated with the dictionary data.
        """
        if data is None:
            return cls()  # Return an empty ImageData instance if input is None

        return cls(
            file_path=data.get("file_path"),
            file_name=data.get("file_name"),
            original_file_name=data.get("original_file_name"),
            file_hash=data.get("file_hash"),
            file_size=data.get("file_size"),
            width=data.get("width"),
            height=data.get("height"),
            mimetype=data.get("mimetype"),
            date_created=data.get("date_created"),
            date_modified=data.get("date_modified"),
            artist_id=data.get("artist_id"),
            source_url=data.get("source_url"),
            source_site=data.get("source_site"),
            license_id=data.get("license_id"),
            exif_data=data.get("exif_data", {}),
            json_metadata=data.get("json_metadata", {}),
            civitai_data=(
                data.get("civitai_data")
                or data.get("civitai")
                or (data.get("json_metadata") or {}).get("civitai", {})
            ),
            civitai_uuid=data.get("civitai_uuid"),
            civitai_hash=data.get("civitai_hash"),
            blurhash=data.get("blurhash"),
            variant_group_key=data.get("variant_group_key"),
            variant_role=data.get("variant_role"),
        )

    @classmethod
    def from_json(cls, json_str: str) -> "ImageData":
        """
        Create an ImageData instance from a JSON string.

        Args:
            json_str: JSON string containing image metadata.

        Returns:
            A new ImageData instance populated with the JSON data.
        """
        data = json.loads(json_str)
        return cls.from_dict(data)

    @classmethod
    def from_json_file(cls, json_path: Path) -> Optional["ImageData"]:
        """
        Create an ImageData instance from a JSON file.

        Args:
            json_path: Path to a JSON file containing image metadata.

        Returns:
            A new ImageData instance populated with the JSON data,
            or None if the file doesn't exist.
        """
        if not json_path.exists():
            return None

        with open(json_path, "r", encoding="utf-8") as f:
            return cls.from_json(f.read())

    @classmethod
    def from_db_record(cls, db_record) -> "ImageData":
        """
        Create an ImageData instance from a database record.

        Args:
            db_record: ImageModel database record.

        Returns:
            A new ImageData instance populated with the database record data.
        """
        return cls(
            file_path=db_record.file_path,
            file_name=db_record.file_name,
            original_file_name=getattr(db_record, "original_file_name", None),
            file_hash=db_record.file_hash,
            file_size=db_record.file_size,
            width=db_record.width,
            height=db_record.height,
            mimetype=db_record.mimetype,
            date_created=(
                db_record.date_created.isoformat()
                if db_record.date_created is not None
                else None
            ),
            date_modified=(
                db_record.date_modified.isoformat()
                if db_record.date_modified is not None
                else None
            ),
            artist_id=db_record.artist_id,
            source_url=db_record.source_url,
            source_site=getattr(db_record, "source_site", None),
            license_id=db_record.license_id,
            exif_data=db_record.exif_data,
            json_metadata=db_record.json_metadata or {},
            civitai_data=((db_record.json_metadata or {}).get("civitai", {})),
            civitai_uuid=getattr(db_record, "civitai_uuid", None),
            civitai_hash=getattr(db_record, "civitai_hash", None),
            blurhash=getattr(db_record, "blurhash", None),
            variant_group_key=getattr(db_record, "variant_group_key", None),
            variant_role=getattr(db_record, "variant_role", None),
        )

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert ImageData to a dictionary.

        Returns:
            Dictionary representation of the image metadata.
        """
        return {
            "file_path": self.file_path,
            "file_name": self.file_name,
            "original_file_name": self.original_file_name,
            "file_hash": self.file_hash,
            "file_size": self.file_size,
            "width": self.width,
            "height": self.height,
            "mimetype": self.mimetype,
            "date_created": self.date_created,
            "date_modified": self.date_modified,
            "artist_id": self.artist_id,
            "source_url": self.source_url,
            "source_site": self.source_site,
            "license_id": self.license_id,
            "exif_data": self.exif_data,
            "json_metadata": self.json_metadata,
            "civitai_data": self.civitai_data,
            "civitai_uuid": self.civitai_uuid,
            "civitai_hash": self.civitai_hash,
            "blurhash": self.blurhash,
            "variant_group_key": self.variant_group_key,
            "variant_role": self.variant_role,
        }

    def to_json(self, indent: int = 2) -> str:
        """
        Convert ImageData to a JSON string.

        Args:
            indent: Number of spaces for indentation (default: 2).

        Returns:
            JSON string representation of the image metadata.
        """
        return json.dumps(
            self.to_dict(), indent=indent, ensure_ascii=False, default=str
        )

    def __add__(self, other: "ImageData | None") -> "ImageData":
        """
        Merge two ImageData instances.

        Values from the 'other' instance (right operand) take precedence over
        values from 'self' (left operand). This allows chaining merges:
            merged = data1 + data2 + data3  # data3 has highest priority

        Args:
            other: Another ImageData instance to merge with this one.

        Returns:
            A new ImageData instance with merged values.
        """
        # Start with self's values
        merged_dict = self.to_dict()

        # Override with other's values (other takes precedence)
        other_dict = other.to_dict() if other is not None else {}

        for key, value in other_dict.items():
            if value is not None:
                # Special handling for exif_data: merge dicts
                if key in {"exif_data", "civitai_data"} and isinstance(value, dict):
                    if merged_dict.get(key) is None:
                        merged_dict[key] = {}
                    merged_dict[key].update(value)
                else:
                    merged_dict[key] = value

        return ImageData.from_dict(merged_dict)

    def __radd__(self, other: "ImageData | None") -> "ImageData":
        """
        Right-side addition for ImageData instances.

        Enables sum([data1, data2, data3]) to work correctly.

        Args:
            other: Another ImageData instance.

        Returns:
            A new ImageData instance with merged values.
        """
        if other is None:
            return self
        return other + self

    def __eq__(self, other: object) -> bool:
        """
        Compare two ImageData instances for equality.

        Args:
            other: Another object to compare with.

        Returns:
            True if both instances have the same metadata values.
        """
        if not isinstance(other, ImageData):
            return False
        return self.to_dict() == other.to_dict()

    def diff(self, other: "ImageData") -> Dict[str, Dict[str, Any]]:
        """
        Compare this ImageData with another and return differences.

        Args:
            other: Another ImageData instance to compare with.

        Returns:
            A dictionary where keys are field names and values are dicts with
            'self' and 'other' values showing the difference.

            Example:
                {
                    "file_name": {"self": "old.jpg", "other": "new.jpg"},
                    "width": {"self": 1920, "other": 3840}
                }
        """
        self_dict = self.to_dict()
        other_dict = other.to_dict()

        differences = {}
        for key in set(self_dict.keys()) | set(other_dict.keys()):
            self_val = self_dict.get(key)
            other_val = other_dict.get(key)

            # Handle None comparisons
            if self_val != other_val:
                # Special handling for exif_data
                if key == "exif_data":
                    if self_val != other_val:
                        differences[key] = {"self": self_val, "other": other_val}
                else:
                    differences[key] = {"self": self_val, "other": other_val}

        return differences

    def __str__(self) -> str:
        """
        Return a formatted, indented string representation of the image data.

        Returns:
            A multi-line string with labeled, indented metadata fields.
        """
        sections = [
            (
                "File Information",
                [
                    ("Name", self.file_name),
                    ("Path", self.file_path),
                    ("Hash", self.file_hash),
                ],
            ),
            (
                "File Properties",
                [
                    ("Size", f"{self.file_size:,} bytes" if self.file_size else None),
                    (
                        "Dimensions",
                        (
                            f"{self.width}x{self.height}"
                            if self.width and self.height
                            else None
                        ),
                    ),
                    ("MIME Type", self.mimetype),
                ],
            ),
            (
                "Metadata",
                [
                    ("Created", self.date_created),
                    ("Modified", self.date_modified),
                    ("Artist ID", self.artist_id),
                    ("Source URL", self.source_url),
                    ("Source Site", self.source_site),
                    ("License ID", self.license_id),
                ],
            ),
        ]

        # Add EXIF data section if it exists
        if self.exif_data:
            exif_fields = [(key, value) for key, value in sorted(self.exif_data.items())]
            sections.append(("EXIF Data", exif_fields))

        # Add CivitAI data section if it exists
        if self.civitai_data:
            civitai_fields = [
                (key, value) for key, value in sorted(self.civitai_data.items())
            ]
            sections.append(("CivitAI Data", civitai_fields))

        return self._format_sections(sections)

    def _format_sections(
        self, sections: list[tuple[str, list[tuple[str, Any]]]]
    ) -> str:
        """Format multiple sections of metadata."""
        lines = ["ImageData:"]
        for section_name, fields in sections:
            section_lines = self._format_section(section_name, fields)
            if len(section_lines) > 1:  # Only add if section has content
                lines.extend(section_lines)
        return "\n".join(lines)

    def _format_section(self, name: str, fields: list[tuple[str, Any]]) -> list[str]:
        """Format a single section with its fields."""
        lines = [f"\n  {name}:"]
        for label, value in fields:
            if value:
                lines.append(f"    {label}: {value}")
        return lines if len(lines) > 1 else []
