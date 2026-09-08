# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/civitai-integration.md
# ──────────────────────────────────────────────────────────────────────────────
"""Browser bridge routes: control and observe the CivitAI CDP egress lane.

Routes (all async, all fail-open):
  GET  /browser-bridge/status      connectivity + page inventory
  POST /browser-bridge/connect     force a (re)connect attempt
  POST /browser-bridge/disconnect  drop the CDP connection
  POST /browser-bridge/navigate    drive the sidecar tab to a URL
  POST /browser-bridge/fetch       page-context fetch through the browser
  GET  /browser-bridge/config      effective lane configuration
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from atelierai.civitai.browser_bridge import get_browser_bridge

router = APIRouter(prefix="/browser-bridge", tags=["browser-bridge"])


class NavigateRequest(BaseModel):
    url: str


class FetchRequest(BaseModel):
    url: str
    method: str = "GET"
    payload: dict[str, Any] | None = None
    headers: dict[str, str] | None = None


@router.get("/status")
async def browser_bridge_status():
    """Connectivity + activity snapshot of the bridge singleton."""
    bridge = get_browser_bridge()
    return await bridge.status()


@router.post("/connect")
async def browser_bridge_connect():
    """Force a connection attempt (also the implicit path for every call)."""
    bridge = get_browser_bridge()
    ok = await bridge._ensure_connected()
    return {"ok": ok, "status": await bridge.status()}


@router.post("/disconnect")
async def browser_bridge_disconnect():
    """Drop the CDP connection. The sidecar browser keeps running."""
    bridge = get_browser_bridge()
    await bridge.disconnect()
    return {"ok": True}


@router.post("/navigate")
async def browser_bridge_navigate(req: NavigateRequest):
    """Drive the civitai tab to a URL (visible via noVNC)."""
    bridge = get_browser_bridge()
    return await bridge.navigate(req.url)


@router.post("/fetch")
async def browser_bridge_fetch(req: FetchRequest):
    """Fetch a URL from inside the civitai page context."""
    browser = get_browser_bridge()
    return await browser.fetch(
        req.url,
        method=req.method,
        payload=req.payload,
        headers=req.headers,
    )


@router.get("/config")
async def browser_bridge_config():
    """Effective bridge configuration (safe, non-secret)."""
    import atelierai.config as app_config

    return {
        "cdp_url": getattr(app_config, "BROWSER_BRIDGE_CDP_URL", ""),
        "default_cdp_url": getattr(
            app_config, "BROWSER_BRIDGE_DEFAULT_CDP_URL", "http://chrome-sidecar:9222"
        ),
        "fetch_timeout": getattr(app_config, "BROWSER_BRIDGE_FETCH_TIMEOUT", 45.0),
        "navigate_timeout": getattr(
            app_config, "BROWSER_BRIDGE_NAVIGATE_TIMEOUT", 30.0
        ),
        "capture_enabled": bool(
            getattr(app_config, "BROWSER_BRIDGE_CAPTURE_PATH", "")
        ),
    }
