from __future__ import annotations

import json

from atelierai.civitai.civitai_api import CivitaiAPI
from atelierai.civitai.response_archive import CivitaiResponseArchive


def test_record_writes_history_and_latest_with_redaction(tmp_path):
    archive = CivitaiResponseArchive(tmp_path)

    history_path = archive.record(
        kind="trpc",
        endpoint="image.get",
        method="GET",
        url="https://civitai.red/api/trpc/image.get",
        request={"id": 42, "token": "secret", "nested": {"authorization": "Bearer secret"}},
        response={"id": 42, "url": "https://image.civitai.com/example"},
        status_code=200,
    )

    assert history_path.exists()
    records = list((tmp_path / "civitai_api_responses" / "latest").glob("*.json"))
    assert len(records) == 1
    payload = json.loads(records[0].read_text(encoding="utf-8"))
    assert payload["request"]["token"] == "[REDACTED]"
    assert payload["request"]["nested"]["authorization"] == "[REDACTED]"
    assert payload["response"]["id"] == 42
    assert payload["success"] is True


def test_latest_is_replayable_and_history_is_immutable(tmp_path):
    archive = CivitaiResponseArchive(tmp_path)
    request = {"queries": [{"q": "portrait", "offset": 0}]}

    first_path = archive.record(
        kind="search",
        endpoint="meilisearch.multi-search",
        request=request,
        response={"results": [{"hits": [1]}]},
        status_code=200,
    )
    second_path = archive.record(
        kind="search",
        endpoint="meilisearch.multi-search",
        request=request,
        response={"results": [{"hits": [2]}]},
        status_code=200,
    )

    assert first_path != second_path
    assert first_path.exists() and second_path.exists()
    latest = archive.read_latest(
        kind="search",
        endpoint="meilisearch.multi-search",
        request=request,
    )
    assert latest is not None
    assert latest["response"]["results"][0]["hits"] == [2]


def test_failure_record_preserves_status_and_error(tmp_path):
    archive = CivitaiResponseArchive(tmp_path)
    archive.record(
        kind="rest",
        endpoint="images",
        request={"id": 99},
        response={"message": "not found"},
        status_code=404,
        error="HTTP 404",
    )

    latest = archive.read_latest(kind="rest", endpoint="images", request={"id": 99})
    assert latest is not None
    assert latest["success"] is False
    assert latest["status_code"] == 404
    assert latest["error"] == "HTTP 404"


def test_api_cache_falls_back_to_latest_filesystem_snapshot(tmp_path):
    archive = CivitaiResponseArchive(tmp_path)
    request = {"id": 42, "authed": True}
    archive.record(
        kind="trpc",
        endpoint="image.get",
        request=request,
        response={"id": 42, "url": "https://image.civitai.com/example"},
        status_code=200,
    )
    cache_hits: list[str] = []
    api = object.__new__(CivitaiAPI)
    api._response_archive = archive
    api.http_client = type(
        "HttpClient",
        (),
        {"record_cache_hit": lambda _self, endpoint: cache_hits.append(endpoint)},
    )()
    api._load_cache_service = lambda: None
    api._load_session_factory = lambda: None

    result = api.get_cached_or_fetch("image.get", request, cache_only=True)

    assert result == {"id": 42, "url": "https://image.civitai.com/example"}
    assert cache_hits == ["image.get"]
