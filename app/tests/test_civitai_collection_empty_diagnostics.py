import json
from types import SimpleNamespace

import main
from atelierai.civitai.civitai import CivitaiPrivateScraper
from atelierai.civitai.civitai_api import CivitaiAPI


def _api(response, *, token="x" * 120):
    return SimpleNamespace(
        session_cookie=token,
        _make_raw_request=lambda *_args, **_kwargs: response,
    )


def _collection_response(*, collection=None, permissions=None):
    return {
        "result": {
            "data": {
                "json": {
                    "collection": collection,
                    "permissions": permissions,
                }
            }
        }
    }


def test_empty_collection_diagnosis_identifies_post_collection(monkeypatch):
    monkeypatch.setattr(
        main,
        "_validate_civitai_session_for_empty_collection",
        lambda _api: (True, False, "Authenticated"),
    )
    api = _api(
        _collection_response(
            collection={"id": 42, "type": "post", "name": "Drafts"},
            permissions={"read": True},
        )
    )

    diagnosis = main._diagnose_civitai_empty_collection(api, 42)

    assert diagnosis.kind == "post"
    assert diagnosis.collection_json["collection"]["name"] == "Drafts"


def test_empty_collection_diagnosis_reports_wrong_account(monkeypatch):
    monkeypatch.setattr(
        main,
        "_validate_civitai_session_for_empty_collection",
        lambda _api: (True, False, "Authenticated"),
    )
    api = _api(
        _collection_response(
            collection=None,
            permissions={"read": False, "isOwner": False, "collectionType": "image"},
        )
    )

    diagnosis = main._diagnose_civitai_empty_collection(api, 17582649)

    assert diagnosis.kind == "authorization"
    assert "configured CivitAI account cannot access" in diagnosis.message
    assert "account that owns or can view" in diagnosis.message
    assert api.session_cookie not in diagnosis.message


def test_empty_collection_diagnosis_reports_expired_session(monkeypatch):
    monkeypatch.setattr(
        main,
        "_validate_civitai_session_for_empty_collection",
        lambda _api: (False, True, "Token rejected by CivitAI (HTTP 401)."),
    )
    api = _api(_collection_response(collection=None, permissions={"read": False}))

    diagnosis = main._diagnose_civitai_empty_collection(api, 42)

    assert diagnosis.kind == "authentication"
    assert "missing or expired" in diagnosis.message
    assert "Refresh the CivitAI session" in diagnosis.message


def test_empty_collection_diagnosis_reports_readable_empty_collection(monkeypatch):
    monkeypatch.setattr(
        main,
        "_validate_civitai_session_for_empty_collection",
        lambda _api: (True, False, "Authenticated"),
    )
    api = _api(
        _collection_response(
            collection={"id": 42, "type": "image", "itemCount": 0},
            permissions={"read": True, "isOwner": True},
        )
    )

    diagnosis = main._diagnose_civitai_empty_collection(api, 42)

    assert diagnosis.kind == "empty"
    assert "accessible but contains no importable" in diagnosis.message


def test_empty_collection_diagnosis_flags_parser_mismatch(monkeypatch):
    monkeypatch.setattr(
        main,
        "_validate_civitai_session_for_empty_collection",
        lambda _api: (True, False, "Authenticated"),
    )
    api = _api(
        _collection_response(
            collection={"id": 42, "type": "image", "itemCount": 7},
            permissions={"read": True},
        )
    )

    diagnosis = main._diagnose_civitai_empty_collection(api, 42)

    assert diagnosis.kind == "response_mismatch"
    assert "reports 7 items" in diagnosis.message


def test_empty_collection_diagnosis_handles_partial_payload(monkeypatch):
    monkeypatch.setattr(
        main,
        "_validate_civitai_session_for_empty_collection",
        lambda _api: (True, False, "Authenticated"),
    )
    api = _api({"result": {"data": {"json": None}}})

    diagnosis = main._diagnose_civitai_empty_collection(api, 42)

    assert diagnosis.kind == "unavailable"
    assert "does not exist or is not available" in diagnosis.message


def test_collection_request_decodes_unwrapped_flat_array():
    flat_array = [
        {"nextCursor": 99, "items": 1},
        [2],
        {"id": 3, "name": 4},
        12345,
        "Example image",
    ]
    api = SimpleNamespace(
        default_params={"authed": True},
        base_url="https://civitai.red/api/trpc",
        _make_raw_request=lambda *_args, **_kwargs: json.dumps(flat_array),
        _deserialize_trpc_flat_array=lambda response: (
            CivitaiAPI._deserialize_trpc_flat_array(None, response)
        ),
        _build_trpc_payload=lambda payload: json.dumps({"json": payload}),
    )
    scraper = object.__new__(CivitaiPrivateScraper)
    scraper.api = api

    data, next_cursor = scraper._make_collection_request(17582649, None, False)

    assert data == {
        "items": [{"id": 12345, "name": "Example image"}],
        "nextCursor": 99,
    }
    assert next_cursor == 99


def test_collection_request_decodes_referenced_metadata_flat_array():
    flat_array = [
        {"nextCursor": 1, "items": 2},
        93492358,
        [3],
        {"id": 4, "name": 5},
        139377972,
        "Current format image",
    ]
    api = SimpleNamespace(
        default_params={"authed": True},
        base_url="https://civitai.red/api/trpc",
        _make_raw_request=lambda *_args, **_kwargs: {
            "result": {"data": json.dumps(flat_array)}
        },
        _deserialize_trpc_flat_array=lambda response: (
            CivitaiAPI._deserialize_trpc_flat_array(None, response)
        ),
        _build_trpc_payload=lambda payload: json.dumps({"json": payload}),
    )
    scraper = object.__new__(CivitaiPrivateScraper)
    scraper.api = api

    data, next_cursor = scraper._make_collection_request(17582649, None, False)

    assert data == {
        "items": [{"id": 139377972, "name": "Current format image"}],
        "nextCursor": 93492358,
    }
    assert next_cursor == 93492358
