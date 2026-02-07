#!/usr/bin/env python3
"""Test different collection endpoints and save to file"""

import requests
import json
from src.civitai import CivitaiPrivateScraper

# Initialize scraper
scraper = CivitaiPrivateScraper(auto_authenticate=True)
headers = scraper._get_headers()

collection_id = 14949699

# Test collection.getById first
print("Testing collection.getById...")
endpoint = "collection.getById"
payload = {
    "id": int(collection_id),
    "authed": True
}

params = {"input": json.dumps({"json": payload, "meta": {"values": {"cursor": ["undefined"]}}})}

response = requests.get(
    f"{scraper.base_url}/{endpoint}",
    headers=headers,
    params=params
)

if response.status_code == 200:
    data = response.json()
    with open("collection_getById_response.json", "w") as f:
        json.dump(data, f, indent=2)
    print("Saved response to collection_getById_response.json")
    
    # Check the data
    result_data = data.get("result", {}).get("data", {}).get("json", {})
    print(f"\nKeys in response: {list(result_data.keys())}")
    print(f"Image count: {result_data.get('imageCount', 'N/A')}")
    print(f"Model count: {result_data.get('modelCount', 'N/A')}")
    print(f"User: {result_data.get('user', {}).get('username', 'N/A')}")
    print(f"Name: {result_data.get('name', 'N/A')}")
else:
    print(f"Failed: {response.status_code}")
    print(response.text[:500])

# Now test image.getInfinite for comparison
print("\n\n" + "=" * 70)
print("Testing image.getInfinite...")
endpoint = "image.getInfinite"
payload = {
    "collectionId": int(collection_id),
    "authed": True,
    "cursor": None
}

params = {"input": json.dumps({"json": payload, "meta": {"values": {"cursor": ["undefined"]}}})}

response = requests.get(
    f"{scraper.base_url}/{endpoint}",
    headers=headers,
    params=params
)

if response.status_code == 200:
    data = response.json()
    result_data = data.get("result", {}).get("data", {}).get("json", {})
    next_cursor = result_data.get("nextCursor")
    items = result_data.get("items", [])
    print(f"Next cursor: {next_cursor}")
    print(f"Items on first page: {len(items)}")
    print(f"Item IDs: {[item.get('id') for item in items[:5]]}")
else:
    print(f"Failed: {response.status_code}")
    print(response.text[:500])

print("\n\nCollection getById response saved to: collection_getById_response.json")
