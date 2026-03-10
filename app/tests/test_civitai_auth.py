#!/usr/bin/env python3
"""
Quick test script to verify CivitAI authentication works.
Run this after setting up authentication for the first time.
"""

import sys


def test_auto_auth():
    """Test automatic authentication"""
    print("=" * 70)
    print("Testing CivitAI Automatic Authentication")
    print("=" * 70)
    print()

    # Import the authentication function
    try:
        from atelierai.civitai.civitai_auth import get_cached_or_refresh_session_token

        print("✅ civitai_auth module imported successfully")
    except ImportError as e:
        print(f"❌ Failed to import civitai_auth: {e}")
        print("\nMake sure Playwright is installed:")
        print("  pip install playwright")
        print("  playwright install chromium")
        return False

    # Try to get a session token
    print()
    print("Attempting to get session token...")
    print("-" * 70)

    try:
        token = get_cached_or_refresh_session_token(headless=True)

        if not token or len(token) < 50:
            print("❌ Invalid session token (too short)")
            return False

        print("-" * 70)
        print()
        print("✅ Authentication successful!")
        print()
        print(f"Session token length: {len(token)} characters")
        print(f"Session token preview: {token[:80]}...")
        print()

        # Check if cached
        import os

        if os.path.exists(".civitai_session"):
            print("✅ Session token cached to .civitai_session")
        else:
            print("⚠️  Session token not cached (this is OK for headless mode)")

        if os.path.exists(".civitai_browser_state"):
            print("✅ Browser state saved to .civitai_browser_state")
        else:
            print("ℹ️  No browser state saved (run without headless first)")

        return True

    except Exception as e:
        print("-" * 70)
        print()
        print(f"❌ Authentication failed: {e}")
        print()
        print("Troubleshooting tips:")
        print("1. First-time setup: Run 'python civitai_auth.py' (no --headless)")
        print("2. Complete Google OAuth in the browser window")
        print("3. Then run this test again")
        return False


def test_scraper_with_auth():
    """Test the scraper with automatic authentication"""
    print()
    print("=" * 70)
    print("Testing CivitAI Scraper with Auto-Auth")
    print("=" * 70)
    print()

    try:
        from atelierai.civitai import CivitaiPrivateScraper

        print("Initializing scraper with auto_authenticate=True...")
        scraper = CivitaiPrivateScraper(auto_authenticate=True)
        print("✅ Scraper initialized successfully")

        print()
        print("Testing with collection ID 11035255...")
        print("(Fetching a small amount of data to verify connection)")
        print()

        data = scraper.scrape(11035255)

        if data:
            print()
            print("=" * 70)
            print("✅ Scraper test successful!")
            print("=" * 70)
            print()
            print(f"Fetched {len(data)} items from collection")
            print()

            if data:
                print("Sample data:")
                first_item = data[0]
                print(f"  Image ID: {first_item.get('image_id')}")
                print(f"  Author: {first_item.get('author')}")
                print(f"  Model: {first_item.get('model')}")
                print(f"  Has prompt: {bool(first_item.get('prompt'))}")
                print()

            return True
        else:
            print()
            print("⚠️  No data returned (collection might be empty or private)")
            print("   But authentication appears to have worked!")
            return True

    except Exception as e:
        print()
        print(f"❌ Scraper test failed: {e}")
        print()

        if "401" in str(e) or "unauthorized" in str(e).lower():
            print("⚠️  Authentication error. Try:")
            print("   1. Delete .civitai_session and .civitai_browser_state")
            print("   2. Run: python civitai_auth.py")
            print("   3. Complete OAuth in the browser window")

        return False


def main():
    """Run all tests"""
    print()
    print("╔" + "=" * 68 + "╗")
    print("║" + " " * 68 + "║")
    print("║" + "  CivitAI Authentication Test Suite".center(68) + "║")
    print("║" + " " * 68 + "║")
    print("╚" + "=" * 68 + "╝")
    print()

    # Test 1: Authentication
    auth_success = test_auto_auth()

    # Test 2: Scraper (only if auth succeeded)
    scraper_success = False
    if auth_success:
        scraper_success = test_scraper_with_auth()

    # Summary
    print()
    print("=" * 70)
    print("Test Summary")
    print("=" * 70)
    print()

    if auth_success:
        print("✅ Authentication: PASS")
    else:
        print("❌ Authentication: FAIL")

    if scraper_success:
        print("✅ Scraper: PASS")
    elif auth_success:
        print("⚠️  Scraper: NOT TESTED (auth failed)")
    else:
        print("❌ Scraper: FAIL")

    print()

    if auth_success and scraper_success:
        print("🎉 All tests passed! Your setup is working correctly.")
        print()
        print("You can now use:")
        print("  scraper = CivitaiPrivateScraper(auto_authenticate=True)")
        print()
        return 0
    else:
        print("⚠️  Some tests failed. See above for troubleshooting tips.")
        print()
        return 1


if __name__ == "__main__":
    sys.exit(main())
