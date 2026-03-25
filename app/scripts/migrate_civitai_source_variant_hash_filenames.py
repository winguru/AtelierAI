#!/usr/bin/env python3
"""Migrate CivitAI source variant resources to hash-based filenames.

This script rewrites legacy variant paths like:
  image_resources/civitai_source_variants/<image_id>.ext
into canonical hash-based paths:
  image_resources/civitai_source_variants/<sha256>.<ext>

It updates both DB json_metadata and sidecar JSON keys:
- civitai_source_variant_static
- civitai_source_variant

Default mode is dry-run. Use --apply to persist changes.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, cast

from path_setup import PROJECT_ROOT  # noqa: F401  # side effect: sys.path setup
from backend.config import IMAGE_LIBRARY_PATH, IMAGE_RESOURCES_PATH
from backend.database import SessionLocal
from backend.models import ImageModel

_VARIANT_KEYS = ("civitai_source_variant_static", "civitai_source_variant")
_VARIANT_ROOT_REL = "civitai_source_variants"
_HASH_RE = re.compile(r"^[0-9a-f]{64}$", re.IGNORECASE)


@dataclass
class MigrationUpdate:
    image_id: int
    key: str
    old_rel_path: str
    new_rel_path: str


def _sha256_file(path: Path) -> str:
    import hashlib

    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            hasher.update(chunk)
    return hasher.hexdigest()


def _detect_suffix(path: Path) -> str:
    suffix = path.suffix.lower()
    try:
        with open(path, "rb") as handle:
            header = handle.read(16)
    except OSError:
        return suffix or ".bin"

    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if header.startswith(b"\xff\xd8"):
        return ".jpg"
    if header.startswith(b"GIF87a") or header.startswith(b"GIF89a"):
        return ".gif"
    if len(header) >= 12 and header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return ".webp"
    if len(header) >= 8 and header[4:8] == b"ftyp":
        return ".mp4"

    return suffix or ".bin"


def _is_hash_filename(path_value: str) -> bool:
    stem = Path(path_value).stem
    return bool(_HASH_RE.fullmatch(stem))


def _load_sidecar(image: ImageModel) -> tuple[Path, dict[str, Any]]:
    sidecar_path = (Path(IMAGE_LIBRARY_PATH) / str(image.file_path)).with_suffix(".json")
    if not sidecar_path.exists():
        return sidecar_path, {}
    try:
        with open(sidecar_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if isinstance(payload, dict):
            return sidecar_path, payload
    except (OSError, json.JSONDecodeError):
        pass
    return sidecar_path, {}


def _extract_variant_rel_paths(payload: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in _VARIANT_KEYS:
        value = payload.get(key)
        if not isinstance(value, dict):
            continue
        rel_path = str(value.get("variant_file_path") or "").strip()
        if rel_path:
            out[key] = rel_path
    return out


def _collect_candidate_updates(
    image: ImageModel,
    db_payload: dict[str, Any],
    sidecar_payload: dict[str, Any],
) -> tuple[list[MigrationUpdate], dict[str, tuple[str, int]], list[str]]:
    variant_root = Path(IMAGE_RESOURCES_PATH) / _VARIANT_ROOT_REL
    warnings: list[str] = []

    refs: list[tuple[str, str]] = []
    for key, rel in _extract_variant_rel_paths(db_payload).items():
        refs.append((key, rel))
    for key, rel in _extract_variant_rel_paths(sidecar_payload).items():
        refs.append((key, rel))

    dedup_refs = list(dict.fromkeys(refs))

    updates: list[MigrationUpdate] = []
    file_targets: dict[str, tuple[str, int]] = {}

    for key, rel_path in dedup_refs:
        rel_norm = rel_path.replace("\\", "/")
        if not rel_norm.startswith(f"{_VARIANT_ROOT_REL}/"):
            continue

        abs_path = Path(IMAGE_RESOURCES_PATH) / rel_norm
        if not abs_path.exists() or not abs_path.is_file():
            warnings.append(
                f"image_id={image.id} key={key} skipped missing variant file: {rel_norm}"
            )
            continue

        file_hash = _sha256_file(abs_path)
        suffix = _detect_suffix(abs_path)
        target_abs = variant_root / f"{file_hash}{suffix}"
        target_rel = str(target_abs.relative_to(Path(IMAGE_RESOURCES_PATH))).replace("\\", "/")
        target_size = int(abs_path.stat().st_size)

        file_targets[rel_norm] = (target_rel, target_size)

        if rel_norm != target_rel:
            updates.append(
                MigrationUpdate(
                    image_id=int(getattr(image, "id")),
                    key=key,
                    old_rel_path=rel_norm,
                    new_rel_path=target_rel,
                )
            )

    return updates, file_targets, warnings


def _apply_payload_updates(
    payload: dict[str, Any],
    *,
    file_targets: dict[str, tuple[str, int]],
) -> bool:
    changed = False
    for key in _VARIANT_KEYS:
        value = payload.get(key)
        if not isinstance(value, dict):
            continue

        rel_path = str(value.get("variant_file_path") or "").strip().replace("\\", "/")
        if not rel_path or rel_path not in file_targets:
            continue

        new_rel_path, actual_size = file_targets[rel_path]
        if rel_path != new_rel_path:
            value["variant_file_path"] = new_rel_path
            changed = True

        # Keep variant_file_hash aligned with filename stem.
        hash_stem = Path(new_rel_path).stem
        if value.get("variant_file_hash") != hash_stem:
            value["variant_file_hash"] = hash_stem
            changed = True

        if int(value.get("actual_file_size") or 0) != actual_size:
            value["actual_file_size"] = actual_size
            changed = True

        # Set actual_mimetype when absent from legacy payloads.
        actual_mimetype = str(value.get("actual_mimetype") or "").strip()
        if not actual_mimetype:
            suffix = Path(new_rel_path).suffix.lower()
            if suffix == ".webp":
                value["actual_mimetype"] = "image/webp"
                changed = True
            elif suffix in {".jpg", ".jpeg"}:
                value["actual_mimetype"] = "image/jpeg"
                changed = True
            elif suffix == ".png":
                value["actual_mimetype"] = "image/png"
                changed = True
            elif suffix == ".gif":
                value["actual_mimetype"] = "image/gif"
                changed = True
            elif suffix == ".mp4":
                value["actual_mimetype"] = "video/mp4"
                changed = True

    return changed


def _migrate_files(file_targets: dict[str, tuple[str, int]], *, apply: bool) -> tuple[int, int, list[str]]:
    """Return (renamed_files, reused_existing_files, warnings)."""
    renamed = 0
    reused = 0
    warnings: list[str] = []

    if not apply:
        return renamed, reused, warnings

    for old_rel_path, (new_rel_path, _size) in file_targets.items():
        if old_rel_path == new_rel_path:
            continue

        old_abs = Path(IMAGE_RESOURCES_PATH) / old_rel_path
        new_abs = Path(IMAGE_RESOURCES_PATH) / new_rel_path
        new_abs.parent.mkdir(parents=True, exist_ok=True)

        if not old_abs.exists() or not old_abs.is_file():
            warnings.append(f"missing source file during apply: {old_rel_path}")
            continue

        # Move/collapse data file.
        if new_abs.exists() and new_abs.is_file():
            try:
                if _sha256_file(old_abs) == _sha256_file(new_abs):
                    old_abs.unlink(missing_ok=True)
                    reused += 1
                else:
                    warnings.append(
                        f"target exists with different hash; skipped rename {old_rel_path} -> {new_rel_path}"
                    )
            except OSError as exc:
                warnings.append(f"failed handling existing target for {old_rel_path}: {exc}")
            continue

        try:
            old_abs.rename(new_abs)
            renamed += 1
        except OSError as exc:
            warnings.append(f"failed rename {old_rel_path} -> {new_rel_path}: {exc}")
            continue

        # Move matching metadata sidecar if present.
        old_meta = old_abs.with_suffix(f"{old_abs.suffix}.json")
        new_meta = new_abs.with_suffix(f"{new_abs.suffix}.json")
        if old_meta.exists() and old_meta.is_file():
            if new_meta.exists() and new_meta.is_file():
                old_meta.unlink(missing_ok=True)
            else:
                try:
                    old_meta.rename(new_meta)
                except OSError as exc:
                    warnings.append(f"failed metadata rename for {old_rel_path}: {exc}")

    return renamed, reused, warnings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate CivitAI source variants to hash filenames")
    parser.add_argument("--apply", action="store_true", help="Apply changes (default: dry-run)")
    parser.add_argument("--limit", type=int, default=0, help="Limit processed image rows (0 = all)")
    parser.add_argument(
        "--only-nonhash",
        action="store_true",
        help="Only process variant paths whose basename is not already a 64-hex hash",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    apply = bool(args.apply)
    limit = max(0, int(args.limit))
    only_nonhash = bool(args.only_nonhash)

    db = SessionLocal()
    try:
        query = db.query(ImageModel).order_by(ImageModel.id.asc())
        if limit > 0:
            query = query.limit(limit)

        scanned_images = 0
        touched_images = 0
        metadata_updates = 0
        planned_file_ops = 0
        renamed_files_total = 0
        reused_files_total = 0
        warnings: list[str] = []

        print("CivitAI source variant filename migration")
        print(f"Mode: {'APPLY' if apply else 'DRY-RUN'}")
        print(f"only_nonhash: {only_nonhash}")

        for image in query.all():
            scanned_images += 1
            raw_json = getattr(image, "json_metadata", None)
            db_json: dict[str, Any] = dict(raw_json) if isinstance(raw_json, dict) else {}
            sidecar_path, sidecar_json = _load_sidecar(image)

            updates, file_targets, image_warnings = _collect_candidate_updates(image, db_json, sidecar_json)
            warnings.extend(image_warnings)

            if only_nonhash:
                updates = [
                    u for u in updates if not _is_hash_filename(u.old_rel_path)
                ]
                file_targets = {
                    old_rel: target
                    for old_rel, target in file_targets.items()
                    if not _is_hash_filename(old_rel)
                }

            if not updates and not file_targets:
                continue

            touched_images += 1
            planned_file_ops += len([1 for old_rel, (new_rel, _s) in file_targets.items() if old_rel != new_rel])

            # Apply file-level moves first, so metadata paths point to final location.
            renamed_files, reused_files, file_warnings = _migrate_files(file_targets, apply=apply)
            renamed_files_total += renamed_files
            reused_files_total += reused_files
            warnings.extend(file_warnings)

            db_changed = _apply_payload_updates(db_json, file_targets=file_targets)
            sidecar_changed = _apply_payload_updates(sidecar_json, file_targets=file_targets)

            if db_changed:
                setattr(image, "json_metadata", cast(Any, db_json))
                metadata_updates += 1

            if sidecar_changed and apply:
                try:
                    with open(sidecar_path, "w", encoding="utf-8") as handle:
                        json.dump(sidecar_json, handle, indent=2)
                except OSError as exc:
                    warnings.append(f"failed writing sidecar for image_id={image.id}: {exc}")

        if apply:
            db.commit()
        else:
            db.rollback()

        print(f"scanned_images={scanned_images}")
        print(f"touched_images={touched_images}")
        print(f"metadata_updates={metadata_updates}")
        print(f"planned_file_ops={planned_file_ops}")
        print(f"renamed_files={renamed_files_total}")
        print(f"reused_existing_files={reused_files_total}")
        if warnings:
            print(f"warnings={len(warnings)}")
            for item in warnings[:30]:
                print(f"  - {item}")
            if len(warnings) > 30:
                print(f"  ... {len(warnings) - 30} more warnings")

        if not apply:
            print("Dry-run only. Re-run with --apply to persist changes.")

        return 0
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
