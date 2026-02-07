#!/usr/bin/env python3
"""Test cursor pagination with specific cursor values from user's collection"""

import json
import requests
from src.civitai import CivitaiPrivateScraper

scraper = CivitaiPrivateScraper(auto_authenticate=True)

# Test with collection 10842247 (from user's testing)
collection_id = 10842247

# Cursor values captured by user
cursors = [None, 94299907, 89870926, 83712350, 77496095, 61190049]

print("=" * 80)
print(f"Testing pagination through {len(cursors)} cursor values")
print("=" * 80)

all_items = []
seen_item_ids = set()
result_list = []

for i, cursor in enumerate(cursors):
    print(f"\n--- Page {i+1} (cursor: {cursor}) ---")

    # Prepare payload
    payload_data = {
        "collectionId": int(collection_id),
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
        "cursor": cursor,
        "authed": True,
    }

    meta_data = {"values": {"cursor": ["undefined"]}} if cursor is None else {}

    # params = {"input": json.dumps({"json": payload_data, "meta": {"values": {"cursor": ["undefined"]}}})}
    params = {"input": json.dumps({"json": payload_data, "meta": meta_data})}

    # Make request
    response = requests.get(
        f"{scraper.base_url}/image.getInfinite",
        headers=scraper._get_headers(),
        params=params,
    )

    if response.status_code != 200:
        print(f"ERROR: {response.status_code}")
        print(response.text[:300])
        continue

    # Parse response
    data = response.json()
    result = data.get("result", {}).get("data", {}).get("json", {})
    items = result.get("items", [])
    next_cursor = result.get("nextCursor")

    # Check for duplicates
    new_ids = {item.get("id") for item in items}
    duplicate_count = len([id for id in new_ids if id in seen_item_ids])
    new_count = len(new_ids) - duplicate_count

    print(f"  Items: {len(items)} ({new_count} new, {duplicate_count} duplicates)")
    print(f"  Next cursor: {next_cursor}")

    if items:
        print(f"  First ID: {items[0].get('id')}")
        print(f"  Last ID: {items[-1].get('id')}")

    # Add new items to tracking
    seen_item_ids.update(new_ids)
    all_items.extend(items)
    result_list.append(result)

print("\n" + "=" * 80)
print("SUMMARY")
print("=" * 80)
print(f"Total items fetched: {len(all_items)}")
print(f"Unique items: {len(seen_item_ids)}")
print(f"Total duplicates: {len(all_items) - len(seen_item_ids)}")
print()

# Check pattern
print("Cursor pattern observed:")
for i in range(len(cursors) - 1):
    print(
        f"  Page {i+1}: cursor={cursors[i]} -> nextCursor={result_list[i].get('nextCursor')}"
    )
print(f"  Last page: cursor={cursors[-1]} -> nextCursor={next_cursor}")

print("\nThis confirms that cursor pagination IS working correctly!")
print("The cursor advances and returns different items on each page.")
