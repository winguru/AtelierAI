# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/civitai-integration.md
# ──────────────────────────────────────────────────────────────────────────────
"""Passive tRPC harvester for the CDP browser bridge.

Wraps ``window.fetch`` (and XMLHttpRequest) inside a civitai tab so every tRPC
response the *page itself* loads is captured — scrolling the feed archives it,
with zero additional requests to CivitAI.

Design notes:

- The wrapper is installed via a Playwright ``add_init_script`` on pages the
  bridge opens. ``add_init_script`` runs before page scripts on every
  subsequent navigation, so the hook survives route changes (SPA-style).
- Captured records queue in a page-global array; the Python side drains them
  periodically. Bodies are capped (default 256 KB) so a huge response can't
  balloon the evaluate payload.
- Captures flow into the existing ``CivitaiResponseArchive`` (same sharded
  layout, same redaction), tagged ``kind="harvested"`` — analysis tooling
  written against the archive reads browser captures for free.
- Fail-open everywhere: harvesting must never break browsing or the bridge.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import timedelta
from typing import Any

from atelierai.civitai.browser_bridge import CivitaiBrowserBridge

# Metadata backfill freshness: browsed-image tiles only need uuid/blurhash/
# artist basics, which never change for a given image id — a week-old cached
# image.get is fine and avoids redundant tRPC budget burn.
_META_MAX_AGE = timedelta(days=7)

# JS injected before page scripts. Keep the record shape in sync with
# _drain_js below. The queue lives on window; navigation replaces the document
# but the wrapper re-installs itself via add_init_script and the queue is
# re-created — so drain BEFORE navigation, or accept losing records queued in
# the final moments before a route change.
_INSTALL_JS = """
(() => {
    if (window.__atelierai_harvest) return;  // idempotent
    const queue = [];
    window.__atelierai_harvest = {
        queue,
        wrapFetch: true,
        // API health for the janitor: 2xx-4xx (excl 429) = valid data;
        // 5xx/429/network-fail = temporary trouble → retry + suspend
        // auto-close until the page has at least one OK response.
        apiOkAt: 0,
        apiErrAt: 0,
        apiLastStatus: null,
    };

    const MAX_BODY = 262144;  // match Python-side cap; JS-side guard only
    const cap = (s) => (typeof s === 'string' && s.length > MAX_BODY)
        ? null : s;

    // ── Post-traffic drain beacon ─────────────────────────────
    // After API traffic settles (DRAIN_DEBOUNCE_MS of quiet), POST the
    // queued captures straight to the backend — no 30s tick wait, and the
    // send happens while the page is still alive (before tab close).
    // Failures are silent: the poll drain remains the safety net.
    const DRAIN_DEBOUNCE_MS = 2000;
    const BEACON_URL = '__BEACON_ORIGIN__/api/browser-bridge/harvest/beacon';
    let drainTimer = null;
    const notifyBackend = () => {
        const batch = window.__atelierai_harvest.queue.splice(0, window.__atelierai_harvest.queue.length);
        if (!batch.length) return;
        fetch(BEACON_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ page_url: location.href, records: batch }),
            keepalive: true,  // survives tab close in-flight
        }).catch(() => {
            // Beacon failed (backend restarting etc.) — requeue for the poll drain.
            window.__atelierai_harvest.queue.push(...batch);
        });
    };
    const scheduleDrain = () => {
        if (drainTimer) clearTimeout(drainTimer);
        drainTimer = setTimeout(notifyBackend, DRAIN_DEBOUNCE_MS);
    };
    window.__atelierai_harvest.scheduleDrain = scheduleDrain;

    // Only capture the main API hosts — civitai.red / civitai.com — NOT
    // subdomains like advertising.civitai.com (ad beacons) or
    // image.civitai.com (CDN; binaries, not tRPC).
    const isApiCall = (u) => {
        try {
            const host = new URL(u, location.origin).hostname;
            if (host !== 'civitai.red' && host !== 'civitai.com') return false;
            return u.includes('/api/trpc/') || u.includes('/api/v1/');
        } catch (e) { return false; }
    };

    const origFetch = window.fetch;
    window.fetch = async function(...args) {
        const started = performance.now();
        let resp, err = null;
        try {
            resp = await origFetch.apply(this, args);
        } catch (e) {
            err = String(e);
            throw e;
        } finally {
            const url = typeof args[0] === 'string' ? args[0]
                : (args[0] && args[0].url) || '';
            const init = args[1] || {};
            if (isApiCall(url)) {
                const status = resp ? resp.status : 0;
                const health = window.__atelierai_harvest;
                health.apiLastStatus = status;
                // Retryable: 409 conflict, 429 rate-limit, 5xx, and
                // network-level failure (status 0). Other 4xx (404 etc.)
                // are permanent — count as a valid response so the tab
                // isn't held open retrying a dead resource forever.
                const retryable = status === 0 || status === 409
                    || status === 429 || status >= 500;
                if (retryable) {
                    health.apiErrAt = Date.now();
                } else {
                    health.apiOkAt = Date.now();
                }
                let bodyText = null, bodyParsed = null;
                if (resp) {
                    try {
                        const text = await resp.clone().text();
                        bodyText = cap(text);
                        try { bodyParsed = JSON.parse(text); } catch (e) {}
                    } catch (e) {}
                }
                queue.push({
                    ts: Date.now() / 1000,
                    url,
                    method: init.method || 'GET',
                    status: resp ? resp.status : 0,
                    bodyText,
                    bodyParsed,
                    error: err,
                    elapsedMs: Math.round(performance.now() - started),
                    source: 'fetch',
                });
                scheduleDrain();
            }
        }
        return resp;
    };

    // XHR wrapper for endpoints the site calls via XHR (some legacy paths).
    const OrigXHROpen = XMLHttpRequest.prototype.open;
    const OrigXHRSend = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function(method, url, ...rest) {
        this.__atelierai_req = { method: method || 'GET', url: String(url) };
        return OrigXHROpen.apply(this, [method, url, ...rest]);
    };
    XMLHttpRequest.prototype.send = function(...args) {
        const meta = this.__atelierai_req;
        const started = performance.now();
        this.addEventListener('loadend', () => {
            if (!meta) return;
            if (!isApiCall(String(meta.url))) return;
            try {
                const text = this.responseText;
                queue.push({
                    ts: Date.now() / 1000,
                    url: meta.url,
                    method: meta.method,
                    status: this.status,
                    bodyText: cap(text),
                    bodyParsed: (() => { try { return JSON.parse(text); } catch (e) { return null; }})(),
                    error: null,
                    elapsedMs: Math.round(performance.now() - started),
                    source: 'xhr',
                });
                scheduleDrain();
            } catch (e) {}
        });
        return OrigXHRSend.apply(this, args);
    };
})();
"""

# Drains the page queue. Returns the records and clears the queue.
_DRAIN_JS = """
(() => {
    const h = window.__atelierai_harvest;
    if (!h) return [];
    const out = h.queue.splice(0, h.queue.length);
    return out;
})();
"""


def _beacon_origin() -> str:
    """Origin for the in-page drain beacon (config-overridable)."""
    try:
        import atelierai.config as app_config

        origin = getattr(app_config, "BROWSER_BRIDGE_BEACON_ORIGIN", "")
        return origin.rstrip("/") if origin else "http://localhost:8000"
    except Exception:  # noqa: BLE001
        return "http://localhost:8000"


def _install_js() -> str:
    """Wrapper JS with the beacon origin templated in."""
    return _INSTALL_JS.replace("__BEACON_ORIGIN__", _beacon_origin())


def _image_id_from_url(url: str) -> int | None:
    """Extract the image id from a civitai /images/{id} tab URL."""
    import re

    m = re.search(r"civitai\.[a-z]+/images/(\d+)", url)
    return int(m.group(1)) if m else None


def _endpoint_from_url(url: str) -> str:
    """Extract a stable endpoint slug from a civitai API URL.

    ``https://civitai.red/api/trpc/image.getInfinite?batch=1...`` →
    ``image.getInfinite``; ``/api/v1/images`` → ``v1.images``.
    """
    from urllib.parse import urlparse

    try:
        path = urlparse(url).path
    except Exception:  # noqa: BLE001 — defensive
        return "unknown"
    if path.startswith("/api/trpc/"):
        return path.removeprefix("/api/trpc/").split("?")[0] or "trpc"
    if path.startswith("/api/v1/"):
        return "v1." + path.removeprefix("/api/v1/").split("?")[0]
    return path.strip("/").replace("/", ".") or "unknown"


def _parse_superjson_envelope(body: Any) -> tuple[str | None, Any]:
    """Return (input_repr, parsed_input) for superjson ``input=`` params.

    CivitAI tRPC GET calls pass input in the query string as superjson.
    Extracting it lets archive records use the same request-hash keying the
    direct-lane archive uses, so browser captures and direct-lane captures
    land in the SAME shard files when the call matches.
    """
    if not isinstance(body, str):
        return None, None
    from urllib.parse import parse_qs, urlparse

    try:
        qs = parse_qs(urlparse(body).query)
        raw = qs.get("input", [None])[0]
        if not raw:
            return None, None
        parsed = json.loads(raw)
        return raw[:512], parsed
    except Exception:  # noqa: BLE001 — best-effort keying
        return None, None


class CivitaiPageHarvester:
    """Installs the capture wrapper and drains captures into the archive."""

    def __init__(self, bridge: CivitaiBrowserBridge | None = None):
        self._bridge = bridge
        self._installed_pages: set[int] = set()
        # Pages whose add_init_script is registered (CDP addScriptToEvaluate-
        # OnNewDocument): the wrapper runs before page scripts on every
        # future navigation — no race with the app's own fetches.
        self._init_scripted_pages: set[int] = set()
        self._archive = None
        # Event-driven wrapping: watchers registered on live contexts so new
        # tabs and navigations get the wrapper immediately (zero blind window)
        # instead of waiting for the next auto-drain tick.
        self._watched_contexts: set[int] = set()
        self._watched_pages: set[int] = set()
        self._watch_errors: list[str] = []
        # Auto-drain loop state
        self._auto_task: asyncio.Task | None = None
        self._auto_interval: float = 30.0
        self._auto_stop = asyncio.Event()
        # Auto-scrape related posts: when an /images/{id} tab is seen, also
        # navigate a hidden tab to the image's POST page so the post's tRPC
        # burst (image.getInfinite?postId=) is captured and staged with full
        # metadata for every image in that post.
        self._auto_scrape_posts: bool = False
        self._scraped_post_ids: set[int] = set()
        # Auto-close idle post/image tabs: free sidecar resources when the
        # user ctrl-clicks many tabs. Only /posts/ and /images/ URLs.
        self._auto_close_seconds: float = 0.0  # 0 = disabled
        self._page_last_active: dict[int, float] = {}  # id(page) → monotonic
        # API-error retry state: attempt count per page (backoff exponent)
        # and monotonic time of next permitted reload. Cleared on recovery.
        self._retry_backoff: dict[int, int] = {}
        self._retry_next: dict[int, float] = {}
        # Failed-backfill attempts per image id (dead upstream assets). After
        # _BACKFILL_MAX_ATTEMPTS the id is quarantined — a permanent 404 must
        # not consume a self-heal slot (or spam the console) forever.
        self._backfill_fails: dict[int, int] = {}
        self._BACKFILL_MAX_ATTEMPTS = 2
        # Max tab recoveries per duty run (probe poll or loop tick) — a
        # fleet of erroring tabs must not fire one synchronized tRPC burst.
        self._RETRY_MAX_PER_CYCLE = 3
        self._auto_stats: dict[str, Any] = {
            "running": False,
            "interval_seconds": 30.0,
            "ticks": 0,
            "total_drained": 0,
            "total_archived": 0,
            "total_staged_new": 0,
            "last_tick_at": None,
            "last_result": None,
            "last_stage": None,
            "last_error": None,
        }

    def _get_bridge(self) -> CivitaiBrowserBridge:
        if self._bridge is None:
            from atelierai.civitai.browser_bridge import get_browser_bridge

            self._bridge = get_browser_bridge()
        return self._bridge

    def _get_archive(self):
        if self._archive is None:
            from atelierai.civitai.response_archive import CivitaiResponseArchive

            self._archive = CivitaiResponseArchive()
        return self._archive

    # JS probe: wrapper state, queued count, and API health timestamps.
    _PROBE_JS = """
    () => {
        const h = window.__atelierai_harvest;
        return {
            wrapped: !!h,
            queued: h && Array.isArray(h.queue) ? h.queue.length : 0,
            apiOkAt: h ? h.apiOkAt : 0,
            apiErrAt: h ? h.apiErrAt : 0,
            apiLastStatus: h ? h.apiLastStatus : null,
        };
    }
    """

    @staticmethod
    def _page_health(result: dict[str, Any]) -> str:
        """Classify a page's API health from probe data.

        pending - no API traffic observed yet (too new to judge)
        error   - temporary error seen and no OK since (5xx/429/network)
        ok      - valid data received at some point
        """
        ok_at = int(result.get("apiOkAt") or 0)
        err_at = int(result.get("apiErrAt") or 0)
        if ok_at == 0 and err_at == 0:
            return "pending"
        return "ok" if ok_at >= err_at else "error"

    async def _arm_page_init_script(self, page: Any) -> bool:
        """Register the wrapper as a new-document init script on one page.

        ``page.add_init_script`` maps to CDP ``Page.addScriptToEvaluateOnNewDocument``:
        the installer runs BEFORE page scripts on every subsequent navigation,
        so the app's first fetch is already wrapped — unlike evaluate-after-load,
        which races the page's initial tRPC burst. Idempotent per page object;
        the installer itself is also guarded (``window.__atelierai_harvest``).
        """
        if id(page) in self._init_scripted_pages:
            return True
        try:
            await page.add_init_script(_install_js())
            self._init_scripted_pages.add(id(page))
            return True
        except Exception:  # noqa: BLE001 — page may be closing
            return False

    async def _wrap_page_now(self, page: Any) -> bool:
        """Install the wrapper on one page immediately (fail-open)."""
        try:
            await page.evaluate(_install_js())
            self._installed_pages.add(id(page))
            return True
        except Exception:  # noqa: BLE001 — page may not be ready yet
            return False

    def _watch_context(self, ctx: Any) -> None:
        """Register event watchers on a browser context (idempotent).

        - ``page`` event → wrap the new tab as soon as it commits to a
          civitai document, covering middle-click/new-tab opens.
        - Per-page ``framenavigated`` → re-wrap after full page loads (reload,
          URL-bar navigation) which create a fresh JS context. SPA pushState
          navigations keep the context and the wrapper — no action needed.
        """
        if id(ctx) in self._watched_contexts:
            return
        self._watched_contexts.add(id(ctx))

        def _on_page(page: Any) -> None:
            # New tab (middle-click, target=_blank, window.open): wrap it as
            # soon as it commits. The evaluate will fail harmlessly until the
            # first document exists, so retry briefly.
            self._watch_page(page)
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            loop.create_task(self._wrap_new_page_with_retry(page))

        try:
            ctx.on("page", _on_page)
            for page in ctx.pages:
                self._watch_page(page)
        except Exception as exc:  # noqa: BLE001 — watcher is best-effort
            self._watch_errors.append(f"ctx: {type(exc).__name__}")

    async def _wrap_new_page_with_retry(self, page: Any, attempts: int = 10) -> None:
        """Wrap a freshly-opened tab: init-script first, then evaluate.

        The init script eliminates the race for every navigation this page
        makes from here on; the retry-evaluate below additionally covers the
        document that is loading right now (the init script may have missed
        the initial navigation if the page object surfaced after commit).
        """
        await self._arm_page_init_script(page)
        for _ in range(attempts):
            url = (page.url or "").lower()
            if url and "civitai" in url:
                if await self._wrap_page_now(page):
                    return
            else:
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=4000)
                except Exception:  # noqa: BLE001, S110 — retry loop handles it
                    pass
            await asyncio.sleep(0.5)
        # Final attempt regardless of URL state (best-effort).

    def _watch_page(self, page: Any) -> None:
        """Watch one page for navigations that reset its JS context."""
        if id(page) in self._watched_pages:
            return
        self._watched_pages.add(id(page))

        def _on_navigated(frame: Any) -> None:
            # Only react to main-frame navigation events synchronously; the
            # async work is scheduled so the handler never blocks CDP.
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            loop.create_task(self._on_page_navigated(page))

        try:
            page.on("framenavigated", _on_navigated)
        except Exception as exc:  # noqa: BLE001 — best-effort
            self._watch_errors.append(f"page: {type(exc).__name__}")

    async def _on_page_navigated(self, page: Any) -> None:
        """After a navigation, ensure init-script + wrapper are in place."""
        try:
            url = (page.url or "").lower()
            if "civitai" not in url:
                return
            # Belt and suspenders: the init script covers future navigations;
            # the live probe covers the current document if it wasn't armed.
            await self._arm_page_init_script(page)
            try:
                alive = await page.evaluate("() => !!window.__atelierai_harvest")
                if alive:
                    return
            except Exception:  # noqa: BLE001, S110 — evaluate fail = fresh document
                pass
            await self._wrap_page_now(page)
        except Exception:  # noqa: BLE001, S110 — watcher must never raise
            pass

    async def watch(self) -> dict[str, Any]:
        """Install event watchers on all live contexts + pages.

        Call once after the bridge connects (and again after sidecar
        restarts — contexts are new objects). New tabs/navigations then get
        wrapped during their first document load, before user browsing
        produces tRPC traffic.
        """
        bridge = self._get_bridge()
        ok = await bridge._ensure_connected()
        if not ok:
            return {"ok": False, "bridge": "unavailable", "error": bridge._state.last_error}
        if bridge._browser is None:
            return {"ok": False, "bridge": "unavailable", "error": "no browser"}

        watched_ctx = 0
        try:
            for ctx in bridge._browser.contexts:
                self._watch_context(ctx)
                watched_ctx += 1
        except Exception as exc:  # noqa: BLE001 — fail-open
            return {"ok": False, "bridge": "watch-failed", "error": f"{type(exc).__name__}: {exc}"}
        return {
            "ok": True,
            "watched_contexts": watched_ctx,
            "watched_pages": len(self._watched_pages),
            "errors": self._watch_errors[-5:],
        }

    async def probe(self) -> dict[str, Any]:
        """Report harvest-wrapper state + queued counts per civitai page.

        Also re-arms the auto-drain loop: the Bridge Lab polls this every
        10s, so any open dashboard self-heals the loop after uvicorn
        --reload restarts (which drop it).
        """
        self._ensure_auto_loop()
        bridge = self._get_bridge()
        ok = await bridge._ensure_connected()
        if not ok:
            return {"ok": False, "bridge": "unavailable", "error": bridge._state.last_error}

        pages: list[dict[str, Any]] = []
        try:
            for ctx in bridge._browser.contexts:
                for page in ctx.pages:
                    url = page.url or ""
                    if "civitai" not in url:
                        continue
                    try:
                        result = await page.evaluate(self._PROBE_JS)
                        queued = int(result.get("queued") or 0)
                        health = self._page_health(result)
                        pages.append({
                            "url": url.split("?")[0],
                            "wrapped": bool(result.get("wrapped")),
                            "queued": queued,
                            "health": health,
                            "api_status": result.get("apiLastStatus"),
                        })
                        # Activity = pending captures (real work), NOT mere
                        # visibility — stamping every poll made the idle-close
                        # window never elapse. New pages get an initial stamp
                        # so they aren't closed before their first traffic.
                        self._page_last_active.setdefault(id(page), time.monotonic())
                        if queued > 0:
                            self._page_last_active[id(page)] = time.monotonic()
                    except Exception as exc:  # noqa: BLE001 — per-page
                        pages.append({
                            "url": url.split("?")[0],
                            "wrapped": False,
                            "queued": 0,
                            "health": "unknown",
                            "api_status": None,
                            "error": f"{type(exc).__name__}",
                        })
        except Exception as exc:  # noqa: BLE001 — fail-open
            return {"ok": False, "bridge": "probe-failed", "error": f"{type(exc).__name__}: {exc}"}

        # Janitor duties ride the probe cycle AND the auto-drain tick.
        await self._run_janitor_duties(bridge)
        return {
            "ok": True,
            "pages": pages,
            "all_wrapped": bool(pages) and all(p.get("wrapped") for p in pages),
            "auto_scrape_posts": self._auto_scrape_posts,
            "auto_close_seconds": self._auto_close_seconds,
        }

    def set_auto_scrape_posts(self, enabled: bool) -> None:
        """Enable/disable auto-navigation to related post pages."""
        self._auto_scrape_posts = bool(enabled)

    def set_auto_close_seconds(self, seconds: float) -> None:
        """Set idle-tab close timeout (0 disables; clamped to >=5s)."""
        self._auto_close_seconds = max(0.0, float(seconds))

    async def _run_janitor_duties(self, bridge: Any) -> None:
        """Scrape + retry + idle-close duties (idempotent).

        Rides BOTH the probe cycle (dashboard poll) and the auto-drain
        tick, so error recovery never depends on the Bridge Lab being
        open (a uvicorn --reload or closed dashboard must not stall it).
        """
        await self._maybe_scrape_related_posts()
        await self._retry_error_pages(bridge)
        await self._maybe_close_idle_pages(bridge)

    async def _maybe_scrape_related_posts(self) -> None:
        """Navigate open image-detail tabs to their owning post page (once).

        In-place navigation instead of scratch tabs: the existing tab is
        reused, its init-script wrapper captures the post's tRPC burst
        (image.getInfinite?postId=X — full metadata for every image in the
        post), the page beacon delivers it, and the idle-close janitor
        reclaims the tab afterward. Navigating counts as activity, so the
        auto-close window restarts naturally.
        """
        if not self._auto_scrape_posts:
            return
        bridge = self._get_bridge()
        if bridge._browser is None:
            return
        try:
            # Work from bridge page objects (not the probe URL list) so we
            # can navigate the actual tabs.
            img_pages = []
            for ctx in bridge._browser.contexts:
                for page in ctx.pages:
                    url = page.url or ""
                    if "/images/" in url and "civitai" in url:
                        img_pages.append((page, _image_id_from_url(url)))
            ids_to_pages, post_ids = self._lookup_post_pages(img_pages)
            base = bridge._web_base_url()
            for post_id in sorted(post_ids):
                if post_id in self._scraped_post_ids:
                    continue
                page = ids_to_pages.get(post_id)
                if page is None:
                    continue
                self._scraped_post_ids.add(post_id)
                try:
                    await page.goto(
                        f"{base}/posts/{post_id}",
                        timeout=bridge._nav_timeout_ms(),
                        wait_until="domcontentloaded",
                    )
                    # Navigation = activity: restart the idle-close window so
                    # the janitor doesn't reclaim the tab before the post's
                    # tRPC burst is captured and beaconed.
                    self._page_last_active[id(page)] = time.monotonic()
                    self._auto_stats["posts_scraped"] = (
                        self._auto_stats.get("posts_scraped", 0) + 1
                    )
                except Exception:  # noqa: BLE001 — per-post
                    self._scraped_post_ids.discard(post_id)
        except Exception:  # noqa: BLE001 — fail-open
            return

    def _lookup_post_pages(
        self, img_pages: list[tuple[Any, int | None]]
    ) -> tuple[dict[int, Any], set[int]]:
        """Map open image tabs to owning post ids via the search-images DB.

        Returns (post_id → page, post_ids). Rows without a post_id (or
        image ids not yet staged) are skipped.
        """
        ids_to_pages: dict[int, Any] = {}
        post_ids: set[int] = set()
        from database import SessionLocal
        from sqlalchemy import text as _sa_text

        with SessionLocal() as db:
            for page, iid in img_pages:
                if iid is None:
                    continue
                row = db.execute(
                    _sa_text(
                        "SELECT post_id FROM civitai_search_images "
                        "WHERE civitai_image_id = :iid"
                    ),
                    {"iid": iid},
                ).fetchone()
                if row and row[0]:
                    pid = int(row[0])
                    ids_to_pages[pid] = page
                    post_ids.add(pid)
        return ids_to_pages, post_ids

    async def _maybe_close_idle_pages(self, bridge: Any) -> None:
        """Close /posts/ and /images/ tabs idle beyond the configured window.

        Frees sidecar memory when the user ctrl-clicks many tabs. Search and
        other page types are never closed. Tabs with pending captures or
        degraded API health are suspended (never closed) — the retry duty
        owns them until valid data arrives.
        """
        if self._auto_close_seconds <= 0 or bridge._browser is None:
            return
        now = time.monotonic()
        closed = 0
        try:
            for ctx in bridge._browser.contexts:
                for page in list(ctx.pages):
                    if await self._should_close_page(page, now):
                        try:
                            await page.close()
                            closed += 1
                        except Exception:  # noqa: BLE001, S110 — best-effort
                            pass
        except Exception:  # noqa: BLE001, S110 — fail-open
            pass
        if closed:
            self._auto_stats["tabs_auto_closed"] = (
                self._auto_stats.get("tabs_auto_closed", 0) + closed
            )

    async def _should_close_page(self, page: Any, now: float) -> bool:
        """True when a tab is a closeable URL, idle, healthy, and drained."""
        last = self._page_last_active.get(id(page))
        if last is None:
            return False
        url = (page.url or "").lower()
        if "/posts/" not in url and "/images/" not in url:
            return False
        # Never close while captures are pending (beacon debounce is 2s;
        # the window >> that in practice), and never close while the page's
        # API health is degraded — the reload/refresh strategy owns those
        # tabs until data is valid.
        try:
            probe = await page.evaluate(self._PROBE_JS)
            if int(probe.get("queued") or 0) > 0:
                self._page_last_active[id(page)] = now
                return False
            if self._page_health(probe) == "error":
                self._page_last_active[id(page)] = now
                return False
        except Exception:  # noqa: BLE001, S110 — page gone/unready
            pass
        return now - last >= self._auto_close_seconds

    async def _retry_error_pages(self, bridge: Any) -> int:
        """Recover tabs whose last API outcome was a temporary error.

        /posts/ tabs reload in place; /images/ tabs navigate to their
        owning post page (recovery + scraping in one step). Exponential
        backoff per page: 2^attempt × 5s, capped at 60s. A successful
        response flips health to ok (wrapper apiOkAt), clears the backoff,
        and normal auto-close resumes. Capped at _RETRY_MAX_PER_CYCLE
        recoveries per run so a fleet of erroring tabs doesn't fire one
        synchronized tRPC burst. Fail-open: never breaks the duty cycle.
        """
        if bridge._browser is None:
            return 0
        retried = 0
        try:
            for ctx in bridge._browser.contexts:
                if retried >= self._RETRY_MAX_PER_CYCLE:
                    break
                for page in list(ctx.pages):
                    if retried >= self._RETRY_MAX_PER_CYCLE:
                        break
                    retried += await self._retry_one_page(page)
        except Exception:  # noqa: BLE001, S110 — fail-open
            pass
        if retried:
            self._auto_stats["pages_retried"] = (
                self._auto_stats.get("pages_retried", 0) + retried
            )
        return retried

    async def _retry_one_page(self, page: Any) -> int:
        """Retry decision for one tab. Returns 1 when a reload was issued."""
        url = (page.url or "").lower()
        if "civitai" not in url or (
            "/posts/" not in url and "/images/" not in url
        ):
            return 0
        try:
            result = await page.evaluate(self._PROBE_JS)
        except Exception:  # noqa: BLE001 — page gone/unready
            return 0
        if self._page_health(result) != "error":
            self._retry_backoff.pop(id(page), None)
            self._retry_next.pop(id(page), None)
            return 0
        # Erroring tab: suspend auto-close while unhealthy.
        self._page_last_active[id(page)] = time.monotonic()
        now = time.monotonic()
        attempt = self._retry_backoff.get(id(page), 0)
        if now < self._retry_next.get(id(page), 0.0):
            return 0
        if "/images/" in url:
            # Image tabs recover by navigating to their owning post page:
            # the post's tRPC burst re-fires through the wrapper — recovery
            # AND scraping in one step.
            return await self._recover_image_page(page, now, attempt)
        self._retry_backoff[id(page)] = attempt + 1
        self._retry_next[id(page)] = now + min(2**attempt * 5, 60)
        try:
            await page.reload(wait_until="domcontentloaded")
            return 1
        except Exception:  # noqa: BLE001 — best-effort reload
            return 0

    async def _recover_image_page(
        self, page: Any, now: float, attempt: int
    ) -> int:
        """Navigate an erroring image tab to its owning post page.

        The post page's tRPC burst (image.getInfinite?postId=X) re-fires
        through the wrapper — recovery and scraping in one step. Falls
        back to a plain reload when the DB has no post_id for the image
        yet. Shares the per-page backoff schedule with /posts/ retries.
        """
        image_id = _image_id_from_url(page.url or "")
        post_id = self._post_id_for_image(image_id) if image_id is not None else None
        self._retry_backoff[id(page)] = attempt + 1
        self._retry_next[id(page)] = now + min(2**attempt * 5, 60)
        try:
            if post_id is not None:
                bridge = self._get_bridge()
                await page.goto(
                    f"{bridge._web_base_url()}/posts/{post_id}",
                    timeout=bridge._nav_timeout_ms(),
                    wait_until="domcontentloaded",
                )
                self._scraped_post_ids.add(post_id)
                self._auto_stats["posts_scraped"] = (
                    self._auto_stats.get("posts_scraped", 0) + 1
                )
            else:
                await page.reload(wait_until="domcontentloaded")
            self._page_last_active[id(page)] = time.monotonic()
            return 1
        except Exception:  # noqa: BLE001 — best-effort navigation
            return 0

    def _post_id_for_image(self, image_id: int) -> int | None:
        """Owning post id for a browsed image id (search DB), or None."""
        try:
            from database import SessionLocal
            from sqlalchemy import text as _sa_text

            with SessionLocal() as db:
                row = db.execute(
                    _sa_text(
                        "SELECT post_id FROM civitai_search_images "
                        "WHERE civitai_image_id = :iid"
                    ),
                    {"iid": int(image_id)},
                ).fetchone()
                return int(row[0]) if row and row[0] else None
        except Exception:  # noqa: BLE001 — best-effort lookup
            return None

    async def install(self) -> dict[str, Any]:
        """Install (or confirm) the harvester on all civitai pages.

        Returns a fail-open status dict, like the bridge itself.
        """
        self._ensure_auto_loop()
        bridge = self._get_bridge()
        ok = await bridge._ensure_connected()
        if not ok:
            return {"ok": False, "bridge": "unavailable", "error": bridge._state.last_error}

        installed: list[str] = []
        failed: list[str] = []
        # add_init_script applies to new navigations of pages in each context;
        # evaluate applies immediately to already-loaded pages.
        try:
            for ctx in bridge._browser.contexts:
                try:
                    await ctx.add_init_script(_install_js())
                except Exception:  # noqa: BLE001, S110 — per-context best-effort
                    pass
                for page in ctx.pages:
                    url = page.url or ""
                    if "civitai" not in url:
                        continue
                    try:
                        # Per-page init script: race-free for this page's future
                        # navigations even if the context-level script missed
                        # (e.g. page created before context arming).
                        await self._arm_page_init_script(page)
                        await page.evaluate(
                            "((flag) => { window.__atelierai_harvest = window.__atelierai_harvest || { queue: [], apiOkAt: 0, apiErrAt: 0, apiLastStatus: null }; return flag; })",
                            True,
                        )
                        # Evaluate the installer directly for immediate effect.
                        await page.evaluate(_install_js())
                        installed.append(url)
                        self._installed_pages.add(id(page))
                    except Exception as exc:  # noqa: BLE001 — per-page
                        failed.append(f"{url}: {type(exc).__name__}")
        except Exception as exc:  # noqa: BLE001 — fail-open contract
            return {"ok": False, "bridge": "no-page", "error": f"{type(exc).__name__}: {exc}"}

        return {
            "ok": True,
            "installed_on": installed,
            "failed": failed,
        }

    async def drain(self, *, archive: bool = True) -> dict[str, Any]:
        """Drain queued captures from all civitai pages.

        Any manual drain also promotes the harvester to self-driving so
        captures can never pile up silently again (uvicorn --reload restarts
        killed the old env-flag-only loop).
        """
        self._ensure_auto_loop()
        bridge = self._get_bridge()
        ok = await bridge._ensure_connected()
        if not ok:
            return {"ok": False, "bridge": "unavailable", "error": bridge._state.last_error}

        records: list[dict[str, Any]] = []
        browsed_image_ids: set[int] = set()
        try:
            for ctx in bridge._browser.contexts:
                for page in ctx.pages:
                    url = page.url or ""
                    if "civitai" not in url:
                        continue
                    # Image-detail tabs (/images/{id}) hydrate via RSC data,
                    # NOT tRPC — nothing to harvest from them. Stage directly
                    # from the URL so browsed images enter Unrated review.
                    img_id = _image_id_from_url(url)
                    if img_id is not None:
                        browsed_image_ids.add(img_id)
                    try:
                        drained = await page.evaluate(_DRAIN_JS)
                        if drained:
                            records.extend(drained)
                    except Exception:  # noqa: BLE001, S112 — per-page
                        continue
        except Exception as exc:  # noqa: BLE001 — fail-open contract
            return {"ok": False, "bridge": "drain-failed", "error": f"{type(exc).__name__}: {exc}"}

        # Stage image-detail URLs visited since the last drain (no tRPC data
        # exists for them; the URL id is the only signal).
        url_staged = 0
        if browsed_image_ids:
            url_staged = await self._stage_browsed_image_ids(browsed_image_ids)

        archived, archive_errors = self._archive_records(records, archive=archive)
        return {
            "ok": True,
            "drained": len(records),
            "archived": archived,
            "url_staged": url_staged,
            "archive_errors": archive_errors,
            "records": records,
        }

    async def harvest_once(self) -> dict[str, Any]:
        """Convenience: watch + install + drain in one call."""
        # Watchers are idempotent and cheap when already armed; re-arming
        # covers sidecar restarts (new context objects) and first use.
        await self.watch()
        install_status = await self.install()
        if not install_status.get("ok"):
            return install_status
        return await self.drain()

    def _ensure_auto_loop(self) -> None:
        """Start the auto-drain loop on first use if not running.

        The loop is the harvester's default operating mode — watchers keep
        pages wrapped, but without the tick nothing drains/stages them, and
        browsed captures pile up until a manual action. Manual install/drain
        calls and the env-flag boot path both funnel through here, so any
        harvester activity promotes the singleton to self-driving. Requires
        a running event loop (all harvester entry points are async).
        """
        if self._auto_task is not None and not self._auto_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        # Reuse start_auto's logic without its async signature (loop exists).
        self._auto_interval = max(5.0, self._auto_interval)
        self._auto_stats.update(
            {"running": True, "interval_seconds": self._auto_interval}
        )
        self._auto_stop.clear()
        self._auto_task = loop.create_task(self._auto_loop())

    # ------------------------------------------------------------------
    # Auto-drain loop
    # ------------------------------------------------------------------

    async def _auto_loop_tick(self) -> None:
        """One watch+drain+archive+stage cycle. Records results, never raises."""
        try:
            result = await self.harvest_once()
            self._auto_stats["ticks"] += 1
            self._auto_stats["last_tick_at"] = time.time()
            if result.get("ok"):
                self._auto_stats["total_drained"] += result.get("drained", 0)
                self._auto_stats["total_archived"] += result.get("archived", 0)
                self._auto_stats["last_error"] = None
            else:
                # Unavailable bridge is expected when the sidecar is down;
                # keep it quiet but visible in stats.
                self._auto_stats["last_error"] = result.get("error") or result.get(
                    "bridge"
                )
            self._auto_stats["last_result"] = {
                k: result.get(k)
                for k in ("ok", "drained", "archived", "archive_errors")
            }
            # Stage any newly harvested feed captures into review tables.
            # Best-effort: staging failures don't affect the drain stats.
            if result.get("ok"):
                stage = await self._stage_new_captures()
                self._auto_stats["last_stage"] = stage
                if isinstance(stage, dict) and stage.get("ok"):
                    self._auto_stats["total_staged_new"] = (
                        self._auto_stats.get("total_staged_new", 0)
                        + (stage.get("images_new") or 0)
                    )
                # Janitor duties also ride the loop tick: error recovery,
                # post scraping, and idle-close must not depend on the
                # Bridge Lab dashboard being open.
                await self._run_janitor_duties(self._get_bridge())
        except Exception as exc:  # noqa: BLE001 — loop must never die
            self._auto_stats["last_error"] = f"{type(exc).__name__}: {exc}"

    async def _stage_new_captures(self) -> dict[str, Any] | None:
        """Run the browsed stager; return counts or None when unavailable."""
        result: dict[str, Any] | None = None
        try:
            from database import SessionLocal
            from services.browsed_stager import stage_harvested_feeds

            with SessionLocal() as db:
                result = stage_harvested_feeds(db)
        except Exception as exc:  # noqa: BLE001 — staging is best-effort
            result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

        # Self-heal pass: backfill a bounded number of stuck bare rows
        # (URL-staged images whose tabs closed before metadata arrived).
        # Unrated-view tiles without a uuid render as forever-loading.
        try:
            backfilled = await self._backfill_bare_rows(limit=3)
            if isinstance(result, dict):
                result["bare_rows_backfilled"] = backfilled
        except Exception:  # noqa: BLE001, S110 — best-effort
            pass
        return result

    async def _backfill_bare_rows(self, limit: int = 3) -> int:
        """Metadata-backfill up to ``limit`` stuck bare unrated rows per tick.

        Bounded so the shared tRPC budget (25 RPM) stays mostly available
        for real browsing; 3/tick clears the current backlog in ~5 ticks.
        Ids that failed backfill twice are quarantined (dead upstream —
        deleted images 404 permanently) and never retried in-process.
        """
        import asyncio

        def _ids() -> set[int]:
            try:
                from database import SessionLocal
                from sqlalchemy import text

                with SessionLocal() as db:
                    rows = db.execute(
                        text(
                            "SELECT i.civitai_image_id FROM civitai_search_images i "
                            "JOIN civitai_search_image_links l ON l.image_id = i.id "
                            "WHERE (i.uuid IS NULL OR i.uuid = '') "
                            "AND l.rating IS NULL AND l.search_id IS NULL "
                            "LIMIT :n"
                        ),
                        {"n": limit + len(self._backfill_fails)},
                    ).fetchall()
                    return {
                        r[0]
                        for r in rows
                        if self._backfill_fails.get(r[0], 0)
                        < self._BACKFILL_MAX_ATTEMPTS
                    }
            except Exception:  # noqa: BLE001 — best-effort
                return set()

        ids = await asyncio.get_running_loop().run_in_executor(None, _ids)
        if not ids:
            return 0
        await self._stage_browsed_image_ids(ids)  # backfills metadata
        # Record which ids STILL lack a uuid after the attempt — those were
        # fetch failures (404/rate-limit); dead ones hit the cap next time.
        after_meta = await self._meta_uuid_snapshot(ids)
        for image_id in ids:
            if not after_meta.get(image_id):
                self._backfill_fails[image_id] = (
                    self._backfill_fails.get(image_id, 0) + 1
                )
            else:
                self._backfill_fails.pop(image_id, None)  # succeeded — reset
        return len(ids)

    async def _meta_uuid_snapshot(self, ids: set[int]) -> dict[int, str]:
        """Return {image_id: uuid} for the given ids (bare = missing key)."""
        import asyncio

        def _snap() -> dict[int, str]:
            try:
                from database import SessionLocal
                from models import CivitaiSearchImage

                out: dict[int, str] = {}
                with SessionLocal() as db:
                    rows = (
                        db.query(
                            CivitaiSearchImage.civitai_image_id,
                            CivitaiSearchImage.uuid,
                        )
                        .filter(CivitaiSearchImage.civitai_image_id.in_(ids))
                        .all()
                    )
                    for cid, uuid in rows:
                        if uuid:
                            out[cid] = uuid
                return out
            except Exception:  # noqa: BLE001 — best-effort
                return {}

        return await asyncio.get_running_loop().run_in_executor(None, _snap)

    def _archive_records(
        self, records: list[dict[str, Any]], *, archive: bool = True
    ) -> tuple[int, list[str]]:
        """Write drained records to the response archive. Returns (count, errors)."""
        archived = 0
        archive_errors: list[str] = []
        if archive and records:
            arch = self._get_archive()
            for rec in records:
                try:
                    _repr, parsed_input = _parse_superjson_envelope(rec.get("url"))
                    endpoint = _endpoint_from_url(rec.get("url", ""))
                    method = rec.get("method") or "GET"
                    body = rec.get("bodyParsed")
                    if body is None:
                        body = rec.get("bodyText")
                    arch.record(
                        kind="harvested",
                        endpoint=endpoint,
                        request=parsed_input if parsed_input is not None else {"url": rec.get("url")},
                        response=body,
                        method=method,
                        url=rec.get("url"),
                        status_code=rec.get("status"),
                        elapsed_seconds=(rec.get("elapsedMs") or 0) / 1000.0,
                        error=rec.get("error"),
                    )
                    archived += 1
                except Exception as exc:  # noqa: BLE001 — per-record
                    archive_errors.append(f"{type(exc).__name__}: {exc}")
        return archived, archive_errors

    async def _stage_browsed_image_ids(self, image_ids: set[int]) -> int:
        """Stage bare image ids (from /images/{id} tab URLs) into review.

        Image-detail tabs hydrate via RSC data — the tRPC harvester sees
        nothing from them — so the only capture signal is the URL itself.
        Creates the row + unrated link, then BACKFILLS metadata via one
        ``image.get`` tRPC call per image (uuid→thumbnails, blurhash,
        artist, dimensions, postId) — bare rows render as forever-loading
        tiles because a missing uuid means no CDN thumbnail URL exists.

        Rows already carrying a uuid are skipped (previously staged /
        rated); fetch failures leave the bare row for the search lab's
        lazy single-image reload to fill later.
        """
        if not image_ids:
            return 0

        import asyncio

        def _work() -> int:
            try:
                from database import SessionLocal
                from models import CivitaiSearchImage, CivitaiSearchImageLink

                from atelierai.civitai.civitai_api import CivitaiAPI

                api = CivitaiAPI.get_instance()
                staged = 0

                # PHASE 1 — network (NO DB session held): fetch metadata for
                # bare rows up front. Doing this inside the write transaction
                # held the SQLite write lock across rate-limited HTTP calls
                # (seconds each) and starved every other writer
                # ("database is locked" storm in the search endpoints).
                metadata: dict[int, dict] = {}
                with SessionLocal() as db:
                    needs_meta = {
                        image_id
                        for image_id in image_ids
                        if not _row_has_uuid(db, image_id)
                    }
                metadata = self._fetch_bare_metadata(api, needs_meta)

                # PHASE 2 — short write transaction (no network inside).
                with SessionLocal() as db:
                    for image_id in image_ids:
                        try:
                            img = (
                                db.query(CivitaiSearchImage)
                                .filter(
                                    CivitaiSearchImage.civitai_image_id == image_id
                                )
                                .first()
                            )
                            if img is None:
                                img = CivitaiSearchImage(civitai_image_id=image_id)
                                db.add(img)
                                db.flush()
                            existing = (
                                db.query(CivitaiSearchImageLink)
                                .filter(CivitaiSearchImageLink.image_id == img.id)
                                .first()
                            )
                            if existing is None:
                                db.add(
                                    CivitaiSearchImageLink(
                                        image_id=img.id,
                                        search_id=None,
                                        rating=None,
                                        is_excluded=False,
                                    )
                                )
                                staged += 1
                            # Metadata backfill: bare rows (no uuid) get the
                            # pre-fetched image.get data so tiles render.
                            if not img.uuid and image_id in metadata:
                                _apply_image_meta(img, metadata[image_id])
                        except Exception:  # noqa: BLE001, S112 — per-id
                            continue
                    db.commit()
                return staged
            except Exception:  # noqa: BLE001 — staging is best-effort
                return 0

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _work)

    def _fetch_bare_metadata(
        self, api: Any, needs_meta: set[int]
    ) -> dict[int, dict]:
        """Fetch image.get metadata for bare ids, cache-first + quarantined.

        Tabs stay open across many drain ticks — a dead image (404 forever,
        uuid never fills) must not be re-fetched every 30s for as long as
        its tab exists. Two guards:
        1. per-id quarantine (same caps as _backfill_bare_rows)
        2. cache-first fetch — tombstoned 404s and cached 200s are served
           from the DB cache without touching the wire.
        """
        metadata: dict[int, dict] = {}
        for image_id in needs_meta:
            if (
                self._backfill_fails.get(image_id, 0)
                >= self._BACKFILL_MAX_ATTEMPTS
            ):
                continue
            try:
                info = (
                    api.fetch_basic_info_cached(image_id, max_age=_META_MAX_AGE)
                    or {}
                )
                if isinstance(info, dict) and info.get("url"):
                    metadata[image_id] = info
                    self._backfill_fails.pop(image_id, None)
                else:
                    self._backfill_fails[image_id] = (
                        self._backfill_fails.get(image_id, 0) + 1
                    )
            except Exception:  # noqa: BLE001 — per-id fetch
                self._backfill_fails[image_id] = (
                    self._backfill_fails.get(image_id, 0) + 1
                )
                continue
        return metadata

    async def _auto_loop(self) -> None:
        """Periodically install (idempotent) + drain while connected.

        Fail-open by design: any error is recorded in stats and the loop
        continues; the loop never raises into the task scheduler.
        """
        while not self._auto_stop.is_set():
            try:
                await asyncio.wait_for(
                    self._auto_stop.wait(), timeout=self._auto_interval
                )
                break  # stop requested
            except asyncio.TimeoutError:
                pass

            await self._auto_loop_tick()

    async def start_auto(self, interval_seconds: float = 30.0) -> dict[str, Any]:
        """Start the auto-drain loop (no-op if already running)."""
        if self._auto_task is not None and not self._auto_task.done():
            return {
                "ok": True,
                "already_running": True,
                "stats": dict(self._auto_stats),
            }
        self._auto_interval = max(5.0, float(interval_seconds))
        self._auto_stats.update(
            {"running": True, "interval_seconds": self._auto_interval}
        )
        self._auto_stop.clear()
        self._auto_task = asyncio.get_running_loop().create_task(self._auto_loop())
        return {"ok": True, "already_running": False, "stats": dict(self._auto_stats)}

    def stop_auto(self) -> dict[str, Any]:
        """Stop the auto-drain loop (drains are best-effort flushed)."""
        was_running = self._auto_task is not None and not self._auto_task.done()
        self._auto_stop.set()
        if self._auto_task is not None:
            self._auto_task.cancel()
            self._auto_task = None
        self._auto_stats["running"] = False
        return {"ok": True, "was_running": was_running}

    def auto_status(self) -> dict[str, Any]:
        """Current auto-drain loop stats."""
        running = self._auto_task is not None and not self._auto_task.done()
        self._auto_stats["running"] = running
        return dict(self._auto_stats)


def _row_has_uuid(db: Any, image_id: int) -> bool:
    """True when the civitai_search_images row exists and carries a uuid."""
    from models import CivitaiSearchImage

    row = (
        db.query(CivitaiSearchImage.uuid)
        .filter(CivitaiSearchImage.civitai_image_id == image_id)
        .first()
    )
    return bool(row and row[0])


def _apply_image_meta(img: Any, info: dict) -> None:
    """Fill-if-absent metadata fields from an image.get response (no clobber)."""
    user = info.get("user") or {}
    img.uuid = info.get("url")
    img.blurhash = img.blurhash or info.get("hash")
    img.post_id = img.post_id or info.get("postId")
    img.file_name = img.file_name or info.get("name")
    img.artist_id = img.artist_id or user.get("id")
    img.artist_name = img.artist_name or user.get("username")


# ── Singleton ───────────────────────────────────────────────────────────────
_HARVESTER_SINGLETON: CivitaiPageHarvester | None = None


def get_page_harvester() -> CivitaiPageHarvester:
    """Return the process-wide harvester instance (lazily constructed)."""
    global _HARVESTER_SINGLETON
    if _HARVESTER_SINGLETON is None:
        _HARVESTER_SINGLETON = CivitaiPageHarvester()
    return _HARVESTER_SINGLETON
