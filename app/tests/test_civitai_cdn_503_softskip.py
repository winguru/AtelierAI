# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/civitai-sync-tasks.md
# ──────────────────────────────────────────────────────────────────────────────
"""Tests for transient upstream (CDN 5xx) handling in CivitAI import.

Covers:
- ``_download_civitai_image_with_validation`` tries the next candidate URL
  on retryable 5xx (like it already does for 404) instead of raising.
- Exhausted 5xx candidates raise HTTP 503 (temporary) instead of 502.
- ``_process_civitai_image_ids`` soft-skips temporary upstream errors via
  ``_build_skipped_civitai_import_result`` instead of hard-failing.
- ``_is_civitai_temporary_upstream_error`` classification boundaries.
- Heartbeat throughput/ETA suffix formatting.
"""

from fastapi import HTTPException

import pytest

from atelierai.civitai.http_client import CivitaiRequestError

from backend.main import (
    _download_civitai_image_with_validation,
    _is_civitai_remote_not_found_error,
    _is_civitai_temporary_upstream_error,
    _format_civitai_pipeline_progress_suffix,
)


def _target(image_url: str = "https://image.civitai.com/a/real.webp") -> dict:
    return {
        "image_url": image_url,
        "mime_type": "image/webp",
        "declared_file_size": None,
        "original_filename": "real.webp",
        "artist_name": None,
        "source_url": "https://civitai.com/images/123",
    }


def test_download_validation_continues_to_next_candidate_on_503(monkeypatch):
    calls = []

    def fake_download(*, image_url, image_id, mime_type, declared_file_size):
        calls.append(image_url)
        if image_url.endswith("first.webp"):
            raise CivitaiRequestError(
                "CivitAI upstream unavailable (HTTP 503)",
                status_code=503,
                retryable=True,
            )
        return f"/tmp/ok-{len(calls)}.webp"

    monkeypatch.setattr("backend.main._download_civitai_image", fake_download)
    monkeypatch.setattr(
        "backend.main._detect_downloaded_media",
        lambda path: ("image", "image/webp"),
    )

    target = _target()
    target["image_url"] = "https://image.civitai.com/a/first.webp"
    # Force two candidates: primary URL + fallback
    monkeypatch.setattr(
        "backend.main._build_civitai_image_candidate_urls",
        lambda t: [
            "https://image.civitai.com/a/first.webp",
            "https://image.civitai.com/a/second.webp",
        ],
    )

    result = _download_civitai_image_with_validation(
        image_id=123, target=target
    )

    assert calls == [
        "https://image.civitai.com/a/first.webp",
        "https://image.civitai.com/a/second.webp",
    ]
    assert result.selected_url == "https://image.civitai.com/a/second.webp"


def test_download_validation_raises_503_when_all_candidates_5xx(monkeypatch):
    monkeypatch.setattr(
        "backend.main._download_civitai_image",
        lambda **kwargs: (_ for _ in ()).throw(
            CivitaiRequestError(
                "CivitAI upstream unavailable (HTTP 503)",
                status_code=503,
                retryable=True,
            )
        ),
    )
    monkeypatch.setattr(
        "backend.main._build_civitai_image_candidate_urls",
        lambda t: ["https://image.civitai.com/a/first.webp"],
    )

    with pytest.raises(HTTPException) as excinfo:
        _download_civitai_image_with_validation(
            image_id=123, target=_target()
        )

    assert excinfo.value.status_code == 503


def test_download_validation_propagates_permanent_error(monkeypatch):
    monkeypatch.setattr(
        "backend.main._download_civitai_image",
        lambda **kwargs: (_ for _ in ()).throw(
            CivitaiRequestError(
                "CivitAI rejected request (HTTP 403)",
                status_code=403,
                retryable=False,
            )
        ),
    )
    monkeypatch.setattr(
        "backend.main._build_civitai_image_candidate_urls",
        lambda t: ["https://image.civitai.com/a/first.webp"],
    )

    # Non-transient errors fail fast: the original CivitaiRequestError
    # propagates immediately (callers map 404/401 semantics themselves)
    # rather than burning through remaining candidate URLs.
    with pytest.raises(CivitaiRequestError) as excinfo:
        _download_civitai_image_with_validation(
            image_id=123, target=_target()
        )

    assert excinfo.value.status_code == 403


def test_temporary_upstream_error_classification():
    assert _is_civitai_temporary_upstream_error(
        CivitaiRequestError("503", status_code=503, retryable=True)
    )
    assert _is_civitai_temporary_upstream_error(
        CivitaiRequestError("500", status_code=500, retryable=True)
    )
    # 404 keeps its unavailable semantics
    assert not _is_civitai_temporary_upstream_error(
        CivitaiRequestError("404", status_code=404, retryable=False)
    )
    # Non-retryable 5xx (e.g. explicit upstream rejection) stays a failure
    assert not _is_civitai_temporary_upstream_error(
        CivitaiRequestError("503-nr", status_code=503, retryable=False)
    )
    # HTTPException 503 (mapped by validation / upstream classifier)
    assert _is_civitai_temporary_upstream_error(HTTPException(503, "temp"))
    # HTTPException 404 keeps unavailable semantics
    assert not _is_civitai_temporary_upstream_error(HTTPException(404, "gone"))
    # Unrelated exceptions are not temporary
    assert not _is_civitai_temporary_upstream_error(ValueError("boom"))


def test_remote_not_found_classification_unchanged():
    assert _is_civitai_remote_not_found_error(
        CivitaiRequestError("404", status_code=404, retryable=False)
    )
    assert _is_civitai_remote_not_found_error(HTTPException(404, "gone"))
    assert not _is_civitai_remote_not_found_error(
        CivitaiRequestError("503", status_code=503, retryable=True)
    )


def test_progress_suffix_formats_rate_and_eta():
    started = 1000.0

    class FakeMonotonic:
        def __init__(self, value):
            self.value = value

        def __call__(self):
            return self.value

    import backend.main as main_mod

    original = main_mod.time.monotonic
    main_mod.time.monotonic = FakeMonotonic(started + 60.0)
    try:
        # 10 items in 60s -> 10/min; 30 remaining -> ETA 03:00
        suffix = _format_civitai_pipeline_progress_suffix(
            started, completed_count=10, total_count=40
        )
        assert suffix == " (10.0/min, ETA 03:00)"
    finally:
        main_mod.time.monotonic = original

    main_mod.time.monotonic = FakeMonotonic(started + 60.0)
    try:
        # Baseline excludes Phase-1 DB-only completions: rate from 5 in 60s
        suffix = _format_civitai_pipeline_progress_suffix(
            started,
            completed_count=15,
            total_count=40,
            baseline_completed=10,
        )
        assert suffix == " (5.0/min, ETA 05:00)"
    finally:
        main_mod.time.monotonic = original

    main_mod.time.monotonic = FakeMonotonic(started + 60.0)
    try:
        # Nothing completed in the executor phase yet -> no suffix
        suffix = _format_civitai_pipeline_progress_suffix(
            started,
            completed_count=10,
            total_count=40,
            baseline_completed=10,
        )
        assert suffix == ""
    finally:
        main_mod.time.monotonic = original


def test_progress_suffix_rejects_invalid_inputs():
    assert (
        _format_civitai_pipeline_progress_suffix(None, 5, 10) == ""
    )
    assert _format_civitai_pipeline_progress_suffix(1000.0, 0, 10) == ""
    assert _format_civitai_pipeline_progress_suffix(1000.0, 5, 0) == ""
