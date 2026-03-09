#!/usr/bin/env python3
"""
Mimic Postman's request format exactly.
If Postman works, this should too.
"""

import requests
import json
import os
import logging
from config import CIVITAI_SESSION_CACHE

# Get session token
if os.path.exists(CIVITAI_SESSION_CACHE):
    with open(CIVITAI_SESSION_CACHE, "r") as f:
        token = f.read().strip()
else:
    from config import MY_SESSION_COOKIE

    token = MY_SESSION_COOKIE

# Postman-style headers
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "Cookie": f"__Secure-next-auth.session-token={token}",
    "DNT": "1",
    "Origin": "https://civitai.com",
    "Pragma": "no-cache",
    "Referer": "https://civitai.com/",
    "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
}

# The exact URL
url = (
    "https://civitai.com/api/trpc/image.getInfinite?"
    "input=%7B%22json%22%3A%7B%22collectionId%22%3A12176069%2C%22period%22%3A%22AllTime%22%2C"
    "%22sort%22%3A%22Newest%22%2C%22browsingLevel%22%3A31%2C%22include%22%3A%5B%22cosmetics%22%5D%2C"
    "%22excludedTagIds%22%3A%5B415792%2C426772%2C5188%2C5249%2C130818%2C130820%2C133182%2C5351%2C"
    "306619%2C154326%2C161829%2C163032%5D%2C%22disablePoi%22%3Atrue%2C%22disableMinor%22%3Atrue%2C"
    "%22cursor%22%3Anull%2C%22authed%22%3Atrue%7D%2C%22meta%22%3A%7B%22values%22%3A%7B%22cursor%22%3A"
    "%5B%22undefined%22%5D%7D%7D%7D"
)

print("=" * 70)
print("Postman-Style Request Test")
print("=" * 70)
print()
print(f"URL: {url}")
print()
print("Headers:")
print(json.dumps(headers, indent=2))
print()
print("=" * 70)
print()

# Enable debug logging
logging.basicConfig()
logging.getLogger().setLevel(logging.DEBUG)

requests_log = logging.getLogger("requests.packages.urllib3")
requests_log.setLevel(logging.DEBUG)
requests_log.propagate = True

# Make the request with debug enabled
print("Making request...")
print()

try:
    response = requests.get(url, headers=headers, allow_redirects=True, verify=True)

    print()
    print("=" * 70)
    print(f"Status: {response.status_code}")
    print(f"Content-Type: {response.headers.get('Content-Type')}")
    print(f"Content-Length: {len(response.content)} bytes")
    print("=" * 70)
    print()

    if response.status_code == 200:
        try:
            data = response.json()
            items = (
                data.get("result", {}).get("data", {}).get("json", {}).get("items", [])
            )

            print(f"✅ Items found: {len(items)}")

            if len(items) > 0:
                print()
                print("🎉 SUCCESS! The request worked!")
                print()
                print("Sample item:")
                print(json.dumps(items[0], indent=2))
                print()

                # Save full response
                with open("postman_like_output.json", "w") as f:
                    json.dump(data, f, indent=2)
                print("Full response saved to: postman_like_output.json")
            else:
                print()
                print("❌ No items found")
                print()
                print("Full response:")
                print(json.dumps(data, indent=2))
        except json.JSONDecodeError:
            print("Response is not JSON")
            print(response.text[:500])
    else:
        print(f"Request failed with status {response.status_code}")
        print()
        print("Response:")
        print(response.text[:500])

except Exception as e:
    print(f"Error: {e}")
    import traceback

    traceback.print_exc()

print()
print("=" * 70)
