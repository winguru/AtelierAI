"""Utilities for extracting promoted metadata columns from image payloads.

Used by both the ingestion pipeline and the backfill script to keep
extraction logic in one place.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional


_A1111_HIRES_KEYWORDS: tuple[str, ...] = (
    "hires upscaler", "hires steps", "hires upscale",
    "hr upscaler", "hr upscale", "denoising strength",
)

_A1111_RP_DIRECTIVE_RE: re.Pattern = re.compile(
    r"\b(ADDCOMM|ADDROW|ADDCOL)\b", re.IGNORECASE,
)


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
    for key in ("parameters", "Parameters"):
        candidate = exif.get(key)
        if isinstance(candidate, str) and candidate.strip():
            norm = candidate.strip().lower()
            has_steps = "steps:" in norm
            has_cfg = "cfg scale:" in norm
            has_sampler = "sampler:" in norm
            has_seed = "seed:" in norm
            has_negative = "negative prompt:" in norm
            if has_steps and (has_cfg or has_sampler or has_seed or has_negative):
                return True

    exact = exif.get("user_comment")
    if isinstance(exact, str) and exact.strip():
        return True

    legacy = exif.get("UserComment")
    if not isinstance(legacy, str):
        return False
    text = legacy.strip()
    if not text:
        return False
    if text.startswith("{") or text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict) and (
                parsed.get("prompt") or parsed.get("workflow") or parsed.get("resource-stack")
            ):
                return False
        except (json.JSONDecodeError, TypeError):
            pass
    norm = text.lower()
    if "civitai resources:" in norm:
        return False
    has_steps = "steps:" in norm
    has_cfg = "cfg scale:" in norm
    has_sampler = "sampler:" in norm
    has_seed = "seed:" in norm
    has_negative = "negative prompt:" in norm
    return has_steps and (has_cfg or has_sampler or has_seed or has_negative)


def _get_a1111_text(exif: dict[str, Any]) -> str:
    """Extract normalized A1111 parameter text from EXIF."""
    for key in ("parameters", "Parameters", "user_comment", "UserComment"):
        value = exif.get(key)
        if not isinstance(value, str):
            continue
        text = value.strip()
        if not text or text.startswith("{") or text.startswith("["):
            continue
        return text.lower()
    return ""


def detect_a1111_features(exif: dict[str, Any]) -> dict[str, bool]:
    """Detect A1111 features from EXIF data.

    Returns dict with keys: has_a1111_metadata, a1111_hires,
    a1111_regional_prompter, a1111_adetailer.
    """
    is_a1111 = _looks_like_a1111(exif)
    if not is_a1111:
        return {
            "has_a1111_metadata": False,
            "a1111_hires": False,
            "a1111_regional_prompter": False,
            "a1111_adetailer": False,
        }

    text = _get_a1111_text(exif)
    hires = any(kw in text for kw in _A1111_HIRES_KEYWORDS) if text else False
    user_comment_text = str(exif.get("user_comment") or exif.get("UserComment") or "")
    rp = (
        "rp active" in text
        or "regional prompt" in text
        or bool(_A1111_RP_DIRECTIVE_RE.search(user_comment_text))
    ) if text else False
    adetailer = "adetailer" in text if text else False

    return {
        "has_a1111_metadata": True,
        "a1111_hires": bool(hires),
        "a1111_regional_prompter": bool(rp),
        "a1111_adetailer": bool(adetailer),
    }


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
