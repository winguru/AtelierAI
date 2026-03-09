#!/usr/bin/env python3
"""Find the owner of a collection"""

import requests
import json

collection_id = 12176069

print("=" * 70)
print("Finding Collection Owner")
print("=" * 70)
print()

# Try to get collection info without authentication (public metadata)
response = requests.get(
    f"https://civitai.com/api/trpc/collection.getById",
    params={"input": json.dumps({"json": {"id": collection_id, "authed": False}, "meta": {"values": {"cursor": ["undefined"]}}})},
    headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    }
)

if response.status_code == 200:
    data = response.json()
    collection = data.get("result", {}).get("data", {}).get("json", {}).get("collection")
    
    if collection:
        print(f"Collection ID: {collection.get('id')}")
        print(f"Collection Name: {collection.get('name', 'Unknown')}")
        
        # Look for user/owner info
        if "user" in collection:
            user = collection["user"]
            print(f"Owner Username: {user.get('username', 'Unknown')}")
            print(f"Owner ID: {user.get('id', 'Unknown')}")
            print(f"Owner Name: {user.get('name', 'Unknown')}")
        elif "userId" in collection:
            print(f"Owner User ID: {collection.get('userId')}")
        
        print(f"Public: {collection.get('public', False)}")
        print()
        print("=" * 70)
        print("IMPORTANT")
        print("=" * 70)
        print()
        print("The owner shown above is the Civitai username that owns this collection.")
        print()
        print("To access this private collection, you need to:")
        print("  1. Sign in to Civitai with this EXACT username")
        print("  2. Get the session token from that account")
        print("  3. Use that token with the scraper")
        print()
        print("Steps:")
        print("  1. Open https://civitai.com in your browser")
        print("  2. Sign out if you're logged in")
        print("  3. Sign in with the Google account linked to username above")
        print("  4. Verify you see your profile picture and correct username")
        print("  5. Get the session token (F12 > Application > Cookies)")
        print("  6. Run: python setup_session_token.py")
        print("  7. Paste the new token")
        print()
    else:
        print("Collection data not found - collection may be private and inaccessible without auth")
else:
    print(f"Failed to fetch collection info: {response.status_code}")
    print(response.text)
