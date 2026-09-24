# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/civitai-sync-tasks.md
# ──────────────────────────────────────────────────────────────────────────────
"""Tests for cooperative cancellation in CivitAI collection sync.

Covers:
- CivitaiPrivateScraper.fetch_collection_items honors the ``should_stop``
  predicate between pages (pagination halts without another API call).
- _process_civitai_image_ids Phase-1 marks remaining images cancelled and
  skips all network work when cancellation is already requested.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

from atelierai.civitai.civitai import CivitaiPrivateScraper


class _StopAfterFirstPage:
    """should_stop predicate that trips once the first page is fetched."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> bool:
        self.calls += 1
        return self.calls > 1


def test_fetch_collection_items_should_stop_halts_pagination(monkeypatch):
    scraper = CivitaiPrivateScraper.__new__(CivitaiPrivateScraper)
    monkeypatch.setattr(
        scraper, "_debug_session_token", MagicMock(), raising=False
    )

    request_count = {"n": 0}

    def fake_request(collection_id, cursor, debug, *, use_cache=True):
        request_count["n"] += 1
        return [
            {"id": 100 + request_count["n"], "type": "Image"},
        ], f"cursor-{request_count['n']}"

    monkeypatch.setattr(scraper, "_make_collection_request", fake_request)
    monkeypatch.setattr(
        scraper,
        "_find_deep_image_list",
        lambda obj, depth=0: obj if isinstance(obj, list) else None,
    )
    monkeypatch.setattr(
        scraper, "_check_duplicates", lambda page_items, seen: False
    )

    stop = _StopAfterFirstPage()
    items = scraper.fetch_collection_items(
        1234, None, debug=False, should_stop=stop
    )

    # First should_stop poll (before page 1) returned False -> page fetched.
    # Second poll (before page 2) returned True -> pagination halted.
    assert request_count["n"] == 1
    assert stop.calls == 2
    assert [item["id"] for item in items] == [101]


def test_fetch_collection_items_should_stop_before_first_page(monkeypatch):
    scraper = CivitaiPrivateScraper.__new__(CivitaiPrivateScraper)
    monkeypatch.setattr(
        scraper, "_debug_session_token", MagicMock(), raising=False
    )

    def fail_request(collection_id, cursor, debug):
        raise AssertionError("No API request should happen when stopped")

    monkeypatch.setattr(scraper, "_make_collection_request", fail_request)

    items = scraper.fetch_collection_items(
        1234, None, debug=False, should_stop=lambda: True
    )

    assert items == []


def test_phase1_marks_remaining_cancelled_when_cancel_already_requested(monkeypatch):
    """Phase-1 of _process_civitai_image_ids must not touch the network or DB
    when cancellation is already requested: every image gets a cancelled
    result and the executor phase is skipped entirely."""
    import main as backend_main

    class FakeTaskContext:
        cancel_requested = True

        def mark_item(self, item_key, status, message=None):
            marked.append((item_key, status))

        def __getattr__(self, name):
            raise AssertionError(
                f"cancel path must not call TaskContext.{name}"
            )

    seen: list[int] = []
    marked: list[tuple[str, str]] = []

    def fail_existing(db, **kwargs):
        seen.append(kwargs.get("image_id"))
        raise AssertionError("existing-check must not run when cancelled")

    monkeypatch.setattr(
        backend_main, "_handle_existing_civitai_image", fail_existing
    )

    results, desired_ids = backend_main._process_civitai_image_ids(
        FakeTaskContext(),
        api=MagicMock(),
        image_ids=[11, 22, 33],
        attach_collection_id=None,
        item_key_prefix="coll:99",
    )

    assert seen == []
    assert desired_ids == set()
    assert len(results) == 3
    assert all(r["cancelled"] is True for r in results)
    assert all(r["error"] is None for r in results)
    assert [r["image_id"] for r in results] == [11, 22, 33]
    assert marked == [
        ("coll:99:image:11", "cancelled"),
        ("coll:99:image:22", "cancelled"),
        ("coll:99:image:33", "cancelled"),
    ]


def test_sync_job_cancel_still_builds_summary_tail(monkeypatch):
    """When the import pipeline raises TaskCancelledError mid-collection, the
    sync job must finalize the collection entry as cancelled, break out of the
    loop, and still run the summary tail (returning a result the task manager
    converts into a cancelled task)."""
    import main as backend_main
    from atelierai.task_manager import TaskCancelledError

    class RecordingTaskContext:
        def __init__(self) -> None:
            self.cancel_requested = False
            self.messages: list[str] = []
            self.metadata: dict = {}
            self.items: list[tuple[str, str]] = []
            self.totals: list[int] = []
            self.cancelled_with: list[tuple[object, str]] = []

        def set_message(self, message: str) -> None:
            self.messages.append(message)

        def set_total(self, total: int) -> None:
            self.totals.append(total)

        def set_metadata(self, key: str, value) -> None:
            self.metadata[key] = value

        def get_metadata(self, key: str, default=None):
            return self.metadata.get(key, default)

        def mark_item(self, item_key: str, status: str, message=None) -> None:
            self.items.append((item_key, status))

        def cancel(self, result=None, message=None) -> None:
            self.cancelled_with.append((result, message))

    ctx = RecordingTaskContext()

    monkeypatch.setattr(
        backend_main,
        "_fetch_civitai_user_image_collections",
        lambda api: [
            {"id": 555, "name": "Test Collection", "type": "image"},
        ],
    )

    def raise_cancelled(task_context, **kwargs):
        # Mirror reality: the manager flag is set, the pipeline observes it
        # and raises TaskCancelledError.
        ctx.cancel_requested = True
        raise TaskCancelledError("Task cancellation requested")

    monkeypatch.setattr(
        backend_main,
        "_run_civitai_collection_import_pipeline",
        raise_cancelled,
    )
    # Keep the sync job away from the network and steer it past the
    # empty/skip fast paths so it reaches the import pipeline.
    monkeypatch.setattr(backend_main, "CivitaiPrivateScraper", MagicMock())
    monkeypatch.setattr(
        backend_main,
        "_probe_civitai_collection_head",
        lambda scraper, collection_id: SimpleNamespace(
            image_ids=[101], has_more=True
        ),
    )
    fake_local_collection = SimpleNamespace(id=1)
    monkeypatch.setattr(
        backend_main,
        "_get_or_create_collection",
        lambda db, **kwargs: fake_local_collection,
    )
    monkeypatch.setattr(
        backend_main,
        "_inspect_local_civitai_collection_health",
        lambda db, local_collection_id: (0, False),
    )
    monkeypatch.setattr(
        backend_main,
        "_civitai_collection_requires_full_verify",
        lambda collection_row, **kwargs: (True, "test"),
    )
    monkeypatch.setattr(
        backend_main,
        "_snapshot_civitai_payload_retry_metrics",
        lambda api: {},
    )
    monkeypatch.setattr(
        backend_main,
        "_record_civitai_payload_retry_metrics",
        lambda task_context, metrics: None,
    )
    monkeypatch.setattr(
        backend_main, "_get_runtime_warnings", lambda: []
    )

    summary = backend_main._run_civitai_collection_sync_job(ctx, limit=None)

    # The task was finalized via cancel() with the summary as result.
    assert len(ctx.cancelled_with) == 1
    result, message = ctx.cancelled_with[0]
    assert message == "Cancelled"
    assert result == summary

    # Collection marked cancelled in structured progress.
    progress = ctx.metadata["collections_progress"]
    assert progress[0]["status"] == "cancelled"
    assert progress[0]["message"] == "Cancelled"

    # Item marked cancelled.
    assert ("collection:555", "cancelled") in ctx.items

    # Summary tail still ran: collections list present with cancelled state.
    collections = summary.get("collections", [])
    assert len(collections) == 1
    assert collections[0]["sync_state"] == "cancelled"
    assert collections[0]["civitai_collection_id"] == 555
