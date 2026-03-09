#!/usr/bin/env python3
"""Test the exact format that worked in user's browser"""

import json
import requests
from src.civitai import CivitaiPrivateScraper

def main() -> None:
    scraper = CivitaiPrivateScraper(auto_authenticate=True)

    # This is the EXACT request from user's Chrome DevTools that worked
    # Using the format that worked (with meta wrapper)
    payload_data = {
        "collectionId": 14949699,
        "period": "AllTime",
        "sort": "Newest",
        "browsingLevel": 31,
        "include": ["cosmetics"],
        "excludedTagIds": [415792, 426772, 5188, 5249, 130818, 130820, 133182, 5351, 306619, 154326, 161829, 163032],
        "disablePoi": True,
        "disableMinor": True,
        "cursor": None,
        "authed": True,
    }

    # With meta wrapper (user's working format)
    params_with_meta = {
        "input": json.dumps({"json": payload_data, "meta": {"values": {"cursor": ["undefined"]}}})
    }

    # Without meta wrapper (my current attempt)
    params_without_meta = {
        "input": json.dumps({"json": payload_data})
    }

    print("Testing WITH meta wrapper (user's format):")
    print(f"Payload: {params_with_meta['input']}")
    print()

    response = requests.get(
        "https://civitai.com/api/trpc/image.getInfinite",
        headers=scraper._get_headers(),
        params=params_with_meta,
    )

    print(f"Status: {response.status_code}")
    if response.status_code == 200:
        data = response.json()
        result = data.get("result", {}).get("data", {}).get("json", {})
        print(f"nextCursor: {result.get('nextCursor')}")
        print(f"Items: {len(result.get('items', []))}")
    else:
        print(f"Error: {response.text[:200]}")

    print("\n" + "=" * 80)
    print("Testing WITHOUT meta wrapper (my format):")
    print(f"Payload: {params_without_meta['input']}")
    print()

    response2 = requests.get(
        "https://civitai.com/api/trpc/image.getInfinite",
        headers=scraper._get_headers(),
        params=params_without_meta,
    )

    print(f"Status: {response2.status_code}")
    if response2.status_code == 200:
        data2 = response2.json()
        result2 = data2.get("result", {}).get("data", {}).get("json", {})
        print(f"nextCursor: {result2.get('nextCursor')}")
        print(f"Items: {len(result2.get('items', []))}")
    else:
        print(f"Error: {response2.status_code}")
        print(response2.text[:200])


if __name__ == "__main__":
    main()
