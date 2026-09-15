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
  POST /browser-bridge/harvest/install   attach the tRPC capture wrapper
  POST /browser-bridge/harvest/drain     drain + archive captured responses
  POST /browser-bridge/harvest/once      install + drain in one call
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


@router.post("/refresh-session")
async def browser_bridge_refresh_session():
    """Pull the live civitai session cookie from the sidecar browser and
    update the server-side CivitaiAPI singleton + session cache.

    Recovers from server-token expiry without manual DevTools cookie
    copying — the sidecar browser is the durable logged-in session.
    """
    bridge = get_browser_bridge()
    result = await bridge.pull_session_cookie()
    if result.get("updated"):
        # Validate the refreshed token against CivitAI.
        try:
            from atelierai.civitai.civitai_api import CivitaiAPI
            from atelierai.civitai.civitai_auth import _validate_token_with_civitai

            is_valid, _definitive, message = _validate_token_with_civitai(
                CivitaiAPI.get_instance().session_cookie
            )
            result["validated"] = is_valid
            result["validation_message"] = message
        except Exception as exc:  # noqa: BLE001 — validation is best-effort
            result["validated"] = None
            result["validation_message"] = f"{type(exc).__name__}: {exc}"
    return result


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


# ---------------------------------------------------------------------------
# Harvester — passive capture of the page's own tRPC responses
# ---------------------------------------------------------------------------


class HarvestDrainRequest(BaseModel):
    """Options for a harvest drain.

    ``archive`` (default true) writes drained records into the CivitAI
    response archive as ``kind="harvested"``.
    """

    archive: bool = True


class HarvestAutoRequest(BaseModel):
    """Options for the auto-drain loop.

    ``interval_seconds`` clamps to a 5s minimum.
    """

    interval_seconds: float = 30.0


@router.post("/harvest/install")
async def browser_bridge_harvest_install():
    """Install the fetch/XHR capture wrapper on all civitai tabs."""
    from atelierai.civitai.page_harvester import get_page_harvester

    return await get_page_harvester().install()


@router.post("/harvest/watch")
async def browser_bridge_harvest_watch():
    """Arm event-driven wrapping: new tabs and navigations get the wrapper
    immediately instead of waiting for the next drain tick."""
    from atelierai.civitai.page_harvester import get_page_harvester

    return await get_page_harvester().watch()


class HarvestBeaconRequest(BaseModel):
    """In-page drain beacon payload (POSTed by the wrapper JS itself).

    Fires ~2s after API traffic settles on a wrapped page — proactive
    draining without waiting for the 30s poll tick, and while the page is
    still alive (pre-tab-close). ``page_url`` enables image-detail URL
    staging for RSC pages that emit no tRPC.
    """

    page_url: str = ""
    records: list[dict[str, Any]] = []


@router.post("/harvest/beacon")
async def browser_bridge_harvest_beacon(req: HarvestBeaconRequest):
    """Receive proactive capture batches from wrapped pages (CORS-enabled)."""
    from atelierai.civitai.page_harvester import (
        _image_id_from_url,
        get_page_harvester,
    )

    harvester = get_page_harvester()
    harvester._ensure_auto_loop()
    records = req.records or []
    archived, archive_errors = harvester._archive_records(records, archive=True)

    # Image-detail pages: stage from URL (no tRPC records exist for them).
    url_staged = 0
    img_id = _image_id_from_url(req.page_url or "")
    if img_id is not None:
        url_staged = await harvester._stage_browsed_image_ids({img_id})

    # Stage any feed captures included in this batch immediately too.
    stage = await harvester._stage_new_captures()
    return {
        "ok": True,
        "received": len(records),
        "archived": archived,
        "url_staged": url_staged,
        "stage": stage if isinstance(stage, dict) else None,
        "archive_errors": archive_errors,
    }


@router.get("/harvest/status")
async def browser_bridge_harvest_status():
    """Per-page harvest wrapper state + queued capture counts + loop stats."""
    from atelierai.civitai.page_harvester import get_page_harvester

    harvester = get_page_harvester()
    probe = await harvester.probe()
    probe["auto"] = harvester.auto_status()
    return probe


class HarvestSettingsRequest(BaseModel):
    """Harvester convenience settings.

    auto_scrape_posts: navigate a hidden tab to an open image's post page
    so the post's tRPC burst (full metadata for every image in the post)
    is captured and staged.

    auto_close_seconds: close idle /posts/ and /images/ tabs after this
    many seconds (0 disables; search/other pages are never closed).
    """

    auto_scrape_posts: bool | None = None
    auto_close_seconds: float | None = None


@router.post("/harvest/settings")
async def browser_bridge_harvest_settings(req: HarvestSettingsRequest):
    """Update harvester convenience settings (auto-scrape / auto-close)."""
    from atelierai.civitai.page_harvester import get_page_harvester

    harvester = get_page_harvester()
    if req.auto_scrape_posts is not None:
        harvester.set_auto_scrape_posts(req.auto_scrape_posts)
    if req.auto_close_seconds is not None:
        harvester.set_auto_close_seconds(req.auto_close_seconds)
    return {
        "ok": True,
        "auto_scrape_posts": harvester._auto_scrape_posts,
        "auto_close_seconds": harvester._auto_close_seconds,
    }


@router.post("/harvest/drain")
async def browser_bridge_harvest_drain(req: HarvestDrainRequest | None = None):
    """Drain captured tRPC responses and archive them."""
    from atelierai.civitai.page_harvester import get_page_harvester

    archive = req.archive if req is not None else True
    return await get_page_harvester().drain(archive=archive)


@router.post("/harvest/once")
async def browser_bridge_harvest_once():
    """Install (if needed) and drain in a single call."""
    from atelierai.civitai.page_harvester import get_page_harvester

    return await get_page_harvester().harvest_once()


@router.post("/harvest/auto/start")
async def browser_bridge_harvest_auto_start(req: HarvestAutoRequest | None = None):
    """Start the background auto-drain loop (install + drain each tick)."""
    from atelierai.civitai.page_harvester import get_page_harvester

    interval = req.interval_seconds if req is not None else 30.0
    return await get_page_harvester().start_auto(interval_seconds=interval)


@router.post("/harvest/auto/stop")
async def browser_bridge_harvest_auto_stop():
    """Stop the background auto-drain loop."""
    from atelierai.civitai.page_harvester import get_page_harvester

    return get_page_harvester().stop_auto()


@router.get("/harvest/auto/status")
async def browser_bridge_harvest_auto_status():
    """Auto-drain loop stats (running, totals, last result)."""
    from atelierai.civitai.page_harvester import get_page_harvester

    return get_page_harvester().auto_status()


# ---------------------------------------------------------------------------
# Browsed staging — feed harvested captures into the search-lab review tables
# ---------------------------------------------------------------------------


class HarvestStageRequest(BaseModel):
    """Options for browsed staging.

    ``re_enrich`` reprocesses all archived captures so improved field
    mappings can fill gaps on previously staged rows (fill-if-absent;
    nothing populated is clobbered).
    """

    re_enrich: bool = False


@router.post("/stage")
async def browser_bridge_stage_browsed(req: HarvestStageRequest | None = None):
    """Stage harvested feed captures into the search-lab review tables.

    Reads harvested ``image.getInfinite`` records from the response archive,
    upserts ``CivitaiSearchImage`` rows + unrated standalone links, and
    returns counts. Idempotent; zero requests to CivitAI.
    """
    from database import SessionLocal
    from services.browsed_stager import stage_harvested_feeds

    re_enrich = req.re_enrich if req is not None else False
    with SessionLocal() as db:
        return stage_harvested_feeds(db, re_enrich=re_enrich)
