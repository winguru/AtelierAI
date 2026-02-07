#!/usr/bin/env python3
"""Test collection 12176069"""

import requests
import json
import os
from config import CIVITAI_SESSION_CACHE

# Get session token
if os.path.exists(CIVITAI_SESSION_CACHE):
    with open(CIVITAI_SESSION_CACHE, 'r') as f:
        token = f.read().strip()
else:
    from config import MY_SESSION_COOKIE
    token = MY_SESSION_COOKIE

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Cookie": f"__Secure-next-auth.session-token={token}",
    "Referer": "https://civitai.com/",
}

collection_id = 12176069

print("=" * 70)
print(f"Testing Collection {collection_id}")
print("=" * 70)
print()

# Test 1: Check permissions
print("Test 1: Collection Permissions")
print("-" * 70)

endpoint = "collection.getById"
payload = {
    "id": int(collection_id),
    "authed": True
}

params = {"input": json.dumps({"json": payload, "meta": {"values": {"cursor": ["undefined"]}}})}

response = requests.get(
    f"https://civitai.com/api/trpc/{endpoint}",
    headers=headers,
    params=params
)

if response.status_code == 200:
    data = response.json()
    permissions = data.get("result", {}).get("data", {}).get("json", {}).get("permissions", {})
    collection = data.get("result", {}).get("data", {}).get("json", {}).get("collection", {})
    
    print("Permissions:")
    for key in ['read', 'write', 'isOwner', 'publicCollection']:
        value = permissions.get(key)
        status = "✅" if value else "❌"
        print(f"  {status} {key}: {value}")
    
    if collection:
        print()
        print("Collection Info:")
        print(f"  Name: {collection.get('name', 'Unknown')}")
        print(f"  Type: {collection.get('type', 'Unknown')}")
        print(f"  Public: {collection.get('public', False)}")
else:
    print(f"Failed: {response.status_code}")
    print(response.text[:200])

print()

# Test 2: Fetch images via image.getInfinite
print("Test 2: Fetch Images (image.getInfinite)")
print("-" * 70)

endpoint = "image.getInfinite"
payload = {
    "collectionId": int(collection_id),
    "authed": True,
    "cursor": None
}

params = {"input": json.dumps({"json": payload, "meta": {"values": {"cursor": ["undefined"]}}})}

response = requests.get(
    f"https://civitai.com/api/trpc/{endpoint}",
    headers=headers,
    params=params
)

if response.status_code == 200:
    data = response.json()
    items = data.get("result", {}).get("data", {}).get("json", {}).get("items", [])
    next_cursor = data.get("result", {}).get("data", {}).get("json", {}).get("nextCursor")
    
    print(f"✅ Successfully fetched {len(items)} items")
    if next_cursor:
        print(f"   Next cursor: {next_cursor} (more items available)")
    else:
        print(f"   No next cursor (all items fetched)")
    
    if len(items) > 0:
        print()
        print("Sample items:")
        for i, item in enumerate(items[:3]):
            print(f"  [{i+1}] ID: {item.get('id')}")
            print(f"      Name: {item.get('name', 'Unknown')}")
            print(f"      Author: {item.get('user', {}).get('username', 'Unknown')}")
            print(f"      Size: {item.get('width')}x{item.get('height')}")
            print()
else:
    print(f"❌ Failed: {response.status_code}")
    print(response.text[:200])

print()
print("=" * 70)
print("Testing Scraper")
print("=" * 70)
print()

# Test with the actual scraper
    from src.civitai import CivitaiPrivateScraper

print("Initializing scraper...")
scraper = CivitaiPrivateScraper(auto_authenticate=False)

print(f"Scraping collection {collection_id}...")
data = scraper.scrape(collection_id)

if data:
    print(f"✅ SUCCESS: Scraped {len(data)} images!")
    print()
    print("Sample data:")
    for i, item in enumerate(data[:2]):
        print(f"  [{i+1}] Image ID: {item['image_id']}")
        print(f"      Author: {item['author']}")
        print(f"      Model: {item['model']} - {item['model_version']}")
        print(f"      URL: {item['image_url']}")
        print()
else:
    print("❌ No data found")
