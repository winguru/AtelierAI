#!/usr/bin/env python3
"""Backfill original CivitAI variant filenames in persisted metadata.

This script populates missing `original_variant_file_name` for:
- json_metadata on ImageModel records
- image sidecar JSON files (image_library/*.json)
- variant metadata sidecars (image_resources/civitai_source_variants/*.ext.json)

Inference priority:
1. existing `original_variant_file_name`
2. `declared_filename`
3. non-hash `variant_file_path` basename
4. image `file_name` (last-resort fallback)

Default mode is dry-run. Use --apply to persist changes.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from path_setup import PROJECT_ROOT  # noqa: F401  # side effect: sys.path setup
from backend.config import IMAGE_LIBRARY_PATH, IMAGE_RESOURCES_PATH
from backend.database import SessionLocal
from backend.models import ImageModel

_VARIANT_KEYS = ("civitai_source_variant_static", "civitai_source_variant")
_HASH_STEM_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)
_URL_PLACEHOLDER_NAMES = {"original", "preview", "download", "file"}
_VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".mkv"}


def _normalize_name(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return Path(text).name.strip()


def _is_hash_basename(name: str) -> bool:
    if not name:
        return False
    stem = Path(name).stem
    return bool(_HASH_STEM_RE.fullmatch(stem))


def _guess_suffix_from_mime(mime_type: Any) -> str:
    mime = str(mime_type or "").strip().lower()
    if "png" in mime:
        return ".png"
    if "webp" in mime:
        return ".webp"
    if "gif" in mime:
        return ".gif"
    if "jpeg" in mime or "jpg" in mime:
        return ".jpg"
    if "mp4" in mime:
        return ".mp4"
    if "webm" in mime:
        return ".webm"
    if "quicktime" in mime or "mov" in mime:
        return ".mov"
    return ".bin"


def _name_from_url(url_value: Any, mime_type: Any) -> str:
    text = str(url_value or "").strip()
    if not text:
        return ""
    try:
        path_name = Path(urlparse(text).path).name.strip()
    except Exception:
        path_name = ""
    if not path_name:
        return ""

    suffix = Path(path_name).suffix.lower()
    stem = Path(path_name).stem.lower()
    if stem in _URL_PLACEHOLDER_NAMES and not suffix:
        return ""
    if not suffix:
        return f"{path_name}{_guess_suffix_from_mime(mime_type)}"
    return path_name


def _extract_civitai_image_id_from_url(url_value: Any) -> str:
    text = str(url_value or "").strip()
    if not text:
        return ""
    try:
        parsed = urlparse(text)
        parts = [part for part in parsed.path.split("/") if part]
    except Exception:
        parts = [part for part in text.split("/") if part]
    for idx, part in enumerate(parts):
        if part.lower() == "images" and idx + 1 < len(parts):
            candidate = parts[idx + 1].strip()
            if candidate.isdigit():
                return candidate
    return ""


def _load_json_file(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, dict):
            return payload
    except (OSError, json.JSONDecodeError):
        pass
    return {}


def _write_json_file(path: Path, payload: dict[str, Any], *, apply: bool) -> None:
    if not apply:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def _infer_original_variant_name(variant_payload: dict[str, Any], image: ImageModel, *, key_name: str = "") -> str:
    actual_mime_type = variant_payload.get("actual_mimetype") or variant_payload.get("declared_mimetype")
    reason = str(variant_payload.get("reason") or "").strip().lower()

    variant_rel = str(variant_payload.get("variant_file_path") or "").strip().replace("\\", "/")
    variant_base = _normalize_name(variant_rel)
    variant_suffix = Path(variant_base).suffix.lower() if variant_base else ""
    image_id = str(variant_payload.get("image_id") or "").strip()
    if not image_id:
        for url_field in ("source_url", "expected_source_url", "mismatch_source_url", "image_url"):
            candidate_id = _extract_civitai_image_id_from_url(variant_payload.get(url_field))
            if candidate_id:
                image_id = candidate_id
                break

    if reason in {
        "civitai_video_preview_variant",
        "civitai_video_url_served_static_fallback",
        "civitai_declared_video_but_served_image",
        "civitai_video_url_but_declared_non_video",
        "archived_static_variant_before_civitai_video_replacement",
        "backfill_static_variant_for_video_source",
    }:
        if variant_base and not _is_hash_basename(variant_base):
            return variant_base
        if image_id.isdigit():
            variant_suffix = Path(variant_rel).suffix.lower() or _guess_suffix_from_mime(actual_mime_type)
            return f"{image_id}{variant_suffix}"

    # For image alternates, prefer variant-specific naming over main media naming.
    if str(actual_mime_type or "").lower().startswith("image/"):
        if variant_base and not _is_hash_basename(variant_base):
            return variant_base
        if image_id.isdigit():
            return f"{image_id}{_guess_suffix_from_mime(actual_mime_type)}"

    for url_field in ("mismatch_source_url", "preview_image_url", "image_url"):
        from_url = _name_from_url(variant_payload.get(url_field), actual_mime_type)
        if from_url and not _is_hash_basename(from_url) and Path(from_url).suffix.lower() not in _VIDEO_SUFFIXES:
            return from_url

    current = _normalize_name(variant_payload.get("original_variant_file_name"))
    current_suffix = Path(current).suffix.lower() if current else ""
    should_override_current = (
        reason in {
            "civitai_video_preview_variant",
            "civitai_video_url_served_static_fallback",
            "civitai_declared_video_but_served_image",
            "civitai_video_url_but_declared_non_video",
            "archived_static_variant_before_civitai_video_replacement",
            "backfill_static_variant_for_video_source",
        }
        and current_suffix in _VIDEO_SUFFIXES
    )
    if not should_override_current and key_name == "civitai_source_variant_static":
        if current_suffix in _VIDEO_SUFFIXES and variant_suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
            should_override_current = True
    if current and not _is_hash_basename(current) and not should_override_current:
        return current

    declared = _normalize_name(variant_payload.get("declared_filename"))
    if declared and Path(declared).suffix.lower() not in _VIDEO_SUFFIXES:
        return declared

    if variant_base and not _is_hash_basename(variant_base):
        return variant_base

    image_file_name = _normalize_name(getattr(image, "file_name", None))
    if image_file_name:
        return image_file_name

    return variant_base


def _update_variant_payload(variant_payload: dict[str, Any], image: ImageModel, *, key_name: str = "") -> bool:
    inferred_name = _infer_original_variant_name(variant_payload, image, key_name=key_name)
    if not inferred_name:
        return False

    changed = False
    if str(variant_payload.get("original_variant_file_name") or "").strip() != inferred_name:
        variant_payload["original_variant_file_name"] = inferred_name
        changed = True

    declared_name = _normalize_name(variant_payload.get("declared_filename"))
    if not declared_name and inferred_name and not _is_hash_basename(inferred_name):
        variant_payload["declared_filename"] = inferred_name
        changed = True

    return changed


def _variant_sidecar_path(variant_payload: dict[str, Any]) -> Path | None:
    rel_path = str(variant_payload.get("variant_file_path") or "").strip().replace("\\", "/")
    if not rel_path:
        return None
    abs_path = Path(IMAGE_RESOURCES_PATH) / rel_path
    if not abs_path.exists() or not abs_path.is_file():
        return None
    return abs_path.with_suffix(f"{abs_path.suffix}.json")


def run_backfill(*, apply: bool, limit: int | None) -> dict[str, Any]:
    db_updates = 0
    image_sidecar_updates = 0
    variant_sidecar_updates = 0
    variants_updated = 0
    images_scanned = 0
    images_touched = 0
    examples: list[str] = []

    with SessionLocal() as db:
        query = db.query(ImageModel).order_by(ImageModel.id.asc())
        if limit is not None and limit > 0:
            query = query.limit(limit)

        images = query.all()
        for image in images:
            images_scanned += 1
            db_payload = image.json_metadata if isinstance(image.json_metadata, dict) else {}
            db_payload_dict: dict[str, Any] = dict(db_payload)
            db_payload_copy: dict[str, Any] = json.loads(json.dumps(db_payload_dict))

            image_sidecar_path = (Path(IMAGE_LIBRARY_PATH) / str(image.file_path)).with_suffix(".json")
            sidecar_payload = _load_json_file(image_sidecar_path)

            db_changed = False
            sidecar_changed = False
            image_variant_updates = 0

            for key in _VARIANT_KEYS:
                db_variant = db_payload_copy.get(key)
                if isinstance(db_variant, dict):
                    if _update_variant_payload(db_variant, image, key_name=key):
                        db_changed = True
                        image_variant_updates += 1

                    variant_sidecar = _variant_sidecar_path(db_variant)
                    if variant_sidecar is not None:
                        variant_payload = _load_json_file(variant_sidecar)
                        if isinstance(variant_payload, dict) and _update_variant_payload(variant_payload, image, key_name=key):
                            _write_json_file(variant_sidecar, variant_payload, apply=apply)
                            variant_sidecar_updates += 1

                sidecar_variant = sidecar_payload.get(key)
                if isinstance(sidecar_variant, dict):
                    if _update_variant_payload(sidecar_variant, image, key_name=key):
                        sidecar_changed = True

            if db_changed:
                variants_updated += image_variant_updates
                images_touched += 1
                db_updates += 1
                if apply:
                    setattr(image, "json_metadata", db_payload_copy)
                if len(examples) < 12:
                    examples.append(f"image_id={image.id} variants_updated={image_variant_updates}")

            if sidecar_changed:
                image_sidecar_updates += 1
                _write_json_file(image_sidecar_path, sidecar_payload, apply=apply)

        if apply:
            db.commit()
        else:
            db.rollback()

    return {
        "apply": apply,
        "images_scanned": images_scanned,
        "images_touched": images_touched,
        "variants_updated": variants_updated,
        "db_updates": db_updates,
        "image_sidecar_updates": image_sidecar_updates,
        "variant_sidecar_updates": variant_sidecar_updates,
        "examples": examples,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill original CivitAI variant filenames.")
    parser.add_argument("--apply", action="store_true", help="Persist updates. Default is dry-run.")
    parser.add_argument("--limit", type=int, default=0, help="Optional max images to scan.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    limit = args.limit if args.limit and args.limit > 0 else None
    result = run_backfill(apply=bool(args.apply), limit=limit)

    mode = "APPLY" if result["apply"] else "DRY-RUN"
    print(f"mode={mode}")
    print(f"images_scanned={result['images_scanned']}")
    print(f"images_touched={result['images_touched']}")
    print(f"variants_updated={result['variants_updated']}")
    print(f"db_updates={result['db_updates']}")
    print(f"image_sidecar_updates={result['image_sidecar_updates']}")
    print(f"variant_sidecar_updates={result['variant_sidecar_updates']}")
    if result["examples"]:
        print("examples:")
        for line in result["examples"]:
            print(f"  - {line}")


if __name__ == "__main__":
    main()
