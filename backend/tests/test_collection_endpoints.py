#!/usr/bin/env python3
"""Test different collection endpoints"""

import requests
import json
from src.civitai import CivitaiPrivateScraper

print("=" * 70)
print("Testing Different Collection Endpoints")
print("=" * 70)
print()

# Initialize scraper
scraper = CivitaiPrivateScraper(auto_authenticate=True)
headers = scraper._get_headers()

collection_id = 11035255

# Test different endpoints
endpoints_to_test = [
    {
        "name": "image.getInfinite (current)",
        "endpoint": "image.getInfinite",
        "payload": {
            "collectionId": int(collection_id),
            "authed": True,
            "cursor": None
        }
    },
    {
        "name": "collection.getById",
        "endpoint": "collection.getById",
        "payload": {
            "id": int(collection_id),
            "authed": True
        }
    },
    {
        "name": "collection.getImages (with cursor)",
        "endpoint": "collection.getImages",
        "payload": {
            "id": int(collection_id),
            "cursor": None,
            "authed": True
        }
    },
    {
        "name": "collection.getImages (without cursor)",
        "endpoint": "collection.getImages",
        "payload": {
            "id": int(collection_id),
            "authed": True
        }
    },
    {
        "name": "collection.get (alternative)",
        "endpoint": "collection.get",
        "payload": {
            "id": int(collection_id),
            "authed": True
        }
    }
]

for test in endpoints_to_test:
    print(f"\nTesting: {test['name']}")
    print("-" * 70)
    
    endpoint = test["endpoint"]
    payload = test["payload"]
    
    # Build tRPC payload
    params = {"input": json.dumps({"json": payload, "meta": {"values": {"cursor": ["undefined"]}}})}
    
    # Make request
    response = requests.get(
        f"{scraper.base_url}/{endpoint}",
        headers=headers,
        params=params
    )
    
    print(f"Status: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        
        # Extract the main data
        try:
            result_data = data.get("result", {}).get("data", {}).get("json", {})
        except:
            result_data = data
        
        print(f"Response type: {type(result_data)}")
        
        if isinstance(result_data, dict):
            keys = list(result_data.keys())
            print(f"Keys: {keys}")
            
            # Look for images/items
            for key in ["items", "images", "posts", "models"]:
                if key in result_data:
                    items = result_data[key]
                    print(f"✅ Found '{key}': {len(items)} items" if isinstance(items, list) else f"✅ Found '{key}': {type(items)}")
            
            # Check if it has pages
            if "pages" in result_data:
                pages = result_data["pages"]
                print(f"✅ Found 'pages': {len(pages)} items" if isinstance(pages, list) else f"✅ Found 'pages': {type(pages)}")
            
            # Look for image data in nested structures
            def count_images(obj, depth=0):
                if depth > 5:
                    return 0
                count = 0
                if isinstance(obj, dict):
                    if "id" in obj and (obj.get("type") == "image" or obj.get("url")):
                        count += 1
                    for v in obj.values():
                        count += count_images(v, depth + 1)
                elif isinstance(obj, list):
                    for item in obj:
                        count += count_images(item, depth + 1)
                return count
            
            image_count = count_images(result_data)
            if image_count > 0:
                print(f"✅ Found {image_count} potential image objects in response")
        
        elif isinstance(result_data, list):
            print(f"✅ Response is a list with {len(result_data)} items")
            if len(result_data) > 0 and isinstance(result_data[0], dict):
                print(f"   First item keys: {list(result_data[0].keys())}")
    else:
        print(f"❌ Request failed")
        print(f"Response: {response.text[:200]}")

print()
print("=" * 70)
print("Recommendation:")
print("-" * 70)
print("Review the output above to find which endpoint returns your collection data.")
print("=" * 70)
