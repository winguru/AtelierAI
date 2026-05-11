import json
from pathlib import Path
from typing import List, Dict, Any, Optional
from datetime import date, datetime

from PIL import Image
from sqlalchemy.orm import Session
from sqlalchemy import func
from models import (
    AuthorityTerm,
    ImageConceptObservation,
    ImageModel,
    ObservationCertainty,
    ObservationSource,
    TagAuthority,
    VariantGroup,
    ImageVariantGroupMembership,
)
from image_processor import ImageProcessor, sanitize_display_filename
from atelierai.config import IMAGE_LIBRARY_PATH
from atelierai.utils.prompt_phrases import (
    build_prompt_tag_payload,
    extract_phrases as shared_extract_phrases,
    merge_prompt_tag_records,
    normalize_prompt_tag_name,
)
from image_data import ImageData
from civitai_enrichment import is_civitai_image_url, fetch_civitai_image_data
from utils.url_helpers import normalize_civitai_url
from services.gallery_tag_service import GalleryTagService
from services.metadata_extraction import extract_civitai_nsfw_level
from services.taxonomy_service import TaxonomyService

try:
    import blurhash  # pyright: ignore[reportMissingImports]
except Exception:
    blurhash = None

try:
    import imagehash  # pyright: ignore[reportMissingImports]
except Exception:
    imagehash = None


class ImageCollection:
    """A class to manage and process a collection of images in the library."""

    def __init__(self, db: Session):
        self.db = db
        self.library_path = Path(IMAGE_LIBRARY_PATH)
        self.gallery_tag_service = GalleryTagService()
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
            # CivitAI enrichment tracking
            "civitai_lookup_attempts": 0,
            "civitai_lookup_successes": 0,
            "civitai_lookup_failures": 0,
            # EXIF backfill tracking
            "exif_backfill_attempts": 0,
            "exif_backfill_successes": 0,
            "exif_backfill_failures": 0,
            # Generation software backfill tracking
            "generation_software_backfill_attempts": 0,
            "generation_software_backfill_successes": 0,
            "generation_software_backfill_failures": 0,
            # Legacy temp-name cleanup tracking
            "filename_backfill_attempts": 0,
            "filename_backfill_successes": 0,
            "filename_backfill_failures": 0,
            # Error count (actual error messages in self.error_messages)
            "errors": 0,
        }
        self.error_messages: List[str] = []

    @staticmethod
    def _normalize_text(value: Any) -> str:
        return str(value or "").strip().lower()

    def _get_or_create_authority(self, name: str, *, is_external: bool) -> TagAuthority:
        normalized = self._normalize_text(name)
        existing = (
            self.db.query(TagAuthority).filter(TagAuthority.name == normalized).first()
        )
        if existing is not None:
            return existing

        defaults = {
            "prompt": {
                "description": "Prompt-derived tags extracted from generation prompts.",
                "base_url": None,
            },
            "danbooru": {
                "description": "Danbooru tag authority and IDs.",
                "base_url": "https://danbooru.donmai.us",
            },
        }
        config = defaults.get(
            normalized,
            {
                "description": f"Auto-created authority '{normalized}'.",
                "base_url": None,
            },
        )

        authority = TagAuthority(
            name=normalized,
            description=str(config.get("description") or ""),
            is_external=is_external,
            base_url=config.get("base_url"),
        )
        self.db.add(authority)
        self.db.flush()
        return authority

    def _upsert_authority_term(
        self,
        *,
        authority_id: int,
        external_tag_id: Optional[int],
        external_name: str,
        metadata: Optional[dict[str, Any]] = None,
    ) -> bool:
        """Upsert an authority term by external id with name fallback; returns True when created."""
        normalized_external_name = self._normalize_text(external_name)
        if not normalized_external_name:
            return False

        now = datetime.utcnow()
        by_id = None
        if external_tag_id:
            by_id = (
                self.db.query(AuthorityTerm)
                .filter(
                    AuthorityTerm.authority_id == authority_id,
                    AuthorityTerm.external_tag_id == external_tag_id,
                )
                .first()
            )
        if by_id is not None:
            # If normalizing the name would collide with a different row, merge instead.
            if by_id.normalized_external_name != normalized_external_name:
                collision = (
                    self.db.query(AuthorityTerm)
                    .filter(
                        AuthorityTerm.authority_id == authority_id,
                        AuthorityTerm.normalized_external_name
                        == normalized_external_name,
                        AuthorityTerm.id != by_id.id,
                    )
                    .first()
                )
                if collision is not None:
                    # Merge: transfer FK refs, delete the old row FIRST to avoid
                    # UNIQUE constraint conflicts on (authority_id, external_tag_id).
                    self.db.query(ImageConceptObservation).filter(
                        ImageConceptObservation.authority_term_id == by_id.id
                    ).update(
                        {"authority_term_id": collision.id}, synchronize_session="fetch"
                    )
                    self.db.delete(by_id)
                    self.db.flush()
                    # Now safe to adopt the external_tag_id and update the surviving row.
                    if not collision.external_tag_id and external_tag_id is not None:
                        collision.external_tag_id = external_tag_id
                    collision.external_name = str(external_name)
                    existing_meta = (
                        collision.metadata_json
                        if isinstance(collision.metadata_json, dict)
                        else {}
                    )
                    collision.metadata_json = (
                        {**existing_meta, **(metadata or {})}
                        if metadata
                        else existing_meta
                    )
                    collision.last_seen_at = now
                    collision.updated_at = now
                    self.db.flush()
                    self.db.expire_all()
                    return False

            row = by_id
            row.external_name = str(external_name)
            row.normalized_external_name = normalized_external_name
            existing_meta = (
                row.metadata_json if isinstance(row.metadata_json, dict) else {}
            )
            row.metadata_json = (
                {**existing_meta, **(metadata or {})} if metadata else existing_meta
            )
            row.last_seen_at = now
            row.updated_at = now
            return False

        by_name = (
            self.db.query(AuthorityTerm)
            .filter(
                AuthorityTerm.authority_id == authority_id,
                AuthorityTerm.normalized_external_name == normalized_external_name,
            )
            .first()
        )
        if by_name is not None:
            row = by_name
            if not row.external_tag_id and external_tag_id is not None:
                # Check if another row already holds this external_tag_id under the same authority.
                tag_id_holder = (
                    self.db.query(AuthorityTerm)
                    .filter(
                        AuthorityTerm.authority_id == authority_id,
                        AuthorityTerm.external_tag_id == external_tag_id,
                        AuthorityTerm.id != row.id,
                    )
                    .first()
                )
                if tag_id_holder is not None:
                    # Merge: delete the tag_id holder FIRST to avoid UNIQUE constraint
                    # conflict on (authority_id, external_tag_id), then adopt the ID.
                    self.db.query(ImageConceptObservation).filter(
                        ImageConceptObservation.authority_term_id == tag_id_holder.id
                    ).update({"authority_term_id": row.id}, synchronize_session="fetch")
                    self.db.delete(tag_id_holder)
                    self.db.flush()
                    row.external_tag_id = external_tag_id
                else:
                    row.external_tag_id = external_tag_id
            row.external_name = str(external_name)
            existing_meta = (
                row.metadata_json if isinstance(row.metadata_json, dict) else {}
            )
            row.metadata_json = (
                {**existing_meta, **(metadata or {})} if metadata else existing_meta
            )
            row.last_seen_at = now
            row.updated_at = now
            return False

        self.db.add(
            AuthorityTerm(
                authority_id=authority_id,
                external_tag_id=external_tag_id,
                external_name=str(external_name),
                normalized_external_name=normalized_external_name,
                concept_id=None,
                metadata_json=metadata or {},
                created_at=now,
                updated_at=now,
                last_seen_at=now,
            )
        )
        self.db.flush()
        return True

    def _sync_image_tags_to_authority_terms(
        self,
        *,
        db_record: ImageModel,
        sidecar_data: dict[str, Any],
    ) -> dict[str, int]:
        """Ensure prompt/danbooru tags from image metadata exist as authority terms for tree views."""
        merged_payload = {
            **ImageData.from_db_record(db_record).to_dict(),
            **(sidecar_data if isinstance(sidecar_data, dict) else {}),
        }
        by_source = self.gallery_tag_service.extract_image_scope_tag_names(
            merged_payload,
            normalize_taxonomy_text=self._normalize_text,
        )

        prompt_records = self._coerce_prompt_tag_records(
            merged_payload.get("prompt_tags"),
            source_label="rescan:prompt_tags",
        )
        danbooru_records = self._coerce_danbooru_tag_records(
            merged_payload.get("danbooru_tags"),
            source_label="rescan:danbooru_tags",
        )
        prompt_record_by_name: dict[str, dict[str, Any]] = {}
        for record in prompt_records:
            normalized_name = normalize_prompt_tag_name(
                str(record.get("normalized_name") or record.get("name") or "")
            )
            if normalized_name:
                prompt_record_by_name[normalized_name] = record
        danbooru_record_by_name: dict[str, dict[str, Any]] = {}
        for record in danbooru_records:
            normalized_name = normalize_prompt_tag_name(
                str(record.get("normalized_name") or record.get("name") or "")
            )
            if normalized_name:
                danbooru_record_by_name[normalized_name] = record

        prompt_authority = self._get_or_create_authority("prompt", is_external=False)
        danbooru_authority = self._get_or_create_authority("danbooru", is_external=True)

        stats = {
            "prompt_terms_created": 0,
            "danbooru_terms_created": 0,
        }

        for name in sorted(by_source.get("prompt", set())):
            normalized_name = normalize_prompt_tag_name(name)
            if not normalized_name:
                continue
            record = prompt_record_by_name.get(normalized_name) or {}
            raw_tag_id = record.get("danbooru_tag_id")
            try:
                external_tag_id = (
                    int(raw_tag_id) if raw_tag_id not in (None, "") else None
                )
            except (TypeError, ValueError):
                external_tag_id = None
            metadata = {
                "origin": "image_rescan",
                "source": "prompt",
            }
            if external_tag_id is not None:
                metadata["mapped_danbooru_tag_id"] = external_tag_id
            created = self._upsert_authority_term(
                authority_id=int(prompt_authority.id),
                external_tag_id=external_tag_id,
                external_name=name,
                metadata=metadata,
            )
            if created:
                stats["prompt_terms_created"] += 1

        for name in sorted(by_source.get("danbooru", set())):
            normalized_name = normalize_prompt_tag_name(name)
            if not normalized_name:
                continue
            record = danbooru_record_by_name.get(normalized_name) or {}
            raw_tag_id = record.get("danbooru_tag_id")
            try:
                external_tag_id = (
                    int(raw_tag_id) if raw_tag_id not in (None, "") else None
                )
            except (TypeError, ValueError):
                external_tag_id = None
            metadata = {
                "origin": "image_rescan",
                "source": "danbooru",
            }
            raw_term_id = record.get("danbooru_term_id")
            try:
                term_id = int(raw_term_id) if raw_term_id not in (None, "") else None
            except (TypeError, ValueError):
                term_id = None
            if term_id is not None:
                metadata["mapped_danbooru_term_id"] = term_id
            created = self._upsert_authority_term(
                authority_id=int(danbooru_authority.id),
                external_tag_id=external_tag_id,
                external_name=name,
                metadata=metadata,
            )
            if created:
                stats["danbooru_terms_created"] += 1

        return stats

    @staticmethod
    def _coerce_danbooru_tag_records(
        value: Any,
        *,
        source_label: Optional[str],
    ) -> List[dict[str, Any]]:
        """Normalize danbooru tags into stable dict records."""
        if not isinstance(value, list):
            return []

        records: List[dict[str, Any]] = []
        for item in value:
            if isinstance(item, str):
                name = item.strip()
                if not name:
                    continue
                records.append(
                    {
                        "name": name,
                        "normalized_name": normalize_prompt_tag_name(name),
                        "source": "danbooru",
                        "source_label": source_label,
                        "confidence": 1.0,
                        "danbooru_tag_id": None,
                        "danbooru_term_id": None,
                    }
                )
                continue

            if not isinstance(item, dict):
                continue

            name_value = item.get("name") or item.get("tag") or item.get("label")
            if not isinstance(name_value, str) or not name_value.strip():
                continue

            record = dict(item)
            record["name"] = name_value.strip()
            record["normalized_name"] = normalize_prompt_tag_name(
                str(record.get("normalized_name") or record["name"])
            )
            record.setdefault("source", "danbooru")
            record.setdefault("source_label", source_label)
            record.setdefault("confidence", 1.0)
            if record.get("danbooru_tag_id") not in (None, ""):
                record["danbooru_tag_id"] = str(record.get("danbooru_tag_id"))
            else:
                record["danbooru_tag_id"] = None
            if record.get("danbooru_term_id") not in (None, ""):
                try:
                    record["danbooru_term_id"] = int(record.get("danbooru_term_id"))
                except (TypeError, ValueError):
                    record["danbooru_term_id"] = None
            else:
                record["danbooru_term_id"] = None
            records.append(record)

        merged: dict[str, dict[str, Any]] = {}
        for record in records:
            normalized_name = normalize_prompt_tag_name(
                str(record.get("normalized_name") or record.get("name") or "")
            )
            if not normalized_name:
                continue
            existing = merged.get(normalized_name)
            if existing is None:
                merged[normalized_name] = record
                continue

            existing_has_id = existing.get("danbooru_tag_id") not in (None, "")
            current_has_id = record.get("danbooru_tag_id") not in (None, "")
            if (not existing_has_id) and current_has_id:
                merged[normalized_name] = record
                continue

            if existing.get("danbooru_term_id") in (None, "") and record.get(
                "danbooru_term_id"
            ) not in (None, ""):
                existing["danbooru_term_id"] = record.get("danbooru_term_id")

        return list(merged.values())

    @staticmethod
    def _extract_mapped_danbooru_tags_from_prompt_tags(
        prompt_tags: List[dict[str, Any]],
    ) -> List[dict[str, Any]]:
        """Build danbooru tag records from prompt tags that already have mappings."""
        records: List[dict[str, Any]] = []
        seen: set[str] = set()
        for record in prompt_tags:
            if not isinstance(record, dict):
                continue
            if record.get("danbooru_tag_id") in (None, ""):
                continue
            name = str(record.get("name") or "").strip()
            normalized = normalize_prompt_tag_name(name)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            danbooru_term_id = record.get("danbooru_term_id")
            if danbooru_term_id not in (None, ""):
                try:
                    danbooru_term_id = int(danbooru_term_id)
                except (TypeError, ValueError):
                    danbooru_term_id = None
            else:
                danbooru_term_id = None
            records.append(
                {
                    "name": name,
                    "normalized_name": normalized,
                    "source": "danbooru",
                    "source_label": record.get("source_label")
                    or "rescan:prompt_mapping",
                    "confidence": (
                        record.get("confidence")
                        if isinstance(record.get("confidence"), (int, float))
                        else 1.0
                    ),
                    "danbooru_tag_id": str(record.get("danbooru_tag_id")),
                    "danbooru_term_id": danbooru_term_id,
                }
            )
        return records

    @staticmethod
    def _coerce_datetime(value: Any) -> Optional[datetime]:
        """Convert common date/datetime JSON values into Python datetime."""
        if value is None:
            return None
        if isinstance(value, datetime):
            return value
        if isinstance(value, date):
            return datetime.combine(value, datetime.min.time())
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return None
            try:
                return datetime.fromisoformat(raw)
            except ValueError:
                return None
        return None

    @staticmethod
    def _normalize_expected_filename(
        candidate_name: Optional[str], fallback_ext: str
    ) -> Optional[str]:
        """Build a safe filename preserving extension when missing."""
        return sanitize_display_filename(candidate_name, fallback_ext=fallback_ext)

    def _backfill_filename_if_needed(
        self,
        db_record: ImageModel,
        image_path: Path,
        processor: ImageProcessor,
    ) -> None:
        """Normalize malformed display names and replace legacy temp_* names."""
        current_name = getattr(db_record, "file_name", None)
        if not isinstance(current_name, str):
            return

        fallback_ext = image_path.suffix or (processor.extension or "")
        normalized_current = self._normalize_expected_filename(
            current_name, fallback_ext
        )
        if normalized_current and normalized_current != current_name.strip():
            self.results["filename_backfill_attempts"] += 1
            try:
                self.db.query(ImageModel).filter(ImageModel.id == db_record.id).update(
                    {ImageModel.file_name: normalized_current},
                    synchronize_session=False,
                )
                self.db.flush()
                self.db.refresh(db_record)

                processor.save_json_metadata(image_path, db_record)
                self.db.commit()
                self.results["filename_backfill_successes"] += 1
            except Exception as e:
                self.results["filename_backfill_failures"] += 1
                self.error_messages.append(
                    f"Could not normalize filename for {image_path.name}: {e}"
                )
            return

        if not current_name.lower().startswith("temp_"):
            return

        source_url_value = getattr(db_record, "source_url", None)
        if not isinstance(source_url_value, str) or not source_url_value:
            return

        if not is_civitai_image_url(source_url_value):
            return

        self.results["filename_backfill_attempts"] += 1

        try:
            civitai_data = fetch_civitai_image_data(source_url_value) or {}
            expected = self._normalize_expected_filename(
                civitai_data.get("image_name"),
                image_path.suffix or (processor.extension or ""),
            )

            if not expected or expected == current_name:
                self.results["filename_backfill_failures"] += 1
                return

            self.db.query(ImageModel).filter(ImageModel.id == db_record.id).update(
                {ImageModel.file_name: expected},
                synchronize_session=False,
            )
            self.db.flush()
            self.db.refresh(db_record)

            processor.save_json_metadata(image_path, db_record)
            self.db.commit()
            self.results["filename_backfill_successes"] += 1
        except Exception as e:
            self.results["filename_backfill_failures"] += 1
            self.error_messages.append(
                f"Could not backfill filename for {image_path.name}: {e}"
            )

    def _hydrate_missing_observations_if_needed(
        self,
        db_record: ImageModel,
        image_path: Path,
        processor: ImageProcessor,
    ) -> None:
        """Create image_concept_observations for tags present in json_metadata
        but not yet represented in the relational observation table.

        Only creates observations for authority_terms that already have a
        ``concept_id``.  Tags without a linked concept are skipped so they
        can be manually assigned later.
        """
        # Build the merged payload (DB record + sidecar JSON)
        sidecar_data = self._load_sidecar_json(image_path.with_suffix(".json"))
        merged_payload = {
            **ImageData.from_db_record(db_record).to_dict(),
            **(sidecar_data if isinstance(sidecar_data, dict) else {}),
        }

        tags_by_source = self.gallery_tag_service.extract_image_scope_tag_names(
            merged_payload,
            normalize_taxonomy_text=self._normalize_text,
        )

        if not any(tags_by_source.values()):
            return

        self.results["observation_hydration_attempts"] = self.results.get(
            "observation_hydration_attempts", 0
        ) + 1

        tax = TaxonomyService()
        now = datetime.utcnow()
        observations_created = 0
        observations_skipped = 0
        _seen: set[tuple[int, int, int]] = set()

        try:
            for source, tag_names in tags_by_source.items():
                if not tag_names:
                    continue

                authority = tax.get_or_create_authority(self.db, source)
                authority_id = int(authority.id)
                normalized_names = {self._normalize_text(n): n for n in tag_names if n}

                if not normalized_names:
                    continue

                # Batch-load matching authority_terms (concept_id may be None — concepts
                # are optional organizational structure, not required for a valid tag)
                terms = (
                    self.db.query(AuthorityTerm)
                    .filter(
                        AuthorityTerm.authority_id == authority_id,
                        AuthorityTerm.normalized_external_name.in_(normalized_names),
                    )
                    .all()
                )

                for term in terms:
                    concept_id = term.concept_id  # may be None

                    obs_key = (db_record.id, term.id)
                    if obs_key in _seen:
                        continue

                    # Check for existing observation keyed by authority_term_id
                    existing = (
                        self.db.query(ImageConceptObservation.id)
                        .filter(
                            ImageConceptObservation.image_id == db_record.id,
                            ImageConceptObservation.authority_term_id == term.id,
                        )
                        .first()
                    )
                    if existing is not None:
                        _seen.add(obs_key)
                        observations_skipped += 1
                        continue

                    self.db.add(
                        ImageConceptObservation(
                            image_id=db_record.id,
                            concept_id=concept_id,
                            authority_id=authority_id,
                            authority_term_id=term.id,
                            source_type=ObservationSource.IMPORT,
                            certainty_label=ObservationCertainty.LIKELY,
                            is_present=True,
                            is_curated=False,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                    _seen.add(obs_key)
                    observations_created += 1

            if observations_created:
                self.db.flush()

            self.results["observations_created"] = self.results.get(
                "observations_created", 0
            ) + observations_created
            self.results["observations_skipped"] = self.results.get(
                "observations_skipped", 0
            ) + observations_skipped

        except Exception as e:
            self.db.rollback()
            self.error_messages.append(
                f"Could not hydrate observations for {image_path.name}: {e}"
            )

    @staticmethod
    def _load_sidecar_json(sidecar_path: Path) -> dict[str, Any]:
        """Load sidecar JSON data, returning an empty dict on read/parse issues."""
        if not sidecar_path.exists():
            return {}

        try:
            with open(sidecar_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            return loaded if isinstance(loaded, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _civitai_payload_has_nsfw_level(payload: Any) -> bool:
        """Return True when a civitai payload already includes a usable NSFW level."""
        if not isinstance(payload, dict):
            return False

        direct_value = payload.get("nsfwLevel")
        if direct_value is not None:
            return True

        image_value = payload.get("image")
        if isinstance(image_value, dict) and image_value.get("nsfwLevel") is not None:
            return True

        meta_value = payload.get("meta")
        if isinstance(meta_value, dict) and meta_value.get("nsfwLevel") is not None:
            return True

        return False

    def _hydrate_missing_exif_if_needed(
        self,
        db_record: ImageModel,
        image_path: Path,
        processor: ImageProcessor,
    ) -> None:
        """Backfill EXIF when sidecar is missing exif_data or generation text fields."""
        sidecar_data = self._load_sidecar_json(image_path.with_suffix(".json"))
        sidecar_exif = sidecar_data.get("exif_data")

        existing_exif = sidecar_exif if isinstance(sidecar_exif, dict) else {}

        def has_generation_fields(exif_dict: dict[str, Any]) -> bool:
            lower_keys = {str(k).strip().lower() for k in exif_dict.keys()}
            return any(
                k in lower_keys
                for k in {"parameters", "comment", "comments", "usercomment"}
            )

        # Treat absent/empty exif as missing so the file is normalized once.
        exif_present = sidecar_exif not in (None, {}, [])
        if exif_present and has_generation_fields(existing_exif):
            return

        self.results["exif_backfill_attempts"] += 1

        try:
            exif_data = (
                processor.exif_data if isinstance(processor.exif_data, dict) else {}
            )

            self.db.query(ImageModel).filter(ImageModel.id == db_record.id).update(
                {ImageModel.exif_data: exif_data},
                synchronize_session=False,
            )
            self.db.flush()
            self.db.refresh(db_record)

            processor.save_json_metadata(image_path, db_record)
            self.db.commit()
            self.results["exif_backfill_successes"] += 1
        except Exception as e:
            self.results["exif_backfill_failures"] += 1
            self.error_messages.append(
                f"Could not backfill EXIF for {image_path.name}: {e}"
            )

    def _hydrate_missing_metadata_fields(
        self,
        db_record: ImageModel,
        image_path: Path,
        processor: ImageProcessor,
    ) -> None:
        """Check for missing metadata fields and run field-specific collectors.

        This is intentionally the single extensible place to add new metadata
        backfill handlers (e.g. AI analysis outputs) in the future.
        """
        self._hydrate_missing_exif_if_needed(db_record, image_path, processor)
        self._hydrate_missing_generation_software_if_needed(
            db_record, image_path, processor
        )
        self._enrich_from_civitai_if_needed(db_record, image_path, processor)
        self._hydrate_local_perceptual_metadata_if_needed(
            db_record, image_path, processor
        )
        self._backfill_filename_if_needed(db_record, image_path, processor)
        self._hydrate_missing_observations_if_needed(db_record, image_path, processor)

    @staticmethod
    def _local_perceptual_metadata_present(payload: Any) -> bool:
        if not isinstance(payload, dict):
            return False

        blurhash_payload = payload.get("blurhash")
        imagehash_payload = payload.get("imagehash")
        blurhash_ok = isinstance(blurhash_payload, dict) and bool(
            str(blurhash_payload.get("4x4") or "").strip()
        )

        if not isinstance(imagehash_payload, dict):
            return False

        required_algorithms = ("phash", "dhash", "whash")
        imagehash_ok = True
        for algorithm in required_algorithms:
            algorithm_payload = imagehash_payload.get(algorithm)
            if (
                not isinstance(algorithm_payload, dict)
                or not str(algorithm_payload.get("8x8") or "").strip()
            ):
                imagehash_ok = False
                break

        return blurhash_ok and imagehash_ok

    @staticmethod
    def _encode_blurhash_4x4(image_rgb: Image.Image) -> Optional[str]:
        if blurhash is None:
            return None

        encode_fn = getattr(blurhash, "encode", None)
        if not callable(encode_fn):
            return None

        analysis_image = image_rgb.copy()
        analysis_image.thumbnail((128, 128), Image.Resampling.LANCZOS)
        width, height = analysis_image.size
        flat_pixels = list(analysis_image.getdata())
        pixel_rows = [
            [
                tuple(pixel)
                for pixel in flat_pixels[row_index * width : (row_index + 1) * width]
            ]
            for row_index in range(height)
        ]

        for kwargs in (
            {"x_components": 4, "y_components": 4},
            {"components_x": 4, "components_y": 4},
        ):
            for source in (analysis_image, pixel_rows):
                try:
                    encoded = encode_fn(source, **kwargs)
                except Exception:
                    continue
                if isinstance(encoded, str) and encoded.strip():
                    return encoded.strip()
        return None

    @staticmethod
    def _compute_local_perceptual_metadata(image_path: Path) -> dict[str, Any]:
        if imagehash is None and blurhash is None:
            return {}

        with Image.open(image_path) as handle:
            normalized_image = handle.convert("RGB")
            payload: dict[str, Any] = {}

            blurhash_value = ImageCollection._encode_blurhash_4x4(normalized_image)
            if blurhash_value:
                payload["blurhash"] = {
                    "4x4": blurhash_value,
                }

            if imagehash is not None:
                payload["imagehash"] = {
                    "phash": {
                        "8x8": str(imagehash.phash(normalized_image, hash_size=8))
                    },
                    "dhash": {
                        "8x8": str(imagehash.dhash(normalized_image, hash_size=8))
                    },
                    "whash": {
                        "8x8": str(imagehash.whash(normalized_image, hash_size=8))
                    },
                }

            return payload

    def _hydrate_local_perceptual_metadata_if_needed(
        self,
        db_record: ImageModel,
        image_path: Path,
        processor: ImageProcessor,
    ) -> None:
        if str(getattr(db_record, "mimetype", "") or "").lower().startswith("video/"):
            return

        sidecar_data = self._load_sidecar_json(image_path.with_suffix(".json"))
        existing_json_metadata = (
            dict(db_record.json_metadata)
            if isinstance(db_record.json_metadata, dict)
            else {}
        )

        if self._local_perceptual_metadata_present(
            sidecar_data
        ) and self._local_perceptual_metadata_present(existing_json_metadata):
            return

        try:
            perceptual_metadata = self._compute_local_perceptual_metadata(image_path)
            if not perceptual_metadata:
                return

            merged_json_metadata = dict(existing_json_metadata)
            for key, value in perceptual_metadata.items():
                existing_value = merged_json_metadata.get(key)
                if isinstance(existing_value, dict) and isinstance(value, dict):
                    merged_json_metadata[key] = {**existing_value, **value}
                else:
                    merged_json_metadata[key] = value

            self.db.query(ImageModel).filter(ImageModel.id == db_record.id).update(
                {ImageModel.json_metadata: merged_json_metadata},
                synchronize_session=False,
            )
            self.db.flush()
            self.db.refresh(db_record)

            processor.save_json_metadata(
                image_path,
                db_record,
                additional_data=perceptual_metadata,
            )
            self.db.commit()
        except Exception as e:
            self.error_messages.append(
                f"Could not backfill local perceptual metadata for {image_path.name}: {e}"
            )

    def _hydrate_missing_generation_software_if_needed(
        self,
        db_record: ImageModel,
        image_path: Path,
        processor: ImageProcessor,
    ) -> None:
        """Backfill generation_software when missing from sidecar metadata."""
        sidecar_data = self._load_sidecar_json(image_path.with_suffix(".json"))
        if sidecar_data.get("generation_software"):
            return

        self.results["generation_software_backfill_attempts"] += 1

        try:
            inferred = processor.infer_generation_software(
                source_url=getattr(db_record, "source_url", None),
                existing_sidecar=sidecar_data,
                db_json_metadata=(
                    db_record.json_metadata
                    if isinstance(db_record.json_metadata, dict)
                    else None
                ),
            )
            if not inferred:
                self.results["generation_software_backfill_failures"] += 1
                return

            processor.save_json_metadata(
                image_path,
                db_record,
                additional_data={"generation_software": inferred},
            )
            self.db.commit()
            self.results["generation_software_backfill_successes"] += 1
        except Exception as e:
            self.results["generation_software_backfill_failures"] += 1
            self.error_messages.append(
                f"Could not backfill generation_software for {image_path.name}: {e}"
            )

    @classmethod
    def _extract_prompt_phrases(
        cls,
        prompt: str,
        min_words: int = 2,
        max_words: int = 4,
    ) -> List[str]:
        """Extract prompt phrases using analyzer-style heuristics."""
        return shared_extract_phrases(prompt, min_words=min_words, max_words=max_words)

    @staticmethod
    def _coerce_prompt_tag_records(
        value: Any,
        *,
        source_label: Optional[str] = None,
    ) -> List[dict[str, Any]]:
        """Coerce legacy prompt tag payloads into the canonical record shape."""
        records: List[dict[str, Any]] = []
        if not isinstance(value, list):
            return records

        for item in value:
            if isinstance(item, str):
                name = item.strip()
                if not name:
                    continue
                records.append(
                    {
                        "name": name,
                        "normalized_name": normalize_prompt_tag_name(name),
                        "kind": "concept",
                        "source": "prompt",
                        "prompt_role": "positive",
                        "source_type": "metadata",
                        "source_label": source_label,
                        "confidence": 1.0,
                        "danbooru_tag_id": None,
                        "danbooru_term_id": None,
                    }
                )
                continue

            if not isinstance(item, dict):
                continue

            name_value = item.get("name") or item.get("tag") or item.get("label")
            if not isinstance(name_value, str) or not name_value.strip():
                continue

            record = dict(item)
            record["name"] = name_value.strip()
            record["normalized_name"] = normalize_prompt_tag_name(
                str(record.get("normalized_name") or record["name"])
            )
            record.setdefault("kind", "concept")
            record.setdefault("source", "prompt")
            record.setdefault("prompt_role", "positive")
            record.setdefault("source_type", "metadata")
            record.setdefault("source_label", source_label)
            record.setdefault("confidence", 1.0)
            record.setdefault("danbooru_tag_id", None)
            record.setdefault("danbooru_term_id", None)
            records.append(record)

        return merge_prompt_tag_records(records)

    def _lookup_danbooru_prompt_tag_mappings(
        self,
        normalized_names: List[str],
    ) -> dict[str, dict[str, Any]]:
        """Resolve exact Danbooru authority-term matches for prompt tag names."""
        cleaned_names = sorted(
            {
                normalize_prompt_tag_name(name)
                for name in normalized_names
                if normalize_prompt_tag_name(name)
            }
        )
        if not cleaned_names:
            return {}

        rows = (
            self.db.query(AuthorityTerm)
            .join(TagAuthority, TagAuthority.id == AuthorityTerm.authority_id)
            .filter(
                TagAuthority.name == "danbooru",
                AuthorityTerm.normalized_external_name.in_(cleaned_names),
            )
            .all()
        )

        mappings: dict[str, dict[str, Any]] = {}
        for row in rows:
            mappings[str(row.normalized_external_name)] = {
                "danbooru_tag_id": row.external_tag_id,
                "danbooru_term_id": int(row.id),
            }
        return mappings

    def _apply_danbooru_prompt_tag_mappings(
        self,
        records: List[dict[str, Any]],
    ) -> List[dict[str, Any]]:
        """Copy prompt tag records and attach any exact Danbooru mappings."""
        mappings = self._lookup_danbooru_prompt_tag_mappings(
            [
                str(record.get("normalized_name") or record.get("name") or "")
                for record in records
            ]
        )
        if not mappings:
            return [dict(record) for record in records]

        enriched_records: List[dict[str, Any]] = []
        for record in records:
            enriched = dict(record)
            normalized_name = normalize_prompt_tag_name(
                str(enriched.get("normalized_name") or enriched.get("name") or "")
            )
            if normalized_name in mappings:
                enriched.update(mappings[normalized_name])
            enriched_records.append(enriched)
        return enriched_records

    def _build_prompt_analysis(
        self,
        prompt_text: str,
        *,
        prompt_role: str,
        source_type: Optional[str],
        source_label: Optional[str],
        min_words: int = 2,
        max_words: int = 4,
    ) -> dict[str, Any]:
        """Build structured prompt analysis data and enrich it with Danbooru mappings."""
        analysis = build_prompt_tag_payload(
            prompt_text,
            prompt_role=prompt_role,
            source_type=source_type,
            source_label=source_label,
            min_words=min_words,
            max_words=max_words,
        )
        analysis["concepts"] = self._apply_danbooru_prompt_tag_mappings(
            list(analysis.get("concepts") or [])
        )
        analysis["phrases"] = self._apply_danbooru_prompt_tag_mappings(
            list(analysis.get("phrases") or [])
        )
        analysis["prompt_tags"] = merge_prompt_tag_records(
            self._apply_danbooru_prompt_tag_mappings(
                list(analysis.get("prompt_tags") or [])
            )
        )
        return analysis

    def _hydrate_generation_prompt_metadata(self, db_record: ImageModel) -> int:
        """Populate parsed prompt metadata fields on stored generation prompts."""
        updated_count = 0
        for process in getattr(db_record, "generation_processes", []) or []:
            for prompt in getattr(process, "prompts", []) or []:
                prompt_text = str(getattr(prompt, "prompt_text", "") or "").strip()
                if not prompt_text:
                    continue

                prompt_role = (
                    str(getattr(prompt, "prompt_role", "positive") or "positive")
                    .strip()
                    .lower()
                    or "positive"
                )
                source_type = getattr(prompt, "source_type", None)
                source_label = f"generation_prompt:process={getattr(process, 'id', 'na')}:prompt={getattr(prompt, 'id', 'na')}"
                analysis = self._build_prompt_analysis(
                    prompt_text,
                    prompt_role=prompt_role,
                    source_type=source_type if isinstance(source_type, str) else None,
                    source_label=source_label,
                )

                prompt.prompt_style = analysis.get("prompt_style")
                prompt.parsed_concepts_json = analysis.get("concepts")
                prompt.parsed_phrases_json = analysis.get("phrases")
                updated_count += 1

        return updated_count

    @staticmethod
    def _extract_prompt_from_structured_payload(prompt: str) -> tuple[bool, str]:
        """Extract likely prompt text from structured JSON payloads.

        Some generators store full workflow graphs under fields like `Prompt`.
        If we parse those blobs as plain prompt text we end up extracting node
        and sampler metadata as prompt tags. This helper returns only likely
        positive prompt strings when a JSON payload is detected.
        """
        if not isinstance(prompt, str):
            return False, ""

        raw = prompt.strip()
        if not raw or raw[0] not in "[{":
            return False, ""

        try:
            payload = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            return False, ""

        extracted: List[str] = []
        seen: set[str] = set()

        def add_candidate(value: Any) -> None:
            if not isinstance(value, str):
                return
            cleaned = value.strip()
            if not cleaned:
                return
            key = cleaned.lower()
            if key in seen:
                return
            seen.add(key)
            extracted.append(cleaned)

        def walk(node: Any, parent_key: str = "") -> None:
            key_lower = str(parent_key or "").strip().lower()

            if isinstance(node, dict):
                class_type = str(node.get("class_type") or "").strip().lower()
                meta = node.get("_meta") if isinstance(node.get("_meta"), dict) else {}
                title = str(meta.get("title") or "").strip().lower()
                inputs = (
                    node.get("inputs") if isinstance(node.get("inputs"), dict) else {}
                )

                text_input = inputs.get("text")
                if isinstance(text_input, str) and text_input.strip():
                    if "cliptextencode" in class_type:
                        if "negative" not in title:
                            add_candidate(text_input)
                    elif key_lower in {
                        "prompt",
                        "positive_prompt",
                        "positiveprompt",
                        "text_positive",
                    }:
                        add_candidate(text_input)

                for child_key, child_value in node.items():
                    walk(child_value, str(child_key))
                return

            if isinstance(node, list):
                for item in node:
                    walk(item, parent_key)
                return

            if isinstance(node, str):
                if key_lower in {
                    "prompt",
                    "positive_prompt",
                    "positiveprompt",
                    "text_positive",
                }:
                    add_candidate(node)

        walk(payload)
        return True, "\n".join(extracted).strip()

    @staticmethod
    def _trim_prompt_payload(prompt: str) -> str:
        """Trim known generation payload suffixes to isolate positive prompt text."""
        if not isinstance(prompt, str):
            return ""

        cleaned = prompt.strip()
        if not cleaned:
            return ""

        is_structured_payload, extracted_prompt = (
            ImageCollection._extract_prompt_from_structured_payload(cleaned)
        )
        if is_structured_payload:
            return extracted_prompt

        for marker in ("\nNegative prompt:", "Negative prompt:"):
            idx = cleaned.find(marker)
            if idx >= 0:
                cleaned = cleaned[:idx].strip()
                break

        for marker in ("\nSteps:", " Steps:"):
            idx = cleaned.find(marker)
            if idx >= 0:
                cleaned = cleaned[:idx].strip()
                break

        return cleaned

    def _collect_generation_prompt_candidates(
        self,
        db_record: ImageModel,
        processor: ImageProcessor,
        image_path: Path,
    ) -> List[tuple[str, str]]:
        """Collect positive prompt text candidates from generation tables, EXIF, and sidecar."""
        candidates: List[tuple[str, str]] = []
        seen: set[str] = set()

        def add_candidate(source: str, value: Any) -> None:
            if not isinstance(value, str):
                return
            trimmed = self._trim_prompt_payload(value)
            if not trimmed:
                return
            key = trimmed.lower()
            if key in seen:
                return
            seen.add(key)
            candidates.append((source, trimmed))

        for process in getattr(db_record, "generation_processes", []) or []:
            for prompt in getattr(process, "prompts", []) or []:
                prompt_role = (
                    str(getattr(prompt, "prompt_role", "") or "").strip().lower()
                )
                if prompt_role and prompt_role != "positive":
                    continue
                add_candidate(
                    f"generation_prompt:process={getattr(process, 'id', 'na')}:prompt={getattr(prompt, 'id', 'na')}",
                    getattr(prompt, "prompt_text", None),
                )

        exif_data = processor.exif_data if isinstance(processor.exif_data, dict) else {}
        for key in (
            "Prompt",
            "prompt",
            "parameters",
            "UserComment",
            "comment",
            "comments",
        ):
            add_candidate(f"processor_exif:{key}", exif_data.get(key))

        sidecar_data = self._load_sidecar_json(image_path.with_suffix(".json"))
        sidecar_exif = (
            sidecar_data.get("exif_data") if isinstance(sidecar_data, dict) else None
        )
        if isinstance(sidecar_exif, dict):
            for key in (
                "Prompt",
                "prompt",
                "parameters",
                "UserComment",
                "comment",
                "comments",
            ):
                add_candidate(f"sidecar_exif:{key}", sidecar_exif.get(key))

        return candidates

    def _ensure_variant_group_for_hash(self, new_image: ImageModel) -> None:
        """Auto-create a hash_duplicate variant group when file_hash matches an existing image.

        If another active image shares the same file_hash, ensures both images belong
        to the same variant group (creating the group if needed). Idempotent — safe to
        call multiple times.
        """
        if not new_image.file_hash or not new_image.id:
            return

        try:
            # Find other active images with the same file_hash
            sibling = (
                self.db.query(ImageModel)
                .filter(
                    ImageModel.file_hash == new_image.file_hash,
                    ImageModel.id != new_image.id,
                    ImageModel.image_status != "tombstoned",
                )
                .first()
            )
            if sibling is None:
                return  # No duplicate, nothing to group

            group_key = f"hash:{new_image.file_hash}"

            # Check if a variant group already exists for this hash
            existing_group = (
                self.db.query(VariantGroup)
                .filter(VariantGroup.group_key == group_key)
                .first()
            )

            if existing_group is None:
                # Create new variant group
                existing_group = VariantGroup(
                    group_key=group_key,
                    group_type="hash_duplicate",
                    group_label="Duplicate File",
                    cover_preference="sort_order",
                )
                self.db.add(existing_group)
                self.db.flush()  # Need group ID for memberships

                # Add the sibling (previously existing image) as first member
                sibling_membership = ImageVariantGroupMembership(
                    image_id=sibling.id,
                    group_id=existing_group.id,
                    role_in_group="member",
                    sort_index=0,
                    source="auto_hash",
                )
                self.db.add(sibling_membership)

            # Check if new_image is already a member of this group
            existing_membership = (
                self.db.query(ImageVariantGroupMembership)
                .filter(
                    ImageVariantGroupMembership.image_id == new_image.id,
                    ImageVariantGroupMembership.group_id == existing_group.id,
                )
                .first()
            )
            if existing_membership is None:
                # Get next sort index
                max_sort = (
                    self.db.query(func.max(ImageVariantGroupMembership.sort_index))
                    .filter(
                        ImageVariantGroupMembership.group_id == existing_group.id
                    )
                    .scalar()
                ) or 0

                new_membership = ImageVariantGroupMembership(
                    image_id=new_image.id,
                    group_id=existing_group.id,
                    role_in_group="member",
                    sort_index=max_sort + 1,
                    source="auto_hash",
                )
                self.db.add(new_membership)

            self.db.flush()
            print(
                f"Auto-grouped image {new_image.id} into variant group "
                f"'{group_key}' (group_id={existing_group.id})"
            )
        except Exception as exc:
            # Fail open — never block image import
            print(f"Warning: could not auto-create variant group: {exc}")

    def _ensure_variant_group_for_civitai_image_id(
        self, db_record: ImageModel, civitai_image_id: int
    ) -> None:
        """Auto-create a civitai_multi_resource variant group when civitai_image_id matches an existing image.

        If another active image shares the same civitai_image_id, ensures both images belong
        to the same variant group (creating the group if needed). Idempotent — safe to
        call multiple times.
        """
        if not civitai_image_id or not db_record.id:
            return

        try:
            # Find other active images with the same civitai_image_id
            sibling = (
                self.db.query(ImageModel)
                .filter(
                    ImageModel.civitai_image_id == civitai_image_id,
                    ImageModel.id != db_record.id,
                    ImageModel.image_status != "tombstoned",
                )
                .first()
            )
            if sibling is None:
                return  # No duplicate, nothing to group

            group_key = f"civitai:{civitai_image_id}"

            # Check if a variant group already exists for this image id
            existing_group = (
                self.db.query(VariantGroup)
                .filter(VariantGroup.group_key == group_key)
                .first()
            )

            if existing_group is None:
                # Create new variant group
                existing_group = VariantGroup(
                    group_key=group_key,
                    group_type="civitai_multi_resource",
                    group_label="CivitAI Multi-Resource",
                    cover_preference="sort_order",
                )
                self.db.add(existing_group)
                self.db.flush()  # Need group ID for memberships

                # Add the sibling (previously existing image) as first member
                sibling_membership = ImageVariantGroupMembership(
                    image_id=sibling.id,
                    group_id=existing_group.id,
                    role_in_group="member",
                    sort_index=0,
                    source="auto_civitai",
                )
                self.db.add(sibling_membership)

            # Check if db_record is already a member of this group
            existing_membership = (
                self.db.query(ImageVariantGroupMembership)
                .filter(
                    ImageVariantGroupMembership.image_id == db_record.id,
                    ImageVariantGroupMembership.group_id == existing_group.id,
                )
                .first()
            )
            if existing_membership is None:
                max_sort = (
                    self.db.query(func.max(ImageVariantGroupMembership.sort_index))
                    .filter(
                        ImageVariantGroupMembership.group_id == existing_group.id
                    )
                    .scalar()
                ) or 0

                new_membership = ImageVariantGroupMembership(
                    image_id=db_record.id,
                    group_id=existing_group.id,
                    role_in_group="member",
                    sort_index=max_sort + 1,
                    source="auto_civitai",
                )
                self.db.add(new_membership)

            self.db.flush()
            print(
                f"Auto-grouped image {db_record.id} into CivitAI variant group "
                f"'{group_key}' (group_id={existing_group.id})"
            )
        except Exception as exc:
            # Fail open — never block enrichment
            print(f"Warning: could not auto-create CivitAI variant group: {exc}")

    def _import_new_image_with_processor(
        self,
        processor: ImageProcessor,
        image_file: Path,
        original_filename: str,
        artist_name: Optional[str] = None,
        source_url: Optional[str] = None,
        license_id: Optional[int] = None,
    ) -> tuple[ImageModel, Path]:
        """Import a newly discovered image using the same pipeline used during scan."""
        file_hash = processor.file_hash
        file_extension = (
            (
                processor.mime_to_extension(processor.mimetype)
                if processor.mimetype
                else None
            )
            or image_file.suffix.lower()
            or processor.extension
            or ".jpg"
        )
        expected_filename = f"{file_hash}{file_extension}"

        if image_file.name != expected_filename:
            print(f"Renaming to '{expected_filename}'")
            processor.save_to_library()
            self.results["files_renamed"] += 1
            relative_path = expected_filename
            final_file_path = processor.original_path
        else:
            relative_path = image_file.name
            final_file_path = image_file

        # Correct the display filename extension to match the actual detected
        # content type.  Providers like CivitAI sometimes report the wrong
        # mimeType in their API (e.g. image/png for a file that downloads as
        # JPEG), so the original_filename may carry a stale extension.
        if original_filename:
            fn_suffix = Path(original_filename).suffix.lower()
            if fn_suffix and fn_suffix != file_extension:
                original_filename = Path(original_filename).stem + file_extension

        artist_obj = None
        if artist_name:
            artist_obj = ImageProcessor.find_or_create_artist(self.db, artist_name)

        new_image = processor.create_database_record(
            relative_filepath=relative_path,
            original_filename=original_filename,
            artist_obj=artist_obj,
            source_url=source_url,
            license_id=license_id,
        )
        self.db.add(new_image)
        self.db.flush()  # Flush to get the ID

        processor.save_json_metadata(processor.original_path, new_image)
        self.results["json_files_created"] += 1
        print(f"Created JSON file for: {final_file_path.name}")

        self._hydrate_missing_metadata_fields(
            db_record=new_image,
            image_path=processor.original_path,
            processor=processor,
        )

        # Auto-create hash_duplicate variant group if another active image
        # shares the same file_hash.
        self._ensure_variant_group_for_hash(new_image)

        self.results["images_added"] += 1
        return new_image, final_file_path

    def ingest_uploaded_file(
        self,
        uploaded_file_path: Path,
        original_filename: str,
        artist_name: Optional[str] = None,
        source_url: Optional[str] = None,
        license_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Ingest one uploaded file through the same import steps used by scan."""
        processor = ImageProcessor(
            str(uploaded_file_path), self.db, str(self.library_path)
        )
        existing_image = processor.find_in_database()

        if existing_image is not None:
            existing_status = (
                getattr(existing_image, "image_status", None) or "active"
            ).lower()
            if existing_status == "tombstoned":
                return {
                    "images_added": 0,
                    "images_skipped": 1,
                    "json_files_created": 0,
                    "image_id": existing_image.id,
                    "skip_reason": "tombstoned_file_hash",
                    "existing_image_id": existing_image.id,
                    "existing_file_hash": existing_image.file_hash,
                    "existing_file_path": existing_image.file_path,
                    "existing_source_url": existing_image.source_url,
                }

            if existing_status == "deleted":
                existing_image.image_status = "active"
                existing_image.status_reason = None
                existing_image.replaced_by_image_id = None
                if source_url:
                    existing_image.source_url = normalize_civitai_url(source_url)
                if license_id is not None:
                    existing_image.license_id = license_id
                if artist_name:
                    artist_obj = ImageProcessor.find_or_create_artist(
                        self.db, artist_name
                    )
                    existing_image.artist_id = artist_obj.id

                self.db.flush()
                self.db.commit()
                return {
                    "images_added": 1,
                    "images_skipped": 0,
                    "json_files_created": 0,
                    "image_id": existing_image.id,
                    "skip_reason": None,
                    "existing_image_id": existing_image.id,
                    "existing_file_hash": existing_image.file_hash,
                    "existing_file_path": existing_image.file_path,
                    "existing_source_url": existing_image.source_url,
                }

            existing_image_path = self.library_path / str(existing_image.file_path)
            self._ensure_json_file_exists(existing_image_path, existing_image)
            self._hydrate_missing_metadata_fields(
                db_record=existing_image,
                image_path=existing_image_path,
                processor=processor,
            )
            return {
                "images_added": 0,
                "images_skipped": 1,
                "json_files_created": 0,
                "image_id": existing_image.id,
                "skip_reason": "existing_file_hash",
                "existing_image_id": existing_image.id,
                "existing_file_hash": existing_image.file_hash,
                "existing_file_path": existing_image.file_path,
                "existing_source_url": existing_image.source_url,
            }

        # For uploads, trust explicit form metadata over absent JSON metadata.
        new_image, _ = self._import_new_image_with_processor(
            processor=processor,
            image_file=uploaded_file_path,
            original_filename=original_filename,
            artist_name=artist_name,
            source_url=source_url,
            license_id=license_id,
        )
        return {
            "images_added": 1,
            "images_skipped": 0,
            "json_files_created": 1,
            "image_id": new_image.id if new_image is not None else None,
            "skip_reason": None,
        }

    def rescan_existing_file(self, db_record: ImageModel) -> Dict[str, Any]:
        """Rescan and hydrate metadata/resources for one existing image record."""
        image_path = self.library_path / str(db_record.file_path)
        if not image_path.exists():
            raise FileNotFoundError(f"Image file is missing on disk: {image_path}")

        processor = ImageProcessor(str(image_path), self.db, str(self.library_path))
        self.results["images_scanned"] += 1

        actions_taken: List[str] = []

        file_extension = (
            (
                processor.mime_to_extension(processor.mimetype)
                if processor.mimetype
                else None
            )
            or processor.original_path.suffix.lower()
            or processor.extension
            or ".jpg"
        )
        expected_filename = f"{processor.file_hash}{file_extension}"

        final_file_path = image_path
        if image_path.name != expected_filename:
            processor.save_to_library()
            self.results["files_renamed"] += 1
            final_file_path = processor.original_path
            actions_taken.append(
                f"Renamed file to standardized name '{expected_filename}'."
            )

            self.db.query(ImageModel).filter(ImageModel.id == db_record.id).update(
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
            self.db.flush()
            self.db.refresh(db_record)
            processor.save_json_metadata(final_file_path, db_record)
            actions_taken.append(
                "Updated DB file-derived fields and rewrote sidecar metadata."
            )
        else:
            actual_file_stat = image_path.stat()
            current_file_size = (
                db_record.file_size if db_record.file_size is not None else 0
            )
            current_width = db_record.width if db_record.width is not None else 0
            current_height = db_record.height if db_record.height is not None else 0
            needs_file_update = bool(
                current_file_size != actual_file_stat.st_size
                or current_width != processor.width
                or current_height != processor.height
            )
            if needs_file_update:
                self.db.query(ImageModel).filter(ImageModel.id == db_record.id).update(
                    {
                        ImageModel.file_size: processor.file_size,
                        ImageModel.width: processor.width,
                        ImageModel.height: processor.height,
                        ImageModel.date_modified: processor.date_modified,
                    },
                    synchronize_session=False,
                )
                self.db.flush()
                self.db.refresh(db_record)
                processor.save_json_metadata(final_file_path, db_record)
                actions_taken.append(
                    "Synchronized file-derived DB fields and sidecar metadata."
                )

        sidecar_before = final_file_path.with_suffix(".json").exists()
        self._ensure_json_file_exists(final_file_path, db_record, processor)
        if not sidecar_before and final_file_path.with_suffix(".json").exists():
            actions_taken.append("Created missing sidecar JSON metadata file.")

        # Rescan order: refresh CivitAI metadata first, parse prompts, then normal EXIF hydration.
        self._enrich_from_civitai_if_needed(
            db_record,
            final_file_path,
            processor,
            force_refresh=True,
        )

        generation_prompt_updates = self._hydrate_generation_prompt_metadata(db_record)
        self._hydrate_missing_exif_if_needed(db_record, final_file_path, processor)
        self._hydrate_missing_generation_software_if_needed(
            db_record, final_file_path, processor
        )
        self._hydrate_local_perceptual_metadata_if_needed(
            db_record, final_file_path, processor
        )
        self._backfill_filename_if_needed(db_record, final_file_path, processor)

        prompt_candidates = self._collect_generation_prompt_candidates(
            db_record=db_record,
            processor=processor,
            image_path=final_file_path,
        )
        extracted_phrases: List[dict[str, Any]] = []
        prompt_tag_records: List[dict[str, Any]] = []
        for source, prompt_text in prompt_candidates:
            analysis = self._build_prompt_analysis(
                prompt_text,
                prompt_role="positive",
                source_type="rescan",
                source_label=source,
                min_words=2,
                max_words=4,
            )
            phrases = [
                str(item.get("name") or "")
                for item in list(analysis.get("phrases") or [])
            ]
            prompt_tags = list(analysis.get("prompt_tags") or [])
            prompt_tag_records.extend(prompt_tags)
            extracted_phrases.append(
                {
                    "source": source,
                    "prompt_preview": prompt_text[:200],
                    "prompt_style": analysis.get("prompt_style"),
                    "phrases": phrases,
                    "phrase_count": len(phrases),
                    "prompt_tags": prompt_tags,
                    "prompt_tag_count": len(prompt_tags),
                }
            )

        prompt_tags = merge_prompt_tag_records(prompt_tag_records)

        mapped_danbooru_from_prompt = (
            self._extract_mapped_danbooru_tags_from_prompt_tags(prompt_tags)
        )
        danbooru_tags = self._coerce_danbooru_tag_records(
            mapped_danbooru_from_prompt,
            source_label="rescan:danbooru_tags",
        )

        existing_json_metadata_value = getattr(db_record, "json_metadata", None)
        merged_json_metadata = (
            dict(existing_json_metadata_value)
            if isinstance(existing_json_metadata_value, dict)
            else {}
        )
        merged_json_metadata["prompt_tags"] = prompt_tags
        merged_json_metadata["danbooru_tags"] = danbooru_tags
        self.db.query(ImageModel).filter(ImageModel.id == db_record.id).update(
            {ImageModel.json_metadata: merged_json_metadata},
            synchronize_session=False,
        )
        self.db.flush()
        self.db.refresh(db_record)

        processor.save_json_metadata(
            final_file_path,
            db_record,
            additional_data={
                "prompt_tags": prompt_tags,
                "danbooru_tags": danbooru_tags,
            },
        )

        sidecar_after = self._load_sidecar_json(final_file_path.with_suffix(".json"))
        authority_sync_stats = self._sync_image_tags_to_authority_terms(
            db_record=db_record,
            sidecar_data=sidecar_after,
        )
        if prompt_tags:
            actions_taken.append(
                f"Stored {len(prompt_tags)} prompt tag(s) in sidecar and json_metadata."
            )
        if generation_prompt_updates:
            actions_taken.append(
                f"Updated parsed metadata for {generation_prompt_updates} generation prompt(s)."
            )
        if authority_sync_stats.get("prompt_terms_created") or authority_sync_stats.get(
            "danbooru_terms_created"
        ):
            actions_taken.append(
                "Synced image tags to taxonomy authority terms "
                f"(prompt +{authority_sync_stats.get('prompt_terms_created', 0)}, "
                f"danbooru +{authority_sync_stats.get('danbooru_terms_created', 0)})."
            )

        print(
            f"[rescan][{db_record.file_hash}] Prompt phrase extraction: "
            f"{len(prompt_candidates)} prompt candidate(s)."
        )
        for item in extracted_phrases:
            print(
                f"[rescan][{db_record.file_hash}] source={item['source']} "
                f"phrase_count={item['phrase_count']}"
            )
            for phrase in item["phrases"]:
                print(f"  - {phrase}")

        self.db.refresh(db_record)
        self.db.commit()

        hydration_summary = {
            "exif_backfill_attempts": self.results.get("exif_backfill_attempts", 0),
            "exif_backfill_successes": self.results.get("exif_backfill_successes", 0),
            "generation_software_backfill_attempts": self.results.get(
                "generation_software_backfill_attempts", 0
            ),
            "generation_software_backfill_successes": self.results.get(
                "generation_software_backfill_successes", 0
            ),
            "civitai_lookup_attempts": self.results.get("civitai_lookup_attempts", 0),
            "civitai_lookup_successes": self.results.get("civitai_lookup_successes", 0),
            "filename_backfill_attempts": self.results.get(
                "filename_backfill_attempts", 0
            ),
            "filename_backfill_successes": self.results.get(
                "filename_backfill_successes", 0
            ),
            "json_files_created": self.results.get("json_files_created", 0),
        }

        return {
            "message": "Single-file rescan completed.",
            "file_hash": str(db_record.file_hash),
            "image_id": int(db_record.id),
            "file_path": str(db_record.file_path),
            "actions_taken": actions_taken,
            "hydration_summary": hydration_summary,
            "prompt_phrase_extraction": extracted_phrases,
            "prompt_tags": prompt_tags,
            "generation_prompt_updates": generation_prompt_updates,
            "authority_term_sync": authority_sync_stats,
            "errors": list(self.error_messages),
        }

    def _enrich_from_civitai_if_needed(
        self,
        db_record: ImageModel,
        image_path: Path,
        processor: ImageProcessor,
        *,
        force_refresh: bool = False,
    ) -> None:
        """Attempt CivitAI enrichment during scan.

        When ``force_refresh`` is True, always fetch fresh CivitAI metadata.
        """
        # Skip images known to be deleted from CivitAI (404 on image.get).
        if getattr(db_record, "civitai_deleted_at", None) is not None:
            return

        source_url_value = getattr(db_record, "source_url", None)
        if not isinstance(source_url_value, str) or not source_url_value:
            return

        if not is_civitai_image_url(source_url_value):
            return

        # Sidecar JSON is the source of authority for deciding refresh behavior.
        # If the whole `civitai` section is missing, fetch again. This lets users
        # manually remove that section to force a refresh on the next scan.
        sidecar_data = self._load_sidecar_json(image_path.with_suffix(".json"))
        sidecar_civitai_payload = sidecar_data.get("civitai")
        sidecar_has_civitai = isinstance(sidecar_civitai_payload, dict)
        sidecar_has_nsfw_level = self._civitai_payload_has_nsfw_level(
            sidecar_civitai_payload
        )

        if not force_refresh and sidecar_has_civitai and sidecar_has_nsfw_level:
            return

        existing_json_metadata_value = getattr(db_record, "json_metadata", None)
        existing_json_metadata = (
            existing_json_metadata_value
            if isinstance(existing_json_metadata_value, dict)
            else {}
        )

        self.results["civitai_lookup_attempts"] += 1

        civitai_data = fetch_civitai_image_data(source_url_value)
        if not civitai_data:
            self.results["civitai_lookup_failures"] += 1
            return

        civitai_uuid = None
        raw_uuid = (
            civitai_data.get("civitai_uuid") if isinstance(civitai_data, dict) else None
        )
        if isinstance(raw_uuid, str) and raw_uuid.strip():
            civitai_uuid = raw_uuid.strip()

        civitai_hash = None
        raw_hash = (
            civitai_data.get("civitai_hash") if isinstance(civitai_data, dict) else None
        )
        if isinstance(raw_hash, str) and raw_hash.strip():
            civitai_hash = raw_hash.strip()

        merged_json_metadata = dict(existing_json_metadata)
        merged_json_metadata["civitai"] = civitai_data

        # Extract civitai_nsfw_level from the enrichment data
        nsfw_level = extract_civitai_nsfw_level({"civitai": civitai_data})

        # Extract declared file size for size-mismatch detection
        update_dict = {
            ImageModel.json_metadata: merged_json_metadata,
            ImageModel.source_site: "civitai",
            ImageModel.civitai_uuid: civitai_uuid,
            ImageModel.civitai_hash: civitai_hash,
            ImageModel.civitai_nsfw_level: nsfw_level,
        }
        declared_file_size = (
            civitai_data.get("declared_file_size") if isinstance(civitai_data, dict) else None
        )
        if declared_file_size is not None:
            try:
                declared_file_size = int(declared_file_size)
            except (TypeError, ValueError):
                declared_file_size = None
        if declared_file_size is not None:
            update_dict[ImageModel.expected_file_size] = declared_file_size

        self.db.query(ImageModel).filter(ImageModel.id == db_record.id).update(
            update_dict,
            synchronize_session=False,
        )
        self.db.flush()
        self.db.refresh(db_record)

        # Auto-create civitai_multi_resource variant group if another image
        # shares the same civitai_image_id.
        if db_record.civitai_image_id:
            self._ensure_variant_group_for_civitai_image_id(db_record, db_record.civitai_image_id)

        processor.save_json_metadata(
            image_path,
            db_record,
            additional_data={"civitai": civitai_data},
        )
        self.db.commit()
        self.results["civitai_lookup_successes"] += 1

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
            file_name = (
                sanitize_display_filename(image_data.file_name, fallback_ext=".jpg")
                or "image.jpg"
            )
            ext = (
                "." + file_name.rsplit(".", 1)[1].lower()
                if "." in file_name
                else ".jpg"
            )
        if not ext:
            file_name = (
                sanitize_display_filename(image_data.file_name, fallback_ext=".jpg")
                or "image.jpg"
            )
            ext = (
                "." + file_name.rsplit(".", 1)[1].lower()
                if "." in file_name
                else ".jpg"
            )

        relative_filepath = f"{file_hash}{ext}"

        date_created = self._coerce_datetime(image_data.date_created)
        date_modified = self._coerce_datetime(image_data.date_modified)

        new_image = ImageModel(
            file_path=relative_filepath,
            file_name=sanitize_display_filename(
                image_data.file_name or json_file.stem,
                fallback_ext=Path(relative_filepath).suffix,
            )
            or json_file.stem,
            file_hash=file_hash,
            file_size=image_data.file_size or 0,
            width=image_data.width or 0,
            height=image_data.height or 0,
            mimetype=image_data.mimetype,
            date_created=date_created,
            date_modified=date_modified,
            artist_id=image_data.artist_id,
            source_url=normalize_civitai_url(image_data.source_url),
            source_site=(
                image_data.source_site
                or ("civitai" if image_data.civitai_data else None)
            ),
            license_id=image_data.license_id,
            exif_data=image_data.exif_data,
            json_metadata=(
                {"civitai": image_data.civitai_data}
                if image_data.civitai_data
                else None
            ),
        )
        try:
            self.db.add(new_image)
            self.db.flush()
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        # Auto-create hash_duplicate variant group if another image shares file_hash
        self._ensure_variant_group_for_hash(new_image)
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
            print(f"Warning: JSON file {json_file.name} has no file_hash, skipping")
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
            self.db.query(ImageModel).filter(ImageModel.file_hash == file_hash).first()
        )

        if db_record is not None:
            # Compare JSON values with database values using ImageData
            print(f"Comparing JSON with database for hash {file_hash}")
            has_differences = self._compare_json_with_database(
                final_image_data or image_data, db_record
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
                self.db.rollback()
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
            print(
                f"  Found {len(differences)} difference(s) between JSON and database:"
            )
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
            "file_name",
            "artist_id",
            "source_url",
            "license_id",
            "date_created",
            "exif_data",
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
            self.db.query(ImageModel).filter(ImageModel.id == db_record.id).update(
                update_fields, synchronize_session=False
            )
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
                processor.save_json_metadata(image_file, db_record)
            else:
                new_processor = ImageProcessor(
                    str(image_file), self.db, str(self.library_path)
                )
                new_processor.save_json_metadata(image_file, db_record)
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
                    (
                        processor.mime_to_extension(processor.mimetype)
                        if processor.mimetype
                        else None
                    )
                    or processor.original_path.suffix.lower()
                    or processor.extension
                    or ".jpg"
                )
                expected_filename = f"{file_hash}{file_extension}"
                db_record = processor.find_in_database()

                # Use JSON metadata as source of authority if available
                json_data = json_data_by_hash.get(file_hash) if file_hash else None

                # If no JSON data, try to load from file
                if not json_data:
                    json_data = ImageData.from_json_file(
                        image_file.with_suffix(".json")
                    )

                # Get original filename from JSON or use current filename
                original_filename = (
                    json_data.file_name
                    if json_data and json_data.file_name
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
                        processor.save_json_metadata(processor.original_path, db_record)
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
                            processor.save_json_metadata(image_file, db_record)
                            self.db.commit()

                        # Check if JSON file exists, create if missing
                        self._ensure_json_file_exists(
                            final_file_path, db_record, processor
                        )

                    self._hydrate_missing_metadata_fields(
                        db_record=db_record,
                        image_path=final_file_path,
                        processor=processor,
                    )
                else:
                    # This shouldn't happen if JSON was processed, but handle it
                    # File is new and needs to be imported
                    print(
                        f"New image file detected (no JSON record): {image_file.name}"
                    )

                    self._import_new_image_with_processor(
                        processor=processor,
                        image_file=image_file,
                        original_filename=original_filename,
                    )

            except Exception as e:
                self.db.rollback()
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
