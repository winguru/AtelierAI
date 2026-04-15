"""Utilities for extracting promoted metadata columns from image payloads.

Used by both the ingestion pipeline and the backfill script to keep
extraction logic in one place.
"""
from __future__ import annotations

from typing import Any, Optional

from services import a1111_parser_service as _a1111_svc


def extract_generation_software(payload: dict[str, Any]) -> Optional[str]:
    """Extract generation_software from a merged image payload."""
    raw = str(payload.get("generation_software") or "").strip()
    return raw.lower() if raw else None


def extract_civitai_nsfw_level(payload: dict[str, Any]) -> Optional[int]:
    """Extract CivitAI nsfwLevel integer from a merged payload."""
    civitai = payload.get("civitai_data") or payload.get("civitai") or {}
    if not isinstance(civitai, dict):
        return None

    for source in (civitai, civitai.get("image") or {}):
        if not isinstance(source, dict):
            continue
        raw = source.get("nsfwLevel")
        if isinstance(raw, int):
            return raw
        if isinstance(raw, str) and raw.strip().isdigit():
            return int(raw.strip())
    return None


def _looks_like_a1111(exif: dict[str, Any]) -> bool:
    """Check if EXIF looks like A1111 generation metadata."""
    return _a1111_svc.looks_like_a1111_exif(exif)


def _get_a1111_text(exif: dict[str, Any]) -> str:
    """Extract normalized A1111 parameter text from EXIF."""
    return _a1111_svc._get_a1111_text(exif)


def detect_a1111_features(exif: dict[str, Any]) -> dict[str, bool]:
    """Detect A1111 features from EXIF data.

    Returns dict with keys: has_a1111_metadata, a1111_hires,
    a1111_regional_prompter, a1111_adetailer.
    """
    return _a1111_svc.detect_a1111_features_from_exif(exif)


def detect_comfyui(exif: dict[str, Any]) -> bool:
    """Detect ComfyUI metadata from EXIF data."""
    for key in ("prompt", "Prompt", "workflow", "Workflow"):
        value = exif.get(key)
        if isinstance(value, str) and value.strip().startswith("{"):
            return True
        if isinstance(value, dict):
            return True
    return False


def detect_generation_prompt(payload: dict[str, Any]) -> bool:
    """Detect whether any generation prompt text exists in the payload."""
    exif = payload.get("exif_data") or {}
    if not isinstance(exif, dict):
        exif = {}

    for key in ("parameters", "Parameters", "user_comment"):
        val = exif.get(key)
        if isinstance(val, str) and val.strip() and not val.strip().startswith("{"):
            return True

    for key in ("prompt", "Prompt"):
        val = exif.get(key)
        if isinstance(val, (dict, str)) and val:
            return True

    civitai = payload.get("civitai_data") or payload.get("civitai") or {}
    if isinstance(civitai, dict):
        meta = civitai.get("meta") or {}
        if isinstance(meta, dict) and meta.get("prompt"):
            return True

    return False


def compute_promoted_columns(
    payload: dict[str, Any],
    exif: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Compute all promoted column values from a merged image payload.

    Args:
        payload: Merged image payload (json_metadata + sidecar).
        exif: EXIF data dict. If None, extracted from payload["exif_data"].

    Returns:
        Dict with column names as keys, suitable for ImageModel update.
    """
    if exif is None:
        exif = payload.get("exif_data") or {}
        if not isinstance(exif, dict):
            exif = {}

    a1111 = detect_a1111_features(exif)

    return {
        "generation_software": extract_generation_software(payload),
        "civitai_nsfw_level": extract_civitai_nsfw_level(payload),
        "has_a1111_metadata": a1111["has_a1111_metadata"],
        "a1111_hires": a1111["a1111_hires"],
        "a1111_regional_prompter": a1111["a1111_regional_prompter"],
        "a1111_adetailer": a1111["a1111_adetailer"],
        "has_comfyui_metadata": detect_comfyui(exif),
        "has_generation_prompt": detect_generation_prompt(payload),
    }
