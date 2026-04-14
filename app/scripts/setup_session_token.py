#!/usr/bin/env python3
"""
Simple script to manually set your CivitAI session token.
Use this if you're in a Docker container and can't run the browser authentication.
"""

import os

# Setup paths
from path_setup import PROJECT_ROOT

def confirm_short_token(token: str) -> bool:
    if len(token) >= 200:
        return True
    print()
    print("⚠️  WARNING: The token seems too short!")
    print("   Session tokens are typically 1000+ characters.")
    print("   Please make sure you copied the full token value.")
    confirm = input("Continue anyway? (y/N): ").strip().lower()
    if confirm != "y":
        print("Cancelled.")
        return False
    return True

def save_token_to_cache(cache_file: str, token: str) -> None:
    try:
        with open(cache_file, "w") as f:
            f.write(token)

        print()
        print("=" * 70)
        print("✅ SUCCESS!")
        print("=" * 70)
        print(f"Token saved to {cache_file}")
        print(f"Token length: {len(token)} characters")
        print()
    except Exception as e:
        print()
        print(f"❌ Error saving token to cache: {e}")
        exit(1)

print("=" * 70)
print("CivitAI Session Token Setup")
print("=" * 70)
print()

print("Please follow these steps:")
print()
print("1. Open Civitai.com in your regular browser")
print("2. Sign in with the Google account that owns the collection")
print("3. Open Developer Tools (F12)")
print("4. Go to Application > Cookies > https://civitai.com")
print("5. Find '__Secure-civitai-token' cookie (NOT next-auth)")
print("6. Copy the Value (starts with 'eyJ...')")
print()
print("=" * 70)
print()

# Get token from user input
token = input("Paste your session token here (or press Enter to skip): ").strip()

if token:
    if not confirm_short_token(token):
        exit(0)

    cache_file = os.path.join(PROJECT_ROOT, ".civitai_session")
    save_token_to_cache(cache_file, token)

    print()
    print("You can now run the scraper:")
    print("  python test_detailed_scrape.py")
    print()
    print("To verify it works:")
    print("  python tests/test_private_access.py")
    print()
else:
    print("No token provided. Exiting.")
