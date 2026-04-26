"""CivitAI sub-package router assembly.

Assembles the three CivitAI sub-routers (auth, search, import) into a single
combined router that is included in the application with prefix ``/api/civitai``.
"""

from __future__ import annotations

from fastapi import APIRouter

from .auth import router as _auth_router
from .search import router as _search_router
from .api import router as _api_router

router = APIRouter(prefix="/civitai")
router.include_router(_auth_router, prefix="/auth")
router.include_router(_search_router, prefix="/search")
router.include_router(_api_router, prefix="/import")
