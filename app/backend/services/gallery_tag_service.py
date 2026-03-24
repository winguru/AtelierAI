from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable


class GalleryTagService:
    """Parses multi-source tag payloads for gallery/taxonomy views."""

    @staticmethod
    def normalize_text(value: str) -> str:
        return str(value or "").strip().lower()

    def add_tag_name(self, bucket: set[str], value: Any) -> None:
        normalized = self.normalize_text(str(value or ""))
        if normalized:
            bucket.add(normalized)

    def add_tag_collection(self, bucket: set[str], value: Any) -> None:
        if value is None:
            return

        if isinstance(value, str):
            self.add_tag_name(bucket, value)
            return

        if isinstance(value, dict):
            if isinstance(value.get("name"), str):
                self.add_tag_name(bucket, value.get("name"))
            if isinstance(value.get("tag"), str):
                self.add_tag_name(bucket, value.get("tag"))
            if isinstance(value.get("label"), str):
                self.add_tag_name(bucket, value.get("label"))
            return

        if isinstance(value, (list, tuple, set)):
            for item in value:
                self.add_tag_collection(bucket, item)

    def extract_image_scope_tag_names(
        self,
        payload: dict[str, Any],
        *,
        normalize_taxonomy_text: Callable[[str], str],
    ) -> dict[str, set[str]]:
        by_source: dict[str, set[str]] = {
            "civitai": set(),
            "danbooru": set(),
            "prompt": set(),
            "user": set(),
        }

        civitai = payload.get("civitai_data") or payload.get("civitai") or {}
        if not isinstance(civitai, dict):
            civitai = {}
        self.add_tag_collection(by_source["civitai"], civitai.get("tags"))
        meta_value = civitai.get("meta")
        meta: dict[str, Any] = meta_value if isinstance(meta_value, dict) else {}
        image_value = civitai.get("image")
        image: dict[str, Any] = image_value if isinstance(image_value, dict) else {}
        self.add_tag_collection(by_source["civitai"], meta.get("tags"))
        self.add_tag_collection(by_source["civitai"], image.get("tags"))

        exif_value = payload.get("exif_data")
        exif: dict[str, Any] = exif_value if isinstance(exif_value, dict) else {}
        self.add_tag_collection(by_source["prompt"], payload.get("prompt_tags"))
        self.add_tag_collection(by_source["danbooru"], payload.get("danbooru_tags"))
        self.add_tag_collection(by_source["user"], payload.get("user_tags"))
        self.add_tag_collection(by_source["user"], exif.get("user_tags"))

        raw_tags = payload.get("tags")
        if isinstance(raw_tags, list):
            for tag in raw_tags:
                if isinstance(tag, str):
                    self.add_tag_name(by_source["user"], tag)
                    continue
                if not isinstance(tag, dict):
                    continue

                name = tag.get("name") or tag.get("tag") or tag.get("label")
                if not isinstance(name, str):
                    continue

                source = normalize_taxonomy_text(str(tag.get("source") or "user")) or "user"
                if source not in by_source:
                    source = "user"
                self.add_tag_name(by_source[source], name)

        return by_source

    @staticmethod
    def load_image_sidecar_payload(*, image_library_path: str, file_path: str) -> dict[str, Any]:
        sidecar_path = (Path(image_library_path) / str(file_path)).with_suffix(".json")
        if not sidecar_path.exists():
            return {}

        try:
            with open(sidecar_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            return loaded if isinstance(loaded, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}
