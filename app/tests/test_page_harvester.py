# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/civitai-integration.md
# ──────────────────────────────────────────────────────────────────────────────
"""Offline tests for the passive tRPC page harvester."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from atelierai.civitai.page_harvester import (
    CivitaiPageHarvester,
    _endpoint_from_url,
    _parse_superjson_envelope,
)

pytestmark = pytest.mark.filterwarnings(
    "ignore::pytest.PytestUnraisableExceptionWarning"
)


class TestEndpointFromUrl:
    def test_trpc_endpoint(self) -> None:
        assert (
            _endpoint_from_url("https://civitai.red/api/trpc/image.getInfinite?batch=1")
            == "image.getInfinite"
        )

    def test_v1_endpoint(self) -> None:
        assert _endpoint_from_url("https://civitai.red/api/v1/images") == "v1.images"

    def test_garbage(self) -> None:
        # urlparse treats a bare string as a path; it passes through as a
        # slug (sanitized downstream by the archive's _slug()).
        assert _endpoint_from_url("not a url") == "not a url"

    def test_root_path(self) -> None:
        assert _endpoint_from_url("https://civitai.red/") == "unknown"


class TestSuperjsonEnvelope:
    def test_extracts_input(self) -> None:
        url = (
            "https://civitai.red/api/trpc/post.get?batch=1&input="
            '%7B%220%22%3A%7B%22json%22%3A%7B%22id%22%3A123%7D%7D%7D'
        )
        raw, parsed = _parse_superjson_envelope(url)
        assert parsed == {"0": {"json": {"id": 123}}}
        assert raw

    def test_no_input_param(self) -> None:
        raw, parsed = _parse_superjson_envelope("https://civitai.red/api/trpc/x")
        assert raw is None and parsed is None

    def test_malformed_json(self) -> None:
        url = "https://civitai.red/api/trpc/x?input=%7Bnot-json"
        raw, parsed = _parse_superjson_envelope(url)
        assert raw is None and parsed is None


def _make_page(url: str = "https://civitai.red/images", drain_result: list | None = None):
    page = MagicMock()
    page.url = url

    async def _evaluate(expression, arg=None):
        # Signature-enforcing fake, mirroring Playwright's real evaluate().
        if "splice" in expression:  # drain JS
            return drain_result or []
        return None

    page.evaluate = _evaluate
    page.goto = AsyncMock()
    return page


def _make_bridge(pages: list):
    from atelierai.civitai.browser_bridge import CivitaiBrowserBridge

    bridge = CivitaiBrowserBridge(cdp_url="http://127.0.0.1:9999")
    browser = MagicMock()
    browser.is_connected.return_value = True
    browser.version = "152.0.0.0"
    ctx = MagicMock()
    ctx.pages = list(pages)
    ctx.new_page = AsyncMock()
    ctx.add_init_script = AsyncMock()
    browser.contexts = [ctx]
    bridge._browser = browser
    return bridge, ctx


class TestInstall:
    def test_installs_on_civitai_pages_only(self) -> None:
        civitai = _make_page("https://civitai.red/images")
        foreign = _make_page("https://example.com/")
        bridge, ctx = _make_bridge([civitai, foreign])

        harvester = CivitaiPageHarvester(bridge)
        out = asyncio.run(harvester.install())

        assert out["ok"] is True
        assert out["installed_on"] == ["https://civitai.red/images"]
        ctx.add_init_script.assert_awaited_once()

    def test_fail_open_when_disconnected(self) -> None:
        from atelierai.civitai.browser_bridge import CivitaiBrowserBridge

        bridge = CivitaiBrowserBridge(cdp_url="http://127.0.0.1:1")
        harvester = CivitaiPageHarvester(bridge)
        out = asyncio.run(harvester.install())
        assert out["ok"] is False
        assert out["bridge"] == "unavailable"


class TestDrain:
    def test_drain_archives_records(self, tmp_path: Path, monkeypatch) -> None:
        # Point the harvester at a stub archive; assert on the calls.
        arch = MagicMock()
        arch.record = MagicMock(return_value=Path("/tmp/fake.json"))
        page = _make_page(
            drain_result=[
                {
                    "ts": 1700000000.0,
                    "url": "https://civitai.red/api/trpc/image.getInfinite?batch=1&input=%7B%220%22%3A%7B%22json%22%3A%7B%22period%22%3A%22AllTime%22%7D%7D%7D",
                    "method": "GET",
                    "status": 200,
                    "bodyParsed": [{"result": {"data": "[...]"}}],
                    "bodyText": None,
                    "error": None,
                    "elapsedMs": 120,
                    "source": "fetch",
                }
            ]
        )
        bridge, _ = _make_bridge([page])
        harvester = CivitaiPageHarvester(bridge)
        monkeypatch.setattr(harvester, "_get_archive", lambda: arch)

        out = asyncio.run(harvester.drain(archive=True))

        assert out["ok"] is True
        assert out["drained"] == 1
        assert out["archived"] == 1
        call = arch.record.call_args
        assert call.kwargs["kind"] == "harvested"
        assert call.kwargs["endpoint"] == "image.getInfinite"
        assert call.kwargs["status_code"] == 200

    def test_drain_skips_archive_when_disabled(self) -> None:
        page = _make_page(drain_result=[{"url": "https://civitai.red/api/trpc/x", "status": 200}])
        bridge, _ = _make_bridge([page])
        harvester = CivitaiPageHarvester(bridge)

        out = asyncio.run(harvester.drain(archive=False))

        assert out["ok"] is True
        assert out["drained"] == 1
        assert out["archived"] == 0

    def test_drain_empty_queue(self) -> None:
        page = _make_page(drain_result=[])
        bridge, _ = _make_bridge([page])
        harvester = CivitaiPageHarvester(bridge)

        out = asyncio.run(harvester.drain())
        assert out["ok"] is True and out["drained"] == 0

    def test_drain_fail_open_when_unavailable(self) -> None:
        from atelierai.civitai.browser_bridge import CivitaiBrowserBridge

        bridge = CivitaiBrowserBridge(cdp_url="http://127.0.0.1:1")
        harvester = CivitaiPageHarvester(bridge)
        out = asyncio.run(harvester.drain())
        assert out["ok"] is False
        assert out["bridge"] == "unavailable"


class TestRecordShape:
    def test_real_record_roundtrip_through_archive(self, tmp_path: Path) -> None:
        """A drained record must survive the archive round-trip intact."""
        from atelierai.civitai.response_archive import CivitaiResponseArchive

        arch = CivitaiResponseArchive(root=tmp_path)
        rec_url = "https://civitai.red/api/trpc/post.get?batch=1&input=%7B%220%22%3A%7B%22json%22%3A%7B%22id%22%3A9%7D%7D%7D"
        _raw, parsed_input = _parse_superjson_envelope(rec_url)
        path = arch.record(
            kind="harvested",
            endpoint=_endpoint_from_url(rec_url),
            request=parsed_input,
            response={"result": {"data": {"json": {"id": 9}}}},
            method="GET",
            url=rec_url,
            status_code=200,
            elapsed_seconds=0.12,
        )
        assert path.exists()
        back = arch.read_latest(
            kind="harvested", endpoint="post.get", request=parsed_input
        )
        assert back is not None
        assert back["status_code"] == 200
        assert back["response"]["result"]["data"]["json"]["id"] == 9
