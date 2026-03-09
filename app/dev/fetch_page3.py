#!/usr/bin/env python3
"""Test fetching page 3 directly"""

import json
import requests
from src.civitai import CivitaiPrivateScraper

# Initialize scraper
scraper = CivitaiPrivateScraper(auto_authenticate=True)

collection_id = 14949699
endpoint = "image.getInfinite"

# First get cursor from page 2
cursor = None
for page in range(1, 3):
    payload_data = {**scraper.default_params}
    payload_data["collectionId"] = int(collection_id)
    payload_data["cursor"] = cursor
    
    params = {"input": scraper._build_trpc_payload(payload_data)}
    
    response = requests.get(
        f"{scraper.base_url}/{endpoint}",
        headers=scraper._get_headers(),
        params=params,
    )
    
    if response.status_code == 200:
        data = response.json()
        items = scraper._find_deep_image_list(data)
        next_cursor = data.get("result", {}).get("data", {}).get("json", {}).get("nextCursor")
        
        print(f"Page {page}: {len(items)} items, next_cursor = {next_cursor}")
        
        if next_cursor and next_cursor != cursor:
            cursor = next_cursor
        else:
            print(f"Stopping: next_cursor = {next_cursor}, cursor = {cursor}")
            break
    else:
        print(f"Page {page} failed")
        break

print(f"\n{'=' * 80}")
print("Now attempting to fetch page 3 with cursor: {cursor}")
print(f"{'=' * 80}")

# Try to fetch page 3
if cursor:
    payload_data = {**scraper.default_params}
    payload_data["collectionId"] = int(collection_id)
    payload_data["cursor"] = cursor
    
    params = {"input": scraper._build_trpc_payload(payload_data)}
    
    print(f"Payload: {json.dumps(payload_data, indent=2)}")
    
    response = requests.get(
        f"{scraper.base_url}/{endpoint}",
        headers=scraper._get_headers(),
        params=params,
    )
    
    print(f"\nStatus: {response.status_code}")
    
    if response.status_code == 200:
        data = response.json()
        
        # Save full response
        with open("page3_response.json", "w") as f:
            json.dump(data, f, indent=2)
        print("Saved response to page3_response.json")
        
        # Check for items
        items = scraper._find_deep_image_list(data)
        next_cursor = data.get("result", {}).get("data", {}).get("json", {}).get("nextCursor")
        
        print(f"Items found: {len(items)}")
        print(f"Next cursor: {next_cursor}")
        
        if items:
            print(f"First item ID: {items[0].get('id')}")
            print(f"Last item ID: {items[-1].get('id')}")
    else:
        print(f"Error: {response.text[:500]}")
