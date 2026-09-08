# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/civitai-integration.md
# ──────────────────────────────────────────────────────────────────────────────
"""Tests for the CDP browser bridge (offline; no sidecar required).

Covers the fail-open contract: every entry point must return a structured
dict — never raise — when the sidecar is unreachable, and the capture log /
state bookkeeping must behave when a connection IS available.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.filterwarnings(
    "ignore::pytest.PytestUnraisableExceptionWarning"
)

# The fail-open tests intentionally abandon asyncio.run() loops mid-operation
# when the CDP connection is refused; playwright's transport finalizers then
# fire during GC on an already-closed loop. The suppressed warning is a test
# artifact, not a bridge defect.

from atelierai.civitai.browser_bridge import (
    BridgeSessionState,
    CivitaiBrowserBridge,
    get_browser_bridge,
)


class TestFailOpenContract:
    """No sidecar running → structured unavailable responses, no raises."""

    @pytest.fixture()
    def bridge(self) -> CivitaiBrowserBridge:
        # Point at a CDP URL that cannot possibly answer.
        return CivitaiBrowserBridge(cdp_url="http://127.0.0.1:1")

    def test_status_never_raises(self, bridge: CivitaiBrowserBridge) -> None:
        result = asyncio.run(bridge.status())
        assert result["connected"] is False
        assert result["last_error"]
        assert result["civitai_pages"] == []

    def test_fetch_never_raises(self, bridge: CivitaiBrowserBridge) -> None:
        result = asyncio.run(
            bridge.fetch("https://civitai.red/api/trpc/post.get?id=1")
        )
        assert result["ok"] is False
        assert result["bridge"] == "unavailable"
        assert result["via"] == "browser-bridge"

    def test_navigate_never_raises(self, bridge: CivitaiBrowserBridge) -> None:
        result = asyncio.run(bridge.navigate("https://civitai.red/"))
        assert result["ok"] is False
        assert result["bridge"] == "unavailable"

    def test_fetch_count_increments_even_on_failure(self, bridge) -> None:
        asyncio.run(bridge.fetch("https://civitai.red/"))
        assert bridge._state.fetch_count == 1


class TestCaptureLog:
    """JSONL capture of fetch records, best-effort semantics."""

    def test_capture_writes_jsonl(self, tmp_path: Path) -> None:
        cap = tmp_path / "captures" / "bridge.jsonl"
        bridge = CivitaiBrowserBridge(
            cdp_url="http://127.0.0.1:1", capture_path=str(cap)
        )
        bridge._capture_record({"ts": 1.0, "url": "https://x", "status": 200})
        bridge._capture_record({"ts": 2.0, "()": "nope", "status": 500})

        lines = cap.read_text().strip().splitlines()
        assert len(lines) == 2
        first = json.loads(lines[0])
        assert first["url"] == "https://x"
        assert first["status"] == 200

    def test_capture_disabled_when_path_empty(self, tmp_path: Path) -> None:
        bridge = CivitaiBrowserBridge(cdp_url="http://127.0.0.1:1")
        bridge._capture_record({"ts": 1.0})  # must not raise
        assert not (tmp_path / "bridge.jsonl").exists()

    def test_capture_survives_unwritable_path(self) -> None:
        bridge = CivitaiBrowserBridge(
            cdp_url="http://127.0.0.1:1", capture_path="/proc/definitely/not/writable"
        )
        bridge._capture_record({"ts": 1.0})  # must not raise


class TestStateAndSingleton:
    def test_singleton_is_shared(self) -> None:
        a = get_browser_bridge()
        b = get_browser_bridge()
        assert a is b

    def test_state_dataclass_defaults(self) -> None:
        s = BridgeSessionState()
        assert s.connected is False
        assert s.fetch_count == 0
        assert s.last_connected_at is None

    def test_disconnect_without_connection_is_noop(self) -> None:
        bridge = CivitaiBrowserBridge(cdp_url="http://127.0.0.1:1")
        asyncio.run(bridge.disconnect())  # must not raise


class TestFetchHappyPath:
    """With a mocked browser+page, fetch() must round-trip cleanly."""

    def _make_bridge_with_page(self, tmp_path: Path, fetch_result: dict):
        bridge = CivitaiBrowserBridge(
            cdp_url="http://127.0.0.1:9999", capture_path=str(tmp_path / "c.jsonl")
        )
        page = MagicMock()
        page.url = "https://civitai.red/images"
        page.evaluate = AsyncMock(return_value=fetch_result)
        page.goto = AsyncMock()

        browser = MagicMock()
        browser.is_connected.return_value = True
        browser.version = "140.0.0.0"
        ctx = MagicMock()
        ctx.pages = [page]
        ctx.new_page = AsyncMock(return_value=page)
        browser.contexts = [ctx]

        bridge._browser = browser
        return bridge, page

    def test_fetch_roundtrip_json(self, tmp_path: Path) -> None:
        result_payload = {
            "status": 200,
            "headers": {"content-type": "application/json"},
            "bodyText": '{"result":{"data":{"json":[1,2,3]}}}',
            "bodyParsed": {"result": {"data": {"json": [1, 2, 3]}}},
        }
        bridge, page = self._make_bridge_with_page(tmp_path, result_payload)

        out = asyncio.run(bridge.fetch("https://civitai.red/api/trpc/fake"))

        assert out["ok"] is True
        assert out["status"] == 200
        assert out["body"] == {"result": {"data": {"json": [1, 2, 3]}}}
        assert out["via"] == "browser-bridge"
        # Page-context evaluate was used (not a Python-side fetch).
        page.evaluate.assert_awaited_once()

    def test_fetch_capture_recorded(self, tmp_path: Path) -> None:
        result_payload = {
            "status": 503,
            "headers": {},
            "bodyText": None,
            "bodyParsed": None,
        }
        bridge, _ = self._make_bridge_with_page(tmp_path, result_payload)

        out = asyncio.run(bridge.fetch("https://civitai.red/api/trpc/fake"))

        assert out["ok"] is False
        assert out["status"] == 503
        cap = tmp_path / "c.jsonl"
        assert cap.exists()
        rec = json.loads(cap.read_text().strip())
        assert rec["status"] == 503
        assert rec["via"] == "browser-bridge"

    def test_fetch_evaluate_failure_is_fail_open(self, tmp_path: Path) -> None:
        bridge, page = self._make_bridge_with_page(
            tmp_path,
            {"status": 200, "headers": {}, "bodyText": "", "bodyParsed": None},
        )
        page.evaluate = AsyncMock(side_effect=RuntimeError("detached frame"))

        out = asyncio.run(bridge.fetch("https://civitai.red/api/trpc/fake"))

        assert out["ok"] is False
        assert out["bridge"] == "evaluate-failed"
        assert "detached frame" in out["error"]


class TestPickPage:
    def test_prefers_existing_civitai_tab(self) -> None:
        bridge = CivitaiBrowserBridge(cdp_url="http://127.0.0.1:9999")
        civitai_page = MagicMock()
        civitai_page.url = "https://civitai.red/images/123"
        other_page = MagicMock()
        other_page.url = "https://example.com/"

        browser = MagicMock()
        browser.is_connected.return_value = True
        ctx = MagicMock()
        ctx.pages = [other_page, civitai_page]
        ctx.new_page = AsyncMock()
        browser.contexts = [ctx]
        bridge._browser = browser

        page = asyncio.run(bridge._pick_page())
        assert page is civitai_page
        ctx.new_page.assert_not_awaited()

    def test_opens_new_tab_when_none_civitai(self) -> None:
        bridge = CivitaiBrowserBridge(cdp_url="http://127.0.0.1:9999")
        page = MagicMock()
        page.url = "https://example.com/"
        page.goto = AsyncMock()

        browser = MagicMock()
        browser.is_connected.return_value = True
        ctx = MagicMock()
        ctx.pages = [page]
        new_page = MagicMock()
        new_page.url = ""
        new_page.goto = AsyncMock()
        ctx.new_page = AsyncMock(return_value=new_page)
        browser.contexts = [ctx]
        bridge._browser = browser

        chosen = asyncio.run(bridge._pick_page())
        assert chosen is new_page
        new_page.goto.assert_awaited_once()

    def test_returns_none_when_disconnected(self) -> None:
        bridge = CivitaiBrowserBridge(cdp_url="http://127.0.0.1:9999")
        assert asyncio.run(bridge._pick_page()) is None
