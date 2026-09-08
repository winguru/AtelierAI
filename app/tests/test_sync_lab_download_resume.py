# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/civitai-sync-tasks.md
# 🀄 docs: app/docs/memories/civitai-integration.md
# ──────────────────────────────────────────────────────────────────────────────
"""Tests for Sync Lab download resume + fail-open preview variant fetch.

Covers the 2026-09-08 interrupted-ingest incident:

1. ``sync_lab_download`` must reuse prepared imports whose temp files still
   exist on disk (persisted via ``SyncSession.prepared_imports``) instead of
   re-downloading from the CDN after an app restart.
2. ``_preserve_civitai_source_variant`` must fail open when its preview
   variant fetch (the original-resolution webm) hits a CDN error — the
   already-successful library ingest must never roll back because a
   best-effort enrichment download failed (this exact path rolled back the
   first video ingest and tripped the 503 flag cooldown).
"""

from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar
from unittest.mock import patch

import main
import pytest

from atelierai.civitai.http_client import CivitaiRequestError


@pytest.fixture(autouse=True)
def _clean_prepared_store():
    main._sync_lab_prepared.clear()
    yield
    main._sync_lab_prepared.clear()


def _make_prepared(image_id: int, temp_path: Path | None) -> main._PreparedCivitaiImport:
    return main._PreparedCivitaiImport(
        image_id=image_id,
        image_url=f"https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA/uuid-{image_id}/original=true/f{image_id}.webm",
        mime_type="video/webm",
        declared_file_size=12345,
        preview_image_url=f"https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA/uuid-{image_id}/original=true/f{image_id}.webm",
        original_filename=f"f{image_id}.webm",
        artist_name="tester",
        source_url=f"https://civitai.red/images/{image_id}",
        temp_path=temp_path,
        civitai_uuid=f"uuid-{image_id}",
        civitai_hash=None,
        raw_basic_info={"id": image_id},
        raw_generation_data={},
        author_id=1,
        author_deleted=False,
        author_original_name=None,
        civitai_post_id=None,
        civitai_post_title=None,
        civitai_post_index=None,
        raw_tag_records=None,
    )


class TestRestorePreparedFromSession:
    def test_restores_entries_with_intact_temp_files(self, tmp_path):
        temp_file = tmp_path / "temp_civitai_100_abc"
        temp_file.write_bytes(b"fake-video-bytes")
        persisted = {
            "100": {
                "image_id": 100,
                "image_url": "https://example.invalid/100",
                "mime_type": "video/webm",
                "declared_file_size": 5,
                "preview_image_url": None,
                "original_filename": "f100.webm",
                "artist_name": None,
                "source_url": "https://civitai.red/images/100",
                "temp_path": str(temp_file),
                "civitai_uuid": "uuid-100",
                "civitai_hash": None,
                "raw_basic_info": None,
                "raw_generation_data": None,
                "author_id": None,
                "author_deleted": False,
                "author_original_name": None,
                "civitai_post_id": None,
                "civitai_post_title": None,
                "civitai_post_index": None,
                "raw_tag_records": None,
            }
        }

        class _FakeSyncSession:
            prepared_imports = persisted

        fake_db = SimpleNamespace(
            query=lambda *a, **k: SimpleNamespace(
                filter=lambda *a, **k: SimpleNamespace(first=lambda: _FakeSyncSession())
            ),
            close=lambda: None,
        )

        with patch.object(main, "SessionLocal", lambda: fake_db):
            restored = main._restore_sync_lab_prepared_from_session("sess-1")

        assert restored == 1
        assert 100 in main._sync_lab_prepared
        assert main._sync_lab_prepared[100].temp_path == temp_file

    def test_skips_entries_with_missing_temp_files(self, tmp_path):
        vanished = tmp_path / "temp_civitai_200_gone"
        persisted = {
            "200": {
                "image_id": 200,
                "image_url": "https://example.invalid/200",
                "mime_type": "video/webm",
                "declared_file_size": 5,
                "preview_image_url": None,
                "original_filename": "f200.webm",
                "artist_name": None,
                "source_url": "https://civitai.red/images/200",
                "temp_path": str(vanished),
                "civitai_uuid": None,
                "civitai_hash": None,
                "raw_basic_info": None,
                "raw_generation_data": None,
                "author_id": None,
                "author_deleted": False,
                "author_original_name": None,
                "civitai_post_id": None,
                "civitai_post_title": None,
                "civitai_post_index": None,
                "raw_tag_records": None,
            }
        }

        class _FakeSyncSession:
            prepared_imports = persisted

        fake_db = SimpleNamespace(
            query=lambda *a, **k: SimpleNamespace(
                filter=lambda *a, **k: SimpleNamespace(first=lambda: _FakeSyncSession())
            ),
            close=lambda: None,
        )

        with patch.object(main, "SessionLocal", lambda: fake_db):
            restored = main._restore_sync_lab_prepared_from_session("sess-1")

        assert restored == 0
        assert 200 not in main._sync_lab_prepared


class TestPreviewVariantFailOpen:
    def test_variant_fetch_failure_does_not_raise(self, tmp_path):
        """A CDN error fetching the preview variant must not propagate."""
        prepared = _make_prepared(300, None)

        # Fake DB image: actual ingested file is a video on disk.
        video_file = tmp_path / "abc123.mp4"
        video_file.write_bytes(b"\x00\x00\x00\x18ftypmp42fake")

        image = SimpleNamespace(
            id=55,
            mimetype="video/mp4",
            file_path="abc123.mp4",
            file_hash="abc123",
            json_metadata={},
        )

        class _Query:
            def __init__(self, result):
                self._result = result

            def filter(self, *a, **k):
                return self

            def first(self):
                return self._result

        fake_db = SimpleNamespace(query=lambda *a, **k: _Query(image))

        def _raise_503(*args, **kwargs):
            raise CivitaiRequestError(
                "CDN 503 during cooldown",
                status_code=503,
                retryable=True,
            )

        fake_client = SimpleNamespace(request=_raise_503)
        fake_api = SimpleNamespace(http_client=fake_client)

        with (
            patch.object(main, "IMAGE_LIBRARY_PATH", str(tmp_path)),
            patch.object(main, "IMAGE_RESOURCES_PATH", str(tmp_path / "resources")),
            patch.object(main.CivitaiAPI, "get_instance", staticmethod(lambda: fake_api)),
        ):
            # Must NOT raise — fail-open enrichment.
            main._preserve_civitai_source_variant(
                fake_db, prepared=prepared, image_db_id=55
            )

    def test_variant_fetch_success_still_works(self, tmp_path):
        """Sanity: a successful preview fetch still writes variant metadata."""
        prepared = _make_prepared(301, None)

        video_file = tmp_path / "def456.mp4"
        video_file.write_bytes(b"\x00\x00\x00\x18ftypmp42fake")

        image = SimpleNamespace(
            id=56,
            mimetype="video/mp4",
            file_path="def456.mp4",
            file_hash="def456",
            json_metadata={},
            file_name="def456.mp4",
            source_url="https://civitai.red/images/301",
            source_site=None,
            license_id=None,
            file_size=len(b"\x00\x00\x00\x18ftypmp42fake"),
            width=None,
            height=None,
            date_created=None,
            date_modified=None,
            artist_id=None,
        )

        class _Query:
            def __init__(self, result):
                self._result = result

            def filter(self, *a, **k):
                return self

            def first(self):
                return self._result

        fake_db = SimpleNamespace(query=lambda *a, **k: _Query(image))

        # Fake JPEG preview response.
        class _FakeResponse:
            headers: ClassVar[dict] = {"Content-Type": "image/jpeg"}
            body = b"\xff\xd8fakejpeg"

            def iter_content(self, chunk_size=None):
                yield self.body

            def close(self):
                pass

        fake_client = SimpleNamespace(request=lambda *a, **k: _FakeResponse())
        fake_api = SimpleNamespace(http_client=fake_client)

        resources_root = tmp_path / "resources"
        with (
            patch.object(main, "IMAGE_LIBRARY_PATH", str(tmp_path)),
            patch.object(main, "IMAGE_RESOURCES_PATH", str(resources_root)),
            patch.object(main.CivitaiAPI, "get_instance", staticmethod(lambda: fake_api)),
        ):
            main._preserve_civitai_source_variant(
                fake_db, prepared=prepared, image_db_id=56
            )

        # A variant file + its JSON metadata sidecar should exist.
        variant_dir = resources_root / "civitai_source_variants"
        assert variant_dir.exists()
        variants = list(variant_dir.glob("*.jpg"))
        assert len(variants) == 1
        assert variants[0].with_suffix(".jpg.json").exists()
