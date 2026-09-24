# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/civitai-cache.md
# ──────────────────────────────────────────────────────────────────────────────
"""Tests for cache-first collection pagination (image.getInfinite pages).

Covers the 2026-09-18 finding: step 3 fetched 919 pages live, refreshed the
page, and fetched all 919 AGAIN — the scraper's ``_make_collection_request``
used ``_make_raw_request`` (never cached) instead of the DB-cache path, so
zero ``collectionId=…`` rows were ever recorded despite ~2k live requests.

Now each page is written through to the DB cache and replayed from it when
fresh (default TTL 15 min via CIVITAI_COLLECTION_PAGE_CACHE_TTL_MINUTES).
"""

from datetime import timedelta
from types import SimpleNamespace

import pytest

from atelierai.civitai.civitai import CivitaiPrivateScraper


class _PageCacheEntry:
    def __init__(self, response_json, fetched_at):
        self.response_json = response_json
        self.fetched_at = fetched_at


class _FakeCacheService:
    """Minimal stand-in for services.civitai_cache_service + a DB session."""

    def __init__(self):
        self.rows: dict[tuple[str, str], object] = {}

    def build_request_key(self, endpoint, payload):
        parts = []
        for field in ("collectionId", "cursor", "sort", "period", "browsingLevel"):
            value = (payload or {}).get(field)
            if value is not None:
                parts.append(f"{field}={value}")
        return "&".join(parts)

    def get_latest(self, db, *, endpoint, request_key):
        return self.rows.get((endpoint, request_key))


@pytest.fixture()
def scraper_with_fake_cache(monkeypatch):
    scraper = CivitaiPrivateScraper(auto_authenticate=False)
    cache = _FakeCacheService()

    live_calls: list[dict] = []

    def fake_raw_request(endpoint, payload_data, *, strict=False):
        live_calls.append(payload_data)
        cursor = payload_data.get("cursor")
        if cursor is None:
            return {
                "items": [
                    {"id": 1, "type": "image", "url": "https://x/1"},
                    {"id": 2, "type": "image", "url": "https://x/2"},
                ],
                "nextCursor": "page-2",
            }
        if cursor == "page-2":
            return {
                "items": [{"id": 3, "type": "image", "url": "https://x/3"}],
                "nextCursor": None,
            }
        return {"items": [], "nextCursor": None}

    def fake_get_cached_or_fetch(
        endpoint, payload_data, *, max_age=None, cache_only=False, strict=False
    ):
        assert cache_only is True, "cache probe must be cache_only (no double fetch)"
        key = cache.build_request_key(endpoint, payload_data)
        return cache.rows.get(("image.getInfinite", key), None)

    def fake_record_to_db_cache(endpoint, payload_data, response_json, status):
        key = cache.build_request_key(endpoint, payload_data)
        cache.rows[("image.getInfinite", key)] = response_json

    monkeypatch.setattr(scraper.api, "_make_raw_request", fake_raw_request)
    monkeypatch.setattr(scraper.api, "get_cached_or_fetch", fake_get_cached_or_fetch)
    monkeypatch.setattr(scraper.api, "_record_to_db_cache", fake_record_to_db_cache)
    monkeypatch.setattr(scraper.api, "_load_cache_service", lambda: cache)
    monkeypatch.setattr(scraper.api, "_load_session_factory", lambda: lambda: None)

    return SimpleNamespace(scraper=scraper, cache=cache, live_calls=live_calls)


class TestCacheFirstPagination:
    def test_pages_are_written_through_on_first_fetch(self, scraper_with_fake_cache):
        env = scraper_with_fake_cache
        items = env.scraper.fetch_collection_items(collection_id=500)

        assert [it["id"] for it in items] == [1, 2, 3]
        # Two live pages fetched, each recorded under its own cache key.
        assert len(env.live_calls) == 2
        keys = {k for (ep, k) in env.cache.rows if ep == "image.getInfinite"}
        assert any("collectionId=500" in k and "cursor" not in k for k in keys)
        assert any("cursor=page-2" in k for k in keys)

    def test_second_fetch_replays_from_cache_with_zero_live_requests(
        self, scraper_with_fake_cache
    ):
        env = scraper_with_fake_cache
        first = env.scraper.fetch_collection_items(collection_id=500)
        live_after_first = len(env.live_calls)

        second = env.scraper.fetch_collection_items(collection_id=500)

        assert [it["id"] for it in second] == [it["id"] for it in first]
        assert len(env.live_calls) == live_after_first  # no new live calls

    def test_force_refresh_bypasses_cache_reads(self, scraper_with_fake_cache):
        env = scraper_with_fake_cache
        env.scraper.fetch_collection_items(collection_id=500)
        live_after_first = len(env.live_calls)

        env.scraper.fetch_collection_items(collection_id=500, use_cache=False)

        # Force refresh re-fetches every page live (and re-records them).
        assert len(env.live_calls) == live_after_first + 2

    def test_cache_miss_probe_never_double_fetches(self, scraper_with_fake_cache):
        """On a cache miss, exactly ONE live request per page must occur."""
        env = scraper_with_fake_cache
        env.scraper.fetch_collection_items(collection_id=500)
        assert len(env.live_calls) == 2

    def test_normalize_collection_page_shapes(self):
        page = {"items": [{"id": 9}], "nextCursor": "x"}
        assert CivitaiPrivateScraper._normalize_collection_page(page) == page
        inner = {"items": [], "nextCursor": None}
        assert (
            CivitaiPrivateScraper._normalize_collection_page(
                {"result": {"data": {"json": inner}}}
            )
            == inner
        )
        assert CivitaiPrivateScraper._normalize_collection_page("not-a-dict") is None
        assert CivitaiPrivateScraper._normalize_collection_page({"other": 1}) is None

    def test_ttl_default_and_env_override(self, monkeypatch):
        monkeypatch.delenv("CIVITAI_COLLECTION_PAGE_CACHE_TTL_MINUTES", raising=False)
        assert CivitaiPrivateScraper._collection_page_cache_ttl() == timedelta(
            minutes=15
        )
        monkeypatch.setenv("CIVITAI_COLLECTION_PAGE_CACHE_TTL_MINUTES", "60")
        assert CivitaiPrivateScraper._collection_page_cache_ttl() == timedelta(
            minutes=60
        )
        monkeypatch.setenv("CIVITAI_COLLECTION_PAGE_CACHE_TTL_MINUTES", "garbage")
        assert CivitaiPrivateScraper._collection_page_cache_ttl() == timedelta(
            minutes=15
        )
