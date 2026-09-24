# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/civitai-cache.md
# 🀄 docs: app/docs/memories/civitai-sync-tasks.md
# ──────────────────────────────────────────────────────────────────────────────
"""Tests for HTTP-404 tombstoning and sync-lab id plausibility guards.

Covers the 2026-09-18 incident: an invalid CivitAI image id (1) entered a
stale step-6 plan, was submitted to the live API, and 404'd — TWICE, 30
seconds apart — because HTTP-level 404s raised by the transport never wrote
a tombstone row (only payload-level tRPC error envelopes did). Cached
callers therefore had nothing to hit and every retry went live.

Fixes:
1. ``CivitaiAPI._make_raw_request`` tombstones HTTP 404s via
   ``_record_to_db_cache`` so dead ids are served from cache thereafter.
2. ``_parse_sync_lab_image_ids`` drops ids below 1000 (real CivitAI image
   ids are well into the millions; tiny ints are UI/state artifacts).
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock

_BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import main
import pytest  # noqa: F401  (fixtures via monkeypatch)

from atelierai.civitai.civitai_api import CivitaiAPI
from atelierai.civitai.http_client import CivitaiRequestError


class TestHttp404Tombstone:
    @staticmethod
    def _bare_api() -> CivitaiAPI:
        """A CivitaiAPI with just enough attributes for _make_raw_request."""
        api = CivitaiAPI.__new__(CivitaiAPI)
        api.base_url = "https://civitai.red/api/trpc"
        api.default_meta = {"meta": {"values": {"cursor": ["undefined"]}}}
        api.http_client = MagicMock()
        return api

    @staticmethod
    def _spy_tombstones(api, recorded):
        """Route this instance's tombstone writes into *recorded*."""
        api._record_to_db_cache = lambda *a, **k: recorded.append((a, k))
        api._record_response_archive = lambda *a, **k: None

    def _api_with_transport(self, monkeypatch, exc):
        api = self._bare_api()
        api.http_client.request_json = MagicMock(side_effect=exc)
        recorded = []
        self._spy_tombstones(api, recorded)
        return api, recorded

    def test_http_404_writes_tombstone(self, monkeypatch):
        api, recorded = self._api_with_transport(
            monkeypatch,
            CivitaiRequestError("Not Found", status_code=404, retryable=False),
        )
        result = api._make_raw_request("image.get", {"id": 1})
        assert result is None
        assert len(recorded) == 1
        endpoint, payload, response_json, status = recorded[0][0]
        assert endpoint == "image.get"
        assert payload == {"id": 1}
        assert response_json is None  # tombstone
        assert status == 404

    def test_other_http_errors_do_not_tombstone(self, monkeypatch):
        api, recorded = self._api_with_transport(
            monkeypatch,
            CivitaiRequestError("Boom", status_code=500, retryable=True),
        )
        result = api._make_raw_request("image.get", {"id": 1})
        assert result is None
        assert recorded == []  # 5xx is transient — never tombstone

    def test_success_path_untouched(self, monkeypatch):
        api = self._bare_api()
        api.http_client.request_json.return_value = {"result": {"data": {}}}
        recorded = []
        self._spy_tombstones(api, recorded)
        result = api._make_raw_request("image.get", {"id": 123})
        assert result == {"result": {"data": {}}}
        assert recorded == []


class TestSyncLabIdPlausibility:
    def test_tiny_ids_dropped(self):
        assert main._parse_sync_lab_image_ids("1,2,3,999") == []

    def test_plausible_ids_kept(self):
        assert main._parse_sync_lab_image_ids("15833361,117062969") == [
            15833361,
            117062969,
        ]

    def test_boundary_1000_kept(self):
        assert main._parse_sync_lab_image_ids("1000") == [1000]

    def test_mixed_input(self):
        assert main._parse_sync_lab_image_ids("1,abc,,5000000,42,117062969") == [
            5000000,
            117062969,
        ]

    def test_stage_resolver_filters_via_parser(self):
        ids, counts = main._resolve_sync_lab_stage_inputs(
            image_ids="1,15833361", selected_ids=None, limit=None
        )
        assert ids == [15833361]
        assert counts["requested_total"] == 1
