"""Unit tests for civitai_cache_service.py.

Uses an in-memory SQLite database — no external dependencies required.
Run with:
    pytest app/tests/test_civitai_cache_service.py -v
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

# Ensure app/backend is on sys.path so relative imports work without install.
_BACKEND = Path(__file__).resolve().parent.parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from database import Base  # noqa: E402
from models import CivitaiApiCacheEntry  # noqa: E402
from services.civitai_cache_service import (  # noqa: E402
    build_request_key,
    canonical_hash,
    get_history,
    get_latest,
    record_response,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def db():
    """Provide a fresh in-memory SQLite session for each test."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


# ---------------------------------------------------------------------------
# canonical_hash tests
# ---------------------------------------------------------------------------


class TestCanonicalHash:
    def test_stable_across_key_order(self):
        """Hash must be identical regardless of dict key ordering."""
        a = canonical_hash({"b": 2, "a": 1})
        b = canonical_hash({"a": 1, "b": 2})
        assert a == b

    def test_none_maps_to_empty_object(self):
        """None serializes as {} so tombstone rows have a deterministic hash."""
        assert canonical_hash(None) == canonical_hash({})

    def test_different_values_produce_different_hashes(self):
        assert canonical_hash({"id": 1}) != canonical_hash({"id": 2})

    def test_returns_hex_string_of_length_64(self):
        h = canonical_hash({"x": 1})
        assert isinstance(h, str)
        assert len(h) == 64


# ---------------------------------------------------------------------------
# build_request_key tests
# ---------------------------------------------------------------------------


class TestBuildRequestKey:
    def test_image_get_id_only(self):
        assert build_request_key("image.get", {"id": 12345}) == "id=12345"

    def test_tag_get_votable_tags(self):
        key = build_request_key("tag.getVotableTags", {"id": 99, "type": "Image"})
        assert key == "id=99&type=Image"

    def test_missing_optional_field_omitted(self):
        # type is optional in tag.getVotableTags
        key = build_request_key("tag.getVotableTags", {"id": 99})
        assert key == "id=99"

    def test_none_payload_returns_empty_string(self):
        assert build_request_key("image.get", None) == ""

    def test_empty_payload_returns_empty_string(self):
        assert build_request_key("image.get", {}) == ""

    def test_unknown_endpoint_sorted_fallback(self):
        key = build_request_key("unknown.endpoint", {"z": 1, "a": 2})
        assert key == "a=2&z=1"

    def test_collection_get_all_user_no_user_id(self):
        """userId absent → empty string (unconstrained user listing)."""
        assert build_request_key("collection.getAllUser", {}) == ""

    def test_image_get_infinite_subset_of_fields(self):
        key = build_request_key(
            "image.getInfinite",
            {"collectionId": 42, "cursor": "abc", "sort": "Newest"},
        )
        assert "collectionId=42" in key
        assert "cursor=abc" in key
        assert "sort=Newest" in key


# ---------------------------------------------------------------------------
# record_response + get_latest tests
# ---------------------------------------------------------------------------


class TestRecordResponse:
    def test_first_insert_creates_latest_row(self, db):
        entry = record_response(
            db,
            endpoint="image.get",
            payload={"id": 1},
            response_json={"result": "data"},
        )
        assert entry is not None
        assert entry.id is not None
        assert entry.is_latest is True
        assert entry.prev_id is None
        assert entry.endpoint == "image.get"  # type: ignore[operator]
        assert entry.request_key == "id=1"  # type: ignore[operator]

    def test_identical_refetch_updates_timestamp_only(self, db):
        payload = {"id": 1}
        body = {"result": "data"}
        e1 = record_response(db, endpoint="image.get", payload=payload, response_json=body)
        assert e1 is not None
        original_id = e1.id

        # Force an earlier timestamp so we can verify it changed
        from datetime import UTC
        e1.fetched_at = datetime(2020, 1, 1, tzinfo=UTC).replace(tzinfo=None)  # type: ignore[assignment]
        db.commit()

        e2 = record_response(db, endpoint="image.get", payload=payload, response_json=body)
        assert e2 is not None

        assert e2.id == original_id, "No new row should be inserted for identical response"  # type: ignore[operator]
        assert e2.fetched_at > datetime(2020, 1, 2), "fetched_at must be updated"  # type: ignore[operator]
        count = db.query(CivitaiApiCacheEntry).count()
        assert count == 1

    def test_changed_response_appends_new_row(self, db):
        payload = {"id": 1}
        e1 = record_response(
            db, endpoint="image.get", payload=payload, response_json={"v": 1}
        )
        assert e1 is not None
        e2 = record_response(
            db, endpoint="image.get", payload=payload, response_json={"v": 2}
        )
        assert e2 is not None

        assert e2.id != e1.id  # type: ignore[operator]
        assert e2.is_latest is True
        assert e2.prev_id == e1.id  # type: ignore[operator]

        db.refresh(e1)
        assert e1.is_latest is False

        count = db.query(CivitaiApiCacheEntry).count()
        assert count == 2

    def test_excluded_endpoint_returns_none(self, db):
        result = record_response(
            db,
            endpoint="signals.getToken",
            payload=None,
            response_json={"token": "secret"},
        )
        assert result is None
        assert db.query(CivitaiApiCacheEntry).count() == 0

    def test_multi_search_excluded(self, db):
        result = record_response(
            db,
            endpoint="multi-search",
            payload={"q": "test"},
            response_json={"hits": []},
        )
        assert result is None

    def test_http_status_stored(self, db):
        entry = record_response(
            db,
            endpoint="image.get",
            payload={"id": 404},
            response_json=None,
            http_status=404,
        )
        assert entry is not None
        assert entry.http_status == 404  # type: ignore[operator]

    def test_tombstone_none_response_hashed_consistently(self, db):
        e1 = record_response(
            db, endpoint="image.get", payload={"id": 1}, response_json=None, http_status=404
        )
        assert e1 is not None
        # Re-fetch with same null response → should update only fetched_at
        e2 = record_response(
            db, endpoint="image.get", payload={"id": 1}, response_json=None, http_status=404
        )
        assert e2 is not None
        assert e1.id == e2.id, "Identical null responses must not create extra rows"  # type: ignore[operator]


# ---------------------------------------------------------------------------
# get_latest tests
# ---------------------------------------------------------------------------


class TestGetLatest:
    def test_returns_none_when_no_entry(self, db):
        assert get_latest(db, endpoint="image.get", request_key="id=999") is None

    def test_returns_latest_entry(self, db):
        record_response(db, endpoint="image.get", payload={"id": 5}, response_json={"v": 1})
        entry = get_latest(db, endpoint="image.get", request_key="id=5")
        assert entry is not None
        assert entry.is_latest is True

    def test_different_endpoints_independent(self, db):
        record_response(db, endpoint="image.get", payload={"id": 1}, response_json={"a": 1})
        record_response(
            db, endpoint="image.getGenerationData", payload={"id": 1}, response_json={"b": 2}
        )
        a = get_latest(db, endpoint="image.get", request_key="id=1")
        b = get_latest(db, endpoint="image.getGenerationData", request_key="id=1")
        assert a is not None
        assert b is not None
        assert a.id != b.id  # type: ignore[operator]


# ---------------------------------------------------------------------------
# get_history tests
# ---------------------------------------------------------------------------


class TestGetHistory:
    def test_returns_empty_list_when_no_entry(self, db):
        assert get_history(db, endpoint="image.get", request_key="id=1") == []

    def test_full_history_chain(self, db):
        payload = {"id": 1}
        record_response(db, endpoint="image.get", payload=payload, response_json={"v": 1})
        record_response(db, endpoint="image.get", payload=payload, response_json={"v": 2})
        record_response(db, endpoint="image.get", payload=payload, response_json={"v": 3})

        history = get_history(db, endpoint="image.get", request_key="id=1")
        assert len(history) == 3
        # newest first
        assert history[0].response_json["v"] == 3  # type: ignore[operator]
        assert history[1].response_json["v"] == 2  # type: ignore[operator]
        assert history[2].response_json["v"] == 1  # type: ignore[operator]

    def test_limit_parameter(self, db):
        payload = {"id": 1}
        for i in range(5):
            record_response(
                db, endpoint="image.get", payload=payload, response_json={"v": i}
            )
        history = get_history(db, endpoint="image.get", request_key="id=1", limit=2)
        assert len(history) == 2

    def test_prev_id_chain_is_consistent(self, db):
        payload = {"id": 1}
        e1 = record_response(db, endpoint="image.get", payload=payload, response_json={"v": 1})
        assert e1 is not None
        e2 = record_response(db, endpoint="image.get", payload=payload, response_json={"v": 2})
        assert e2 is not None
        e3 = record_response(db, endpoint="image.get", payload=payload, response_json={"v": 3})
        assert e3 is not None

        assert e3.prev_id == e2.id  # type: ignore[operator]
        assert e2.prev_id == e1.id  # type: ignore[operator]
        assert e1.prev_id is None
