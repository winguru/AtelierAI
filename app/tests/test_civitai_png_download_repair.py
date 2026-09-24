# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/civitai-integration.md
# ──────────────────────────────────────────────────────────────────────────────
"""Tests for PNG structure repair at CivitAI download-validation time.

Covers the 2026-09-17 incident: a CivitAI fallback route served a
spec-invalid PNG (``tEXt`` chunk before ``IHDR``). PIL ingested it fine,
but browsers refuse to render chunk-misordered PNGs, so the image showed
broken in the UI until a manual Repair repacked it. The download pipeline
now repacks damaged PNGs in place via :class:`PngRepacker` so ingested
bytes are always renderable.
"""

import struct
import zlib
from types import SimpleNamespace

import pytest

# The download-validation pipeline now reads/writes the CDN media cache
# (image_resources/civitai_search_media). Isolate it per test so suites
# never pollute (or read) the real cache. NOTE: 'backend.main' imports the
# cache service as 'services.civitai_search_media' — patch that instance.
from services import civitai_search_media as _media_cache

from atelierai.civitai.http_client import CivitaiRequestError
from backend.main import (
    _download_civitai_image_with_validation,
    _repack_downloaded_png_if_needed,
)


@pytest.fixture(autouse=True)
def _isolated_media_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(
        _media_cache, "_cache_root", tmp_path / "civitai_search_media"
    )
    monkeypatch.setattr(_media_cache, "_image_locks", {})
    monkeypatch.setattr(
        _media_cache,
        "app_config",
        SimpleNamespace(IMAGE_RESOURCES_PATH=str(tmp_path)),
    )
    yield


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def _ihdr(width: int = 2, height: int = 2) -> bytes:
    return _png_chunk(
        b"IHDR",
        struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0),  # 8-bit RGB
    )


def _make_idat() -> bytes:
    # 2x2 RGB image: each row = filter byte + 2 pixels * 3 bytes
    rows = b""
    for _ in range(2):
        rows += b"\x00" + b"\xff\x00\x00" + b"\x00\xff\x00"
    return _png_chunk(b"IDAT", zlib.compress(rows))


def _healthy_png() -> bytes:
    return b"\x89PNG\r\n\x1a\n" + _ihdr() + _make_idat() + _png_chunk(b"IEND", b"")


def _misordered_png() -> bytes:
    """tEXt BEFORE IHDR — spec-invalid; browsers won't render it, PIL will."""
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"tEXt", b"parameters\x00fake generation params")
        + _ihdr()
        + _make_idat()
        + _png_chunk(b"IEND", b"")
    )


def _bad_crc_png() -> bytes:
    chunk = bytearray(_ihdr())
    chunk[-1] ^= 0xFF  # corrupt the stored CRC
    return b"\x89PNG\r\n\x1a\n" + bytes(chunk) + _make_idat() + _png_chunk(b"IEND", b"")


class TestRepackDownloadedPngIfNeeded:
    def test_healthy_png_untouched(self, tmp_path):
        raw = _healthy_png()
        path = tmp_path / "ok.png"
        path.write_bytes(raw)

        category, mime = _repack_downloaded_png_if_needed(path, "image", "image/png")

        assert (category, mime) == ("image", "image/png")
        assert path.read_bytes() == raw  # no rewrite for healthy files

    def test_misordered_png_repacked_in_place(self, tmp_path):
        path = tmp_path / "bad.png"
        path.write_bytes(_misordered_png())

        category, mime = _repack_downloaded_png_if_needed(path, "image", "image/png")

        assert (category, mime) == ("image", "image/png")
        repacked = path.read_bytes()
        # IHDR must now be the first chunk after the signature (offset 12:
        # after signature + 4-byte length prefix), and the generation-
        # parameters tEXt chunk must survive the repack.
        assert repacked[12:16] == b"IHDR"
        assert b"parameters" in repacked
        assert b"fake generation params" in repacked
        # Must decode cleanly with PIL (what ingest relies on).
        from PIL import Image

        with Image.open(path) as img:
            img.load()
            assert img.size == (2, 2)

    def test_bad_crc_png_repacked(self, tmp_path):
        path = tmp_path / "crc.png"
        path.write_bytes(_bad_crc_png())

        category, mime = _repack_downloaded_png_if_needed(path, "image", "image/png")

        assert (category, mime) == ("image", "image/png")
        assert path.read_bytes()[12:16] == b"IHDR"  # structure rebuilt

    def test_non_png_media_passes_through(self, tmp_path):
        path = tmp_path / "clip.webp"
        path.write_bytes(b"RIFF\x00\x00\x00\x00WEBP")

        category, mime = _repack_downloaded_png_if_needed(path, "image", "image/webp")

        assert (category, mime) == ("image", "image/webp")
        assert path.read_bytes()[:4] == b"RIFF"

    def test_garbage_png_raises_value_error(self, tmp_path):
        path = tmp_path / "garbage.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)

        with pytest.raises(ValueError):
            _repack_downloaded_png_if_needed(path, "image", "image/png")


class TestDownloadValidationPngGuard:
    def _target(self) -> dict:
        return {
            "image_url": "https://image.civitai.com/a/primary.png",
            "mime_type": "image/png",
            "declared_file_size": None,
            "original_filename": "primary.png",
            "artist_name": None,
            "source_url": "https://civitai.com/images/123",
        }

    def test_damaged_png_download_is_repacked_before_return(
        self, monkeypatch, tmp_path
    ):
        misordered_file = tmp_path / "downloaded.png"
        misordered_file.write_bytes(_misordered_png())

        monkeypatch.setattr(
            "backend.main._download_civitai_image",
            lambda **kwargs: misordered_file,
        )
        monkeypatch.setattr(
            "backend.main._build_civitai_image_candidate_urls",
            lambda t: ["https://image.civitai.com/a/primary.png"],
        )

        result = _download_civitai_image_with_validation(
            image_id=123, target=self._target()
        )

        returned_bytes = result.temp_path.read_bytes()
        assert returned_bytes[12:16] == b"IHDR"  # renderable chunk order
        assert result.selected_category == "image"
        assert result.selected_mime_type == "image/png"

    def test_unrepairable_png_fails_non_retryable(self, monkeypatch, tmp_path):
        garbage_file = tmp_path / "garbage.png"
        garbage_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)

        monkeypatch.setattr(
            "backend.main._download_civitai_image",
            lambda **kwargs: garbage_file,
        )
        monkeypatch.setattr(
            "backend.main._build_civitai_image_candidate_urls",
            lambda t: ["https://image.civitai.com/a/primary.png"],
        )

        with pytest.raises(CivitaiRequestError) as excinfo:
            _download_civitai_image_with_validation(
                image_id=123, target=self._target()
            )

        assert excinfo.value.retryable is False
        assert not garbage_file.exists()  # temp file cleaned up
