#!/usr/bin/env python3
"""Test aggressive pagination - keep going even with some duplicates"""

import json
import requests
from src.civitai import CivitaiPrivateScraper

scraper = CivitaiPrivateScraper(auto_authenticate=True)
collection_id = 14949699

print("=" * 80)
print("Testing: Fetch 10 pages without duplicate detection")
print("=" * 80)

endpoint = "image.getInfinite"
cursor = None
seen_item_ids = set()
all_items = []

for page_num in range(1, 11):
    print(f"\n--- Page {page_num} ---")
    
    payload_data = {
        "collectionId": int(collection_id),
        "period": "AllTime",
        "sort": "Newest",
        "browsingLevel": 31,
        "include": ["cosmetics"],
        "excludedTagIds": [415792, 426772, 5188, 5249, 130818, 130820, 133182, 5351, 306619, 154326, 161829, 163032],
        "disablePoi": True,
        "disableMinor": True,
        "cursor": cursor,
        "authed": True,
    }
    
    params = {"input": json.dumps({"json": payload_data, "meta": {"values": {"cursor": ["undefined"]}}})}
    
    response = requests.get(
        f"{scraper.base_url}/{endpoint}",
        headers=scraper._get_headers(),
        params=params,
    )
    
    if response.status_code != 200:
        print(f"ERROR: {response.status_code}")
        break
    
    data = response.json()
    items = scraper._find_deep_image_list(data)
    next_cursor = data.get("result", {}).get("data", {}).get("json", {}).get("nextCursor")
    
    # Check IDs
    new_ids = {item.get("id") for item in items}
    new_unique = [id for id in new_ids if id not in seen_item_ids]
    new_duplicates = len(new_ids) - len(new_unique)
    
    print(f"  Items: {len(items)}")
    print(f"  NEW items: {len(new_unique)}")
    print(f"  Duplicates: {new_duplicates}")
    print(f"  Next cursor: {next_cursor}")
    print(f"  Cursor changed: {cursor != next_cursor}")
    
    if items:
        print(f"  First ID: {items[0].get('id')}")
        print(f"  Last ID: {items[-1].get('id')}")
    
    # Add ALL items (including duplicates) to see full count
    seen_item_ids.update(new_ids)
    all_items.extend(items)
    cursor = next_cursor

print("\n" + "=" * 80)
print("SUMMARY")
print("=" * 80)
print(f"Total items fetched: {len(all_items)}")
print(f"Unique items: {len(seen_item_ids)}")
print(f"Total duplicates: {len(all_items) - len(seen_item_ids)}")
