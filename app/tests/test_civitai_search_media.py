from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from atelierai.civitai.http_client import CivitaiRequestError
from backend.services import civitai_search_media as media


class FakeHttpClient:
    def __init__(self, payload: bytes, failing_urls: set[str] | None = None) -> None:
        self.payload = payload
        self.failing_urls = failing_urls or set()
        self.calls: list[str] = []

    def download_to_temp(self, url, *, output_dir, prefix, suffix, **_kwargs):
        self.calls.append(url)
        if url in self.failing_urls:
            raise CivitaiRequestError("missing", status_code=404)
        path = Path(output_dir) / f"{prefix}fixture{suffix}"
        path.write_bytes(self.payload)
        return path


@pytest.fixture()
def media_root(tmp_path, monkeypatch):
    monkeypatch.setattr(media.app_config, "IMAGE_RESOURCES_PATH", str(tmp_path))
    monkeypatch.setattr(media, "_cache_root", tmp_path / "civitai_search_media")
    media._image_locks.clear()
    return tmp_path


def test_preserve_uses_fallback_and_reuses_local_file(media_root, monkeypatch):
    original = "https://image.civitai.com/example/original=true/example.jpg"
    fallback = "https://image.civitai.com/example/width=2048/example.jpg"
    client = FakeHttpClient(b"\xff\xd8\xfffixture", {original})
    monkeypatch.setattr(
        media.CivitaiAPI,
        "get_instance",
        lambda: type("API", (), {"http_client": client})(),
    )
    monkeypatch.setattr(media, "_candidate_urls", lambda _metadata: [original, fallback])

    first = media.preserve_search_media(123, {"image_url": original})
    second = media.preserve_search_media(123, {"image_url": original})

    assert first == second
    assert first.absolute_path.read_bytes() == b"\xff\xd8\xfffixture"
    assert first.source_url == fallback
    assert client.calls == [original, fallback]


def test_preserve_deduplicates_concurrent_downloads(media_root, monkeypatch):
    client = FakeHttpClient(b"\x89PNG\r\n\x1a\nfixture")
    monkeypatch.setattr(
        media.CivitaiAPI,
        "get_instance",
        lambda: type("API", (), {"http_client": client})(),
    )
    monkeypatch.setattr(media, "_candidate_urls", lambda _metadata: ["https://image.civitai.com/x.png"])

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: media.preserve_search_media(456, {}), range(2)))

    assert results[0] == results[1]
    assert client.calls == ["https://image.civitai.com/x.png"]


def test_candidate_urls_reject_non_civitai_hosts():
    candidates = media._candidate_urls(
        {
            "image_url": "https://example.com/private-file",
            "preserved_source_url": "http://image.civitai.com/insecure",
        }
    )

    assert candidates == []
