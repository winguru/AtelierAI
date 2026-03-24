from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Any, Optional

from sqlalchemy import func, or_

from models import Artist, CollectionModel, ImageModel, Tag


class ImageQueryService:
    _NSFW_GRANULAR_RATINGS: frozenset[str] = frozenset({"pg", "pg13", "r", "x", "xxx"})
    _NSFW_SAFETY_CLASSES: frozenset[str] = frozenset({"safe", "mature", "explicit"})
    _NSFW_NA_SENTINELS: frozenset[str] = frozenset({"n/a"})

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
