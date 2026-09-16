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
    _META_MAX_AGE,
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


class TestPageHealth:
    """_page_health classifies probe results from the wrapper's timestamps."""

    def test_pending_when_no_traffic(self) -> None:
        assert CivitaiPageHarvester._page_health({"apiOkAt": 0, "apiErrAt": 0}) == "pending"

    def test_ok_after_success(self) -> None:
        assert (
            CivitaiPageHarvester._page_health({"apiOkAt": 100, "apiErrAt": 0}) == "ok"
        )

    def test_error_when_only_failure(self) -> None:
        assert (
            CivitaiPageHarvester._page_health({"apiOkAt": 0, "apiErrAt": 100})
            == "error"
        )

    def test_ok_wins_when_success_is_newer(self) -> None:
        assert (
            CivitaiPageHarvester._page_health({"apiOkAt": 200, "apiErrAt": 100})
            == "ok"
        )

    def test_error_when_failure_is_newer(self) -> None:
        assert (
            CivitaiPageHarvester._page_health({"apiOkAt": 100, "apiErrAt": 200})
            == "error"
        )

    def test_missing_fields_are_pending(self) -> None:
        assert CivitaiPageHarvester._page_health({}) == "pending"


class TestRetryErrorPages:
    """_retry_error_pages reloads /posts/ tabs in error with backoff."""

    def _make_harvester(self):
        h = CivitaiPageHarvester(bridge=MagicMock())
        h._bridge = MagicMock()
        h._bridge._browser = MagicMock()
        return h

    def _error_page(self, url: str):
        page = MagicMock()
        page.url = url
        page.evaluate = AsyncMock(
            return_value={"apiOkAt": 0, "apiErrAt": 9_000, "apiLastStatus": 503}
        )
        page.reload = AsyncMock()
        return page

    def _ok_page(self, url: str):
        page = MagicMock()
        page.url = url
        page.evaluate = AsyncMock(
            return_value={"apiOkAt": 9_000, "apiErrAt": 0, "apiLastStatus": 200}
        )
        page.reload = AsyncMock()
        return page

    def test_error_post_page_gets_reloaded(self) -> None:
        h = self._make_harvester()
        page = self._error_page("https://civitai.red/posts/12345")
        ctx = MagicMock()
        ctx.pages = [page]
        h._bridge._browser.contexts = [ctx]

        retried = asyncio.run(h._retry_error_pages(h._bridge))

        assert retried == 1
        page.reload.assert_awaited_once()
        assert h._auto_stats["pages_retried"] == 1

    def test_backoff_delays_second_reload(self) -> None:
        h = self._make_harvester()
        page = self._error_page("https://civitai.red/posts/12345")
        ctx = MagicMock()
        ctx.pages = [page]
        h._bridge._browser.contexts = [ctx]

        asyncio.run(h._retry_error_pages(h._bridge))  # attempt 1 → reload
        asyncio.run(h._retry_error_pages(h._bridge))  # within 5s backoff → skip
        page.reload.assert_awaited_once()

    def test_healthy_pages_are_not_reloaded(self) -> None:
        h = self._make_harvester()
        page = self._ok_page("https://civitai.red/posts/12345")
        ctx = MagicMock()
        ctx.pages = [page]
        h._bridge._browser.contexts = [ctx]

        assert asyncio.run(h._retry_error_pages(h._bridge)) == 0
        page.reload.assert_not_awaited()

    def test_image_tab_recovers_via_post_navigation(self) -> None:
        """Erroring /images/ tabs navigate to their owning post page.

        The post's tRPC burst re-fires through the wrapper — recovery and
        scraping in one step. The old behavior (skip image tabs entirely,
        wait for the separately-gated scrape duty) left them stuck when
        auto_scrape_posts reset on reload.
        """
        h = self._make_harvester()
        h._post_id_for_image = MagicMock(return_value=12345)
        page = self._error_page("https://civitai.red/images/999")
        page.goto = AsyncMock()
        ctx = MagicMock()
        ctx.pages = [page]
        h._bridge._browser.contexts = [ctx]

        assert asyncio.run(h._retry_error_pages(h._bridge)) == 1
        page.goto.assert_awaited_once()
        nav_url = page.goto.call_args.args[0]
        assert "/posts/12345" in nav_url
        page.reload.assert_not_awaited()
        # Recovery counts as a scrape and restarts the idle window.
        assert h._auto_stats.get("posts_scraped") == 1
        assert id(page) in h._page_last_active

    def test_image_tab_falls_back_to_reload_without_post_id(self) -> None:
        h = self._make_harvester()
        h._post_id_for_image = MagicMock(return_value=None)
        page = self._error_page("https://civitai.red/images/999")
        page.goto = AsyncMock()
        ctx = MagicMock()
        ctx.pages = [page]
        h._bridge._browser.contexts = [ctx]

        assert asyncio.run(h._retry_error_pages(h._bridge)) == 1
        page.reload.assert_awaited_once()
        page.goto.assert_not_awaited()

    def test_retry_capped_per_cycle(self) -> None:
        """A fleet of erroring tabs must not fire one synchronized burst."""
        h = self._make_harvester()
        h._RETRY_MAX_PER_CYCLE = 3
        pages = [self._error_page(f"https://civitai.red/posts/{i}") for i in range(6)]
        ctx = MagicMock()
        ctx.pages = pages
        h._bridge._browser.contexts = [ctx]

        retried = asyncio.run(h._retry_error_pages(h._bridge))

        assert retried == 3
        reloads = sum(p.reload.await_count for p in pages)
        assert reloads == 3
        # Capped-out pages weren't touched this cycle; they're protected
        # from close via the last-is-None guard and the error-health gate
        # (see TestCloseSuspendedWhileErroring), and retried next cycle.

    def test_non_civitai_and_non_target_urls_skipped(self) -> None:
        h = self._make_harvester()
        ctx = MagicMock()
        ctx.pages = [
            self._error_page("https://example.com/posts/1"),
            self._error_page("https://civitai.red/search?query=x"),
        ]
        h._bridge._browser.contexts = [ctx]

        assert asyncio.run(h._retry_error_pages(h._bridge)) == 0

    def test_recovery_clears_backoff_state(self) -> None:
        h = self._make_harvester()
        page = self._error_page("https://civitai.red/posts/12345")
        ctx = MagicMock()
        ctx.pages = [page]
        h._bridge._browser.contexts = [ctx]

        asyncio.run(h._retry_error_pages(h._bridge))
        assert h._retry_backoff.get(id(page)) == 1

        # Page recovers: next probe reports ok → state cleared.
        page.evaluate = AsyncMock(
            return_value={"apiOkAt": 99_000, "apiErrAt": 9_000, "apiLastStatus": 200}
        )
        asyncio.run(h._retry_error_pages(h._bridge))
        assert id(page) not in h._retry_backoff
        assert id(page) not in h._retry_next

    def test_probe_reports_health_fields(self) -> None:
        h = self._make_harvester()
        page = self._ok_page("https://civitai.red/posts/12345")
        ctx = MagicMock()
        ctx.pages = [page]
        h._bridge._browser.contexts = [ctx]
        h._bridge._ensure_connected = AsyncMock(return_value=True)
        h._maybe_scrape_related_posts = AsyncMock()
        h._maybe_close_idle_pages = AsyncMock()
        h._retry_error_pages = AsyncMock(return_value=0)
        h._ensure_auto_loop = MagicMock()

        result = asyncio.run(h.probe())

        assert result["ok"] is True
        assert result["pages"][0]["health"] == "ok"
        assert result["pages"][0]["api_status"] == 200


class TestCloseSuspendedWhileErroring:
    """Auto-close must not reap tabs whose API health is degraded."""

    def test_error_page_not_closed_even_when_idle(self) -> None:
        h = CivitaiPageHarvester(bridge=MagicMock())
        h._bridge = MagicMock()
        h._bridge._browser = MagicMock()
        h.set_auto_close_seconds(5.0)

        page = MagicMock()
        page.url = "https://civitai.red/posts/12345"
        page.evaluate = AsyncMock(
            return_value={"apiOkAt": 0, "apiErrAt": 9_000, "apiLastStatus": 503}
        )
        page.close = AsyncMock()
        ctx = MagicMock()
        ctx.pages = [page]
        h._bridge._browser.contexts = [ctx]
        # Tab idle far beyond the window.
        h._page_last_active[id(page)] = 100.0
        import atelierai.civitai.page_harvester as ph

        orig_monotonic = ph.time.monotonic
        ph.time.monotonic = lambda: 10_000.0
        try:
            asyncio.run(h._maybe_close_idle_pages(h._bridge))
        finally:
            ph.time.monotonic = orig_monotonic

        page.close.assert_not_awaited()

    def test_healthy_idle_page_still_closed(self) -> None:
        h = CivitaiPageHarvester(bridge=MagicMock())
        h._bridge = MagicMock()
        h._bridge._browser = MagicMock()
        h.set_auto_close_seconds(5.0)

        page = MagicMock()
        page.url = "https://civitai.red/posts/12345"
        page.evaluate = AsyncMock(
            return_value={"apiOkAt": 9_000, "apiErrAt": 0, "apiLastStatus": 200}
        )
        page.close = AsyncMock()
        ctx = MagicMock()
        ctx.pages = [page]
        h._bridge._browser.contexts = [ctx]
        h._page_last_active[id(page)] = 100.0
        import atelierai.civitai.page_harvester as ph

        orig_monotonic = ph.time.monotonic
        ph.time.monotonic = lambda: 10_000.0
        try:
            asyncio.run(h._maybe_close_idle_pages(h._bridge))
        finally:
            ph.time.monotonic = orig_monotonic

        page.close.assert_awaited_once()


class TestBareMetadataQuarantine:
    """_fetch_bare_metadata: cache-first + dead-id quarantine.

    Regression: open /images/{id} tabs re-staged every 30s drain tick used
    uncached fetch_basic_info, so a dead (404) or rate-limited id was
    re-fetched indefinitely for as long as its tab stayed open —
    self-inflicted 429s from the harvester itself.
    """

    def _harvester(self):
        h = CivitaiPageHarvester(bridge=MagicMock())
        h._BACKFILL_MAX_ATTEMPTS = 2
        return h

    def test_uses_cached_variant_with_ttl(self) -> None:
        h = self._harvester()
        api = MagicMock()
        api.fetch_basic_info_cached = MagicMock(
            return_value={"url": "https://cdn/abc.webp", "id": 1}
        )

        meta = h._fetch_bare_metadata(api, {1})

        assert meta == {1: {"url": "https://cdn/abc.webp", "id": 1}}
        api.fetch_basic_info_cached.assert_called_once()
        # Stale-but-fresh-enough cache lookups must be bounded by the TTL.
        _args, kwargs = api.fetch_basic_info_cached.call_args
        assert kwargs.get("max_age") == _META_MAX_AGE

    def test_dead_id_quarantined_after_max_attempts(self) -> None:
        h = self._harvester()
        api = MagicMock()
        api.fetch_basic_info_cached = MagicMock(return_value=None)

        h._fetch_bare_metadata(api, {42})  # fail 1
        h._fetch_bare_metadata(api, {42})  # fail 2 → at cap
        count_after_two = api.fetch_basic_info_cached.call_count
        h._fetch_bare_metadata(api, {42})  # quarantined → no call

        assert count_after_two == 2
        assert api.fetch_basic_info_cached.call_count == 2
        assert h._backfill_fails[42] == 2

    def test_success_resets_failure_count(self) -> None:
        h = self._harvester()
        fail_api = MagicMock()
        fail_api.fetch_basic_info_cached = MagicMock(return_value=None)
        ok_api = MagicMock()
        ok_api.fetch_basic_info_cached = MagicMock(
            return_value={"url": "https://cdn/x.webp"}
        )

        h._fetch_bare_metadata(fail_api, {7})
        h._fetch_bare_metadata(ok_api, {7})

        assert h._backfill_fails.get(7) is None
        assert 7 in h._fetch_bare_metadata(ok_api, {7})

    def test_exception_counts_as_failure_not_crash(self) -> None:
        h = self._harvester()
        api = MagicMock()
        api.fetch_basic_info_cached = MagicMock(side_effect=RuntimeError("boom"))

        meta = h._fetch_bare_metadata(api, {9})

        assert meta == {}
        assert h._backfill_fails[9] == 1


class TestSettingsPersistence:
    """Janitor settings survive process restarts via the app_settings table.

    Regression: auto_scrape_posts/auto_close_seconds were per-process and
    silently reset on every uvicorn --reload, stalling recovery flows.
    """

    @staticmethod
    def _fake_database_module(tmp_path, monkeypatch, with_table: bool):
        """Swap sys.modules['database'] for a real sqlite/SQLAlchemy engine."""
        import sys
        import types

        from sqlalchemy import create_engine, text
        from sqlalchemy.orm import sessionmaker

        engine = create_engine(f"sqlite:///{tmp_path / 'settings.sqlite3'}")
        if with_table:
            with engine.begin() as conn:
                conn.execute(text(
                    "CREATE TABLE app_settings ("
                    "key VARCHAR PRIMARY KEY, value VARCHAR NOT NULL, "
                    "updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)"
                ))
        SessionLocal = sessionmaker(bind=engine)

        fake_db = types.ModuleType("database")
        fake_db.SessionLocal = SessionLocal
        monkeypatch.setitem(sys.modules, "database", fake_db)
        return SessionLocal

    def test_setters_persist_and_reload_restores(self, tmp_path, monkeypatch) -> None:
        self._fake_database_module(tmp_path, monkeypatch, with_table=True)

        h1 = CivitaiPageHarvester(bridge=MagicMock())
        h1.set_auto_scrape_posts(True)
        h1.set_auto_close_seconds(300)

        # Simulated reload: fresh harvester, same persisted settings.
        h2 = CivitaiPageHarvester(bridge=MagicMock())
        assert h2._auto_scrape_posts is True
        assert h2._auto_close_seconds == 300.0

        # Toggling off also persists.
        h2.set_auto_scrape_posts(False)
        h2.set_auto_close_seconds(0)
        h3 = CivitaiPageHarvester(bridge=MagicMock())
        assert h3._auto_scrape_posts is False
        assert h3._auto_close_seconds == 0.0

    def test_missing_table_means_defaults(self, tmp_path, monkeypatch) -> None:
        # Fresh DB, no app_settings table yet (pre-startup): fail-open to
        # defaults and never raise — persistence must not break construction.
        self._fake_database_module(tmp_path, monkeypatch, with_table=False)

        h = CivitaiPageHarvester(bridge=MagicMock())
        assert h._auto_scrape_posts is False
        assert h._auto_close_seconds == 0.0

        # Setters on a missing table must not raise either.
        h.set_auto_scrape_posts(True)
        h.set_auto_close_seconds(120)
