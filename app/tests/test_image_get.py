#!/usr/bin/env python3
"""Test different API endpoints to find basic image info."""

import requests
import json
from src.civitai import CivitaiPrivateScraper

def test_endpoint(endpoint_name: str, image_id: int, scraper: CivitaiPrivateScraper):
    """Test a specific API endpoint."""
    print(f"\n{'='*60}")
    print(f"Testing: {endpoint_name}")
    print(f"{'='*60}")

    url = f"{scraper.base_url}/{endpoint_name}"
    payload_data = {"id": int(image_id), "authed": True}
    params = {"input": scraper._build_trpc_payload(payload_data)}

    print(f"Requesting: {url}")

    response = requests.get(url, headers=scraper._get_headers(), params=params)

    print(f"Status code: {response.status_code}")

    if response.status_code == 200:
        try:
            data = response.json()

            # Navigate tRPC response structure
            if "result" in data and "data" in data["result"]:
                result_data = data["result"]["data"]
                if "json" in result_data:
                    api_data = result_data["json"]
                else:
                    api_data = result_data
            else:
                api_data = data

            print(f"\n✅ Success!")
            print(f"Top-level keys: {list(api_data.keys()) if isinstance(api_data, dict) else 'Not a dict'}")

            # Check for our needed fields
            needed = ["username", "user", "author", "createdAt", "nsfw", "url"]
            found = []

            for field in needed:
                if field in api_data:
                    found.append(field)
                    if field != "url":
                        print(f"  ✅ Found '{field}': {api_data[field]}")
                    else:
                        print(f"  ✅ Found '{field}'")

            if not found:
                print("  ❌ None of the needed fields found")

            # Show relevant fields
            print(f"\nKeys matching our needs: {found}")

        except Exception as e:
            print(f"❌ Error parsing JSON: {e}")
    else:
        print(f"❌ Request failed with status {response.status_code}")

def main():
    image_id = 117165031

    print(f"Testing API Endpoints for Image ID {image_id}\n")
    print("Looking for: username, user, author, createdAt, nsfw, url\n")

    scraper = CivitaiPrivateScraper(auto_authenticate=True)

    # Test different endpoint variations
    endpoints_to_test = [
        "image.getById",
        "image.get",
        "image.info",
        "images.getById",
        "get.image",
        "image.metadata",
    ]

    for endpoint in endpoints_to_test:
        test_endpoint(endpoint, image_id, scraper)

    print(f"\n{'='*60}")
    print("Testing complete!")
    print(f"{'='*60}\n")

    print("Summary:")
    print("If none of these endpoints work, we'll need to:")
    print("  1. Use available data from image.getGenerationData")
    print("  2. Mark author/nsfw/created_at as 'Not available'")

if __name__ == "__main__":
    main()
