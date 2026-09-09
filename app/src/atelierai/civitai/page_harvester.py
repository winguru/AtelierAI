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

import json
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
        self._archive = None

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
        """Convenience: install + drain in one call."""
        install_status = await self.install()
        if not install_status.get("ok"):
            return install_status
        return await self.drain()


# ── Singleton ───────────────────────────────────────────────────────────────
_HARVESTER_SINGLETON: CivitaiPageHarvester | None = None


def get_page_harvester() -> CivitaiPageHarvester:
    """Return the process-wide harvester instance (lazily constructed)."""
    global _HARVESTER_SINGLETON
    if _HARVESTER_SINGLETON is None:
        _HARVESTER_SINGLETON = CivitaiPageHarvester()
    return _HARVESTER_SINGLETON
