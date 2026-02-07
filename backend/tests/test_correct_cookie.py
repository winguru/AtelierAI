#!/usr/bin/env python3
"""Test with the CORRECT cookie name: __Secure-civitai-token"""

import requests
import json
import os
from config import CIVITAI_SESSION_CACHE
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

collection_id = 12176069

fmt.print_header("Testing with CORRECT Cookie Name")
fmt.print_blank()

# Test with OLD (wrong) cookie name
fmt.print_subheader("Test 1: OLD cookie name (WRONG)")
fmt.print_info("Cookie: __Secure-next-auth.session-token", indent=3)

headers_old = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Cookie": f"__Secure-next-auth.session-token={token}",
    "Referer": "https://civitai.com/",
}

endpoint = "image.getInfinite"
payload = {"collectionId": int(collection_id), "authed": True, "cursor": None}
params = {
    "input": json.dumps(
        {"json": payload, "meta": {"values": {"cursor": ["undefined"]}}}
    )
}

response = requests.get(
    f"https://civitai.com/api/trpc/{endpoint}", headers=headers_old, params=params
)

data = response.json()
items_old = data.get("result", {}).get("data", {}).get("json", {}).get("items", [])
fmt.print_info(f"Items: {len(items_old)}", indent=3)
fmt.print_blank()

# Test with NEW (correct) cookie name
fmt.print_subheader("Test 2: NEW cookie name (CORRECT)")
fmt.print_info("Cookie: __Secure-civitai-token", indent=3)

headers_new = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Cookie": f"__Secure-civitai-token={token}",
    "Referer": "https://civitai.com/",
}

response = requests.get(
    f"https://civitai.com/api/trpc/{endpoint}", headers=headers_new, params=params
)

data = response.json()
items_new = data.get("result", {}).get("data", {}).get("json", {}).get("items", [])
fmt.print_info(f"Items: {len(items_new)}", indent=3)

if len(items_new) > 0:
    fmt.print_blank()
    fmt.print_success("The correct cookie name works!", indent=3)
    fmt.print_blank()
    fmt.print_info("Sample item:")
    print(json.dumps(items_new[0], indent=2))

    # Save to file
    with open("test_correct_cookie_output.json", "w") as f:
        json.dump(data, f, indent=2)
    fmt.print_blank()
    fmt.print_info("Full response saved to: test_correct_cookie_output.json")
else:
    fmt.print_blank()
    fmt.print_error("Still no items")

fmt.print_blank()
fmt.print_header("Comparison")
fmt.print_blank()
fmt.print_key_value("OLD cookie name (__Secure-next-auth.session-token)", f"{len(items_old)} items")
fmt.print_key_value("NEW cookie name (__Secure-civitai-token)", f"{len(items_new)} items")
