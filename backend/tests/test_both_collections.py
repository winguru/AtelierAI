#!/usr/bin/env python3
import json
import requests
from src.civitai import CivitaiPrivateScraper

scraper = CivitaiPrivateScraper(auto_authenticate=True)

# Test both collections with NULL cursor
for test_id, name in [(10842247, "User's collection"), (14949699, "Your collection")]:
    print(f"\n{name} (ID: {test_id})")
    print("-" * 60)
    
    payload = {
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
    
    params = {"input": scraper._build_trpc_payload(payload)}
    
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

print("\n" + "=" * 60)
print("Now testing YOUR collection with cursor 46456936")
print("=" * 60)

payload = {
    "collectionId": 14949699,
    "period": "AllTime",
    "sort": "Newest",
    "browsingLevel": 31,
    "include": ["cosmetics"],
    "excludedTagIds": [415792, 426772, 5188, 5249, 130818, 130820, 133182, 5351, 306619, 154326, 161829, 163032],
    "disablePoi": True,
    "disableMinor": True,
    "cursor": 46456936,
    "authed": True,
}

params = {"input": scraper._build_trpc_payload(payload)}

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
        
        if first_id == 118404227 and last_id == 47032568:
            print("\n⚠️  SAME 50 ITEMS AS FIRST PAGE - CURSOR IS STUCK!")
        else:
            print("\n✅ DIFFERENT ITEMS - CURSOR ADVANCED!")
