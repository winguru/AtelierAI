#!/usr/bin/env python3
"""Test with exact parameters from user's collection"""

import json
import requests
from src.civitai import CivitaiPrivateScraper

scraper = CivitaiPrivateScraper(auto_authenticate=True)

# Test BOTH collections with NULL cursor
print("=" * 80)
print("Testing BOTH collections with NULL cursor to compare behavior")
print("=" * 80)

for test_id, name in [(10842247, "User's collection"), (14949699, "Your collection")]:
    print(f"\n{name} (ID: {test_id})")
    print("-" * 80)
    
    payload_data = {
        "collectionId": int(test_id),
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
    
    params = {"input": json.dumps({"json": payload_data, "meta": {"values": {"cursor": ["undefined"]}}})}
    
    response = requests.get(
        f"{scraper.base_url}/image.getInfinite",
        headers=scraper._get_headers(),
        params=params,
    )
    
    if response.status_code == 200:
        data = response.json()
        result = data.get("result", {}).get("data", {}).get("json", {})
        items = result.get("items", [])
        next_cursor = result.get("nextCursor")
        
        print(f"Items: {len(items)}")
        if items:
            print(f"First ID: {items[0].get('id')}")
            print(f"Last ID: {items[-1].get('id')}")
        print(f"Next cursor: {next_cursor}")
        print(f"Type of nextCursor: {type(next_cursor)}")
    else:
        print(f"ERROR: {response.status_code}")

print("\n" + "=" * 80)
print("Now testing YOUR collection (14949699) with your captured cursor value")
print("=" * 80)

# Try with cursor = 46456936 (the stuck cursor)
test_cursor = 46456936
payload_data = {
    "collectionId": 14949699,
    "period": "AllTime",
    "sort": "Newest",
    "browsingLevel": 31,
    "include": ["cosmetics"],
    "excludedTagIds": [415792, 426772, 5188, 5249, 130818, 130820, 133182, 5351, 306619, 154326, 161829, 163032],
    "disablePoi": True,
    "disableMinor": True,
    "cursor": test_cursor,
    "authed": True,
}

params = {"input": json.dumps({"json": payload_data, "meta": {"values": {"cursor": ["undefined"]}}})}

response = requests.get(
    f"{scraper.base_url}/image.getInfinite",
    headers=scraper._get_headers(),
    params=params,
)

if response.status_code == 200:
    data = response.json()
    result = data.get("result", {}).get("data", {}).get("json", {})
    items = result.get("items", [])
    next_cursor = result.get("nextCursor")
    
    print(f"Items: {len(items)}")
    if items:
        first_id = items[0].get('id')
        last_id = items[-1].get('id')
        print(f"First ID: {first_id}")
        print(f"Last ID: {last_id}")
        print(f"Next cursor: {next_cursor}")
        
        # Check if these are the SAME as the first 50 items
        if first_id == 118404227 and last_id == 47032568:
            print("\n⚠️  SAME 50 ITEMS AS FIRST PAGE - CURSOR IS STUCK!")
        else:
            print("\n✅ DIFFERENT ITEMS - CURSOR ADVANCED!")
else:
    print(f"ERROR: {response.status_code}")
    print(response.text[:300])
