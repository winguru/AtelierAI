"""CivitAI authentication and session-management routes.

TODO: Extract from main.py (lines ~19983–20107).

Mounted at: /api/civitai/auth
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["civitai-auth"])

# TODO: routes to be extracted from main.py
