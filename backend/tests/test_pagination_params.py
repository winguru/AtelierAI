#!/usr/bin/env python3
"""Test different pagination parameters"""

import json
import requests
from src.civitai import CivitaiPrivateScraper

scraper = CivitaiPrivateScraper(auto_authenticate=True)
collection_id = 14949699

# First, let's check what parameters image.getInfinite accepts
print("=" * 80)
print("Testing image.getInfinite with different parameter combinations")
print("=" * 80)

# Test 1: collectionId only (no cursor)
print("\nTest 1: collectionId only (initial request)")
payload = {
    "collectionId": int(collection_id),
    "authed": True,
}
params = {"input": json.dumps({"json": payload, "meta": {"values": {"cursor": ["undefined"]}}})}

response = requests.get(
    f"{scraper.base_url}/image.getInfinite",
    headers=scraper._get_headers(),
    params=params,
)

if response.status_code == 200:
    data = response.json()
    result = data.get("result", {}).get("data", {}).get("json", {})
    items = result.get("items", [])
    print(f"Items: {len(items)}")
    print(f"nextCursor: {result.get('nextCursor')}")
    print(f"Last ID: {items[-1].get('id') if items else 'N/A'}")
    
    # Check all keys
    print(f"\nAll keys in result: {list(result.keys())}")
    
    # Check for metadata
    if "metadata" in result:
        print(f"Metadata: {result['metadata']}")

# Test 2: Try with 'page' parameter instead of cursor
print("\n\nTest 2: Using 'page' parameter instead of 'cursor'")
payload = {
    "collectionId": int(collection_id),
    "page": 2,
    "authed": True,
}
params = {"input": json.dumps({"json": payload, "meta": {"values": {"page": ["undefined"]}}})}

response = requests.get(
    f"{scraper.base_url}/image.getInfinite",
    headers=scraper._get_headers(),
    params=params,
)

if response.status_code == 200:
    data = response.json()
    result = data.get("result", {}).get("data", {}).get("json", {})
    items = result.get("items", [])
    print(f"Items: {len(items)}")
    if items:
        first_id = items[0].get('id')
        last_id = items[-1].get('id')
        print(f"First ID: {first_id}")
        print(f"Last ID: {last_id}")
else:
    print(f"Failed: {response.status_code}")
    print(response.text[:300])

# Test 3: Try without any pagination, just with cursor=None
print("\n\nTest 3: cursor=None explicitly")
payload = {
    "collectionId": int(collection_id),
    "cursor": None,
    "authed": True,
}
params = {"input": json.dumps({"json": payload, "meta": {"values": {"cursor": ["undefined"]}}})}

response = requests.get(
    f"{scraper.base_url}/image.getInfinite",
    headers=scraper._get_headers(),
    params=params,
)

if response.status_code == 200:
    data = response.json()
    result = data.get("result", {}).get("data", {}).get("json", {})
    items = result.get("items", [])
    cursor1 = result.get('nextCursor')
    print(f"Items: {len(items)}")
    print(f"nextCursor: {cursor1}")
    print(f"Last ID: {items[-1].get('id') if items else 'N/A'}")
    
    # Test 4: Use the cursor we just got
    print("\n\nTest 4: Using cursor from previous response")
    payload2 = {
        "collectionId": int(collection_id),
        "cursor": cursor1,
        "authed": True,
    }
    params2 = {"input": json.dumps({"json": payload2, "meta": {"values": {"cursor": ["undefined"]}}})}
    
    response2 = requests.get(
        f"{scraper.base_url}/image.getInfinite",
        headers=scraper._get_headers(),
        params=params2,
    )
    
    if response2.status_code == 200:
        data2 = response2.json()
        result2 = data2.get("result", {}).get("data", {}).get("json", {})
        items2 = result2.get("items", [])
        cursor2 = result2.get('nextCursor')
        
        print(f"Items: {len(items2)}")
        print(f"nextCursor: {cursor2}")
        if items2:
            print(f"First ID: {items2[0].get('id')}")
            print(f"Last ID: {items2[-1].get('id')}")
            
            # Check if same as page 1
            if items2 and items:
                same_first = items2[0].get('id') == items[0].get('id')
                print(f"\nSame first ID as page 1? {same_first}")
                print(f"Page 1 first ID: {items[0].get('id')}")
                print(f"Page 2 first ID: {items2[0].get('id')}")
    else:
        print(f"Failed: {response2.status_code}")

# Test 5: Check if there's a different endpoint structure
print("\n\nTest 5: Check REST API for collections")
# Try the REST API endpoint
rest_url = f"https://civitai.com/api/v1/collections/{collection_id}"
response = requests.get(rest_url, headers=scraper._get_headers())
print(f"REST API Status: {response.status_code}")
if response.status_code == 200:
    data = response.json()
    print(f"REST API Response keys: {list(data.keys())}")
    # Save to file
    with open("rest_collection_response.json", "w") as f:
        json.dump(data, f, indent=2)
    print("Saved to rest_collection_response.json")
else:
    print(f"REST API Error: {response.text[:300]}")
