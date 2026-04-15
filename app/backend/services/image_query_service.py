from __future__ import annotations

import json
import re
from pathlib import Path
from threading import RLock
from typing import Any, Optional

from sqlalchemy import exists, false as sa_false, func, or_, select, text

from services import a1111_parser_service as _a1111_svc

from models import (
    Artist,
    CollectionModel,
    Concept,
    ConceptAlias,
    ImageCollectionMembership,
    ImageConceptObservation,
    ImageModel,
    ImageTag,
    Tag,
)


def db_false():
    """Return a SQLAlchemy expression evaluating to FALSE in SQL."""
    return sa_false()


class ImageQueryService:
    _NSFW_GRANULAR_RATINGS: frozenset[str] = frozenset({"pg", "pg13", "r", "x", "xxx"})
    _NSFW_SAFETY_CLASSES: frozenset[str] = frozenset({"safe", "mature", "explicit"})
    _NSFW_NA_SENTINELS: frozenset[str] = frozenset({"n/a"})
    _A1111_RP_DIRECTIVE_RE: re.Pattern = _a1111_svc.A1111_RP_DIRECTIVE_RE
    _A1111_HIRES_KEYWORDS: tuple[str, ...] = _a1111_svc.A1111_HIRES_KEYWORDS

    """Encapsulates image list filtering and generation software lookup logic."""

    def __init__(self, *, image_library_path: str):
        self._image_library_path = Path(image_library_path)
        self._generation_software_cache: dict[str, tuple[Optional[float], Optional[str]]] = {}
        self._nsfw_ratings_cache: dict[str, tuple[Optional[float], list[str]]] = {}
        self._cache_lock = RLock()

    @staticmethod
    def _dedupe_labels(values: list[str]) -> list[str]:
        seen: set[str] = set()
        deduped: list[str] = []
        for value in values:
            label = str(value or "").strip()
            if not label:
                continue
            key = label.lower()
            if key in seen:
                continue
            seen.add(key)
            deduped.append(label)
        return deduped

    @classmethod
    def _normalize_nsfw_level_value(cls, value: Any) -> Optional[int]:
        if value is None:
            return None
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            cleaned = value.strip()
            if not cleaned:
                return None
            try:
                return int(float(cleaned))
            except ValueError:
                return None
        return None

    @classmethod
    def _labels_from_nsfw_level(cls, level: int) -> list[str]:
        # CivitAI NSFW levels are flag values:
        # 1=PG, 2=PG13, 4=R, 8=X, 16=XXX
        if level <= 0:
            return ["Safe"]

        flag_to_labels: list[tuple[int, list[str]]] = [
            (1, ["PG", "Safe"]),
            (2, ["PG13", "Safe"]),
            (4, ["R", "Mature"]),
            (8, ["X", "Explicit"]),
            (16, ["XXX", "Explicit"]),
        ]

        labels: list[str] = []
        for flag, mapped_labels in flag_to_labels:
            if level & flag:
                labels.extend(mapped_labels)

        # Fallback for unexpected positive integers that don't match known flags.
        if not labels:
            return ["Explicit"]
        return cls._dedupe_labels(labels)

    @classmethod
    def _normalize_nsfw_labels(cls, value: Any) -> list[str]:
        if value is None:
            return []

        if isinstance(value, bool):
            return ["Explicit"] if value else ["Safe"]

        if isinstance(value, list):
            labels: list[str] = []
            for item in value:
                labels.extend(cls._normalize_nsfw_labels(item))
            return cls._dedupe_labels(labels)

        normalized_level = cls._normalize_nsfw_level_value(value)
        if normalized_level is not None:
            return cls._labels_from_nsfw_level(normalized_level)

        normalized = str(value or "").strip().lower()
        if not normalized:
            return []

        if normalized in {"pg", "pg13", "pg-13", "pg_13"}:
            return ["PG13", "Safe"] if "13" in normalized else ["PG", "Safe"]
        if normalized == "r":
            return ["R", "Mature"]
        if normalized in {"x", "r18", "r-18", "r18+"}:
            return ["X", "Explicit"]
        if normalized == "xxx":
            return ["XXX", "Explicit"]

        if normalized in {"safe", "sfw", "none"} or "safe" in normalized:
            return ["Safe"]
        if normalized in {"mature", "moderate", "r15"} or "mature" in normalized:
            return ["Mature"]
        if normalized in {"explicit", "adult", "nsfw"} or "explicit" in normalized:
            return ["Explicit"]

        return []

    @classmethod
    def _extract_nsfw_labels_from_payload(cls, payload: Any) -> list[str]:
        if not isinstance(payload, dict):
            return []

        civitai_payload = {}
        raw_civitai = payload.get("civitai")
        raw_civitai_data = payload.get("civitai_data")
        if isinstance(raw_civitai, dict):
            civitai_payload = raw_civitai
        elif isinstance(raw_civitai_data, dict):
            civitai_payload = raw_civitai_data

        raw_meta = civitai_payload.get("meta")
        raw_image = civitai_payload.get("image")
        civitai_meta = raw_meta if isinstance(raw_meta, dict) else {}
        civitai_image = raw_image if isinstance(raw_image, dict) else {}

        candidates = [
            payload.get("nsfw_rating"),
            payload.get("nsfw_ratings"),
            payload.get("nsfw_level"),
            payload.get("nsfw"),
            payload.get("rating"),
            payload.get("content_rating"),
            civitai_payload.get("nsfwLevel"),
            civitai_payload.get("nsfw"),
            civitai_payload.get("rating"),
            civitai_payload.get("nsfwRating"),
            civitai_image.get("nsfwLevel"),
            civitai_image.get("nsfw"),
            civitai_image.get("rating"),
            civitai_image.get("nsfwRating"),
            civitai_meta.get("nsfwLevel"),
            civitai_meta.get("nsfw"),
            civitai_meta.get("rating"),
            civitai_meta.get("nsfwRating"),
        ]

        labels: list[str] = []
        for candidate in candidates:
            labels.extend(cls._normalize_nsfw_labels(candidate))
        return cls._dedupe_labels(labels)

    @staticmethod
    def normalize_query_values(values: Optional[list[str]]) -> list[str]:
        if not values:
            return []
        normalized: list[str] = []
        for value in values:
            next_value = str(value or "").strip().lower()
            if next_value:
                normalized.append(next_value)
        return normalized

    def read_generation_software_for_image(self, image: Any) -> Optional[str]:
        db_json = image.json_metadata if isinstance(image.json_metadata, dict) else {}
        db_value = str(db_json.get("generation_software") or "").strip()
        if db_value:
            return db_value.lower()

        sidecar_path = (self._image_library_path / str(image.file_path)).with_suffix(".json")
        cache_key = str(sidecar_path)
        try:
            sidecar_mtime = sidecar_path.stat().st_mtime if sidecar_path.exists() else None
        except OSError:
            sidecar_mtime = None

        with self._cache_lock:
            cached_entry = self._generation_software_cache.get(cache_key)
            if cached_entry and cached_entry[0] == sidecar_mtime:
                return cached_entry[1]

        normalized_value: Optional[str] = None
        if sidecar_mtime is not None:
            try:
                with open(sidecar_path, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                if isinstance(payload, dict):
                    raw_value = str(payload.get("generation_software") or "").strip()
                    normalized_value = raw_value.lower() if raw_value else None
            except (OSError, json.JSONDecodeError):
                normalized_value = None

        with self._cache_lock:
            self._generation_software_cache[cache_key] = (sidecar_mtime, normalized_value)
        return normalized_value

    def read_nsfw_ratings_for_image(self, image: Any) -> list[str]:
        # User-defined overrides take priority over all source-derived labels.
        user_rating = getattr(image, "user_nsfw_rating", None)
        user_safety = getattr(image, "user_nsfw_safety_class", None)
        if user_rating or user_safety:
            labels: list[str] = []
            if user_rating:
                labels.extend(self._normalize_nsfw_labels(str(user_rating)))
            if user_safety:
                labels.extend(self._normalize_nsfw_labels(str(user_safety)))
            return self._dedupe_labels(labels)

        db_json = image.json_metadata if isinstance(getattr(image, "json_metadata", None), dict) else {}
        db_labels = self._extract_nsfw_labels_from_payload({
            "json_metadata": db_json,
            "civitai": db_json.get("civitai"),
            "nsfw_rating": db_json.get("nsfw_rating"),
            "nsfw_ratings": db_json.get("nsfw_ratings"),
            "rating": db_json.get("rating"),
        })

        sidecar_path = (self._image_library_path / str(image.file_path)).with_suffix(".json")
        cache_key = str(sidecar_path)
        try:
            sidecar_mtime = sidecar_path.stat().st_mtime if sidecar_path.exists() else None
        except OSError:
            sidecar_mtime = None

        sidecar_labels: list[str] = []
        with self._cache_lock:
            cached_entry = self._nsfw_ratings_cache.get(cache_key)
            if cached_entry and cached_entry[0] == sidecar_mtime:
                sidecar_labels = list(cached_entry[1])

        if not sidecar_labels and sidecar_mtime is not None:
            try:
                with open(sidecar_path, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                if isinstance(payload, dict):
                    sidecar_labels = self._extract_nsfw_labels_from_payload(payload)
            except (OSError, json.JSONDecodeError):
                sidecar_labels = []

            with self._cache_lock:
                self._nsfw_ratings_cache[cache_key] = (sidecar_mtime, list(sidecar_labels))

        return self._dedupe_labels(sidecar_labels + db_labels)

    def _read_exif_for_image(self, image: Any) -> dict[str, Any]:
        """Read EXIF data from DB json_metadata or sidecar JSON."""
        db_json = image.json_metadata if isinstance(getattr(image, "json_metadata", None), dict) else {}
        raw_exif = db_json.get("exif_data") if isinstance(db_json, dict) else None
        exif_from_db: dict[str, Any] = raw_exif if isinstance(raw_exif, dict) else {}

        sidecar_path = (self._image_library_path / str(image.file_path)).with_suffix(".json")
        try:
            sidecar_mtime = sidecar_path.stat().st_mtime if sidecar_path.exists() else None
        except OSError:
            sidecar_mtime = None

        sidecar_exif: dict[str, Any] = {}
        if sidecar_mtime is not None:
            try:
                with open(sidecar_path, "r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                if isinstance(payload, dict):
                    raw_sidecar_exif = payload.get("exif_data")
                    sidecar_exif = raw_sidecar_exif if isinstance(raw_sidecar_exif, dict) else {}
            except (OSError, json.JSONDecodeError):
                sidecar_exif = {}

        return {**exif_from_db, **sidecar_exif}

    def _looks_like_a1111_user_comment_payload(self, exif: dict[str, Any]) -> bool:
        """Detect whether EXIF contains A1111-style generation metadata."""
        return _a1111_svc.looks_like_a1111_exif(exif)

    def _get_a1111_parameter_text(self, exif: dict[str, Any]) -> str:
        """Extract normalized A1111 parameter text from EXIF fields."""
        return _a1111_svc._get_a1111_text(exif)

    def read_a1111_features_for_image(self, image: Any) -> dict[str, bool]:
        """Detect A1111 feature flags (Hires, RP, ADetailer) from image EXIF.

        Returns:
            dict with keys: "hires_upscale", "regional_prompter", "adetailer"
        """
        exif = self._read_exif_for_image(image)
        features = _a1111_svc.detect_a1111_features_from_exif(exif)
        return {
            "hires_upscale": features.get("a1111_hires", False),
            "regional_prompter": features.get("a1111_regional_prompter", False),
            "adetailer": features.get("a1111_adetailer", False),
        }

    def filter_image_ids_by_tag_names(
        self,
        images_query,
        *,
        include_tags: Optional[list[str]] = None,
        exclude_tags: Optional[list[str]] = None,
    ) -> Optional[list[int]]:
        """Filter images by exact tag name match across all tag sources.

        Looks up tags via two FK paths:
          1. Tag.name -> ImageTag.image_id  (legacy image_tags table)
          2. Concept.canonical_name / ConceptAlias.normalized_alias
             -> ImageConceptObservation.image_id  (taxonomy observations)

        For include_tags: image must have ALL listed tags (AND semantics).
        For exclude_tags: image must have NONE of the listed tags.

        Returns:
            Filtered list of image IDs, or None when no tag constraints are specified.
        """
        normalized_include = self.normalize_query_values(include_tags)
        normalized_exclude = self.normalize_query_values(exclude_tags)
        if not normalized_include and not normalized_exclude:
            return None

        session = images_query.session

        def _image_ids_for_tag_name(tag_name: str) -> set[int]:
            """Return image IDs that carry *tag_name* from any tag source."""
            ids: set[int] = set()

            # Path 1: Tag.name -> image_tags
            tag_ids = [
                row[0]
                for row in session.query(Tag.id).filter(func.lower(Tag.name) == tag_name)
            ]
            if tag_ids:
                for row in session.query(ImageTag.image_id).filter(
                    ImageTag.tag_id.in_(tag_ids)
                ):
                    ids.add(row[0])

            # Path 2a: Concept.canonical_name -> image_concept_observations
            concept_ids: set[int] = set()
            for row in session.query(Concept.id).filter(
                func.lower(Concept.canonical_name) == tag_name
            ):
                concept_ids.add(row[0])

            # Path 2b: ConceptAlias.normalized_alias -> concept -> observations
            for row in session.query(ConceptAlias.concept_id).filter(
                func.lower(ConceptAlias.normalized_alias) == tag_name
            ):
                concept_ids.add(row[0])

            if concept_ids:
                for row in session.query(ImageConceptObservation.image_id).filter(
                    ImageConceptObservation.concept_id.in_(list(concept_ids))
                ).distinct():
                    ids.add(row[0])

            return ids

        # Start with all candidate IDs from the base query.
        all_ids: Optional[set[int]] = None

        # Include: intersect per tag (AND semantics).
        if normalized_include:
            for tag_name in normalized_include:
                tag_ids = _image_ids_for_tag_name(tag_name)
                if all_ids is None:
                    all_ids = tag_ids
                else:
                    all_ids &= tag_ids
                if not all_ids:
                    return []

        # Exclude: remove images carrying any excluded tag.
        if normalized_exclude:
            exclude_ids: set[int] = set()
            for tag_name in normalized_exclude:
                exclude_ids |= _image_ids_for_tag_name(tag_name)
            if exclude_ids:
                if all_ids is None:
                    # No include constraint — start from the full query set.
                    base_ids = {
                        row[0]
                        for row in images_query.options()
                        .with_entities(ImageModel.id)
                        .all()
                    }
                    all_ids = base_ids - exclude_ids
                else:
                    all_ids -= exclude_ids

        if all_ids is None:
            return None
        return list(all_ids)

    def filter_image_ids_by_a1111_features(
        self,
        images_query,
        *,
        a1111_hires: Optional[list[str]] = None,
        a1111_regional_prompter: Optional[list[str]] = None,
        a1111_adetailer: Optional[list[str]] = None,
    ) -> Optional[list[int]]:
        """Filter images by A1111 feature presence/absence using DB columns.

        Args:
            a1111_hires: ["present"] to show images with Hires, ["absent"] to show without
            a1111_regional_prompter: ["present"] to show images with RP, ["absent"] to show without
            a1111_adetailer: ["present"] to show images with ADetailer, ["absent"] to show without

        Returns:
            List of image IDs matching all specified feature constraints, or None if no constraints
        """
        column_map = {
            "hires": (self.normalize_query_values(a1111_hires), ImageModel.a1111_hires),
            "regional_prompter": (self.normalize_query_values(a1111_regional_prompter), ImageModel.a1111_regional_prompter),
            "adetailer": (self.normalize_query_values(a1111_adetailer), ImageModel.a1111_adetailer),
        }

        conditions = []
        for _key, (values, column) in column_map.items():
            if not values:
                continue
            modes = set(values)
            if "present" in modes:
                conditions.append(column == True)  # noqa: E712
            elif "absent" in modes:
                conditions.append(column == False)  # noqa: E712

        if not conditions:
            return None

        result = (
            images_query.options()
            .with_entities(ImageModel.id)
            .filter(*conditions)
            .all()
        )
        return [int(row[0]) for row in result]

    def filter_image_ids_by_generation_software(
        self,
        images_query,
        generation_softwares: Optional[list[str]],
    ) -> Optional[list[int]]:
        """Filter images by generation software using the DB column."""
        normalized_generation_softwares = self.normalize_query_values(generation_softwares)
        if not normalized_generation_softwares:
            return None

        allowed_values = set(normalized_generation_softwares)

        # Handle N/A (images with no generation software detected)
        want_na = "n/a" in allowed_values
        named_values = allowed_values - {"n/a"}

        conditions: list = []
        if named_values:
            conditions.append(
                func.lower(ImageModel.generation_software).in_(named_values)
            )
        if want_na:
            conditions.append(ImageModel.generation_software.is_(None))

        if not conditions:
            return None

        combined = or_(*conditions) if len(conditions) > 1 else conditions[0]
        result = (
            images_query.options()
            .with_entities(ImageModel.id)
            .filter(combined)
            .all()
        )
        return [int(row[0]) for row in result]

    def filter_image_ids_by_nsfw_ratings(
        self,
        images_query,
        nsfw_ratings: Optional[list[str]],
    ) -> Optional[list[int]]:
        """Filter images by NSFW ratings using pure SQL (no Python loop or sidecar reads).

        All NSFW data lives in the database — either as user overrides
        (user_nsfw_rating / user_nsfw_safety_class columns) or as
        civitai.nsfwLevel inside json_metadata.  Sidecar reads are never needed.

        CivitAI nsfwLevel flags: 1=PG, 2=PG13, 4=R, 8=X, 16=XXX.
        These map to granular ratings: pg, pg13, r, x, xxx.
        """
        normalized_nsfw_ratings = self.normalize_query_values(nsfw_ratings)
        if not normalized_nsfw_ratings:
            return None

        want_na_rating = "n/a" in normalized_nsfw_ratings
        allowed_values = {v for v in normalized_nsfw_ratings if v != "n/a"}

        # Build the set of CivitAI nsfwLevel values that correspond to
        # the allowed filter values.  The frontend may send granular ratings
        # (pg, pg13, r, x, xxx) *and/or* safety class labels (safe, mature,
        # explicit) because the old Python filter's _labels_from_nsfw_level()
        # produces BOTH in the label set for each nsfwLevel.
        #   pg     -> 1       pg13   -> 2       r      -> 4
        #   x      -> 8       xxx    -> 16
        #   safe   -> 1, 2    mature -> 4       explicit -> 8, 16
        allowed_nsfw_levels: set[int] = set()
        if "pg" in allowed_values:
            allowed_nsfw_levels.add(1)
        if "pg13" in allowed_values:
            allowed_nsfw_levels.add(2)
        if "r" in allowed_values:
            allowed_nsfw_levels.add(4)
        if "x" in allowed_values:
            allowed_nsfw_levels.add(8)
        if "xxx" in allowed_values:
            allowed_nsfw_levels.add(16)
        # Safety class labels also map to nsfwLevel values.
        if "safe" in allowed_values:
            allowed_nsfw_levels.update((1, 2))
        if "mature" in allowed_values:
            allowed_nsfw_levels.add(4)
        if "explicit" in allowed_values:
            allowed_nsfw_levels.update((8, 16))

        # Build SQL conditions
        conditions: list = []

        # --- User overrides take absolute priority ---
        # A non-empty user_nsfw_rating column means the user has explicitly
        # classified this image; the civitai value should be ignored.
        user_has_override = or_(
            ImageModel.user_nsfw_rating.isnot(None),
            ImageModel.user_nsfw_safety_class.isnot(None),
        )

        # When n/a is requested, match images that have no granular rating at all
        # (user override is empty AND civitai nsfwLevel is absent/NULL).
        if want_na_rating:
            no_user_override = ~user_has_override
            # Images without any NSFW data: no user override, no civitai nsfwLevel.
            na_condition = no_user_override & (
                ImageModel.civitai_nsfw_level.is_(None)
            )
            conditions.append(na_condition)

        if allowed_values:
            # User-override path: check user_nsfw_rating / user_nsfw_safety_class
            user_conditions: list = []
            # user_nsfw_rating stores granular ratings like 'pg', 'r', 'xxx'
            if allowed_values:
                user_conditions.append(
                    func.lower(ImageModel.user_nsfw_rating).in_(allowed_values)
                )
            # user_nsfw_safety_class stores safety classes: 'safe', 'mature', 'explicit'
            # Map them back to granular ratings
            safety_to_ratings = {
                "safe": {"pg", "pg13"},
                "mature": {"r"},
                "explicit": {"x", "xxx"},
            }
            for safety_class, ratings in safety_to_ratings.items():
                if allowed_values & ratings:
                    user_conditions.append(
                        func.lower(ImageModel.user_nsfw_safety_class) == safety_class
                    )
            user_match = or_(*user_conditions) if user_conditions else db_false()

            # Civitai nsfwLevel path: match against the promoted column.
            civitai_conditions: list = []
            if allowed_nsfw_levels:
                civitai_conditions.append(
                    ImageModel.civitai_nsfw_level.in_(sorted(allowed_nsfw_levels))
                )
                civitai_match = or_(*civitai_conditions)
            else:
                civitai_match = db_false()

            # An image matches if:
            #   (a) user has an override AND it matches allowed values, OR
            #   (b) user has NO override AND civitai nsfwLevel matches
            has_match = user_has_override & user_match | ~user_has_override & civitai_match
            conditions.append(has_match)

        if not conditions:
            return None

        combined = or_(*conditions)
        result = (
            images_query.options()
            .with_entities(ImageModel.id)
            .filter(combined)
            .all()
        )
        return [int(row[0]) for row in result]

    def filter_image_ids_by_nsfw_safety_classes(
        self,
        images_query,
        nsfw_safety_classes: Optional[list[str]],
    ) -> Optional[list[int]]:
        """Filter images by NSFW safety class using pure SQL.

        Safety classes: safe, mature, explicit.
        Maps CivitAI nsfwLevel flags to safety classes:
          safe    <- nsfwLevel 1 (PG) or 2 (PG13)
          mature  <- nsfwLevel 4 (R)
          explicit <- nsfwLevel 8 (X) or 16 (XXX)
        """
        normalized_nsfw_safety_classes = self.normalize_query_values(nsfw_safety_classes)
        if not normalized_nsfw_safety_classes:
            return None

        want_na_safety = "n/a" in normalized_nsfw_safety_classes
        allowed_values = {v for v in normalized_nsfw_safety_classes if v != "n/a"}

        # Map safety classes to CivitAI nsfwLevel values
        safety_to_levels: dict[str, list[int]] = {
            "safe": [1, 2],
            "mature": [4],
            "explicit": [8, 16],
        }

        # Build SQL conditions
        conditions: list = []

        user_has_override = or_(
            ImageModel.user_nsfw_rating.isnot(None),
            ImageModel.user_nsfw_safety_class.isnot(None),
        )

        if want_na_safety:
            no_user_override = ~user_has_override
            na_condition = no_user_override & (
                ImageModel.civitai_nsfw_level.is_(None)
            )
            conditions.append(na_condition)

        if allowed_values:
            # User-override path
            user_conditions: list = []
            user_conditions.append(
                func.lower(ImageModel.user_nsfw_safety_class).in_(allowed_values)
            )
            # Also check user_nsfw_rating mapped to safety class
            rating_to_safety = {
                "pg": "safe", "pg13": "safe",
                "r": "mature",
                "x": "explicit", "xxx": "explicit",
            }
            for rating, safety in rating_to_safety.items():
                if safety in allowed_values:
                    user_conditions.append(
                        func.lower(ImageModel.user_nsfw_rating) == rating
                    )
            user_match = or_(*user_conditions) if user_conditions else db_false()

            # Civitai nsfwLevel path: use the promoted column.
            allowed_levels: set[int] = set()
            for safety_class in allowed_values:
                allowed_levels.update(safety_to_levels.get(safety_class, []))
            civitai_match = ImageModel.civitai_nsfw_level.in_(sorted(allowed_levels)) if allowed_levels else db_false()

            has_match = user_has_override & user_match | ~user_has_override & civitai_match
            conditions.append(has_match)

        if not conditions:
            return None

        combined = or_(*conditions)
        result = (
            images_query.options()
            .with_entities(ImageModel.id)
            .filter(combined)
            .all()
        )
        return [int(row[0]) for row in result]

    def apply_image_list_filters(
        self,
        images_query,
        *,
        search: Optional[str] = None,
        source_sites: Optional[list[str]] = None,
        mimetypes: Optional[list[str]] = None,
        artist_names: Optional[list[str]] = None,
        collection_names: Optional[list[str]] = None,
        exclude_artist_names: Optional[list[str]] = None,
        exclude_collection_names: Optional[list[str]] = None,
    ):
        normalized_source_sites = self.normalize_query_values(source_sites)
        normalized_mimetypes = self.normalize_query_values(mimetypes)
        normalized_artist_names = self.normalize_query_values(artist_names)
        normalized_collection_names = self.normalize_query_values(collection_names)
        normalized_exclude_artist_names = self.normalize_query_values(exclude_artist_names)
        normalized_exclude_collection_names = self.normalize_query_values(exclude_collection_names)
        normalized_search = str(search or "").strip().lower()

        if normalized_source_sites:
            images_query = images_query.filter(func.lower(ImageModel.source_site).in_(normalized_source_sites))

        if normalized_mimetypes:
            images_query = images_query.filter(func.lower(ImageModel.mimetype).in_(normalized_mimetypes))

        if normalized_artist_names:
            images_query = images_query.filter(
                ImageModel.artist.has(func.lower(Artist.name).in_(normalized_artist_names))
            )

        if normalized_exclude_artist_names:
            # Use NOT IN subquery instead of ~has() (NOT EXISTS) — SQLite
            # materialises the subquery once with a Bloom filter vs running a
            # correlated subquery per row.
            excluded_artist_ids_sq = (
                select(ImageModel.id)
                .join(Artist, ImageModel.artist_id == Artist.id)
                .where(func.lower(Artist.name).in_(normalized_exclude_artist_names))
                .correlate(None)
                .scalar_subquery()
            )
            images_query = images_query.filter(ImageModel.id.notin_(excluded_artist_ids_sq))

        if normalized_collection_names:
            images_query = images_query.filter(
                ImageModel.collections.any(func.lower(CollectionModel.name).in_(normalized_collection_names))
            )

        if normalized_exclude_collection_names:
            # Use NOT IN subquery for same Bloom-filter optimisation.
            excluded_coll_ids_sq = (
                select(ImageCollectionMembership.image_id)
                .join(CollectionModel, CollectionModel.id == ImageCollectionMembership.collection_id)
                .where(func.lower(CollectionModel.name).in_(normalized_exclude_collection_names))
                .correlate(None)
                .scalar_subquery()
            )
            images_query = images_query.filter(ImageModel.id.notin_(excluded_coll_ids_sq))

        if normalized_search:
            matched_ids = self._search_image_ids_phased(
                images_query.session, normalized_search,
            )
            if matched_ids:
                images_query = images_query.filter(ImageModel.id.in_(matched_ids))
            else:
                images_query = images_query.filter(text("1 = 0"))

        return images_query

    # ------------------------------------------------------------------
    # Phased full-text search
    # ------------------------------------------------------------------

    _HEX_CHARS: frozenset[str] = frozenset("0123456789abcdef")

    @classmethod
    def _is_hex_compatible(cls, value: str) -> bool:
        """Return True when *value* could be a substring of a hex hash."""
        return all(c in cls._HEX_CHARS for c in value)

    @staticmethod
    def _search_image_ids_phased(
        session,
        normalized_search: str,
    ) -> set[int]:
        """Collect matching image IDs in fast-to-slow order.

        Phase 1 – Lookup-table name searches (tiny tables, indexed names)
                  → FK join to image IDs.
        Phase 2 – Fast indexed-column searches on the images table
                  (skips file_hash when the term is not hex-compatible).
        Phase 3 – Expensive JSON-blob LIKE scans (exif_data, json_metadata).
        """
        like_term = f"%{normalized_search}%"
        matched: set[int] = set()

        import time as _time
        _t0 = _time.perf_counter()

        # ---- Phase 1: small lookup tables → FK → image IDs ----

        # Concepts (canonical_name) + aliases (normalized_alias)
        matching_concept_ids: set[int] = set()
        for row in (
            session.query(Concept.id)
            .filter(func.lower(Concept.canonical_name).like(like_term))
        ):
            matching_concept_ids.add(row[0])
        for row in (
            session.query(ConceptAlias.concept_id)
            .filter(func.lower(ConceptAlias.normalized_alias).like(like_term))
        ):
            matching_concept_ids.add(row[0])
        if matching_concept_ids:
            for row in (
                session.query(ImageConceptObservation.image_id)
                .filter(ImageConceptObservation.concept_id.in_(matching_concept_ids))
                .distinct()
            ):
                matched.add(row[0])

        # Tags (name)
        matching_tag_ids = [
            row[0]
            for row in session.query(Tag.id).filter(func.lower(Tag.name).like(like_term))
        ]
        if matching_tag_ids:
            for row in (
                session.query(ImageTag.image_id)
                .filter(ImageTag.tag_id.in_(matching_tag_ids))
            ):
                matched.add(row[0])

        # Artists (name)
        matching_artist_ids = [
            row[0]
            for row in session.query(Artist.id).filter(func.lower(Artist.name).like(like_term))
        ]
        if matching_artist_ids:
            for row in (
                session.query(ImageModel.id)
                .filter(ImageModel.artist_id.in_(matching_artist_ids))
            ):
                matched.add(row[0])

        # Collections (name)
        matching_collection_ids = [
            row[0]
            for row in session.query(CollectionModel.id).filter(
                func.lower(CollectionModel.name).like(like_term)
            )
        ]
        if matching_collection_ids:
            for row in (
                session.query(ImageCollectionMembership.image_id)
                .filter(ImageCollectionMembership.collection_id.in_(matching_collection_ids))
            ):
                matched.add(row[0])

        # ---- Phase 2: fast column scans on images table ----
        _t1 = _time.perf_counter()
        print(f"[PERF] search phase1 (lookup tables): {_t1-_t0:.3f}s  ids={len(matched)}")
        column_conditions = [
            func.lower(ImageModel.file_name).like(like_term),
            func.lower(ImageModel.source_url).like(like_term),
            func.lower(ImageModel.source_site).like(like_term),
            func.lower(ImageModel.mimetype).like(like_term),
            func.lower(ImageModel.generation_software).like(like_term),
        ]
        if ImageQueryService._is_hex_compatible(normalized_search):
            column_conditions.append(func.lower(ImageModel.file_hash).like(like_term))

        for row in (
            session.query(ImageModel.id).filter(or_(*column_conditions))
        ):
            matched.add(row[0])

        _t2 = _time.perf_counter()
        print(f"[PERF] search phase2 (columns): {_t2-_t1:.3f}s  ids={len(matched)}")

        return matched
