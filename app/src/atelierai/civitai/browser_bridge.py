# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/civitai-integration.md
# ──────────────────────────────────────────────────────────────────────────────
"""CDP browser bridge: route CivitAI requests through a real Chromium session.

An optional sidecar container (docker/chrome-sidecar) runs a headed Chromium
under Xvfb with a persistent profile. AtelierAI connects via Playwright's
``connect_over_cdp`` and issues tRPC fetches from inside a civitai tab's page
context. Because the request originates in the page:

- the TLS/HTTP2 fingerprint is Chromium's (not Python/OpenSSL),
- cookies come from the profile's live jar (no server-side cookie copies),
- headers/Referer match what the page itself would send.

The bridge is deliberately *fail-open*: every entry point catches connection
errors and returns a structured unavailable response instead of raising, so a
missing or crashed sidecar never blocks syncs — callers fall back to the
direct lane.

Lane selection is the caller's policy; this module only provides transport.
See docs/features/browser-bridge.md for the operational runbook.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# playwright imports are done lazily inside methods so a missing install never
# breaks backend startup (fail-open contract).


def _is_ip_literal(host: str) -> bool:
    """True when ``host`` is already an IPv4/IPv6 literal (no DNS needed)."""
    import ipaddress

    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


@dataclass
class BridgeSessionState:
    """Snapshot of bridge connectivity for status endpoints."""

    connected: bool = False
    cdp_url: str = ""
    connect_url: str = ""
    browser_version: str = ""
    last_error: str = ""
    last_connected_at: float | None = None
    fetch_count: int = 0
    last_fetch_at: float | None = None


class CivitaiBrowserBridge:
    """Owns the CDP connection to the sidecar Chromium.

    One instance per backend process. The connection is established on
    demand and re-established after sidecar restarts (the profile volume
    keeps cookies alive across restarts, so reconnection is transparent).
    """

    def __init__(self, cdp_url: str = "", *, capture_path: str = ""):
        self._cdp_url = cdp_url
        self._capture_path = capture_path
        self._lock = asyncio.Lock()
        self._playwright = None
        self._browser = None
        self._state = BridgeSessionState(cdp_url=cdp_url)

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------

    def _resolve_cdp_url(self) -> str:
        """Return the configured CDP URL, falling back to config defaults."""
        if self._cdp_url:
            return self._cdp_url
        try:
            import atelierai.config as app_config

            configured = (
                getattr(app_config, "BROWSER_BRIDGE_CDP_URL", "") or ""
            ).strip()
            if configured:
                return configured
            return getattr(
                app_config,
                "BROWSER_BRIDGE_DEFAULT_CDP_URL",
                "http://chrome-sidecar:9222",
            )
        except Exception:  # noqa: BLE001 — fail-open contract
            return "http://chrome-sidecar:9222"

    def _connectable_cdp_url(self) -> str:
        """Return a CDP URL whose Host header DevTools will accept.

        Chromium's DevTools HTTP server rejects requests whose ``Host`` is
        neither an IP literal nor ``localhost`` (DNS-rebinding protection).
        A compose service name (``chrome-sidecar``) therefore gets HTTP 500
        even though the port is reachable. Resolve the hostname to its IP
        and rewrite the URL; when ``/json/version`` answers, Chromium echoes
        the IP into ``webSocketDebuggerUrl``, so the WebSocket upgrade rides
        the same acceptable Host. Returns the original URL when resolution
        fails — the connect attempt then surfaces a clear error.
        """
        import socket
        from urllib.parse import urlparse, urlunparse

        base = self._resolve_cdp_url()
        try:
            parsed = urlparse(base)
            host = parsed.hostname or ""
            if not host or _is_ip_literal(host):
                return base
            ip = socket.getaddrinfo(
                host, parsed.port or 80, proto=socket.IPPROTO_TCP
            )[0][4][0]
            # Replace ONLY the hostname; keep scheme/port/path untouched.
            netloc = f"{ip}:{parsed.port}" if parsed.port else ip
            return urlunparse(parsed._replace(netloc=netloc))
        except Exception:  # noqa: BLE001 — resolution failure falls back
            return base

    def _nav_timeout_ms(self) -> int:
        try:
            import atelierai.config as app_config

            return int(
                getattr(app_config, "BROWSER_BRIDGE_NAVIGATE_TIMEOUT", 30.0) * 1000
            )
        except Exception:  # noqa: BLE001
            return 30000

    @staticmethod
    def _web_base_url() -> str:
        try:
            import atelierai.config as app_config

            return getattr(app_config, "CIVITAI_WEB_BASE_URL", "https://civitai.red")
        except Exception:  # noqa: BLE001
            return "https://civitai.red"

    def _capture_record(self, record: dict[str, Any]) -> None:
        """Append a fetch record to the JSONL capture log, best-effort."""
        if not self._capture_path:
            # Fall back to the configured capture path when the instance was
            # not given an explicit one.
            try:
                import atelierai.config as app_config

                self._capture_path = (
                    getattr(app_config, "BROWSER_BRIDGE_CAPTURE_PATH", "") or ""
                ).strip()
            except Exception:  # noqa: BLE001, S110
                pass
        if not self._capture_path:
            return
        try:
            path = Path(self._capture_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError:
            # Capture is observability, not correctness — never propagate.
            pass

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def _ensure_connected(self) -> bool:
        """Connect to the sidecar if not already. Returns True when usable.

        Self-heals dead transports: when the sidecar restarts, the cached
        Playwright connection's unix-socket transport closes and every call
        raises ``RuntimeError: ... WriteUnixTransport ... the handler is
        closed`` (``is_connected()`` can lag behind). On any error we tear
        down the cached connection + playwright runtime and reconnect fresh.
        """
        if self._browser is not None and self._browser.is_connected():
            return True

        async with self._lock:
            # Double-check after acquiring the lock.
            if self._browser is not None and self._browser.is_connected():
                return True

            # A non-None but disconnected browser means the transport died
            # (sidecar restart). Reset fully so we rebuild both the playwright
            # runtime and the CDP connection — reusing the dead runtime keeps
            # hitting 'handler is closed'.
            if self._browser is not None or self._playwright is not None:
                await self._reset_connection_locked()

            cdp_url = self._resolve_cdp_url()
            connect_url = self._connectable_cdp_url()
            try:
                from playwright.async_api import async_playwright

                if self._playwright is None:
                    self._playwright = await async_playwright().start()
                self._browser = await self._playwright.chromium.connect_over_cdp(
                    connect_url
                )
                self._state.connected = True
                self._state.cdp_url = cdp_url
                self._state.connect_url = connect_url
                self._state.browser_version = self._browser.version
                self._state.last_error = ""
                self._state.last_connected_at = time.time()
                return True
            except Exception as exc:  # noqa: BLE001 — fail-open contract
                self._state.connected = False
                self._state.last_error = f"{type(exc).__name__}: {exc}"
                # Failed connect with a half-started runtime — reset so the
                # next attempt starts clean instead of stacking dead state.
                await self._reset_connection_locked()
                return False

    async def _reset_connection_locked(self) -> None:
        """Tear down browser + playwright runtime (caller holds the lock)."""
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:  # noqa: BLE001, S110 — best-effort teardown
                pass
            self._browser = None
        if self._playwright is not None:
            try:
                await self._playwright.stop()
            except Exception:  # noqa: BLE001, S110 — best-effort teardown
                pass
            self._playwright = None

    async def disconnect(self) -> None:
        """Close the CDP connection (the sidecar browser keeps running)."""
        async with self._lock:
            if self._browser is not None:
                try:
                    await self._browser.close()
                except Exception:  # noqa: BLE001, S110 — best-effort teardown
                    pass
                self._browser = None
            if self._playwright is not None:
                try:
                    await self._playwright.stop()
                except Exception:  # noqa: BLE001, S110 — best-effort teardown
                    pass
                self._playwright = None

    async def status(self) -> dict[str, Any]:
        """Return a connectivity + activity snapshot for status endpoints."""
        await self._ensure_connected()
        pages: list[str] = []
        if self._browser is not None and self._browser.is_connected():
            try:
                for ctx in self._browser.contexts:
                    for page in ctx.pages:
                        url = page.url or ""
                        if url and not url.startswith(("chrome", "about", "devtools")):
                            pages.append(url)
            except Exception:  # noqa: BLE001 — snapshot is best-effort
                pages = []
        return {
            "connected": self._state.connected,
            "cdp_url": self._state.cdp_url or self._resolve_cdp_url(),
            "connect_url": self._state.connect_url,
            "browser_version": self._state.browser_version,
            "civitai_pages": pages,
            "last_error": self._state.last_error,
            "last_connected_at": self._state.last_connected_at,
            "fetch_count": self._state.fetch_count,
            "last_fetch_at": self._state.last_fetch_at,
        }

    # ------------------------------------------------------------------
    # Page-context fetch
    # ------------------------------------------------------------------

    async def _pick_page(self) -> Any | None:
        """Return a live page on a civitai origin, or None.

        Prefers an existing civitai tab; otherwise opens one on the
        configured base domain so commanded fetches always have a
        same-origin context to ride on.
        """
        if self._browser is None or not self._browser.is_connected():
            return None
        try:
            for ctx in self._browser.contexts:
                for page in ctx.pages:
                    url = (page.url or "").lower()
                    if "civitai" in url:
                        return page
            ctx = self._browser.contexts[0] if self._browser.contexts else None
            if ctx is None:
                return None
            page = await ctx.new_page()
            try:
                await page.goto(
                    self._web_base_url(),
                    timeout=self._nav_timeout_ms(),
                    wait_until="domcontentloaded",
                )
            except Exception:  # noqa: BLE001, S110 — page may half-load; still usable
                pass
            return page
        except Exception:  # noqa: BLE001 — fail-open contract
            return None

    # JS executed inside the page context. Keep the return shape in sync
    # with the response handling in fetch() below. Playwright's evaluate()
    # takes exactly ONE argument — url/init are packed into a single object.
    _FETCH_JS = """
    async ({url, init}) => {
        const resp = await fetch(url, init);
        const text = await resp.text();
        let parsed = null;
        try { parsed = JSON.parse(text); } catch (e) { /* keep null */ }
        return {
            status: resp.status,
            headers: Object.fromEntries(resp.headers.entries()),
            bodyText: text.length > 200000 ? null : text,
            bodyParsed: parsed,
        };
    }
    """

    _FETCH_BINARY_JS = """
    async ({url}) => {
        const resp = await fetch(url, { credentials: 'include' });
        if (!resp.ok) {
            return { status: resp.status, contentType: resp.headers.get('content-type') || '', base64: null };
        }
        const buf = await resp.arrayBuffer();
        const bytes = new Uint8Array(buf);
        // Chunked base64: btoa on >~30MB strings can blow the call stack;
        // 32KB chunks keep each call small and the join cheap.
        let binary = '';
        const CHUNK = 32768;
        for (let i = 0; i < bytes.length; i += CHUNK) {
            binary += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK));
        }
        return {
            status: resp.status,
            contentType: resp.headers.get('content-type') || '',
            base64: btoa(binary),
            byteLength: bytes.length,
        };
    }
    """

    async def fetch_binary(
        self,
        url: str,
        *,
        max_bytes: int = 80 * 1024 * 1024,
    ) -> dict[str, Any]:
        """Fetch a binary asset (e.g. CDN image/video) through the sidecar
        browser session and return raw bytes.

        Same fail-open contract as :meth:`fetch`. The response carries the
        real Chromium TLS fingerprint and cookies — the browser lane, not
        the server HTTP client — so media fetched here never touches the
        shared rate-limited queue. ``data`` is ``bytes`` on success.

        ``max_bytes`` guards against absurd payloads; larger responses are
        truncated-refused (ok=False, reason=too-large).
        """
        started = time.time()
        ok = await self._ensure_connected()
        if not ok:
            return {"ok": False, "bridge": "unavailable",
                    "error": self._state.last_error}

        page = await self._pick_page()
        if page is None:
            return {"ok": False, "bridge": "no-page",
                    "error": "No usable civitai page context available"}

        try:
            result = await page.evaluate(self._FETCH_BINARY_JS, {"url": url})
        except Exception as exc:  # noqa: BLE001 — fail-open contract
            return {"ok": False, "bridge": "evaluate-failed",
                    "error": f"{type(exc).__name__}: {exc}"}

        status = int(result.get("status") or 0)
        byte_length = int(result.get("byteLength") or 0)
        if status != 200:
            return {"ok": False, "bridge": "http-error",
                    "status": status, "url": url}
        if byte_length > max_bytes:
            return {"ok": False, "bridge": "too-large",
                    "byte_length": byte_length, "url": url}

        import base64 as _b64

        try:
            data = _b64.b64decode(result.get("base64") or "")
        except Exception as exc:  # noqa: BLE001 — fail-open
            return {"ok": False, "bridge": "decode-failed",
                    "error": f"{type(exc).__name__}: {exc}"}

        return {
            "ok": True,
            "status": 200,
            "content_type": result.get("contentType") or "",
            "data": data,
            "byte_length": len(data),
            "elapsed_seconds": round(time.time() - started, 3),
        }

    async def fetch(
        self,
        url: str,
        *,
        method: str = "GET",
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Fetch a CivitAI URL from inside a civitai page context.

        Returns a plain dict with ok/status/body/headers. Never raises —
        callers treat the bridge as an optional lane (fail-open contract).
        """
        started = time.time()
        self._state.fetch_count += 1

        ok = await self._ensure_connected()
        if not ok:
            return {
                "ok": False,
                "bridge": "unavailable",
                "error": self._state.last_error,
                "elapsed_seconds": round(time.time() - started, 3),
                "via": "browser-bridge",
            }

        page = await self._pick_page()
        if page is None:
            return {
                "ok": False,
                "bridge": "no-page",
                "error": "No usable civitai page context available",
                "elapsed_seconds": round(time.time() - started, 3),
                "via": "browser-bridge",
            }

        init: dict[str, Any] = {"method": method}
        if payload is not None:
            init["body"] = json.dumps(payload)
        if headers:
            init["headers"] = headers

        try:
            result = await page.evaluate(self._FETCH_JS, {"url": url, "init": init})
        except Exception as exc:  # noqa: BLE001 — fail-open contract
            return {
                "ok": False,
                "bridge": "evaluate-failed",
                "error": f"{type(exc).__name__}: {exc}",
                "elapsed_seconds": round(time.time() - started, 3),
                "via": "browser-bridge",
            }

        self._state.last_fetch_at = time.time()
        status = int(result.get("status") or 0)
        body: Any = result.get("bodyParsed")
        if body is None:
            body = result.get("bodyText") or ""

        self._capture_record(
            {
                "ts": time.time(),
                "url": url,
                "method": method,
                "status": status,
                "elapsed_seconds": round(time.time() - started, 3),
                "via": "browser-bridge",
            }
        )

        return {
            "ok": 200 <= status < 300,
            "status": status,
            "body": body,
            "headers": result.get("headers", {}),
            "elapsed_seconds": round(time.time() - started, 3),
            "via": "browser-bridge",
        }

    async def navigate(self, url: str) -> dict[str, Any]:
        """Navigate a civitai tab to a URL (visible in the noVNC window)."""
        ok = await self._ensure_connected()
        if not ok:
            return {
                "ok": False,
                "bridge": "unavailable",
                "error": self._state.last_error,
                "via": "browser-bridge",
            }

        page = await self._pick_page()
        if page is None:
            return {
                "ok": False,
                "bridge": "no-page",
                "error": "No page available",
                "via": "browser-bridge",
            }

        try:
            await page.goto(
                url, timeout=self._nav_timeout_ms(), wait_until="domcontentloaded"
            )
            return {"ok": True, "url": page.url, "via": "browser-bridge"}
        except Exception as exc:  # noqa: BLE001 — fail-open contract
            return {
                "ok": False,
                "bridge": "navigate-failed",
                "error": f"{type(exc).__name__}: {exc}",
                "via": "browser-bridge",
            }

    _COOKIE_NAMES = ("__Secure-civ-token", "__Secure-civitai-token")

    async def _find_session_cookie(self) -> dict[str, Any] | None:
        """Return the freshest civitai session cookie across sidecar contexts."""
        from urllib.parse import urlparse

        domain = urlparse(self._web_base_url()).hostname or "civitai.red"
        best: dict[str, Any] | None = None
        for ctx in self._browser.contexts:
            try:
                for cookie in await ctx.cookies(f"https://{domain}"):
                    if cookie.get("name") not in self._COOKIE_NAMES:
                        continue
                    if not cookie.get("value"):
                        continue
                    if best is None or (cookie.get("expires") or 0) > (
                        best.get("expires") or 0
                    ):
                        best = cookie
            except Exception:  # noqa: BLE001, S112 — per-context
                continue
        return best

    async def pull_session_cookie(self) -> dict[str, Any]:
        """Pull the live CivitAI session cookie from the sidecar browser.

        Reads the browser context's cookie jar (Playwright exposes httpOnly
        cookies that ``document.cookie`` cannot see) for the configured base
        domain, then pushes the freshest ``__Secure-civ-token`` (or legacy
        names) into the CivitaiAPI singleton via ``update_session_cookie`` —
        which also persists it to the session cache file.

        Use case: the server-side token expired (auth/status → 401, API
        degrades to anonymous → SFW-only results); the sidecar browser still
        holds a valid logged-in session. This closes the loop without manual
        DevTools cookie copying.

        Returns a fail-open dict; ``updated`` is True when the singleton was
        refreshed with a token that differs from the current one.
        """
        ok = await self._ensure_connected()
        if not ok:
            return {"ok": False, "bridge": "unavailable",
                    "error": self._state.last_error}

        if self._browser is None:
            return {"ok": False, "bridge": "unavailable", "error": "no browser"}
        try:
            best = await self._find_session_cookie()
        except Exception as exc:  # noqa: BLE001 — fail-open contract
            return {"ok": False, "bridge": "cookie-read-failed",
                    "error": f"{type(exc).__name__}: {exc}"}

        if not best:
            return {"ok": True, "updated": False,
                    "reason": "no civitai session cookie in sidecar browser"}

        try:
            from atelierai.civitai.civitai_api import CivitaiAPI

            api = CivitaiAPI.get_instance()
            new_token = best["value"]
            if api.session_cookie == new_token:
                return {"ok": True, "updated": False, "reason": "token unchanged"}
            api.update_session_cookie(new_token)
            return {"ok": True, "updated": True, "cookie_name": best.get("name")}
        except Exception as exc:  # noqa: BLE001 — fail-open contract
            return {"ok": False, "bridge": "cookie-apply-failed",
                    "error": f"{type(exc).__name__}: {exc}"}


# ── Singleton access ────────────────────────────────────────────────────────
_BRIDGE_SINGLETON: CivitaiBrowserBridge | None = None


def get_browser_bridge() -> CivitaiBrowserBridge:
    """Return the process-wide bridge instance (lazily constructed)."""
    global _BRIDGE_SINGLETON
    if _BRIDGE_SINGLETON is None:
        _BRIDGE_SINGLETON = CivitaiBrowserBridge()
    return _BRIDGE_SINGLETON
