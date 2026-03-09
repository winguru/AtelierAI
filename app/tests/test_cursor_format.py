#!/usr/bin/env python3
"""Test different cursor formats"""

import json
import requests
from src.civitai import CivitaiPrivateScraper

scraper = CivitaiPrivateScraper(auto_authenticate=True)

collection_id = 14949699

print("Testing different cursor formats...")
print("=" * 80)

# First, get the cursor from page 1
payload_data = {**scraper.default_params}
payload_data["collectionId"] = int(collection_id)
payload_data["cursor"] = None

params = {"input": scraper._build_trpc_payload(payload_data)}
response = requests.get(
    f"{scraper.base_url}/image.getInfinite",
    headers=scraper._get_headers(),
    params=params,
)

data = response.json()
cursor = data.get("result", {}).get("data", {}).get("json", {}).get("nextCursor")
items = scraper._find_deep_image_list(data)

print(f"\nPage 1 (cursor=None): {len(items)} items")
print(f"nextCursor value: {cursor}")
print(f"nextCursor type: {type(cursor)}")
print(f"Last 3 item IDs: {[item.get('id') for item in items[-3:]]}")

# Now try different formats for page 2
print("\n" + "=" * 80)
print("Testing different cursor formats for page 2:")
print("=" * 80)

formats_to_test = [
    ("Number (as-is)", cursor),
    ("String", str(cursor)),
    ("Integer", int(cursor)),
]

for format_name, cursor_value in formats_to_test:
    print(f"\n--- {format_name}: {cursor_value} ---")
    
    payload_data = {**scraper.default_params}
    payload_data["collectionId"] = int(collection_id)
    payload_data["cursor"] = cursor_value
    
    params = {"input": scraper._build_trpc_payload(payload_data)}
    
    response = requests.get(
        f"{scraper.base_url}/image.getInfinite",
        headers=scraper._get_headers(),
        params=params,
    )
    
    if response.status_code == 200:
        data = response.json()
        items2 = scraper._find_deep_image_list(data)
        next_cursor2 = data.get("result", {}).get("data", {}).get("json", {}).get("nextCursor")
        
        if items2:
            first_id = items2[0].get('id')
            last_id = items2[-1].get('id')
            same_as_page1 = first_id == items[0].get('id')
            
            print(f"  Items: {len(items2)}")
            print(f"  First ID: {first_id}")
            print(f"  Last ID: {last_id}")
            print(f"  Same as page 1? {same_as_page1}")
            print(f"  nextCursor: {next_cursor2}")
            
            if not same_as_page1:
                print(f"  ✅ DIFFERENT PAGE - This format works!")
        else:
            print(f"  ❌ No items returned")
    else:
        print(f"  ❌ Error: {response.status_code}")

# Also try using the last item ID as cursor
print("\n" + "=" * 80)
print("Testing with last item ID as cursor:")
print("=" * 80)

last_item_id = items[-1].get('id')
print(f"Last item ID from page 1: {last_item_id}")

payload_data = {**scraper.default_params}
payload_data["collectionId"] = int(collection_id)
payload_data["cursor"] = last_item_id

params = {"input": scraper._build_trpc_payload(payload_data)}
response = requests.get(
    f"{scraper.base_url}/image.getInfinite",
    headers=scraper._get_headers(),
    params=params,
)

if response.status_code == 200:
    data = response.json()
    items2 = scraper._find_deep_image_list(data)
    
    if items2:
        first_id = items2[0].get('id')
        same_as_page1 = first_id == items[0].get('id')
        print(f"Items: {len(items2)}")
        print(f"First ID: {first_id}")
        print(f"Same as page 1? {same_as_page1}")
        if not same_as_page1:
            print(f"✅ This works!")
