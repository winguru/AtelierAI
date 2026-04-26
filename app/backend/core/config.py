"""Typed configuration interface for AtelierAI.

Re-exports all settings from ``atelierai.config`` (which dynamically loads
``backend.config``) with explicit type annotations.  Import from this module
inside router/service code for IDE autocompletion and type-checking.
"""

from __future__ import annotations

from typing import Any

import atelierai.config as _cfg


def _get(name: str, default: Any) -> Any:
    return getattr(_cfg, name, default)


IMAGE_LIBRARY_PATH: str = str(_get("IMAGE_LIBRARY_PATH", "image_library"))
IMAGE_RESOURCES_PATH: str = str(_get("IMAGE_RESOURCES_PATH", "image_resources"))
DATABASE_URL: str = str(_get("DATABASE_URL", "sqlite:///image_db.sqlite"))
CURRENT_SCHEMA_VERSION: str = str(_get("CURRENT_SCHEMA_VERSION", "1.0"))
ALLOW_SCHEMA_RESET: bool = bool(_get("ALLOW_SCHEMA_RESET", False))

ATELIER_COMFYUI_BASE_URL: str = str(_get("ATELIER_COMFYUI_BASE_URL", "")).strip()
ATELIER_COMFY_MATCH_THRESHOLD: float = float(_get("ATELIER_COMFY_MATCH_THRESHOLD", 0.95))

CIVITAI_WEB_BASE_URL: str = str(_get("CIVITAI_WEB_BASE_URL", "https://civitai.red"))
CIVITAI_BASE_DOMAIN: str = str(_get("CIVITAI_BASE_DOMAIN", "civitai.red"))
CIVITAI_CDN_BASE_URL: str = str(
    _get("CIVITAI_CDN_BASE_URL", "https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA")
)
CIVITAI_CDN_ALT_BASE_URL: str = str(
    _get("CIVITAI_CDN_ALT_BASE_URL", "https://image-b2.civitai.com")
)
