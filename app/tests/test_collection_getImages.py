#!/usr/bin/env python3
"""Test collection.getImages endpoint for fetching collection images"""

import json
import requests
from src.civitai import CivitaiPrivateScraper

scraper = CivitaiPrivateScraper(auto_authenticate=True)

collection_id = 14949699

# Test collection.getImages endpoint
print("=" * 80)
print("Testing collection.getImages endpoint")
print("=" * 80)

endpoint = "collection.getImages"
payload = {
    "id": int(collection_id),
    "cursor": None,
    "limit": 50,
    "authed": True
}

params = {"input": json.dumps({"json": payload, "meta": {"values": {"cursor": ["undefined"]}}})}

response = requests.get(
    f"{scraper.base_url}/{endpoint}",
    headers=scraper._get_headers(),
    params=params,
)

print(f"\nStatus: {response.status_code}")

if response.status_code == 200:
    data = response.json()
    
    # Save full response
    with open("collection_getImages_page1.json", "w") as f:
        json.dump(data, f, indent=2)
    print("Saved response to collection_getImages_page1.json")
    
    # Check structure
    result_data = data.get("result", {}).get("data", {}).get("json", {})
    print(f"\nKeys in response: {list(result_data.keys())}")
    
    # Check for different cursor fields
    print(f"\nnextCursor: {result_data.get('nextCursor')}")
    print(f"cursor: {result_data.get('cursor')}")
    print(f"prevCursor: {result_data.get('prevCursor')}")
    print(f"nextPage: {result_data.get('nextPage')}")
    
    # Check for metadata
    if "metadata" in result_data:
        metadata = result_data["metadata"]
        print(f"\nMetadata keys: {list(metadata.keys())}")
        print(f"Metadata nextPage: {metadata.get('nextPage')}")
        print(f"Metadata nextCursor: {metadata.get('nextCursor')}")
        print(f"Metadata totalItems: {metadata.get('totalItems')}")
    
    # Get items
    if "items" in result_data:
        items = result_data["items"]
        print(f"\nItems: {len(items)}")
        if items:
            print(f"First item ID: {items[0].get('id')}")
            print(f"Last item ID: {items[-1].get('id')}")
    else:
        # Try other keys
        for key in ["images", "pages", "data"]:
            if key in result_data and isinstance(result_data[key], list):
                items = result_data[key]
                print(f"\nFound items in '{key}': {len(items)}")
                if items:
                    print(f"First item ID: {items[0].get('id')}")
                    print(f"Last item ID: {items[-1].get('id')}")
                break
else:
    print(f"Error: {response.text[:500]}")

# Try without limit
print("\n" + "=" * 80)
print("Testing collection.getImages WITHOUT limit parameter")
print("=" * 80)

payload = {
    "id": int(collection_id),
    "cursor": None,
    "authed": True
}

params = {"input": json.dumps({"json": payload, "meta": {"values": {"cursor": ["undefined"]}}})}

response = requests.get(
    f"{scraper.base_url}/{endpoint}",
    headers=scraper._get_headers(),
    params=params,
)

print(f"Status: {response.status_code}")

if response.status_code == 200:
    data = response.json()
    result_data = data.get("result", {}).get("data", {}).get("json", {})
    
    print(f"Keys: {list(result_data.keys())}")
    items_data = result_data.get('items') or result_data.get('images') or result_data.get('data')
    print(f"Items: {items_data}")
    
    # Check metadata
    if "metadata" in result_data:
        metadata = result_data["metadata"]
        print(f"Total items: {metadata.get('totalItems')}")
