# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/civitai-integration.md
# ──────────────────────────────────────────────────────────────────────────────
"""CivitAI authentication and session-management routes.

Extracted from main.py (lines ~19983–20134).

Routes:
  GET  /civitai/auth/status
  POST /civitai/auth/cookie
  POST /civitai/auth/refresh
  GET  /civitai/auth/rate-limit-status
"""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException

from schemas import CivitaiCookieRequest

router = APIRouter(prefix="/civitai/auth", tags=["civitai-auth"])


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _get_civitai_session_cache_path() -> str:
    """Return the session cache file path from config, with a sensible default."""
    try:
        from atelierai.config import CIVITAI_SESSION_CACHE

        return CIVITAI_SESSION_CACHE
    except ImportError:
        pass
    env_val = os.getenv("CIVITAI_SESSION_CACHE", "")
    return env_val or ".civitai_session"


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/status", response_model=dict)
def civitai_auth_status():
    """Check whether the current CivitAI session cookie is valid.

    Returns ``{ authenticated: bool, message: str }``.  Probes
    ``collection.getAllUser`` (an authenticated endpoint) to verify the token.
    """
    try:
        from atelierai.civitai.civitai_auth import _validate_token_with_civitai
    except ImportError:
        return {"authenticated": False, "message": "CivitAI auth module not available."}

    from atelierai.civitai.civitai_api import CivitaiAPI

    api = CivitaiAPI.get_instance()
    cookie = getattr(api, "session_cookie", None)
    if not cookie or len(cookie) < 100:
        return {"authenticated": False, "message": "No session cookie is configured."}

    is_valid, _definitive, message = _validate_token_with_civitai(cookie)
    return {"authenticated": is_valid, "message": message}


@router.post("/cookie", response_model=dict)
def civitai_auth_save_cookie(payload: CivitaiCookieRequest):
    """Accept a manually-pasted CivitAI session cookie.

    The caller supplies just the ``__Secure-civitai-token`` value (the long
    JWT-like string starting with ``eyJ``).  The endpoint validates it against
    CivitAI before persisting.
    """
    try:
        from atelierai.civitai.civitai_auth import (
            _normalize_token,
            _validate_token_with_civitai,
        )
    except ImportError:
        raise HTTPException(
            status_code=500, detail="CivitAI auth module not available."
        )

    token = _normalize_token(payload.cookie)
    if not token:
        raise HTTPException(
            status_code=400,
            detail="The provided value does not look like a valid CivitAI session token.",
        )

    is_valid, is_definitive, message = _validate_token_with_civitai(token)
    if not is_valid and is_definitive:
        raise HTTPException(
            status_code=401,
            detail=f"Token rejected by CivitAI: {message}",
        )

    # Update the running singleton so subsequent requests use the new cookie.
    try:
        from atelierai.civitai.civitai_api import CivitaiAPI

        api = CivitaiAPI.get_instance()
        api.update_session_cookie(token)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update session cookie: {exc}",
        )

    status_msg = (
        "CivitAI session cookie saved and validated."
        if is_valid
        else (
            f"Cookie saved (validation inconclusive: {message}). It will be used for future requests."
        )
    )
    return {"success": True, "message": status_msg, "validated": is_valid}


@router.post("/refresh", response_model=dict)
def civitai_auth_refresh():
    """Trigger a Playwright-based re-authentication with CivitAI.

    This runs synchronously and may take 30+ seconds.  The browser window
    will open on the server host for the user to complete OAuth login.
    """
    try:
        from atelierai.civitai.civitai_auth import get_cached_or_refresh_session_token
    except ImportError:
        raise HTTPException(
            status_code=500, detail="CivitAI auth module not available."
        )

    cache_file = _get_civitai_session_cache_path()

    try:
        token = get_cached_or_refresh_session_token(
            cache_file=cache_file,
            headless=False,  # OAuth requires visible browser
            force_reauth=True,
            non_interactive=True,
        )
    except Exception as exc:
        return {"success": False, "error": str(exc)}

    # Update the running singleton.
    try:
        from atelierai.civitai.civitai_api import CivitaiAPI

        api = CivitaiAPI.get_instance()
        api.update_session_cookie(token)
    except Exception as exc:
        return {
            "success": False,
            "error": f"Token obtained but failed to update singleton: {exc}",
        }

    return {"success": True, "message": "CivitAI session refreshed successfully."}


@router.get("/rate-limit-status", response_model=dict)
def civitai_rate_limit_status():
    """Return current CivitAI API request metrics and rate-limit state.

    Shows sliding-window RPM, 429 counts, global backoff status, FIFO queue
    depth, and per-type / per-FQDN / per-endpoint request breakdowns.
    """
    try:
        from atelierai.civitai.http_client import CivitaiHttpClient
    except ImportError:
        return {"available": False, "message": "CivitAI HTTP client module not available."}

    metrics = CivitaiHttpClient.get_request_metrics()
    return {
        "available": True,
        **metrics,
    }
