#!/usr/bin/env python3
"""Check collection structure"""

import json
import requests
from src.civitai import CivitaiPrivateScraper

scraper = CivitaiPrivateScraper(auto_authenticate=True)
collection_id = 14949699

# Get collection details
payload = {"id": int(collection_id), "authed": True}
payload_json = json.dumps(payload)
meta = {"values": {"cursor": ["undefined"]}}
params = {"input": json.dumps({"json": payload, "meta": meta})}

print(f"  DEBUG: Request URL: {scraper.api.base_url}/collection.getById")
print(f"  DEBUG: Payload Data: {json.dumps(params, indent=2)}")
print(f"  DEBUG: TRPC Payload: {scraper._build_trpc_payload(payload)}")

response = requests.get(
    f"{scraper.api.base_url}/collection.getById",
    headers=scraper.api._get_headers(),
    params=params,
)

if response.status_code == 200:
    data = response.json()
    collection = (
        data.get("result", {}).get("data", {}).get("json", {}).get("collection", {})
    )

    print("Collection info:")
    print(f'  Name: {collection.get("name")}')
    print(f'  Type: {collection.get("type")}')
    print(f"  Keys: {list(collection.keys())}")
    print()

    # Check for image-related fields
    for key in [
        "imageCount",
        "count",
        "itemCount",
        "items",
        "images",
        "total",
        "modelVersions",
        "models",
    ]:
        if key in collection:
            val = collection[key]
            if isinstance(val, list):
                print(f"  {key}: list with {len(val)} items")
                if val:
                    print(f"    First item keys: {list(val[0].keys())[:5]}")
            else:
                print(f"  {key}: {val}")
else:
    print(f"Failed: {response.status_code}")
    print(response.text[:300])
