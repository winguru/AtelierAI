"""Health, status, and frontend-serving routes.

Includes:
- Static page FileResponse handlers for all frontend SPA pages.
- ``GET /api/config`` — safe read-only frontend configuration.
- ``GET /healthz`` — database liveness probe.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

import atelierai.config as _app_config
from database import get_db

router = APIRouter(tags=["health"])


# ---------------------------------------------------------------------------
# Frontend SPA pages
# ---------------------------------------------------------------------------


@router.get("/")
async def read_index():
    return FileResponse("frontend/index.html")


@router.get("/tree")
async def read_tree_prototype():
    return FileResponse("frontend/tree.html")


@router.get("/generation-lab")
async def read_generation_lab():
    return FileResponse("frontend/generation-lab.html")


@router.get("/model-lab")
async def read_model_lab():
    return FileResponse("frontend/model-lab.html")


@router.get("/folder-lab")
async def read_folder_lab():
    return FileResponse("frontend/folder-lab.html")


@router.get("/perceptual-lab")
async def read_perceptual_lab():
    return FileResponse("frontend/perceptual-lab.html")


@router.get("/expression-lab")
async def read_expression_lab():
    return FileResponse("frontend/expression-lab.html")


@router.get("/comfyui-lab")
async def read_comfyui_lab():
    return FileResponse("frontend/comfyui-lab.html")


# ---------------------------------------------------------------------------
# Frontend configuration — exposes safe, read-only settings to the UI
# ---------------------------------------------------------------------------


@router.get("/api/config")
async def get_frontend_config():
    """Return CivitAI domain configuration for frontend URL construction."""
    return {
        "civitai_web_base_url": getattr(
            _app_config, "CIVITAI_WEB_BASE_URL", "https://civitai.red"
        ),
        "civitai_base_domain": getattr(
            _app_config, "CIVITAI_BASE_DOMAIN", "civitai.red"
        ),
        "civitai_cdn_base_url": getattr(
            _app_config,
            "CIVITAI_CDN_BASE_URL",
            "https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA",
        ),
        "civitai_cdn_alt_base_url": getattr(
            _app_config, "CIVITAI_CDN_ALT_BASE_URL", "https://image-b2.civitai.com"
        ),
    }


# ---------------------------------------------------------------------------
# Liveness probe
# ---------------------------------------------------------------------------


@router.get("/healthz", response_model=dict)
def read_healthz(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Database healthcheck failed: {exc}",
        )

    return {
        "status": "ok",
        "database": "ok",
        "app": "atelierai",
    }
