#!/usr/bin/env python3
"""Check whether CivitAI tRPC meta payload is required per endpoint."""

import json
import requests

from atelierai.civitai.civitai_api import CivitaiAPI


def call(base_url, headers, endpoint, payload, include_meta):
    body = {"json": payload}
    if include_meta:
        body["meta"] = {"values": {"cursor": ["undefined"]}}

    response = requests.get(
        f"{base_url}/{endpoint}",
        headers=headers,
        params={"input": json.dumps(body, separators=(",", ":"))},
        timeout=45,
    )
    data = response.json()
    parsed = data.get("result", {}).get("data", {}).get("json")
    return response.status_code, parsed


def main():
    api = CivitaiAPI.get_instance()
    base_url = api.base_url
    headers = api._get_headers()

    endpoint_checks = [
        ("system.getBrowsingSettingAddons", {"authed": True}),
        ("hiddenPreferences.getHidden", {"authed": True}),
        ("collection.getAllUser", {"authed": True}),
        ("image.get", {"id": 117165031, "authed": True}),
    ]

    print("NON-INFINITE")
    for endpoint, payload in endpoint_checks:
        status_meta, data_meta = call(base_url, headers, endpoint, payload, include_meta=True)
        status_no_meta, data_no_meta = call(base_url, headers, endpoint, payload, include_meta=False)
        print(
            endpoint,
            {
                "status_meta": status_meta,
                "status_no_meta": status_no_meta,
                "equal": data_meta == data_no_meta,
                "type_meta": type(data_meta).__name__,
                "type_no_meta": type(data_no_meta).__name__,
            },
        )

    infinite_payload = {
        "collectionId": 11035255,
        "authed": True,
        "period": "AllTime",
        "sort": "Newest",
        "browsingLevel": 31,
        "include": ["cosmetics"],
        "excludedTagIds": [
            415792,
            426772,
            5188,
            5249,
            130818,
            130820,
            133182,
            5351,
            306619,
            154326,
            161829,
            163032,
        ],
        "disablePoi": True,
        "disableMinor": True,
        "cursor": None,
    }

    status_meta_1, data_meta_1 = call(base_url, headers, "image.getInfinite", infinite_payload, include_meta=True)
    status_no_meta_1, data_no_meta_1 = call(base_url, headers, "image.getInfinite", infinite_payload, include_meta=False)

    first_items_meta = len((data_meta_1 or {}).get("items", []))
    first_items_no_meta = len((data_no_meta_1 or {}).get("items", []))
    print(
        "INFINITE_FIRST",
        {
            "status_meta": status_meta_1,
            "status_no_meta": status_no_meta_1,
            "items_meta": first_items_meta,
            "items_no_meta": first_items_no_meta,
            "equal": data_meta_1 == data_no_meta_1,
        },
    )

    next_cursor = (data_meta_1 or {}).get("nextCursor")
    if not next_cursor:
        print("INFINITE_SECOND", {"error": "No nextCursor returned from first page."})
        return

    second_payload = dict(infinite_payload)
    second_payload["cursor"] = next_cursor

    status_meta_2, data_meta_2 = call(base_url, headers, "image.getInfinite", second_payload, include_meta=True)
    status_no_meta_2, data_no_meta_2 = call(base_url, headers, "image.getInfinite", second_payload, include_meta=False)

    first_ids = [item.get("id") for item in (data_no_meta_1 or {}).get("items", [])]
    second_ids_meta = [item.get("id") for item in (data_meta_2 or {}).get("items", [])]
    second_ids_no_meta = [item.get("id") for item in (data_no_meta_2 or {}).get("items", [])]

    overlap_meta = len(set(first_ids) & set(second_ids_meta))
    overlap_no_meta = len(set(first_ids) & set(second_ids_no_meta))

    print(
        "INFINITE_SECOND",
        {
            "status_meta": status_meta_2,
            "status_no_meta": status_no_meta_2,
            "items_meta": len(second_ids_meta),
            "items_no_meta": len(second_ids_no_meta),
            "overlap_with_first_meta": overlap_meta,
            "overlap_with_first_no_meta": overlap_no_meta,
            "equal": data_meta_2 == data_no_meta_2,
        },
    )


if __name__ == "__main__":
    main()
