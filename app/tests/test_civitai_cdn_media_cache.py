# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/civitai-cache.md
# 🀄 docs: app/docs/memories/civitai-sync-tasks.md
# ──────────────────────────────────────────────────────────────────────────────
"""Tests for CDN media cache read-through/write-through in download validation.

Covers the caching added 2026-09-17: repeated downloads of the same CivitAI
asset (Search Lab preserve → Sync Lab ingest, or any re-ingest) previously
re-fetched from the CDN every time. ``_download_civitai_image_with_validation``
now checks the per-image media cache first (instant, zero rate-limit
pressure) and records every verified download into it.

1. Cache hit: no CDN request; temp file is a COPY (ingest moves its temp).
2. Cache miss → CDN download → write-through records the asset.
3. Second call after write-through hits the cache.
4. Video-declared targets never fall back to a cached static image.
5. ``record_ingested_media`` is idempotent (existing record wins).
"""

import json
from types import SimpleNamespace

import main
import pytest
from main import _download_civitai_image_with_validation

# main.py does `from services.civitai_search_media import ...` — patch THAT
# module instance ('services.*'), NOT 'backend.services.*' (with both app/
# and app/backend on PYTHONPATH they are two distinct module objects, and
# only the former is wired into main's functions).
from services import civitai_search_media as media_cache


@pytest.fixture(autouse=True)
def _isolated_cache(monkeypatch, tmp_path):
    """Redirect the media cache root AND the absolute-path base to a temp dir.

    PreservedSearchMedia.absolute_path derives from
    ``app_config.IMAGE_RESOURCES_PATH`` (module-level reference), so both the
    cache root and that base must move together for records to resolve.
    """
    cache_root = tmp_path / "civitai_search_media"
    monkeypatch.setattr(media_cache, "_cache_root", cache_root)
    monkeypatch.setattr(media_cache, "_image_locks", {})
    monkeypatch.setattr(
        media_cache,
        "app_config",
        SimpleNamespace(IMAGE_RESOURCES_PATH=str(tmp_path)),
    )
    yield


def _minimal_png() -> bytes:
    import struct
    import zlib

    def chunk(ctype: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(ctype + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + ctype + data + struct.pack(">I", crc)

    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0))
    rows = b"\x00" + b"\xff\x00\x00" + b"\x00\xff\x00"
    idat = chunk(b"IDAT", zlib.compress(rows * 2))
    return b"\x89PNG\r\n\x1a\n" + ihdr + idat + chunk(b"IEND", b"")


def _image_target() -> dict:
    return {
        "image_id": 4242,
        "image_url": "https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA/uuid-4242/original=true/f4242.png",
        "mime_type": "image/png",
        "declared_file_size": None,
        "original_filename": "f4242.png",
        "artist_name": None,
        "source_url": "https://civitai.red/images/4242",
    }


def _video_target() -> dict:
    return {
        "image_id": 777,
        "image_url": "https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA/uuid-777/original=true/f777.webm",
        "mime_type": "video/webm",
        "declared_file_size": None,
        "original_filename": "f777.webm",
        "artist_name": None,
        "source_url": "https://civitai.red/images/777",
    }


def _prime_cache(image_id: int, payload: bytes, mime: str = "image/png") -> None:
    """Insert a record the way preserve_search_media would have."""
    from datetime import datetime, timezone

    cache_root = media_cache._cache_root
    directory = cache_root / str(image_id)
    directory.mkdir(parents=True, exist_ok=True)
    suffix = ".png"
    destination = directory / f"original{suffix}"
    destination.write_bytes(payload)
    record = {
        "image_id": image_id,
        "relative_path": str(destination.relative_to(media_cache._cache_root.parent)),
        "mime_type": mime,
        "sha256": "0" * 64,
        "source_url": "https://image.civitai.com/x/original=true/a.png",
        "size": len(payload),
        "saved_at": datetime.now(timezone.utc).isoformat(),
    }
    (directory / "media.json").write_text(json.dumps(record), encoding="utf-8")


class TestCacheReadThrough:
    def test_cache_hit_skips_cdn(self, monkeypatch):
        _prime_cache(4242, _minimal_png())
        calls = []
        monkeypatch.setattr(
            main, "_download_civitai_image",
            lambda **kwargs: calls.append(kwargs) or (_ for _ in ()).throw(
                AssertionError("CDN must not be hit on a cache hit")
            ),
        )

        result = _download_civitai_image_with_validation(
            image_id=4242, target=_image_target()
        )

        assert calls == []
        assert result.selected_url.startswith("cache://")
        assert result.selected_category == "image"
        assert result.selected_mime_type == "image/png"
        # Caller gets a usable temp COPY (not the cache file itself — ingest
        # moves its temp into the library).
        assert result.temp_path.exists()
        assert result.temp_path != media_cache.get_cached_media_path(4242)[0]
        assert result.temp_path.read_bytes() == _minimal_png()
        result.temp_path.unlink()

    def test_cached_file_survives_temp_cleanup(self, monkeypatch):
        payload = _minimal_png()
        _prime_cache(4242, payload)

        result = _download_civitai_image_with_validation(
            image_id=4242, target=_image_target()
        )
        from main import _cleanup_temp_file

        _cleanup_temp_file(result.temp_path)

        # The cache entry itself is untouched.
        cached = media_cache.get_cached_media_path(4242)
        assert cached is not None
        assert cached[0].read_bytes() == payload

    def test_video_target_ignores_static_cached_image(self, monkeypatch):
        # A cached STATIC image for a video-declared asset must not satisfy
        # the download (mirrors the live mismatch-handling rules).
        _prime_cache(777, _minimal_png())

        def fake_download(*, image_url, image_id, mime_type, declared_file_size):
            out = media_cache._cache_root / f"dl_{image_id}.webm"
            out.write_bytes(b"\x1a\x45\xdf\xa3" + b"\x00" * 64)  # webm magic
            return out

        monkeypatch.setattr(main, "_download_civitai_image", fake_download)
        monkeypatch.setattr(
            main, "_build_civitai_video_candidate_urls", lambda t: [t["image_url"]]
        )
        monkeypatch.setattr(
            main, "_build_civitai_image_candidate_urls", lambda t: [t["image_url"]]
        )

        result = _download_civitai_image_with_validation(
            image_id=777, target=_video_target()
        )
        assert not result.selected_url.startswith("cache://")
        assert result.selected_category == "video"
        result.temp_path.unlink(missing_ok=True)


class TestCacheWriteThrough:
    def test_successful_download_is_recorded(self, monkeypatch, tmp_path):
        def fake_download(*, image_url, image_id, mime_type, declared_file_size):
            out = tmp_path / f"dl_{image_id}.png"
            out.write_bytes(_minimal_png())
            return out

        monkeypatch.setattr(main, "_download_civitai_image", fake_download)
        monkeypatch.setattr(
            main, "_build_civitai_image_candidate_urls", lambda t: [t["image_url"]]
        )

        result = _download_civitai_image_with_validation(
            image_id=4242, target=_image_target()
        )
        assert not result.selected_url.startswith("cache://")

        cached = media_cache.get_cached_media_path(4242)
        assert cached is not None
        assert cached[0].read_bytes() == _minimal_png()
        assert cached[1] == "image/png"

    def test_second_download_hits_cache_after_write_through(self, monkeypatch, tmp_path):
        def fake_download(*, image_url, image_id, mime_type, declared_file_size):
            out = tmp_path / f"dl_{image_id}.png"
            out.write_bytes(_minimal_png())
            return out

        monkeypatch.setattr(main, "_download_civitai_image", fake_download)
        monkeypatch.setattr(
            main, "_build_civitai_image_candidate_urls", lambda t: [t["image_url"]]
        )

        first = _download_civitai_image_with_validation(
            image_id=4242, target=_image_target()
        )
        first.temp_path.unlink()

        second = _download_civitai_image_with_validation(
            image_id=4242, target=_image_target()
        )
        assert second.selected_url.startswith("cache://")
        assert second.temp_path.read_bytes() == _minimal_png()
        second.temp_path.unlink()

    def test_write_through_failure_does_not_break_download(self, monkeypatch, tmp_path):
        def fake_download(*, image_url, image_id, mime_type, declared_file_size):
            out = tmp_path / f"dl_{image_id}.png"
            out.write_bytes(_minimal_png())
            return out

        monkeypatch.setattr(main, "_download_civitai_image", fake_download)
        monkeypatch.setattr(
            main, "_build_civitai_image_candidate_urls", lambda t: [t["image_url"]]
        )
        monkeypatch.setattr(
            main, "record_ingested_media",
            lambda **kwargs: (_ for _ in ()).throw(RuntimeError("disk full")),
        )

        result = _download_civitai_image_with_validation(
            image_id=4242, target=_image_target()
        )
        # Download still succeeds despite the cache write failing.
        assert result.temp_path.exists()
        result.temp_path.unlink()


class TestRecordIngestedMedia:
    def test_idempotent_existing_record_wins(self, monkeypatch, tmp_path):
        _prime_cache(4242, _minimal_png())
        before = media_cache.get_preserved_search_media(4242)

        newer = tmp_path / "newer.png"
        newer.write_bytes(_minimal_png() + b"\x00trailing")
        record = media_cache.record_ingested_media(
            image_id=4242,
            media_path=newer,
            mime_type="image/png",
            sha256="f" * 64,
            source_url="https://other",
        )
        # Existing preserved record is returned, bytes NOT overwritten.
        assert record.sha256 == before.sha256
        cached = media_cache.get_cached_media_path(4242)[0]
        assert cached.read_bytes() == _minimal_png()
