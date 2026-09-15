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


class TestAutoDrainLoop:
    def test_start_stop_lifecycle(self) -> None:
        async def run():
            harvester = CivitaiPageHarvester(bridge=_disconnected_bridge())
            out = await harvester.start_auto(interval_seconds=5)
            assert out["ok"] is True and out["already_running"] is False

            status = harvester.auto_status()
            assert status["running"] is True
            assert status["interval_seconds"] == 5.0

            # Starting again is a no-op that keeps the loop.
            out2 = await harvester.start_auto(interval_seconds=10)
            assert out2["already_running"] is True
            assert harvester.auto_status()["interval_seconds"] == 5.0

            stop = harvester.stop_auto()
            assert stop["ok"] is True and stop["was_running"] is True
            assert harvester.auto_status()["running"] is False

        asyncio.run(run())

    def test_interval_clamps_to_minimum(self) -> None:
        async def run():
            harvester = CivitaiPageHarvester(bridge=_disconnected_bridge())
            await harvester.start_auto(interval_seconds=0.1)
            try:
                assert harvester.auto_status()["interval_seconds"] == 5.0
            finally:
                harvester.stop_auto()

        asyncio.run(run())

    def test_loop_tick_records_unavailable_bridge(self) -> None:
        """With no sidecar, each tick records the error and keeps looping."""
        harvester = CivitaiPageHarvester(bridge=_disconnected_bridge())

        async def run():
            await harvester.start_auto(interval_seconds=5)
            try:
                # Run exactly one tick manually (the loop task is sleeping).
                await harvester._auto_loop_tick()
                stats = harvester.auto_status()
                assert stats["ticks"] == 1
                assert stats["last_error"]  # bridge unavailable surfaced
            finally:
                harvester.stop_auto()

        asyncio.run(run())

    def test_stop_when_never_started(self) -> None:
        harvester = CivitaiPageHarvester(bridge=_disconnected_bridge())
        out = harvester.stop_auto()
        assert out["ok"] is True and out["was_running"] is False


def _disconnected_bridge():
    from atelierai.civitai.browser_bridge import CivitaiBrowserBridge

    return CivitaiBrowserBridge(cdp_url="http://127.0.0.1:1")


class TestEventWatcher:
    """Event-driven wrapping: new tabs + navigations get wrapped instantly."""

    def _make_context(self, pages):
        """Fake Playwright context/page with event-emitter semantics."""
        events = {"page": []}

        class _Ctx:
            def __init__(self):
                self.pages = list(pages)

            def on(self, name, handler):
                if name in events:
                    events[name].append(handler)

            def emit(self, name, page):
                for h in events[name]:
                    h(page)

        ctx = _Ctx()
        return ctx, events

    def _make_page(self, url="https://civitai.red/images"):
        nav_handlers = []

        class _Page:
            def __init__(self):
                self.url = url
                self.evaluate_calls = 0

            def on(self, name, handler):
                assert name == "framenavigated"
                nav_handlers.append(handler)

            async def evaluate(self, expression, arg=None):
                self.evaluate_calls += 1
                # First probe asks if the wrapper is alive → no; then the
                # installer runs (any expression) and succeeds.
                return not expression.strip().startswith("() =>")

            def emit_nav(self):
                for h in nav_handlers:
                    h(object())  # frame object; handler ignores its fields

        return _Page(), nav_handlers

    def test_watch_registers_context_and_page_handlers(self) -> None:
        harvester = CivitaiPageHarvester(bridge=_disconnected_bridge())
        page, _ = self._make_page()
        ctx, events = self._make_context([page])

        # Drive _watch_context directly (unit level).
        harvester._watch_context(_CtxShim(ctx, events))
        assert len(events["page"]) == 1, "context page handler registered"
        assert harvester._watched_pages, "existing pages should be watched"

        # Re-watching the same context is a no-op (idempotent).
        harvester._watch_context(_CtxShim(ctx, events))
        assert len(events["page"]) == 1

    def test_new_tab_event_wraps_page(self) -> None:
        harvester = CivitaiPageHarvester(bridge=_disconnected_bridge())
        ctx, events = self._make_context([])
        harvester._watch_context(_CtxShim(ctx, events))

        new_page, _nav = self._make_page("https://civitai.red/posts/1")
        for h in events["page"]:
            h(_PageShim(new_page))

        # Navigation handler is scheduled async; run it directly.
        import asyncio as _a

        _a.run(harvester._on_page_navigated(_PageShim(new_page)))
        assert new_page.evaluate_calls >= 1

    def test_watch_fail_open_when_disconnected(self) -> None:
        harvester = CivitaiPageHarvester(bridge=_disconnected_bridge())
        out = asyncio.run(harvester.watch())
        assert out["ok"] is False
        assert out["bridge"] == "unavailable"


class _CtxShim:
    """Adapter so fake contexts expose .on()/.pages like Playwright."""

    def __init__(self, ctx, events):
        self._ctx = ctx
        self._events = events

    def on(self, name, handler):
        self._ctx.on(name, handler)

    @property
    def pages(self):
        return [_PageShim(p) for p in self._ctx.pages]


class _PageShim:
    def __init__(self, page):
        self._page = page

    def on(self, name, handler):
        self._page.on(name, handler)

    @property
    def url(self):
        return self._page.url

    async def evaluate(self, expression, arg=None):
        return await self._page.evaluate(expression, arg)

