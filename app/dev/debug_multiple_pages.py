#!/usr/bin/env python3
"""Debug script to check multiple pages of cursor pagination."""

import json
import requests
from src.civitai import CivitaiPrivateScraper

# Initialize scraper
scraper = CivitaiPrivateScraper(auto_authenticate=True)

collection_id = 14949699
endpoint = "image.getInfinite"

cursor = None
page_num = 0
total_items = 0

print("=" * 80)
print(f"Fetching collection {collection_id}")
print("=" * 80)

while page_num < 5:  # Max 5 pages for debugging
    page_num += 1
    
    # Prepare payload
    payload_data = {**scraper.default_params}
    payload_data["collectionId"] = int(collection_id)
    payload_data["cursor"] = cursor
    
    params = {"input": scraper._build_trpc_payload(payload_data)}
    
    print(f"\n--- Page {page_num} ---")
    print(f"Cursor: {cursor}")
    print(f"URL: {scraper.base_url}/{endpoint}?input={params['input'][:100]}...")
    
    # Make request
    response = requests.get(
        f"{scraper.base_url}/{endpoint}",
        headers=scraper._get_headers(),
        params=params,
    )
    
    print(f"Status: {response.status_code}")
    
    if response.status_code != 200:
        print(f"ERROR: {response.text}")
        break
    
    # Parse response
    data = response.json()
    items = scraper._find_deep_image_list(data)
    
    if not items:
        print("No items found - stopping pagination")
        break
    
    print(f"Items returned: {len(items)}")
    total_items += len(items)
    
    # Check for next cursor
    next_cursor = data.get("result", {}).get("data", {}).get("json", {}).get("nextCursor")
    print(f"Next cursor: {next_cursor}")
    
    # Check if cursor changed
    if next_cursor and next_cursor != cursor:
        cursor = next_cursor
        print("Cursor updated, continuing...")
    else:
        print("No new cursor or same cursor - stopping pagination")
        break

print(f"\n{'=' * 80}")
print(f"SUMMARY: Fetched {total_items} items across {page_num} page(s)")
print(f"{'=' * 80}")
