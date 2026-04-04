from __future__ import annotations

import json
import re
from pathlib import Path
from threading import RLock
from typing import Any, Optional

from sqlalchemy import func, or_

from models import Artist, CollectionModel, ImageModel, Tag


class ImageQueryService:
    _NSFW_GRANULAR_RATINGS: frozenset[str] = frozenset({"pg", "pg13", "r", "x", "xxx"})
    _NSFW_SAFETY_CLASSES: frozenset[str] = frozenset({"safe", "mature", "explicit"})
    _NSFW_NA_SENTINELS: frozenset[str] = frozenset({"n/a"})
    _A1111_RP_DIRECTIVE_RE: re.Pattern = re.compile(r"\b(ADDCOMM|ADDROW|ADDCOL)\b", re.IGNORECASE)
    _A1111_HIRES_KEYWORDS: tuple[str, ...] = (
        "hires upscaler", "hires steps", "hires upscale",
        "hr upscaler", "hr upscale", "denoising strength"
    )

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
        exif_parameters = [exif.get("parameters"), exif.get("Parameters")]
        candidate = next((v for v in exif_parameters if isinstance(v, str) and v.strip()), None)
        if candidate:
            normalized = candidate.strip().lower()
            has_steps = "steps:" in normalized
            has_seed = "seed:" in normalized
            has_sampler = "sampler:" in normalized
            has_cfg = "cfg scale:" in normalized
            has_negative = "negative prompt:" in normalized
            if has_steps and (has_cfg or has_sampler or has_seed or has_negative):
                return True

        exact_user_comment = exif.get("user_comment")
        if isinstance(exact_user_comment, str) and exact_user_comment.strip():
            return True

        legacy_user_comment = exif.get("UserComment")
        if not isinstance(legacy_user_comment, str):
            return False

        text = legacy_user_comment.strip()
        if not text:
            return False

        if text.startswith("{") or text.startswith("["):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, dict):
                    if parsed.get("prompt") or parsed.get("workflow") or parsed.get("resource-stack"):
                        return False
            except (json.JSONDecodeError, TypeError):
                pass

        normalized = text.lower()
        if "civitai resources:" in normalized:
            return False
        has_steps = "steps:" in normalized
        has_seed = "seed:" in normalized
        has_sampler = "sampler:" in normalized
        has_cfg = "cfg scale:" in normalized
        has_negative = "negative prompt:" in normalized
        return has_steps and (has_cfg or has_sampler or has_seed or has_negative)

    def _get_a1111_parameter_text(self, exif: dict[str, Any]) -> str:
        """Extract normalized A1111 parameter text from EXIF fields."""
        candidate_values = [
            exif.get("parameters"),
            exif.get("Parameters"),
            exif.get("user_comment"),
            exif.get("UserComment"),
        ]

        for value in candidate_values:
            if not isinstance(value, str):
                continue
            text = value.strip()
            if not text:
                continue
            if text.startswith("{") or text.startswith("["):
                continue
            return text.lower()
        return ""

    def read_a1111_features_for_image(self, image: Any) -> dict[str, bool]:
        """Detect A1111 feature flags (Hires, RP, ADetailer) from image EXIF.

        Returns:
            dict with keys: "hires_upscale", "regional_prompter", "adetailer"
        """
        exif = self._read_exif_for_image(image)
        if not self._looks_like_a1111_user_comment_payload(exif):
            return {"hires_upscale": False, "regional_prompter": False, "adetailer": False}

        text = self._get_a1111_parameter_text(exif)
        if not text:
            return {"hires_upscale": False, "regional_prompter": False, "adetailer": False}

        hires_upscale = any(keyword in text for keyword in self._A1111_HIRES_KEYWORDS)

        user_comment_text = str(exif.get("user_comment") or exif.get("UserComment") or "")
        regional_prompter = (
            "rp active" in text or
            "regional prompt" in text or
            bool(self._A1111_RP_DIRECTIVE_RE.search(user_comment_text))
        )

        adetailer = "adetailer" in text

        return {
            "hires_upscale": bool(hires_upscale),
            "regional_prompter": bool(regional_prompter),
            "adetailer": bool(adetailer),
        }

    def filter_image_ids_by_a1111_features(
        self,
        images_query,
        *,
        a1111_hires: Optional[list[str]] = None,
        a1111_regional_prompter: Optional[list[str]] = None,
        a1111_adetailer: Optional[list[str]] = None,
    ) -> Optional[list[int]]:
        """Filter images by A1111 feature presence/absence.

        Args:
            a1111_hires: ["present"] to show images with Hires, ["absent"] to show without
            a1111_regional_prompter: ["present"] to show images with RP, ["absent"] to show without
            a1111_adetailer: ["present"] to show images with ADetailer, ["absent"] to show without

        Returns:
            List of image IDs matching all specified feature constraints, or None if no constraints
        """
        constraints = {
            "hires_upscale": self.normalize_query_values(a1111_hires),
            "regional_prompter": self.normalize_query_values(a1111_regional_prompter),
            "adetailer": self.normalize_query_values(a1111_adetailer),
        }

        active_constraints = {
            key: set(values) for key, values in constraints.items() if values
        }
        if not active_constraints:
            return None

        candidate_images = (
            images_query.options()
            .with_entities(
                ImageModel.id,
                ImageModel.file_path,
                ImageModel.json_metadata,
            )
            .all()
        )

        matched_ids: list[int] = []
        for image_id, file_path, json_metadata in candidate_images:
            stub = type(
                "_ImageStub",
                (),
                {
                    "id": image_id,
                    "file_path": file_path,
                    "json_metadata": json_metadata,
                },
            )()
            features = self.read_a1111_features_for_image(stub)

            matches = True
            for feature_key, allowed_modes in active_constraints.items():
                has_feature = bool(features.get(feature_key, False))
                if "present" in allowed_modes and not has_feature:
                    matches = False
                    break
                if "absent" in allowed_modes and has_feature:
                    matches = False
                    break

            if matches:
                matched_ids.append(int(image_id))

        return matched_ids

    def filter_image_ids_by_generation_software(
        self,
        images_query,
        generation_softwares: Optional[list[str]],
    ) -> Optional[list[int]]:
        normalized_generation_softwares = self.normalize_query_values(generation_softwares)
        if not normalized_generation_softwares:
            return None

        allowed_values = set(normalized_generation_softwares)
        candidate_images = (
            images_query.options()
            .with_entities(
                ImageModel.id,
                ImageModel.file_path,
                ImageModel.json_metadata,
                ImageModel.user_nsfw_rating,
                ImageModel.user_nsfw_safety_class,
            )
            .all()
        )

        matched_ids: list[int] = []
        for image_id, file_path, json_metadata, user_nsfw_rating, user_nsfw_safety_class in candidate_images:
            stub = type(
                "_ImageStub",
                (),
                {
                    "id": image_id,
                    "file_path": file_path,
                    "json_metadata": json_metadata,
                    "user_nsfw_rating": user_nsfw_rating,
                    "user_nsfw_safety_class": user_nsfw_safety_class,
                },
            )()
            if self.read_generation_software_for_image(stub) in allowed_values:
                matched_ids.append(int(image_id))
        return matched_ids

    def filter_image_ids_by_nsfw_ratings(
        self,
        images_query,
        nsfw_ratings: Optional[list[str]],
    ) -> Optional[list[int]]:
        normalized_nsfw_ratings = self.normalize_query_values(nsfw_ratings)
        if not normalized_nsfw_ratings:
            return None

        want_na_rating = "n/a" in normalized_nsfw_ratings
        allowed_values = {v for v in normalized_nsfw_ratings if v != "n/a"}

        candidate_images = (
            images_query.options()
            .with_entities(
                ImageModel.id,
                ImageModel.file_path,
                ImageModel.json_metadata,
                ImageModel.user_nsfw_rating,
                ImageModel.user_nsfw_safety_class,
            )
            .all()
        )

        matched_ids: list[int] = []
        for image_id, file_path, json_metadata, user_nsfw_rating, user_nsfw_safety_class in candidate_images:
            stub = type(
                "_ImageStub",
                (),
                {
                    "id": image_id,
                    "file_path": file_path,
                    "json_metadata": json_metadata,
                    "user_nsfw_rating": user_nsfw_rating,
                    "user_nsfw_safety_class": user_nsfw_safety_class,
                },
            )()
            labels = {
                str(label or "").strip().lower()
                for label in self.read_nsfw_ratings_for_image(stub)
                if str(label or "").strip()
            }
            matched = False
            if want_na_rating and not labels.intersection(self._NSFW_GRANULAR_RATINGS):
                matched = True
            elif allowed_values and labels.intersection(allowed_values):
                matched = True
            if matched:
                matched_ids.append(int(image_id))

        return matched_ids

    def filter_image_ids_by_nsfw_safety_classes(
        self,
        images_query,
        nsfw_safety_classes: Optional[list[str]],
    ) -> Optional[list[int]]:
        normalized_nsfw_safety_classes = self.normalize_query_values(nsfw_safety_classes)
        if not normalized_nsfw_safety_classes:
            return None

        want_na_safety = "n/a" in normalized_nsfw_safety_classes
        allowed_values = {v for v in normalized_nsfw_safety_classes if v != "n/a"}

        candidate_images = (
            images_query.options()
            .with_entities(
                ImageModel.id,
                ImageModel.file_path,
                ImageModel.json_metadata,
                ImageModel.user_nsfw_rating,
                ImageModel.user_nsfw_safety_class,
            )
            .all()
        )

        matched_ids: list[int] = []
        for image_id, file_path, json_metadata, user_nsfw_rating, user_nsfw_safety_class in candidate_images:
            stub = type(
                "_ImageStub",
                (),
                {
                    "id": image_id,
                    "file_path": file_path,
                    "json_metadata": json_metadata,
                    "user_nsfw_rating": user_nsfw_rating,
                    "user_nsfw_safety_class": user_nsfw_safety_class,
                },
            )()
            labels = {
                str(label or "").strip().lower()
                for label in self.read_nsfw_ratings_for_image(stub)
                if str(label or "").strip()
            }
            matched = False
            if want_na_safety and not labels.intersection(self._NSFW_SAFETY_CLASSES):
                matched = True
            elif allowed_values and labels.intersection(allowed_values):
                matched = True
            if matched:
                matched_ids.append(int(image_id))

        return matched_ids

    def apply_image_list_filters(
        self,
        images_query,
        *,
        search: Optional[str] = None,
        source_sites: Optional[list[str]] = None,
        mimetypes: Optional[list[str]] = None,
        artist_names: Optional[list[str]] = None,
        collection_names: Optional[list[str]] = None,
    ):
        normalized_source_sites = self.normalize_query_values(source_sites)
        normalized_mimetypes = self.normalize_query_values(mimetypes)
        normalized_artist_names = self.normalize_query_values(artist_names)
        normalized_collection_names = self.normalize_query_values(collection_names)
        normalized_search = str(search or "").strip().lower()

        if normalized_source_sites:
            images_query = images_query.filter(func.lower(ImageModel.source_site).in_(normalized_source_sites))

        if normalized_mimetypes:
            images_query = images_query.filter(func.lower(ImageModel.mimetype).in_(normalized_mimetypes))

        if normalized_artist_names:
            images_query = images_query.filter(
                ImageModel.artist.has(func.lower(Artist.name).in_(normalized_artist_names))
            )

        if normalized_collection_names:
            images_query = images_query.filter(
                ImageModel.collections.any(func.lower(CollectionModel.name).in_(normalized_collection_names))
            )

        if normalized_search:
            like_term = f"%{normalized_search}%"
            images_query = images_query.filter(
                or_(
                    func.lower(ImageModel.file_name).like(like_term),
                    func.lower(ImageModel.file_hash).like(like_term),
                    func.lower(ImageModel.source_url).like(like_term),
                    func.lower(ImageModel.source_site).like(like_term),
                    func.lower(ImageModel.mimetype).like(like_term),
                    ImageModel.artist.has(func.lower(Artist.name).like(like_term)),
                    ImageModel.collections.any(func.lower(CollectionModel.name).like(like_term)),
                    ImageModel.tags.any(func.lower(Tag.name).like(like_term)),
                )
            )

        return images_query
