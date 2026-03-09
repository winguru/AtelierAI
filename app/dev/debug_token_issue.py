#!/usr/bin/env python3
"""Debug why the token isn't working"""

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

collection_id = 12176069

print("=" * 70)
print("Debug: Token Issue Investigation")
print("=" * 70)
print()

print(f"Session Token:")
print(f"  Length: {len(token)} chars")
print(f"  Prefix: {token[:50]}...")
print(f"  Suffix: ...{token[-50:]}")
print()

# Test 1: Request WITHOUT any authentication
print("Test 1: Request WITHOUT authentication")
print("-" * 70)

headers_no_auth = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://civitai.com/",
}

endpoint = "image.getInfinite"
payload = {
    "collectionId": int(collection_id),
    "authed": False,  # Try without auth
    "cursor": None
}

params = {"input": json.dumps({"json": payload, "meta": {"values": {"cursor": ["undefined"]}}})}

response = requests.get(
    f"https://civitai.com/api/trpc/{endpoint}",
    headers=headers_no_auth,
    params=params
)

print(f"Status: {response.status_code}")

if response.status_code == 200:
    data = response.json()
    items = data.get("result", {}).get("data", {}).get("json", {}).get("items", [])
    print(f"Items: {len(items)}")
    
    if len(items) > 0:
        print(f"✅ Got {len(items)} items WITHOUT authentication!")
        print(f"   This suggests the collection might be publicly accessible")
    else:
        print(f"❌ Got 0 items without auth")
else:
    print(f"Response: {response.text[:200]}")

print()

# Test 2: Request WITH your token
print("Test 2: Request WITH your session token")
print("-" * 70)

headers_with_token = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Cookie": f"__Secure-next-auth.session-token={token}",
    "Referer": "https://civitai.com/",
}

payload_auth = {
    "collectionId": int(collection_id),
    "authed": True,  # With auth
    "cursor": None
}

params_auth = {"input": json.dumps({"json": payload_auth, "meta": {"values": {"cursor": ["undefined"]}}})}

response_auth = requests.get(
    f"https://civitai.com/api/trpc/{endpoint}",
    headers=headers_with_token,
    params=params_auth
)

print(f"Status: {response_auth.status_code}")

if response_auth.status_code == 200:
    data_auth = response_auth.json()
    items_auth = data_auth.get("result", {}).get("data", {}).get("json", {}).get("items", [])
    print(f"Items: {len(items_auth)}")
    
    if len(items_auth) > 0:
        print(f"✅ Got {len(items_auth)} items WITH authentication")
    else:
        print(f"❌ Got 0 items with auth - token may be invalid")
        print()
        print("Full response:")
        print(json.dumps(data_auth, indent=2))
else:
    print(f"Response: {response_auth.text[:200]}")

print()

# Test 3: Try the EXACT URL you provided (decoded)
print("Test 3: Try exact URL from your browser (decoded)")
print("-" * 70)

from urllib.parse import unquote

url = "https://civitai.com/api/trpc/image.getInfinite?input=%7B%22json%22%3A%7B%22collectionId%22%3A12176069%2C%22period%22%3A%22AllTime%22%2C%22sort%22%3A%22Newest%22%2C%22browsingLevel%22%3A31%2C%22include%22%3A%5B%22cosmetics%22%5D%2C%22excludedTagIds%22%3A%5B415792%2C426772%2C5188%2C5249%2C130818%2C130820%2C133182%2C5351%2C306619%2C154326%2C161829%2C163032%5D%2C%22disablePoi%22%3Atrue%2C%22disableMinor%22%3Atrue%2C%22cursor%22%3Anull%2C%22authed%22%3Atrue%7D%2C%22meta%22%3A%7B%22values%22%3A%7B%22cursor%22%3A%5B%22undefined%22%5D%7D%7D%7D"

# Decode the input parameter
parsed_url = unquote(url)
print(f"Decoded URL: {parsed_url}")
print()

# Try without auth first
response_no_auth = requests.get(url, headers=headers_no_auth)

print(f"Status (no auth): {response_no_auth.status_code}")
if response_no_auth.status_code == 200:
    data_no_auth = response_no_auth.json()
    items_no_auth = data_no_auth.get("result", {}).get("data", {}).get("json", {}).get("items", [])
    print(f"Items: {len(items_no_auth)}")
    if len(items_no_auth) > 0:
        print(f"✅ Works without auth!")

# Try with token
response_with_token = requests.get(url, headers=headers_with_token)
print(f"Status (with token): {response_with_token.status_code}")
if response_with_token.status_code == 200:
    data_with_token = response_with_token.json()
    items_with_token = data_with_token.get("result", {}).get("data", {}).get("json", {}).get("items", [])
    print(f"Items: {len(items_with_token)}")

print()
print("=" * 70)
print("Summary & Recommendations")
print("=" * 70)
print()

if response_no_auth.status_code == 200:
    data_no_auth = response_no_auth.json()
    items_no_auth = data_no_auth.get("result", {}).get("data", {}).get("json", {}).get("items", [])
    
    if len(items_no_auth) > 0:
        print("✅ GOOD NEWS: The collection works WITHOUT authentication!")
        print()
        print("This means:")
        print("  - The collection is actually public (not private)")
        print("  - Your session token might be corrupted or from a different account")
        print("  - The scraper can work without session tokens for public collections")
        print()
        print("Recommendation: Try scraping with authed=False")
    else:
        print("❌ The collection requires authentication, but your token isn't working")
        print()
        print("Possible causes:")
        print("  1. Token expired (session tokens last ~30 days)")
        print("  2. Token copied from wrong browser tab/session")
        print("  3. You have multiple Civitai accounts")
        print()
        print("To fix:")
        print("  1. Open Civitai.com in a fresh tab")
        print("  2. Sign in (make sure you're on the right account)")
        print("  3. Get a fresh token from that tab")
        print("  4. Run: python setup_session_token.py")
