#!/usr/bin/env python3
"""Debug script to check cursor pagination response structure."""

import json
import requests
from src.civitai import CivitaiPrivateScraper

# Initialize scraper
scraper = CivitaiPrivateScraper(auto_authenticate=True)

collection_id = 14949699
endpoint = "image.getInfinite"

# First request without cursor
payload_data = {**scraper.default_params}
payload_data["collectionId"] = int(collection_id)
payload_data["cursor"] = None

params = {"input": scraper._build_trpc_payload(payload_data)}

print("=" * 80)
print("First request (no cursor):")
print("=" * 80)
print(f"Payload: {json.dumps(payload_data, indent=2)}")
print()

response = requests.get(
    f"{scraper.base_url}/{endpoint}",
    headers=scraper._get_headers(),
    params=params,
)

print(f"Status: {response.status_code}")
print()

if response.status_code == 200:
    data = response.json()
    
    # Pretty print the full response structure
    print("Full response structure:")
    print(json.dumps(data, indent=2))
    print()
    
    # Check different paths for nextCursor
    print("=" * 80)
    print("Checking for nextCursor in different locations:")
    print("=" * 80)
    
    # Path 1: result.data.json.nextCursor
    cursor1 = data.get("result", {}).get("data", {}).get("json", {}).get("nextCursor")
    print(f"Path 1 - result.data.json.nextCursor: {cursor1}")
    
    # Path 2: result.data.json.pages.nextCursor
    cursor2 = data.get("result", {}).get("data", {}).get("json", {}).get("pages", {}).get("nextCursor")
    print(f"Path 2 - result.data.json.pages.nextCursor: {cursor2}")
    
    # Path 3: result.json.nextCursor
    cursor3 = data.get("result", {}).get("json", {}).get("nextCursor")
    print(f"Path 3 - result.json.nextCursor: {cursor3}")
    
    # Path 4: result.data.nextCursor
    cursor4 = data.get("result", {}).get("data", {}).get("nextCursor")
    print(f"Path 4 - result.data.nextCursor: {cursor4}")
    
    # Find the image list
    items = scraper._find_deep_image_list(data)
    if items:
        print()
        print("=" * 80)
        print(f"Found {len(items)} image items")
        print("=" * 80)
        # Print first item structure
        if items:
            print("First item:")
            print(json.dumps(items[0], indent=2))
    else:
        print("No image items found!")
else:
    print(f"Request failed: {response.text}")
