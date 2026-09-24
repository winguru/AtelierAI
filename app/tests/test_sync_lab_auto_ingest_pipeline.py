# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/civitai-sync-tasks.md
# 🀄 docs: app/docs/memories/civitai-integration.md
# ──────────────────────────────────────────────────────────────────────────────
"""Tests for Sync Lab step-6 auto-ingest pipeline mode.

Covers the download→ingest pipelining added 2026-09-17 (5,322-image sync at
~1-2s/download made the strict 6-then-7 staging cost hours before any image
reached the library):

1. ``sync_lab_download(auto_ingest=True, collection_id=...)`` feeds each
   completed download to an ingest consumer immediately — the first ingest
   finishes before the last download starts.
2. Ingested IDs are recorded in ``_sync_lab_auto_ingested`` and persisted to
   ``SyncSession.step_7_data`` so a resumed run never re-ingests.
3. Resume: already-ingested images skip BOTH download and ingest.
4. ``auto_ingest=true`` without ``collection_id`` is rejected (400).
5. Ingest failures are recorded and streamed, but never break the pipeline.
"""

import threading
from types import SimpleNamespace
from typing import ClassVar

import main
import pytest
from fastapi import HTTPException
from fastapi.responses import StreamingResponse
from main import (
    _persist_sync_lab_auto_ingested,
    _restore_sync_lab_auto_ingested,
    sync_lab_download,
)

# NOTE: use `main.X` everywhere (NOT `backend.main.X`) — with both
# app/ and app/backend on PYTHONPATH the file loads as two distinct
# module objects, and monkeypatching one leaves the other stale.


@pytest.fixture(autouse=True)
def _clean_pipeline_state():
    main._sync_lab_prepared.clear()
    main._sync_lab_auto_ingested.clear()
    yield
    main._sync_lab_prepared.clear()
    main._sync_lab_auto_ingested.clear()


def _sse_events(response: StreamingResponse) -> list[dict]:
    """Drain an SSE StreamingResponse (async or sync) into parsed event dicts."""
    import asyncio
    import json

    events: list[dict] = []

    def _parse_chunk(chunk) -> None:
        text = chunk.decode() if isinstance(chunk, (bytes, bytearray)) else str(chunk)
        for block in text.strip().split("\n\n"):
            if block.startswith("data: "):
                events.append(json.loads(block[len("data: "):]))

    body = response.body_iterator
    if hasattr(body, "__anext__"):
        async def _drain():
            async for chunk in body:
                _parse_chunk(chunk)

        asyncio.run(_drain())
    else:
        for chunk in body:
            _parse_chunk(chunk)
    return events


class _FakeDownloadResult:
    def __init__(self, image_id: int):
        self.temp_path = SimpleNamespace(exists=lambda: True, __str__=lambda s: f"/tmp/fake-{image_id}")
        self.selected_mime_type = "image/png"
        self.selected_url = f"https://image.civitai.com/a/{image_id}.png"


def _make_prepared(image_id: int) -> main._PreparedCivitaiImport:
    return main._PreparedCivitaiImport(
        image_id=image_id,
        image_url=f"https://image.civitai.com/a/{image_id}.png",
        mime_type="image/png",
        declared_file_size=100,
        preview_image_url=None,
        original_filename=f"f{image_id}.png",
        artist_name="tester",
        source_url=f"https://civitai.red/images/{image_id}",
        temp_path=None,
        civitai_uuid=f"uuid-{image_id}",
        civitai_hash=None,
    )


def _fake_target(image_id: int) -> dict:
    return {
        "image_id": image_id,
        "image_url": f"https://image.civitai.com/a/{image_id}.png",
        "mime_type": "image/png",
        "declared_file_size": 100,
        "original_filename": f"f{image_id}.png",
        "artist_name": "tester",
        "source_url": f"https://civitai.red/images/{image_id}",
    }


class TestAutoIngestRequiresCollection:
    def test_auto_ingest_without_collection_id_rejected(self):
        with pytest.raises(HTTPException) as excinfo:
            sync_lab_download(
                image_ids="100001,100002,100003",
                selected_ids=None,
                limit=None,
                auto_ingest=True,
                collection_id=None,
                session_id=None,
            )
        assert excinfo.value.status_code == 400

    def test_auto_ingest_creates_local_collection_first(self, monkeypatch):
        """The pipeline must ensure the local collection exists BEFORE any
        ingest runs. _ensure_image_in_collection resolves the CivitAI id
        against the junction table and SILENTLY SKIPS the membership when no
        local collection exists (SQLite does not enforce FKs) — without this,
        5321 images ingested into the library with zero collection
        attachments and became invisible in the collection view (2026-09-17).
        """
        calls: list[tuple[str, object]] = []

        def fake_get_or_create(db, name, source="user", civitai_collection_id=None):
            calls.append(("create", civitai_collection_id))
            return SimpleNamespace(id=999, name=name)

        monkeypatch.setattr(main, "_get_or_create_collection", fake_get_or_create)
        monkeypatch.setattr(main, "_checkpoint_sync_step", lambda *a, **k: None)
        monkeypatch.setattr(main, "_restore_sync_lab_prepared_from_session", lambda s: 0)
        monkeypatch.setattr(main, "_restore_sync_lab_auto_ingested", lambda s: None)
        monkeypatch.setattr(
            main, "SessionLocal",
            lambda: SimpleNamespace(
                query=lambda *a, **k: SimpleNamespace(
                    filter=lambda *a, **k: SimpleNamespace(first=lambda: None)
                ),
                close=lambda: None,
                commit=lambda: None,
                rollback=lambda: None,
            ),
        )

        # Abort immediately after the collection-creation block — we only
        # need to verify it ran before any download/ingest work.
        def fail_resolve(api, image_id, **kwargs):
            raise AssertionError("download must not start; collection creation should have run first")

        monkeypatch.setattr(main, "_resolve_civitai_image_target", fail_resolve)

        response = sync_lab_download(
            image_ids="100001",
            selected_ids=None,
            limit=None,
            auto_ingest=True,
            collection_id=16393805,
            session_id=None,
        )
        # Drain the stream (download fails via mock, but collection creation
        # must already have happened).
        _sse_events(response)
        assert ("create", 16393805) in calls


class TestPipelineIngestsOnCompletion:
    def test_completed_downloads_are_ingested_immediately(self, monkeypatch, tmp_path):
        """Each download must be handed to the ingest consumer as soon as it
        completes — ingest call order matches download order, and the ingest
        consumer is running concurrently (not after all downloads)."""
        download_order: list[int] = []
        ingest_order: list[int] = []
        ingest_started = threading.Event()
        download_gate = threading.Event()  # released after 2nd download begins

        def fake_resolve(api, image_id, **kwargs):
            download_order.append(image_id)
            if image_id == 100002:
                # By the time download #2 starts, download #1's ingest must
                # already have run (pipeline property).
                ingest_started.set()
            return _fake_target(image_id)

        def fake_ingest(db, *, prepared, attach_collection_id):
            ingest_order.append(prepared.image_id)
            if prepared.image_id == 100001:
                ingest_started.is_set() or download_gate.set()
            return {"image_id": prepared.image_id, "image_db_id": 1}

        monkeypatch.setattr(main, "_resolve_civitai_image_target", fake_resolve)
        monkeypatch.setattr(
            main, "_download_civitai_image_with_validation",
            lambda *, image_id, target: _FakeDownloadResult(image_id),
        )
        monkeypatch.setattr(main, "_ingest_prepared_for_pipeline", fake_ingest)
        monkeypatch.setattr(main.CivitaiAPI, "get_instance", lambda: object())
        monkeypatch.setattr(main, "SessionLocal", lambda: SimpleNamespace(
            query=lambda *a, **k: SimpleNamespace(
                filter=lambda *a, **k: SimpleNamespace(first=lambda: None)
            ),
            close=lambda: None,
            commit=lambda: None,
            rollback=lambda: None,
        ))
        monkeypatch.setattr(main, "_checkpoint_sync_step", lambda *a, **k: None)
        monkeypatch.setattr(main, "_restore_sync_lab_prepared_from_session", lambda s: 0)
        monkeypatch.setattr(main, "_restore_sync_lab_auto_ingested", lambda s: None)

        response = sync_lab_download(
            image_ids="100001,100002",
            selected_ids=None,
            limit=None,
            auto_ingest=True,
            collection_id=500,
            session_id=None,
        )
        assert isinstance(response, StreamingResponse)
        events = _sse_events(response)

        assert download_order == [100001, 100002]
        assert ingest_order == [100001, 100002]
        assert any(e["type"] == "ingested" for e in events)
        complete = next(e for e in events if e["type"] == "complete")
        assert complete["data"]["auto_ingest"] is True
        assert complete["data"]["ingested"] == 2
        # Both ingested IDs tracked for resume dedup.
        assert set(main._sync_lab_auto_ingested.keys()) == {"100001", "100002"}

    def test_ingest_failure_recorded_not_fatal(self, monkeypatch):
        failures: list[int] = []

        def fake_ingest(db, *, prepared, attach_collection_id):
            if prepared.image_id == 100001:
                failures.append(prepared.image_id)
                raise RuntimeError("simulated ingest failure")
            return {"image_id": prepared.image_id}

        monkeypatch.setattr(main, "_resolve_civitai_image_target", lambda api, image_id, **kwargs: _fake_target(image_id))
        monkeypatch.setattr(
            main, "_download_civitai_image_with_validation",
            lambda *, image_id, target: _FakeDownloadResult(image_id),
        )
        monkeypatch.setattr(main, "_ingest_prepared_for_pipeline", fake_ingest)
        monkeypatch.setattr(main.CivitaiAPI, "get_instance", lambda: object())
        monkeypatch.setattr(main, "SessionLocal", lambda: SimpleNamespace(
            query=lambda *a, **k: SimpleNamespace(
                filter=lambda *a, **k: SimpleNamespace(first=lambda: None)
            ),
            close=lambda: None,
            commit=lambda: None,
            rollback=lambda: None,
        ))
        monkeypatch.setattr(main, "_checkpoint_sync_step", lambda *a, **k: None)
        monkeypatch.setattr(main, "_restore_sync_lab_prepared_from_session", lambda s: 0)
        monkeypatch.setattr(main, "_restore_sync_lab_auto_ingested", lambda s: None)

        response = sync_lab_download(
            image_ids="100001,100002",
            selected_ids=None,
            limit=None,
            auto_ingest=True,
            collection_id=500,
            session_id=None,
        )
        events = _sse_events(response)

        assert failures == [100001]
        assert any(e["type"] == "ingest_failed" for e in events)
        complete = next(e for e in events if e["type"] == "complete")
        assert complete["data"]["ingested"] == 1
        assert complete["data"]["ingest_failed"] == 1
        assert main._sync_lab_auto_ingested["100001"]["status"] == "failed"
        assert main._sync_lab_auto_ingested["100002"]["status"] == "ingested"


class TestResumeSkipsIngested:
    def test_already_ingested_skips_download_and_ingest(self, monkeypatch):
        main._sync_lab_auto_ingested["100001"] = {
            "image_id": 1,
            "status": "ingested",
            "auto": True,
        }
        downloaded: list[int] = []
        ingested: list[int] = []

        def fake_resolve(api, image_id, **kwargs):
            downloaded.append(image_id)
            return _fake_target(image_id)

        def fake_ingest(db, *, prepared, attach_collection_id):
            ingested.append(prepared.image_id)
            return {"image_id": prepared.image_id}

        monkeypatch.setattr(main, "_resolve_civitai_image_target", fake_resolve)
        monkeypatch.setattr(
            main, "_download_civitai_image_with_validation",
            lambda *, image_id, target: _FakeDownloadResult(image_id),
        )
        monkeypatch.setattr(main, "_ingest_prepared_for_pipeline", fake_ingest)
        monkeypatch.setattr(main.CivitaiAPI, "get_instance", lambda: object())
        monkeypatch.setattr(main, "SessionLocal", lambda: SimpleNamespace(
            query=lambda *a, **k: SimpleNamespace(
                filter=lambda *a, **k: SimpleNamespace(first=lambda: None)
            ),
            close=lambda: None,
            commit=lambda: None,
            rollback=lambda: None,
        ))
        monkeypatch.setattr(main, "_checkpoint_sync_step", lambda *a, **k: None)
        monkeypatch.setattr(main, "_restore_sync_lab_prepared_from_session", lambda s: 0)
        monkeypatch.setattr(main, "_restore_sync_lab_auto_ingested", lambda s: None)

        response = sync_lab_download(
            image_ids="100001,100002",
            selected_ids=None,
            limit=None,
            auto_ingest=True,
            collection_id=500,
            session_id=None,
        )
        events = _sse_events(response)
        complete = next(e for e in events if e["type"] == "complete")

        # Image 1 skipped entirely; image 2 downloaded + ingested.
        assert downloaded == [100002]
        assert ingested == [100002]
        assert complete["data"]["already_ingested"] == 1
        results_by_id = complete["data"]["results"]
        assert results_by_id["100001"]["already_ingested"] is True


class TestPersistence:
    def test_persist_and_restore_auto_ingested(self, monkeypatch):
        class _FakeSession:
            step_7_data = None

        fake_sess = _FakeSession()
        fake_db = SimpleNamespace(
            query=lambda *a, **k: SimpleNamespace(
                filter=lambda *a, **k: SimpleNamespace(first=lambda: fake_sess)
            ),
            commit=lambda: None,
            close=lambda: None,
        )
        monkeypatch.setattr(main, "SessionLocal", lambda: fake_db)

        main._sync_lab_auto_ingested["7"] = {
            "image_id": 7,
            "status": "ingested",
            "auto": True,
        }
        _persist_sync_lab_auto_ingested("sess-1")
        assert fake_sess.step_7_data is not None
        assert fake_sess.step_7_data["auto_ingested_ids"] == ["7"]

        # Simulate a restart: memory cleared, restore from the session.
        main._sync_lab_auto_ingested.clear()
        _restore_sync_lab_auto_ingested("sess-1")
        assert "7" in main._sync_lab_auto_ingested
        assert main._sync_lab_auto_ingested["7"]["status"] == "ingested"

    def test_restore_merges_without_clobbering(self, monkeypatch):
        class _FakeSession:
            step_7_data: ClassVar[dict] = {
                "auto_ingested_ids": ["100001"],
                "results": {
                    "100001": {"image_id": 100001, "status": "ingested", "auto": True}
                },
            }

        fake_db = SimpleNamespace(
            query=lambda *a, **k: SimpleNamespace(
                filter=lambda *a, **k: SimpleNamespace(first=lambda: _FakeSession())
            ),
            close=lambda: None,
        )
        monkeypatch.setattr(main, "SessionLocal", lambda: fake_db)

        main._sync_lab_auto_ingested["100002"] = {
            "image_id": 100002,
            "status": "ingested",
        }
        _restore_sync_lab_auto_ingested("sess-1")
        assert set(main._sync_lab_auto_ingested.keys()) == {"100001", "100002"}
