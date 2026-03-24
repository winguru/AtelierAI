#!/usr/bin/env python3
"""Backfill static source variants for CivitAI mixed-media records.

Use this when historical rows have a local static image (jpg/png/webp) but CivitAI
metadata points to a video URL (mp4/webm/mov/mkv). The script preserves the local
static media under image_resources/civitai_source_variants.

Default mode is dry-run. Use --apply to persist changes.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from path_setup import PROJECT_ROOT  # noqa: F401  # side effect: import path setup
from backend.config import IMAGE_LIBRARY_PATH, IMAGE_RESOURCES_PATH
from backend.database import SessionLocal
from backend.models import ImageModel

_VIDEO_SUFFIXES = {".mp4", ".webm", ".mov", ".mkv"}
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
_CIVITAI_IMAGE_ID_RE = re.compile(r"/images/(?P<image_id>\d+)(?:/|$)")
_VARIANT_DIRNAME = "civitai_source_variants"


@dataclass
class Candidate:
    image_db_id: int
    civitai_image_id: int
    file_path: str
    mimetype: Optional[str]
    civitai_url: str


def _normalize_mime(value: Optional[str]) -> str:
    return str(value or "").split(";", 1)[0].strip().lower()


def _looks_like_video_url(url: Optional[str]) -> bool:
    text = str(url or "").strip()
    if not text:
        return False
    try:
        suffix = Path(urlparse(text).path).suffix.lower()
    except Exception:
        return False
    return suffix in _VIDEO_SUFFIXES


def _extract_civitai_image_id(source_url: Optional[str], fallback_id: int) -> int:
    text = str(source_url or "").strip()
    if text:
        match = _CIVITAI_IMAGE_ID_RE.search(text)
        if match:
            try:
                return int(match.group("image_id"))
            except (TypeError, ValueError):
                pass
    return int(fallback_id)


def _get_civitai_media_url(image: ImageModel) -> Optional[str]:
    raw_metadata = getattr(image, "json_metadata", None)
    metadata = raw_metadata if isinstance(raw_metadata, dict) else None
    if not metadata:
        return None
    civitai = metadata.get("civitai")
    if not isinstance(civitai, dict):
        return None
    url = civitai.get("url")
    if isinstance(url, str) and url.strip():
        return url.strip()
    return None


def _variant_root() -> Path:
    root = Path(IMAGE_RESOURCES_PATH) / _VARIANT_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def _build_variant_path(civitai_image_id: int, local_path: Path, mimetype: Optional[str]) -> Path:
    suffix = local_path.suffix.lower()
    if not suffix:
        normalized_mime = _normalize_mime(mimetype)
        if normalized_mime.endswith("png"):
            suffix = ".png"
        elif normalized_mime.endswith("webp"):
            suffix = ".webp"
        elif normalized_mime.endswith("gif"):
            suffix = ".gif"
        else:
            suffix = ".jpg"
    return _variant_root() / f"{civitai_image_id}_static_source{suffix}"


def _scan_candidates(limit: int) -> list[Candidate]:
    db = SessionLocal()
    try:
        query = db.query(ImageModel).order_by(ImageModel.id.asc())
        if limit > 0:
            query = query.limit(limit)

        out: list[Candidate] = []
        for image in query.all():
            source_url = str(getattr(image, "source_url", "") or "").strip()
            if "civitai.com/images/" not in source_url:
                continue

            local_mime = _normalize_mime(getattr(image, "mimetype", None))
            local_suffix = Path(str(image.file_path or "")).suffix.lower()
            local_is_image = local_mime.startswith("image/") or local_suffix in _IMAGE_SUFFIXES
            if not local_is_image:
                continue

            civitai_url = _get_civitai_media_url(image)
            if not _looks_like_video_url(civitai_url):
                continue

            image_db_id = int(getattr(image, "id"))
            civitai_image_id = _extract_civitai_image_id(source_url, image_db_id)
            out.append(
                Candidate(
                    image_db_id=image_db_id,
                    civitai_image_id=civitai_image_id,
                    file_path=str(image.file_path),
                    mimetype=str(getattr(image, "mimetype", "") or "") or None,
                    civitai_url=str(civitai_url or ""),
                )
            )

        return out
    finally:
        db.close()


def _apply_candidates(candidates: list[Candidate], overwrite: bool) -> tuple[int, int, int]:
    """Return (copied_count, updated_metadata_count, skipped_missing_local_count)."""
    copied_count = 0
    updated_metadata_count = 0
    missing_local_count = 0

    db = SessionLocal()
    try:
        for item in candidates:
            image = db.query(ImageModel).filter(ImageModel.id == item.image_db_id).first()
            if image is None:
                continue

            local_path = Path(IMAGE_LIBRARY_PATH) / str(image.file_path)
            if not local_path.exists():
                missing_local_count += 1
                continue

            image_mime = str(getattr(image, "mimetype", "") or "") or None
            variant_path = _build_variant_path(item.civitai_image_id, local_path, image_mime)
            should_copy = overwrite or not variant_path.exists()
            if not should_copy and local_path.stat().st_mtime_ns > variant_path.stat().st_mtime_ns:
                should_copy = True

            if should_copy:
                shutil.copy2(local_path, variant_path)
                copied_count += 1

            metadata = {
                "image_id": item.civitai_image_id,
                "image_db_id": item.image_db_id,
                "declared_civitai_url": item.civitai_url,
                "actual_mimetype": image.mimetype,
                "library_file_path": str(image.file_path),
                "variant_file_path": str(variant_path.relative_to(Path(IMAGE_RESOURCES_PATH))),
                "actual_file_size": local_path.stat().st_size,
                "reason": "backfill_static_variant_for_video_source",
                "saved_at": datetime.now(timezone.utc).isoformat(),
            }

            metadata_path = variant_path.with_suffix(f"{variant_path.suffix}.json")
            with open(metadata_path, "w", encoding="utf-8") as handle:
                json.dump(metadata, handle, indent=2)

            raw_json = getattr(image, "json_metadata", None)
            merged_json: dict[str, Any] = dict(raw_json) if isinstance(raw_json, dict) else {}
            merged_json["civitai_source_variant_static"] = metadata
            setattr(image, "json_metadata", merged_json)
            updated_metadata_count += 1

        db.commit()
        return copied_count, updated_metadata_count, missing_local_count
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill static CivitAI source variants for mixed-media records"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes (default: dry-run)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit scanned image rows (0 = no limit)",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Force re-copy variant files even if target exists",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    candidates = _scan_candidates(limit=max(0, int(args.limit)))

    print("CivitAI static source variant backfill")
    print(f"Mode: {'APPLY' if args.apply else 'DRY-RUN'}")
    print(f"Candidates: {len(candidates)}")

    preview = candidates[:20]
    if preview:
        print("Preview (first 20):")
        for item in preview:
            print(
                f"  - db_id={item.image_db_id} civitai_id={item.civitai_image_id} "
                f"file={item.file_path} url={item.civitai_url}"
            )

    if not args.apply:
        print("No changes applied. Re-run with --apply to persist static variants.")
        return 0

    copied_count, updated_metadata_count, missing_local_count = _apply_candidates(
        candidates=candidates,
        overwrite=bool(args.overwrite),
    )

    print("Completed:")
    print(f"  variants copied: {copied_count}")
    print(f"  image metadata updated: {updated_metadata_count}")
    print(f"  missing local files skipped: {missing_local_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
