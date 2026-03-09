#!/usr/bin/env python3
"""Debug script to test fetching private collections"""

from src.civitai import CivitaiPrivateScraper
import json

print("=" * 70)
print("Debug: Private Collection Fetching")
print("=" * 70)
print()

# Initialize scraper
scraper = CivitaiPrivateScraper(auto_authenticate=True)

# Test collection ID (private)
collection_id = 11035255
print(f"Testing collection ID: {collection_id}")
print()

# Check if we're authenticated
print("Checking authentication status...")
headers = scraper._get_headers()
session_cookie = scraper.session_cookie
print(f"Session cookie length: {len(session_cookie)}")
print(f"Session cookie prefix: {session_cookie[:30]}...")
print()

# Test fetching collection items
print("Fetching collection items...")
endpoint = "image.getInfinite"

# Build payload with collection ID
payload_data = {**scraper.default_params}
payload_data["collectionId"] = int(collection_id)
payload_data["cursor"] = None

params = {"input": scraper._build_trpc_payload(payload_data)}

print(f"Endpoint: {scraper.base_url}/{endpoint}")
print(f"Params (partial): collectionId={collection_id}")
print()

# Make request
import requests
response = requests.get(
    f"{scraper.base_url}/{endpoint}",
    headers=headers,
    params=params,
)

print(f"Response status: {response.status_code}")
print()

if response.status_code == 200:
    data = response.json()
    print("RAW API RESPONSE:")
    print("-" * 70)
    print(json.dumps(data, indent=2))
    print()

    # Try to find items
    items = scraper._find_deep_image_list(data)
    if items:
        print(f"✅ Found {len(items)} items in response")
    else:
        print("❌ No items found in response")
        print()
        print("Searching for any arrays in response...")
        print(f"Response type: {type(data)}")
        print(f"Response keys: {list(data.keys()) if isinstance(data, dict) else 'N/A'}")

        # Look for any arrays that might contain data
        def find_arrays(obj, path="root"):
            if isinstance(obj, dict):
                for key, value in obj.items():
                    if isinstance(value, list):
                        print(f"  Found array at {path}.{key}: {len(value)} items")
                        if len(value) > 0 and isinstance(value[0], dict):
                            print(f"    First item keys: {list(value[0].keys())}")
                    find_arrays(value, f"{path}.{key}")
            elif isinstance(obj, list):
                for idx, item in enumerate(obj):
                    find_arrays(item, f"{path}[{idx}]")

        find_arrays(data)

else:
    print(f"Request failed with status {response.status_code}")
    print("Response:")
    print(response.text)

print()
print("=" * 70)
