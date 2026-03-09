#!/usr/bin/env python3
"""Test private collection access with current session"""

import requests
import json
from config import CIVITAI_SESSION_CACHE
import os
from console_utils import ConsoleFormatter

# Initialize formatter with default line length of 70
fmt = ConsoleFormatter()

# Get session token
if os.path.exists(CIVITAI_SESSION_CACHE):
    with open(CIVITAI_SESSION_CACHE, "r") as f:
        token = f.read().strip()
else:
    from config import CIVITAI_SESSION_COOKIE
    token = CIVITAI_SESSION_COOKIE

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Cookie": f"__Secure-civitai-token={token}",  # FIXED: Correct cookie name
    "Referer": "https://civitai.com/",
}

collection_id = 11035255

fmt.print_header(f"Testing Private Collection Access")
fmt.print_blank()

# Test 1: Check collection permissions
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

collection_data = None  # Store collection data for Test 2

if response.status_code == 200:
    data = response.json()
    collection_data = data.get("result", {}).get("data", {}).get("json", {}).get("collection", {})
    permissions = (
        data.get("result", {}).get("data", {}).get("json", {}).get("permissions", {})
    )

    # Display collection information
    fmt.print_info("Collection Details:")
    fmt.print_key_value("Collection ID", collection_data.get('id'), indent=4)
    fmt.print_key_value("Collection URL", f"https://civitai.com/collections/{collection_data.get('id')}", indent=4)
    fmt.print_key_value("Collection Name", collection_data.get('name', 'Unknown'), indent=4)
    fmt.print_key_value("Description", collection_data.get('description') or 'No description', indent=4)
    fmt.print_key_value("Read Access", collection_data.get('read'), indent=4)
    fmt.print_key_value("Write Access", collection_data.get('write'), indent=4)
    fmt.print_key_value("Type", collection_data.get('type'), indent=4)
    fmt.print_key_value("Availability", collection_data.get('availability'), indent=4)
    fmt.print_key_value("NSFW Level", collection_data.get('nsfwLevel'), indent=4)
    fmt.print_blank()
    
    fmt.print_info("Permissions:")
    for key, value in permissions.items():
        fmt.print_permission(key, value, indent=4)

    # Check if we're the owner
    if permissions.get("isOwner"):
        fmt.print_blank()
        fmt.print_success("You are the collection owner!", indent=4)
        fmt.print_info("The collection should be accessible.", indent=2)
    elif permissions.get("read"):
        fmt.print_blank()
        fmt.print_success("You have read access to this collection")
    else:
        fmt.print_blank()
        fmt.print_error("You don't have read access to this collection!", indent=4)
        fmt.print_info("This means:", indent=4)
        fmt.print_info("1. The collection is private", indent=4)
        fmt.print_info("2. Your session is NOT authenticated as the owner", indent=4)
        fmt.print_info("3. You're signed in with the wrong Google account", indent=4)
else:
    print(f"Failed: {response.status_code}")
    print(response.text)

fmt.print_blank()

# Test 2: Display current user info from collection response
fmt.print_subheader("Test 2: Find Current User")

if collection_data and collection_data.get("user"):
    user_info = collection_data.get("user")
    
    fmt.print_success("Current User Information (from collection owner):", indent=2)
    fmt.print_blank()
    fmt.print_key_value("User ID", user_info.get('id'), indent=5)
    fmt.print_key_value("User Profile URL", f"https://civitai.com/user/{user_info.get('username')}", indent=5)
    fmt.print_key_value("Username", user_info.get('username'), indent=5)
    
    # Fix: Display account status properly
    deleted_at = user_info.get('deletedAt')
    if deleted_at:
        fmt.print_key_value("Account Status", "Deleted", indent=5)
        fmt.print_key_value("Deleted At", deleted_at, indent=5)
    else:
        fmt.print_key_value("Account Status", "Active", indent=5)
    
    profile_image = user_info.get('image')
    if profile_image:
        fmt.print_key_value("Profile Image URL", profile_image, indent=5)
    else:
        fmt.print_info("No profile image available", indent=5)
    
    cosmetics = user_info.get('cosmetics', [])
    if cosmetics:
        fmt.print_key_value("Cosmetics", f"{len(cosmetics)} items", indent=5)
    
    fmt.print_blank()
    fmt.print_info("This user information comes from the collection owner data.", indent=2)
    fmt.print_info("If this is not your account, you're signed in with the wrong Google account.", indent=2)
else:
    fmt.print_error("Could not extract user info from collection data", indent=2)

fmt.print_blank()

# Test 3: Check what collections the user has access to
fmt.print_subheader("Test 3: List User Collections")

# Get username from collection data if available
username = collection_data.get("user", {}).get("username") if collection_data else None

# Try different approaches to list collections
test_approaches = [
    {
        "name": "user.getCollections with empty username (current user)",
        "endpoint": "user.getCollections",
        "payload": {
            "username": "",
            "limit": 10,
            "authed": True
        }
    },
    {
        "name": f"user.getCollections with username '{username}'",
        "endpoint": "user.getCollections",
        "payload": {
            "username": username,
            "limit": 10,
            "authed": True
        }
    },
    {
        "name": "collections.getByUser",
        "endpoint": "collections.getByUser",
        "payload": {
            "username": username,
            "limit": 10,
            "authed": True
        }
    }
]

collections_found = False

for approach in test_approaches:
    if not approach["payload"].get("username"):
        continue  # Skip empty username tests
    
    endpoint = approach["endpoint"]
    payload = approach["payload"]
    
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
        collections = data.get("result", {}).get("data", {}).get("json", [])
        
        # Handle both list and dict responses
        if isinstance(collections, dict):
            # Try to find items in common keys
            collections = collections.get("items") or collections.get("collections") or []
        
        if collections and len(collections) > 0:
            fmt.print_success(f"✅ Found {len(collections)} collections via: {approach['name']}")
            for coll in collections[:5]:
                coll_id = coll.get("id")
                coll_name = coll.get("name", "Unknown")
                coll_private = coll.get("read", "Unknown")
                fmt.print_info(f"ID {coll_id}: {coll_name} (Access: {coll_private})", indent=3)
            collections_found = True
            break
    else:
        # Only show the first failure for empty username
        if not approach["payload"].get("username"):
            continue

if not collections_found:
    fmt.print_info("Could not retrieve user collections via tested endpoints.", indent=3)
    fmt.print_info("This endpoint may not exist or may require different parameters.", indent=3)

fmt.print_blank()

# Test 4: Try with image.getInfinite (the original endpoint)
fmt.print_subheader("Test 4: image.getInfinite with Private Collection")

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

    fmt.print_info(f"Items returned: {len(items)}", indent=3)

    if len(items) > 0:
        fmt.print_success("Collection is accessible via image.getInfinite", indent=3)
    else:
        fmt.print_error("Empty items array - collection may be private or inaccessible", indent=3)
else:
    fmt.print_error(f"Failed: {response.status_code}")

fmt.print_blank()
fmt.print_header("Summary")
fmt.print_blank()
fmt.print_info("If you see 'read: false' and 'isOwner: false', you need to:")
fmt.print_blank()
fmt.print_info("1. Delete current session:")
fmt.print_info("   rm -f .civitai_browser_state .civitai_session")
fmt.print_blank()
fmt.print_info("2. Re-authenticate with correct account:")
fmt.print_info("   python civitai_auth.py --headless=false")
fmt.print_blank()
fmt.print_info("3. IMPORTANT: Sign in with the Google account that owns")
fmt.print_info("   the Civitai account that created the collection!")
fmt.print_blank()
