# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/civitai-integration.md
# ──────────────────────────────────────────────────────────────────────────────
"""Tests for CivitAI CDN 503 flag detection and per-endpoint 503 tracking.

Covers the two observability/prevention features added to
``CivitaiHttpClient`` after the 2026-09-05/06 transport-log analysis:

* Per-endpoint 503 counters (``_ENDPOINT_503_COUNTS``) surfaced through
  ``get_request_metrics`` / ``_build_tpm_breakdown`` / ``_format_tpm_table``.
* CDN 503 flag detection: N consecutive CDN 503s trip a CDN-scoped
  escalating cooldown (300→600→1200s capped), inner retry probes abort
  while the cooldown is active, a CDN success resets the streak, and
  tRPC traffic is never paused by the CDN flag.

These tests call ``_execute_envelope_request`` directly (no FIFO queue),
so no consumer-thread timing is involved.
"""

import time
from concurrent.futures import Future

import pytest

from atelierai.civitai.http_client import (
    CivitaiHttpClient,
    CivitaiRequestError,
    RequestType,
)

CDN_URL = "https://image.civitai.com/abc123/original=true/456.webp"
TRPC_URL = "https://civitai.red/api/trpc/image.get"


class _FakeResponse:
    """Minimal requests.Response stand-in for _execute_envelope_request."""

    def __init__(self, status_code: int, url: str = CDN_URL, text: str = ""):
        self.status_code = status_code
        self.url = url
        self.text = text
        self.headers = {}
        self.content = b"fake-bytes"
        self.elapsed = None


class _FakeSession:
    """Returns queued responses in order; repeats the last one if exhausted."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = 0

    def request(self, method, url, **kwargs):
        self.calls += 1
        if len(self._responses) > 1:
            return self._responses.pop(0)
        return self._responses[0]


def _make_client(monkeypatch, responses, *, max_attempts: int = 4):
    client = CivitaiHttpClient(
        headers_factory=dict,
        max_attempts=max_attempts,
        backoff_base_seconds=0.001,
    )
    session = _FakeSession(responses)
    monkeypatch.setattr(client, "_get_session", lambda: session)
    return client


def _cdn_envelope(endpoint: str = "cdn_get.image.civitai.com"):
    return CivitaiHttpClient._RequestEnvelope(
        method="GET",
        url=CDN_URL,
        kwargs={},
        future=Future(),
        request_type=RequestType.CDN_DOWNLOAD,
        fqdn="image.civitai.com",
        endpoint=endpoint,
    )


def _trpc_envelope(endpoint: str = "image.get"):
    return CivitaiHttpClient._RequestEnvelope(
        method="GET",
        url=TRPC_URL,
        kwargs={},
        future=Future(),
        request_type=RequestType.TRPC,
        fqdn="civitai.red",
        endpoint=endpoint,
    )


@pytest.fixture(autouse=True)
def _reset_503_state(monkeypatch):
    """Isolate class-level 503/flag counters per test."""
    monkeypatch.setattr(CivitaiHttpClient, "_TYPE_503_COUNTS", {})
    monkeypatch.setattr(CivitaiHttpClient, "_ENDPOINT_503_COUNTS", {})
    monkeypatch.setattr(CivitaiHttpClient, "_RATE_LIMITED_503", 0)
    monkeypatch.setattr(CivitaiHttpClient, "_LAST_RPM_AT_503", None)
    monkeypatch.setattr(CivitaiHttpClient, "_LAST_503_TIME", None)
    monkeypatch.setattr(CivitaiHttpClient, "_CDN_503_CONSECUTIVE", 0)
    monkeypatch.setattr(CivitaiHttpClient, "_CDN_FLAG_STRIKE_COUNT", 0)
    monkeypatch.setattr(CivitaiHttpClient, "_CDN_FLAG_COOLDOWN_UNTIL", 0.0)
    monkeypatch.setattr(CivitaiHttpClient, "_CDN_FLAG_REASON", "")
    # Pin the threshold regardless of any ambient env override.
    monkeypatch.setattr(CivitaiHttpClient, "_CDN_503_FLAG_THRESHOLD", 3)
    yield


class TestPerEndpoint503Counts:
    def test_503s_counted_per_endpoint(self, monkeypatch):
        client = _make_client(monkeypatch, [_FakeResponse(503)], max_attempts=1)
        with pytest.raises(CivitaiRequestError):
            client._execute_envelope_request(_cdn_envelope(), timing={})

        client2 = _make_client(
            monkeypatch,
            [_FakeResponse(503, url=TRPC_URL)],
            max_attempts=1,
        )
        with pytest.raises(CivitaiRequestError):
            client2._execute_envelope_request(_trpc_envelope(), timing={})

        counts = CivitaiHttpClient._ENDPOINT_503_COUNTS
        assert counts == {
            "cdn_get.image.civitai.com": 1,
            "image.get": 1,
        }
        # One CDN 503 alone must not trip the flag (threshold is 3).
        assert not CivitaiHttpClient.is_cdn_flag_active()
        assert CivitaiHttpClient._CDN_503_CONSECUTIVE == 1

    def test_metrics_expose_endpoint_503_counts(self, monkeypatch):
        client = _make_client(monkeypatch, [_FakeResponse(503)], max_attempts=1)
        with pytest.raises(CivitaiRequestError):
            client._execute_envelope_request(_cdn_envelope(), timing={})

        metrics = CivitaiHttpClient.get_request_metrics()
        assert metrics["endpoint_503_counts"] == {
            "cdn_get.image.civitai.com": 1
        }
        assert metrics["cdn_503_consecutive"] == 1
        assert metrics["cdn_flag_active"] is False
        assert metrics["cdn_flag_remaining_seconds"] == 0.0
        assert metrics["cdn_flag_strikes"] == 0

    def test_breakdown_and_table_include_503s(self, monkeypatch):
        client = _make_client(monkeypatch, [_FakeResponse(503)], max_attempts=1)
        with pytest.raises(CivitaiRequestError):
            client._execute_envelope_request(_cdn_envelope(), timing={})

        breakdown = CivitaiHttpClient._build_tpm_breakdown()
        assert breakdown["session_503s"] == 1
        ep = breakdown["endpoints"]["cdn_get.image.civitai.com"]
        assert ep["503s"] == 1

        table = CivitaiHttpClient._format_tpm_table()
        assert "503s" in table  # header present
        assert "Total Aggregate" in table


class TestCdnFlagTripAndAbort:
    def test_consecutive_503s_trip_flag_and_abort_retries(self, monkeypatch):
        # Always-503 CDN envelope: attempts 1-2 count toward the streak,
        # attempt 3 trips the cooldown and the abort check raises immediately.
        client = _make_client(
            monkeypatch, [_FakeResponse(503)], max_attempts=4
        )
        timing: dict = {}
        with pytest.raises(CivitaiRequestError) as excinfo:
            client._execute_envelope_request(_cdn_envelope(), timing=timing)

        assert excinfo.value.retryable is True  # soft-skip → retried next sync
        assert timing.get("cdn_flag_tripped") is True
        assert timing.get("cdn_flag_abort") is True
        # Aborted at attempt 3 (threshold), not the full 4 attempts.
        assert timing["attempts_used"] == 3

        assert CivitaiHttpClient._CDN_FLAG_STRIKE_COUNT == 1
        assert CivitaiHttpClient.is_cdn_flag_active()
        remaining = CivitaiHttpClient.get_cdn_flag_remaining_seconds()
        assert 0.0 < remaining <= 300.5  # first strike → base cooldown

        metrics = CivitaiHttpClient.get_request_metrics()
        assert metrics["cdn_flag_active"] is True
        assert metrics["cdn_flag_remaining_seconds"] == pytest.approx(
            remaining, abs=1.0
        )
        assert metrics["cdn_flag_strikes"] == 1
        assert metrics["cdn_503_consecutive"] == 3

    def test_below_threshold_does_not_trip(self, monkeypatch):
        client = _make_client(
            monkeypatch, [_FakeResponse(503)], max_attempts=2
        )
        timing: dict = {}
        with pytest.raises(CivitaiRequestError):
            client._execute_envelope_request(_cdn_envelope(), timing=timing)

        assert "cdn_flag_tripped" not in timing
        assert "cdn_flag_abort" not in timing
        assert not CivitaiHttpClient.is_cdn_flag_active()
        assert CivitaiHttpClient._CDN_503_CONSECUTIVE == 2
        assert CivitaiHttpClient._CDN_FLAG_STRIKE_COUNT == 0

    def test_best_effort_503s_do_not_trip_flag(self, monkeypatch):
        """Enrichment fetches (best_effort=True) must not feed the flag streak.

        A preview-variant 503 previously tripped the cooldown and paused ALL
        CDN downloads for 300s during primary ingest work (2026-09-08).
        """
        client = _make_client(monkeypatch, [_FakeResponse(503)], max_attempts=4)
        envelope = _cdn_envelope()
        envelope.best_effort = True
        timing: dict = {}
        with pytest.raises(CivitaiRequestError):
            client._execute_envelope_request(envelope, timing=timing)

        # Stats still recorded (503 counted), but streak untouched.
        assert CivitaiHttpClient._ENDPOINT_503_COUNTS == {
            "cdn_get.image.civitai.com": 4
        }
        assert CivitaiHttpClient._CDN_503_CONSECUTIVE == 0
        assert not CivitaiHttpClient.is_cdn_flag_active()
        assert CivitaiHttpClient._CDN_FLAG_STRIKE_COUNT == 0

    def test_slow_origin_timeout_503s_do_not_trip_flag(self, monkeypatch):
        """35s-origin-timeout 503s (webm transcode misses) are per-asset
        failures, not edge rejections — they must NOT feed the flag streak."""
        client = _make_client(monkeypatch, [_FakeResponse(503)], max_attempts=4)
        # Simulate slow attempts: patch time.monotonic so each attempt appears
        # to have taken >10s (the slow-503 threshold).
        monotonic_calls = {"n": 0}

        def _slow_monotonic():
            monotonic_calls["n"] += 1
            # Alternate baseline/measure so elapsed appears ~35s per attempt.
            return 1_000_000.0 + (monotonic_calls["n"] // 2) * 35.0

        monkeypatch.setattr(
            "atelierai.civitai.http_client.time.monotonic", _slow_monotonic
        )
        timing: dict = {}
        with pytest.raises(CivitaiRequestError):
            client._execute_envelope_request(_cdn_envelope(), timing=timing)

        assert CivitaiHttpClient._CDN_503_CONSECUTIVE == 0  # streak untouched
        assert not CivitaiHttpClient.is_cdn_flag_active()
        assert CivitaiHttpClient._CDN_FLAG_STRIKE_COUNT == 0

    def test_trpc_503s_never_trip_cdn_flag(self, monkeypatch):
        client = _make_client(
            monkeypatch, [_FakeResponse(503, url=TRPC_URL)], max_attempts=1
        )
        with pytest.raises(CivitaiRequestError):
            client._execute_envelope_request(_trpc_envelope(), timing={})

        assert CivitaiHttpClient._CDN_503_CONSECUTIVE == 0
        assert not CivitaiHttpClient.is_cdn_flag_active()


class TestCooldownEscalation:
    def test_schedule_escalates_and_caps(self):
        schedule = CivitaiHttpClient._CDN_FLAG_COOLDOWN_SCHEDULE
        assert schedule == (300.0, 600.0, 1200.0)

        first = CivitaiHttpClient.activate_cdn_flag_cooldown()
        assert first == 300.0
        assert CivitaiHttpClient._CDN_FLAG_STRIKE_COUNT == 1
        assert CivitaiHttpClient.is_cdn_flag_active()

        second = CivitaiHttpClient.activate_cdn_flag_cooldown()
        assert second == 600.0
        assert CivitaiHttpClient._CDN_FLAG_STRIKE_COUNT == 2

        third = CivitaiHttpClient.activate_cdn_flag_cooldown()
        assert third == 1200.0

        fourth = CivitaiHttpClient.activate_cdn_flag_cooldown()
        assert fourth == 1200.0  # capped at the last schedule entry
        assert CivitaiHttpClient._CDN_FLAG_STRIKE_COUNT == 4


class TestSuccessResets:
    def test_cdn_success_resets_streak(self, monkeypatch):
        CivitaiHttpClient._CDN_503_CONSECUTIVE = 2
        client = _make_client(monkeypatch, [_FakeResponse(200)])

        response = client._execute_envelope_request(_cdn_envelope(), timing={})
        assert response.status_code == 200
        assert CivitaiHttpClient._CDN_503_CONSECUTIVE == 0

    def test_success_after_expiry_resets_escalation(self, monkeypatch):
        # Cooldown expired (until=0) with prior strikes → successful probe
        # resets escalation so the next trip starts at the base cooldown.
        CivitaiHttpClient._CDN_503_CONSECUTIVE = 5
        CivitaiHttpClient._CDN_FLAG_STRIKE_COUNT = 2
        CivitaiHttpClient._CDN_FLAG_COOLDOWN_UNTIL = 0.0
        CivitaiHttpClient._CDN_FLAG_REASON = "cdn_503_flag"
        client = _make_client(monkeypatch, [_FakeResponse(200)])

        client._execute_envelope_request(_cdn_envelope(), timing={})
        assert CivitaiHttpClient._CDN_FLAG_STRIKE_COUNT == 0
        assert CivitaiHttpClient._CDN_FLAG_REASON == ""
        assert CivitaiHttpClient._CDN_503_CONSECUTIVE == 0

    def test_success_during_active_cooldown_keeps_strike(self, monkeypatch):
        # A success while the cooldown is still running resets the streak
        # but must NOT clear the active cooldown/escalation.
        CivitaiHttpClient._CDN_503_CONSECUTIVE = 3
        CivitaiHttpClient._CDN_FLAG_STRIKE_COUNT = 1
        CivitaiHttpClient._CDN_FLAG_COOLDOWN_UNTIL = time.time() + 300.0
        client = _make_client(monkeypatch, [_FakeResponse(200)])

        client._execute_envelope_request(_cdn_envelope(), timing={})
        assert CivitaiHttpClient._CDN_503_CONSECUTIVE == 0
        assert CivitaiHttpClient._CDN_FLAG_STRIKE_COUNT == 1
        assert CivitaiHttpClient.is_cdn_flag_active()


class TestTrpcUnaffected:
    def test_trpc_dispatches_normally_while_cdn_flag_active(self, monkeypatch):
        # Activate the CDN flag, then verify no global backoff was engaged
        # and a tRPC envelope still executes successfully.
        CivitaiHttpClient.activate_cdn_flag_cooldown()
        assert CivitaiHttpClient.is_cdn_flag_active()
        assert not CivitaiHttpClient.is_global_backoff_active()
        assert CivitaiHttpClient.get_global_backoff_remaining_seconds() == 0.0

        client = _make_client(
            monkeypatch, [_FakeResponse(200, url=TRPC_URL)]
        )
        response = client._execute_envelope_request(
            _trpc_envelope(), timing={}
        )
        assert response.status_code == 200
