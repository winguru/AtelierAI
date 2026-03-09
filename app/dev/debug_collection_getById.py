#!/usr/bin/env python3
"""Debug script to inspect collection.getById response"""

import requests
import json
from src.civitai import CivitaiPrivateScraper

print("=" * 70)
print("Debug: collection.getById Response")
print("=" * 70)
print()

# Initialize scraper
scraper = CivitaiPrivateScraper(auto_authenticate=True)
headers = scraper._get_headers()

collection_id = 11035255

# Test collection.getById
print(f"Fetching collection {collection_id}...")
print()

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

print(f"Status: {response.status_code}")
print()

if response.status_code == 200:
    data = response.json()
    
    # Extract collection data
    collection_data = data.get("result", {}).get("data", {}).get("json", {}).get("collection", {})
    permissions = data.get("result", {}).get("data", {}).get("json", {}).get("permissions", {})
    
    print("COLLECTION DATA:")
    print("-" * 70)
    print(json.dumps(collection_data, indent=2))
    print()
    
    print("PERMISSIONS:")
    print("-" * 70)
    print(json.dumps(permissions, indent=2))
    print()
    
    print("=" * 70)
    print("ANALYSIS:")
    print("=" * 70)
    
    print(f"\nCollection keys: {list(collection_data.keys())}")
    
    # Look for images/items in various places
    for key in ["items", "images", "posts", "models", "children"]:
        if key in collection_data:
            items = collection_data[key]
            if isinstance(items, list):
                print(f"✅ Found '{key}': {len(items)} items")
                if len(items) > 0:
                    print(f"   First item keys: {list(items[0].keys())}")
                    print(f"   First item sample:")
                    print(json.dumps(items[0], indent=4))
            else:
                print(f"✅ Found '{key}': {type(items)}")
    
    # Check if there's a model count
    if "count" in collection_data or "modelCount" in collection_data or "imageCount" in collection_data:
        print(f"\n✅ Found count fields:")
        for k in ["count", "modelCount", "imageCount", "postCount"]:
            if k in collection_data:
                print(f"   {k}: {collection_data[k]}")
    
    # Check if it's a nested structure
    if "models" in collection_data:
        models = collection_data["models"]
        if isinstance(models, dict):
            print(f"\n✅ 'models' is a dict with keys: {list(models.keys())}")
            for k, v in models.items():
                if isinstance(v, list):
                    print(f"   models.{k}: {len(v)} items")
        elif isinstance(models, list):
            print(f"\n✅ 'models' is a list with {len(models)} items")
            if len(models) > 0:
                print(f"   First item keys: {list(models[0].keys())}")
    
    # Check for nested collections
    if "collection" in collection_data:
        nested = collection_data["collection"]
        print(f"\n✅ Found nested 'collection' object with keys: {list(nested.keys())}")

else:
    print(f"Request failed: {response.status_code}")
    print(response.text)

print()
print("=" * 70)
