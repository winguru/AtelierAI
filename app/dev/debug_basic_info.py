#!/usr/bin/env python3
"""Debug script to see what image.getById API returns."""

import json
from src.civitai import CivitaiPrivateScraper

def main():
    image_id = 117165031

    print(f"=== Debugging image.getById API - ID {image_id} ===\n")

    scraper = CivitaiPrivateScraper(auto_authenticate=True)

    print("Calling fetch_image_basic_info()...")
    basic_info = scraper.fetch_image_basic_info(image_id)

    if basic_info:
        print(f"\n✅ Success! Response type: {type(basic_info)}")
        print(f"Is dict: {isinstance(basic_info, dict)}")

        if isinstance(basic_info, dict):
            print(f"\nTop-level keys: {list(basic_info.keys())}")

            print("\n" + "="*60)
            print("FULL RESPONSE:")
            print("="*60)
            print(json.dumps(basic_info, indent=2))
        else:
            print(f"\nUnexpected response type: {type(basic_info)}")
            print(f"Content: {basic_info}")
    else:
        print("❌ No data returned from API!")
        print("\nThis might mean:")
        print("  1. API endpoint name is incorrect")
        print("  2. Image doesn't exist or is private")
        print("  3. API response structure is different")
        print("  4. Authentication issue")

if __name__ == "__main__":
    main()
