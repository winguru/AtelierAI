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
from typing import Any

from atelierai.civitai.browser_bridge import CivitaiBrowserBridge

# JS injected before page scripts. Keep the record shape in sync with
# _drain_js below. The queue lives on window; navigation replaces the document
# but the wrapper re-installs itself via add_init_script and the queue is
# re-created — so drain BEFORE navigation, or accept losing records queued in
# the final moments before a route change.
_INSTALL_JS = """
(() => {
    if (window.__atelierai_harvest) return;  // idempotent
    const queue = [];
    window.__atelierai_harvest = { queue, wrapFetch: true };

    const MAX_BODY = 262144;  // match Python-side cap; JS-side guard only
    const cap = (s) => (typeof s === 'string' && s.length > MAX_BODY)
        ? null : s;

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

    # JS probe: does the page have the harvest wrapper, and how many
    # captures are queued? Cheap to evaluate on any civitai page.
    _PROBE_JS = """
    () => {
        const h = window.__atelierai_harvest;
        return {
            wrapped: !!h,
            queued: h && Array.isArray(h.queue) ? h.queue.length : 0,
        };
    }
    """

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
            await page.add_init_script(_INSTALL_JS)
            self._init_scripted_pages.add(id(page))
            return True
        except Exception:  # noqa: BLE001 — page may be closing
            return False

    async def _wrap_page_now(self, page: Any) -> bool:
        """Install the wrapper on one page immediately (fail-open)."""
        try:
            await page.evaluate(_INSTALL_JS)
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
        """Report harvest-wrapper state + queued counts per civitai page."""
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
                        pages.append({
                            "url": url.split("?")[0],
                            "wrapped": bool(result.get("wrapped")),
                            "queued": int(result.get("queued") or 0),
                        })
                    except Exception as exc:  # noqa: BLE001 — per-page
                        pages.append({
                            "url": url.split("?")[0],
                            "wrapped": False,
                            "queued": 0,
                            "error": f"{type(exc).__name__}",
                        })
        except Exception as exc:  # noqa: BLE001 — fail-open
            return {"ok": False, "bridge": "probe-failed", "error": f"{type(exc).__name__}: {exc}"}
        return {
            "ok": True,
            "pages": pages,
            "all_wrapped": bool(pages) and all(p.get("wrapped") for p in pages),
        }

    async def install(self) -> dict[str, Any]:
        """Install (or confirm) the harvester on all civitai pages.

        Returns a fail-open status dict, like the bridge itself.
        """
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
                    await ctx.add_init_script(_INSTALL_JS)
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
                            "((flag) => { window.__atelierai_harvest = window.__atelierai_harvest || { queue: [] }; return flag; })",
                            True,
                        )
                        # Evaluate the installer directly for immediate effect.
                        await page.evaluate(_INSTALL_JS)
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

        Returns counts and (optionally) the drained records themselves.
        When ``archive=True`` each record is written to the response archive
        as ``kind="harvested"`` before returning.
        """
        bridge = self._get_bridge()
        ok = await bridge._ensure_connected()
        if not ok:
            return {"ok": False, "bridge": "unavailable", "error": bridge._state.last_error}

        records: list[dict[str, Any]] = []
        try:
            for ctx in bridge._browser.contexts:
                for page in ctx.pages:
                    url = page.url or ""
                    if "civitai" not in url:
                        continue
                    try:
                        drained = await page.evaluate(_DRAIN_JS)
                        if drained:
                            records.extend(drained)
                    except Exception:  # noqa: BLE001, S112 — per-page
                        continue
        except Exception as exc:  # noqa: BLE001 — fail-open contract
            return {"ok": False, "bridge": "drain-failed", "error": f"{type(exc).__name__}: {exc}"}

        archived = 0
        archive_errors: list[str] = []
        if archive and records:
            arch = self._get_archive()
            for rec in records:
                try:
                    _input_repr, parsed_input = _parse_superjson_envelope(rec.get("url"))
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

        return {
            "ok": True,
            "drained": len(records),
            "archived": archived,
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
        except Exception as exc:  # noqa: BLE001 — loop must never die
            self._auto_stats["last_error"] = f"{type(exc).__name__}: {exc}"

    async def _stage_new_captures(self) -> dict[str, Any] | None:
        """Run the browsed stager; return counts or None when unavailable."""
        try:
            from database import SessionLocal
            from services.browsed_stager import stage_harvested_feeds

            with SessionLocal() as db:
                return stage_harvested_feeds(db)
        except Exception as exc:  # noqa: BLE001 — staging is best-effort
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

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


# ── Singleton ───────────────────────────────────────────────────────────────
_HARVESTER_SINGLETON: CivitaiPageHarvester | None = None


def get_page_harvester() -> CivitaiPageHarvester:
    """Return the process-wide harvester instance (lazily constructed)."""
    global _HARVESTER_SINGLETON
    if _HARVESTER_SINGLETON is None:
        _HARVESTER_SINGLETON = CivitaiPageHarvester()
    return _HARVESTER_SINGLETON
