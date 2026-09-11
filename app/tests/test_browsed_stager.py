# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/civitai-integration.md
# ──────────────────────────────────────────────────────────────────────────────
"""Tests for staging harvested feed captures into the search-lab tables."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from database import Base
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

pytestmark = pytest.mark.filterwarnings(
    "ignore::pytest.PytestUnraisableExceptionWarning"
)


@pytest.fixture()
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _feed_response(items: list[dict], next_cursor: int | None = 1) -> dict:
    """Build a flat-array envelope the way CivitAI encodes it.

    Ints inside templates are *references* into the flat array (the
    deserializer resolves them); strings/None pass through inline.
    """
    flat: list = [{"nextCursor": None, "items": 2, "source": 1238}, [1], []]

    def _store(value):
        if isinstance(value, int) and not isinstance(value, bool):
            flat.append(value)
            return len(flat) - 1
        if isinstance(value, dict):
            nested = {}
            for k, v in value.items():
                nested[k] = _store(v)
            flat.append(nested)
            return len(flat) - 1
        return value  # strings / None / lists pass through inline

    offsets = []
    for it in items:
        template = {key: _store(val) for key, val in it.items()}
        flat.append(template)
        offsets.append(len(flat) - 1)
    flat[2] = offsets
    return {"result": {"data": json.dumps(flat)}}


ITEM_A = {
    "id": 111,
    "postId": 222,
    "url": "aaaabbbb-cccc-dddd-eeee-ffff00001111",
    "width": 864,
    "height": 1152,
    "hash": "UJEcqQWV",
    "nsfwLevel": 1,
    "type": "image",
    "username": "someartist",
    "reactionCount": 42,
    "stats": {"likeCountAllTime": 40, "collectedCountAllTime": 2},
    "baseModel": "Pony",
    "prompt": None,
    "tags": [],
}
ITEM_B = {
    "id": 333,
    "postId": 444,
    "url": "bbbbcccc-dddd-eeee-ffff-000011112222",
    "width": 1024,
    "height": 1024,
    "hash": "BBBBhash",
    "nsfwLevel": 2,
    "type": "image",
    "username": "otherartist",
    "reactionCount": 7,
    "stats": {"likeCountAllTime": 6},
    "baseModel": "SDXL 1.0",
    "prompt": None,
    "tags": [],
}


def _isolate_archive_root(monkeypatch, tmp_path: Path) -> None:
    """Point the default CivitaiResponseArchive root at tmp_path.

    The stager constructs its own archive (no root arg), which resolves via
    atelierai.config.IMAGE_RESOURCES_PATH — patch that so tests never touch
    the real archive on disk.
    """
    import atelierai.config as app_config

    monkeypatch.setattr(
        app_config, "IMAGE_RESOURCES_PATH", str(tmp_path), raising=False
    )


class TestDecodeFeedItems:
    def test_decodes_flat_array(self) -> None:
        from services.browsed_stager import _decode_feed_items

        parsed = _decode_feed_items(_feed_response([ITEM_A, ITEM_B]))
        assert isinstance(parsed, list) and len(parsed) == 2
        assert parsed[0]["id"] == 111
        assert parsed[1]["url"].startswith("bbbbcccc")

    def test_malformed_returns_empty(self) -> None:
        from services.browsed_stager import _decode_feed_items

        assert _decode_feed_items({"result": {"data": "not-json"}}) == []
        assert _decode_feed_items(None) == []
        assert _decode_feed_items({"unexpected": 1}) == []


class TestUpsertBrowsedImage:
    def test_creates_row_and_fills_fields(self, db_session) -> None:
        from models import CivitaiSearchImage
        from services.browsed_stager import _upsert_browsed_image

        img = _upsert_browsed_image(db_session, ITEM_A)
        assert img is not None
        assert img.civitai_image_id == 111
        assert img.post_id == 222
        assert img.uuid == ITEM_A["url"]
        assert img.artist_name == "someartist"
        assert img.reactions == 42
        assert img.likes == 40
        assert db_session.query(CivitaiSearchImage).count() == 1

    def test_fill_if_absent_never_clobbers(self, db_session) -> None:
        from services.browsed_stager import _upsert_browsed_image

        first = _upsert_browsed_image(db_session, ITEM_A)
        first.artist_name = "CuratedName"
        db_session.flush()

        second = _upsert_browsed_image(db_session, ITEM_A)
        assert second.id == first.id
        assert second.artist_name == "CuratedName"  # preserved

    def test_missing_id_returns_none(self, db_session) -> None:
        from services.browsed_stager import _upsert_browsed_image

        assert _upsert_browsed_image(db_session, {"postId": 5}) is None


class TestStageHarvestedFeeds:
    def test_stages_archive_files(self, tmp_path: Path, db_session, monkeypatch) -> None:
        from models import CivitaiSearchImageLink
        from services.browsed_stager import stage_harvested_feeds

        from atelierai.civitai.response_archive import CivitaiResponseArchive

        archive = CivitaiResponseArchive(root=tmp_path)
        archive.record(
            kind="harvested",
            endpoint="image.getInfinite",
            request={"json": {"period": "AllTime"}},
            response=_feed_response([ITEM_A, ITEM_B]),
            method="GET",
            status_code=200,
        )
        _isolate_archive_root(monkeypatch, tmp_path)
        monkeypatch.setattr(
            "services.browsed_stager._stager_state_path",
            lambda: tmp_path / "browsed_staging_state.json",
        )

        out = stage_harvested_feeds(db_session)
        assert out["ok"] is True
        assert out["files_staged"] == 1
        assert out["images_upserted"] == 2
        assert out["links_created"] == 2

        links = (
            db_session.query(CivitaiSearchImageLink)
            .filter(CivitaiSearchImageLink.rating.is_(None))
            .all()
        )
        assert len(links) == 2
        assert all(link.search_id is None for link in links)

    def test_idempotent_rerun(self, tmp_path: Path, db_session, monkeypatch) -> None:
        from services.browsed_stager import stage_harvested_feeds

        from atelierai.civitai.response_archive import CivitaiResponseArchive

        archive = CivitaiResponseArchive(root=tmp_path)
        archive.record(
            kind="harvested",
            endpoint="image.getInfinite",
            request={"json": {"period": "AllTime"}},
            response=_feed_response([ITEM_A]),
            method="GET",
            status_code=200,
        )
        _isolate_archive_root(monkeypatch, tmp_path)
        monkeypatch.setattr(
            "services.browsed_stager._stager_state_path",
            lambda: tmp_path / "browsed_staging_state.json",
        )

        first = stage_harvested_feeds(db_session)
        second = stage_harvested_feeds(db_session)
        assert first["links_created"] == 1
        assert second["files_staged"] == 0
        assert second["links_created"] == 0

    def test_respects_existing_rating_links(
        self, tmp_path: Path, db_session, monkeypatch
    ) -> None:
        """An image already rated via search must not get a second link."""
        from models import CivitaiSearchImageLink
        from services.browsed_stager import (
            _upsert_browsed_image,
            stage_harvested_feeds,
        )

        from atelierai.civitai.response_archive import CivitaiResponseArchive

        archive = CivitaiResponseArchive(root=tmp_path)
        archive.record(
            kind="harvested",
            endpoint="image.getInfinite",
            request={"json": {"period": "AllTime"}},
            response=_feed_response([ITEM_A]),
            method="GET",
            status_code=200,
        )
        _isolate_archive_root(monkeypatch, tmp_path)
        monkeypatch.setattr(
            "services.browsed_stager._stager_state_path",
            lambda: tmp_path / "browsed_staging_state.json",
        )

        # Pre-rate image 111 as keep (standalone link, search_id NULL)
        img = _upsert_browsed_image(db_session, ITEM_A)
        db_session.add(
            CivitaiSearchImageLink(
                image_id=img.id, search_id=None, rating="keep", is_excluded=False
            )
        )
        db_session.commit()

        out = stage_harvested_feeds(db_session)
        assert out["images_upserted"] == 1
        assert out["links_created"] == 0  # rating link already exists
        links = (
            db_session.query(CivitaiSearchImageLink)
            .filter(CivitaiSearchImageLink.image_id == img.id)
            .all()
        )
        assert len(links) == 1 and links[0].rating == "keep"
