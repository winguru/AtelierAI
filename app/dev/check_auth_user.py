#!/usr/bin/env python3
"""Check which user we're currently authenticated as"""

from config import CIVITAI_SESSION_CACHE

# Default browser state path
CIVITAI_BROWSER_STATE = ".civitai_browser_state"

import os
import json

print("=" * 70)
print("Checking Current Authentication")
print("=" * 70)
print()

# Check browser state
browser_state_path = CIVITAI_BROWSER_STATE
if os.path.exists(browser_state_path):
    print(f"Browser state exists: {browser_state_path}")
    print(f"   Size: {os.path.getsize(browser_state_path)} bytes")
else:
    print(f"No browser state found at: {browser_state_path}")

# Check session cache
session_cache_path = CIVITAI_SESSION_CACHE
if os.path.exists(session_cache_path):
    print(f"Session cache exists: {session_cache_path}")
    with open(session_cache_path, 'r') as f:
        token = f.read().strip()
        print(f"   Token length: {len(token)} chars")
        print(f"   Token prefix: {token[:50]}...")
else:
    print(f"No session cache found at: {session_cache_path}")

print()
print("=" * 70)
print("Fetching Current User from Civitai")
print("=" * 70)
print()

# Try to get current user info using the API
import requests

try:
    # Get session token from cache or config
    token = None
    if os.path.exists(session_cache_path):
        with open(session_cache_path, 'r') as f:
            token = f.read().strip()
    else:
        # Fall back to config
        from config import MY_SESSION_COOKIE
        token = MY_SESSION_COOKIE

    # Try to fetch user info
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Cookie": f"__Secure-next-auth.session-token={token}",
        "Referer": "https://civitai.com/",
    }

    # Try session endpoint
    response = requests.get(
        "https://civitai.com/api/trpc/session.get",
        headers=headers
    )

    if response.status_code == 200:
        data = response.json()
        user_data = data.get("result", {}).get("data", {}).get("json")

        if user_data:
            print("Successfully fetched user data:")
            print(f"   User ID: {user_data.get('id')}")
            print(f"   Username: {user_data.get('username')}")
            print(f"   Name: {user_data.get('name')}")
            print(f"   Image: {user_data.get('image')}")

            # Check if username is set
            username = user_data.get('username', 'NOT_SET')
            if username == 'NOT_SET':
                print()
                print("WARNING: Username is not set for this account!")
                print("   You may need to complete your profile setup.")
        else:
            print("User data is null or empty - session may be invalid")
    else:
        print(f"Failed to fetch user data: {response.status_code}")
        print(f"   Response: {response.text[:200]}")

except Exception as e:
    print(f"Error fetching user: {e}")

print()
print("=" * 70)
print("Recommendation")
print("=" * 70)
print()
print("If the username shown above is NOT your Civitai username that owns")
print("the collection, you need to re-authenticate with the correct account.")
print()
print("To re-authenticate:")
print("  1. Delete browser state and session cache")
print("  2. Run the authentication script")
print("  3. Sign in with the Google account linked to your Civitai account")
print()
print("Commands:")
print("  rm -f .civitai_browser_state .civitai_session")
print("  python civitai_auth.py --headless=false")
print()
print("Make sure to sign in with the SAME Google account that owns the")
print("collection (or has access to it)!")
print()
