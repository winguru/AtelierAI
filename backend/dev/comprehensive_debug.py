#!/usr/bin/env python3
"""Comprehensive debug script to understand the issue"""

import requests
import json
import os
from config import CIVITAI_SESSION_CACHE
from urllib.parse import unquote

# Get session token
if os.path.exists(CIVITAI_SESSION_CACHE):
    with open(CIVITAI_SESSION_CACHE, 'r') as f:
        token = f.read().strip()
else:
    from config import MY_SESSION_COOKIE
    token = MY_SESSION_COOKIE

collection_id = 12176069

print("=" * 70)
print("COMPREHENSIVE DEBUG")
print("=" * 70)
print()

print("Collection ID:", collection_id)
print()

# Test 1: Try multiple endpoint variations
print("Test 1: Try Different Endpoint Variations")
print("-" * 70)

test_endpoints = [
    ("image.getInfinite with authed=true", {"collectionId": collection_id, "authed": True, "cursor": None}),
    ("image.getInfinite with authed=false", {"collectionId": collection_id, "authed": False, "cursor": None}),
    ("image.getInfinite minimal", {"collectionId": collection_id}),
]

for test_name, payload in test_endpoints:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Cookie": f"__Secure-next-auth.session-token={token}",
        "Referer": "https://civitai.com/",
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
    }
    
    params = {"input": json.dumps({"json": payload, "meta": {"values": {"cursor": ["undefined"]}}})}
    
    response = requests.get(
        "https://civitai.com/api/trpc/image.getInfinite",
        headers=headers,
        params=params
    )
    
    data = response.json()
    items = data.get("result", {}).get("data", {}).get("json", {}).get("items", [])
    
    print(f"{test_name}:")
    print(f"  Status: {response.status_code}")
    print(f"  Items: {len(items)}")
    print()

print()

# Test 2: Decode the exact URL from browser
print("Test 2: Decode Browser URL")
print("-" * 70)

url_from_browser = "https://civitai.com/api/trpc/image.getInfinite?input=%7B%22json%22%3A%7B%22collectionId%22%3A12176069%2C%22period%22%3A%22AllTime%22%2C%22sort%22%3A%22Newest%22%2C%22browsingLevel%22%3A31%2C%22include%22%3A%5B%22cosmetics%22%5D%2C%22excludedTagIds%22%3A%5B415792%2C426772%2C5188%2C5249%2C130818%2C130820%2C133182%2C5351%2C306619%2C154326%2C161829%2C163032%5D%2C%22disablePoi%22%3Atrue%2C%22disableMinor%22%3Atrue%2C%22cursor%22%3Anull%2C%22authed%22%3Atrue%7D%2C%22meta%22%3A%7B%22values%22%3A%7B%22cursor%22%3A%5B%22undefined%22%5D%7D%7D%7D"

# Parse the URL to extract the input parameter
from urllib.parse import urlparse, parse_qs

parsed = urlparse(url_from_browser)
input_param = parse_qs(parsed.urlencode())['input'][0] if 'input' in parse_qs(parsed.urlencode()) else None

if input_param:
    decoded = json.loads(unquote(input_param))
    print("Decoded input from browser URL:")
    print(json.dumps(decoded, indent=2))
    print()

# Test the exact URL
headers_no_cookie = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://civitai.com/",
}

headers_with_cookie = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Cookie": f"__Secure-next-auth.session-token={token}",
    "Referer": "https://civitai.com/",
}

print("Testing exact browser URL:")
print()

response = requests.get(url_from_browser, headers=headers_no_cookie)
print(f"No cookie: {response.status_code}, Items: {len(response.json().get('result', {}).get('data', {}).get('json', {}).get('items', []))}")

response = requests.get(url_from_browser, headers=headers_with_cookie)
print(f"With cookie: {response.status_code}, Items: {len(response.json().get('result', {}).get('data', {}).get('json', {}).get('items', []))}")

print()
print()

# Test 3: Check if we can get the collection page directly
print("Test 3: Fetch Collection HTML Page")
print("-" * 70)

collection_url = f"https://civitai.com/collections/{collection_id}"
response = requests.get(collection_url, headers=headers_no_cookie)

print(f"Collection page status: {response.status_code}")

if response.status_code == 200:
    # Check if page has content
    content_length = len(response.text)
    print(f"Content length: {content_length} bytes")
    
    # Look for any clues in HTML
    if "images" in response.text.lower() or "items" in response.text.lower():
        print("✅ Page contains image/item references")
    else:
        print("❌ Page doesn't seem to have image references")
    
    # Check if there's a redirect to login
    if "sign-in" in response.text.lower() or "login" in response.text.lower():
        print("⚠️  Page might be redirecting to login")

print()
print()

# Test 4: Validate token structure
print("Test 4: Validate Token Structure")
print("-" * 70)

print(f"Token length: {len(token)}")
print(f"Token parts (split by '.'): {len(token.split('.'))}")

parts = token.split('.')
if len(parts) == 3:
    header = parts[0]
    print(f"Header length: {len(header)}")
    print(f"Header prefix: {header[:30]}...")
else:
    print("Token structure looks unusual (not standard JWT)")

print()
print()

# Test 5: Try a known public collection for comparison
print("Test 5: Test with a Known Public Collection")
print("-" * 70)

# Try a small collection that's likely public
test_public_id = 11035255  # The original one
endpoint = "image.getInfinite"
payload = {
    "collectionId": int(test_public_id),
    "authed": True,
    "cursor": None
}
params = {"input": json.dumps({"json": payload, "meta": {"values": {"cursor": ["undefined"]}}})}

response = requests.get(
    f"https://civitai.com/api/trpc/{endpoint}",
    headers=headers_with_cookie,
    params=params
)

data = response.json()
items = data.get("result", {}).get("data", {}).get("json", {}).get("items", [])

print(f"Collection {test_public_id}: {len(items)} items")

print()
print("=" * 70)
print("SUMMARY")
print("=" * 70)
print()

print("Based on the tests above:")
print()
print("If ALL tests return 0 items:")
print("  → The collections may not be accessible via API")
print("  → There might be IP-based or device-based restrictions")
print()
print("If some tests work and others don't:")
print("  → Note which configuration worked")
print()
print("RECOMMENDATION:")
print("  The session token you provided may not have the right permissions.")
print("  Please ensure you:")
print("  1. Are on the EXACT same browser tab where you see the collections")
print("  2. Get a FRESH token from that tab")
print("  3. Run: python setup_session_token.py")
print()
