"""CivitAI sub-package router assembly.

Combines the four CivitAI sub-routers (auth, search, api, models) into a single
router that is included in the application without an extra prefix (each
sub-router carries its own correct prefix).
"""

from __future__ import annotations

from fastapi import APIRouter

from .auth import router as _auth_router
from .search import router as _search_router
from .api import router as _api_router
from .models import router as _models_router

# No shared prefix here — each sub-router already declares its own:
#   auth.py   → prefix="/civitai/auth"
#   search.py → prefix="/civitai-search"
#   api.py    → prefix="/civitai"  (backfill/sync routes)
#   models.py → prefix="/civitai/models"  (model catalog routes)
router = APIRouter(tags=["civitai"])
router.include_router(_auth_router)
router.include_router(_search_router)
router.include_router(_api_router)
router.include_router(_models_router)
