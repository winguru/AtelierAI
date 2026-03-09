#!/usr/bin/env python3
"""Test collection 12176069 with CORRECT cookie name"""

import requests
import json
import os
from config import CIVITAI_SESSION_CACHE
from src.civitai import CivitaiPrivateScraper
from src.console_utils import ConsoleFormatter

# Initialize formatter
fmt = ConsoleFormatter()

# Get session token
if os.path.exists(CIVITAI_SESSION_CACHE):
    with open(CIVITAI_SESSION_CACHE, "r") as f:
        token = f.read().strip()
else:
    from config import MY_SESSION_COOKIE

    token = MY_SESSION_COOKIE

# FIXED: Use correct cookie name
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Cookie": f"__Secure-civitai-token={token}",  # FIXED COOKIE NAME
    "Referer": "https://civitai.com/",
}

collection_id = 12176069

fmt.print_header(f"Testing Collection {collection_id} (FIXED)")
fmt.print_blank()

# Test 1: Check permissions
fmt.print_subheader("Test 1: Collection Permissions")

endpoint = "collection.getById"
payload = {"id": int(collection_id), "authed": True}
params = {
    "input": json.dumps(
        {"json": payload, "meta": {"values": {"cursor": ["undefined"]}}}
    )
}

response = requests.get(
    f"https://civitai.com/api/trpc/{endpoint}", headers=headers, params=params
)

if response.status_code == 200:
    data = response.json()
    permissions = (
        data.get("result", {}).get("data", {}).get("json", {}).get("permissions", {})
    )
    collection = (
        data.get("result", {}).get("data", {}).get("json", {}).get("collection", {})
    )

    fmt.print_info("Permissions:")
    for key in ["read", "write", "isOwner", "publicCollection"]:
        value = permissions.get(key)
        fmt.print_permission(key, value)

    if collection:
        fmt.print_blank()
        fmt.print_info("Collection Info:")
        fmt.print_key_value("Name", collection.get('name', 'Unknown'), indent=2)
        fmt.print_key_value("Type", collection.get('type', 'Unknown'), indent=2)
        fmt.print_key_value("Public", collection.get('public', False), indent=2)
else:
    fmt.print_error(f"Failed: {response.status_code}")
    fmt.print_info(response.text[:200], indent=2)

fmt.print_blank()

# Test 2: Fetch images via image.getInfinite
fmt.print_subheader("Test 2: Fetch Images (image.getInfinite)")

endpoint = "image.getInfinite"
payload = {"collectionId": int(collection_id), "authed": True, "cursor": None}

params = {
    "input": json.dumps(
        {"json": payload, "meta": {"values": {"cursor": ["undefined"]}}}
    )
}

response = requests.get(
    f"https://civitai.com/api/trpc/{endpoint}", headers=headers, params=params
)

if response.status_code == 200:
    data = response.json()
    items = data.get("result", {}).get("data", {}).get("json", {}).get("items", [])
    next_cursor = (
        data.get("result", {}).get("data", {}).get("json", {}).get("nextCursor")
    )

    fmt.print_success(f"Successfully fetched {len(items)} items")
    if next_cursor:
        fmt.print_info(f"Next cursor: {next_cursor} (more items available)", indent=3)
    else:
        fmt.print_info("No next cursor (all items fetched)", indent=3)

    if len(items) > 0:
        fmt.print_blank()
        fmt.print_info("Sample items:")
        for i, item in enumerate(items[:3]):
            fmt.print_info(f"[{i+1}] ID: {item.get('id')}", indent=2)
            fmt.print_key_value("Name", item.get('name', 'Unknown'), indent=6)
            fmt.print_key_value("Author", item.get('user', {}).get('username', 'Unknown'), indent=6)
            fmt.print_key_value("Size", f"{item.get('width')}x{item.get('height')}", indent=6)
            fmt.print_blank()
else:
    fmt.print_error(f"Failed: {response.status_code}")
    fmt.print_info(response.text[:200], indent=2)

fmt.print_blank()
fmt.print_header("Testing Scraper")
fmt.print_blank()

# Test with the actual scraper
fmt.print_info("Initializing scraper...")
scraper = CivitaiPrivateScraper(auto_authenticate=False)

fmt.print_info(f"Scraping collection {collection_id}...")
data = scraper.scrape(collection_id)

if data:
    fmt.print_success(f"SUCCESS: Scraped {len(data)} images!")
    fmt.print_blank()
    fmt.print_info("Sample data:")
    for i, item in enumerate(data[:2]):
        fmt.print_info(f"[{i+1}] Image ID: {item['image_id']}", indent=2)
        fmt.print_key_value("Author", item['author'], indent=6)
        fmt.print_key_value("Model", f"{item['model']} - {item['model_version']}", indent=6)
        fmt.print_key_value("URL", item['image_url'], indent=6)
        fmt.print_blank()
else:
    fmt.print_error("No data found")
