#!/usr/bin/env python3
"""Debug script to see FULL getGenerationData response."""

import json
from src.civitai import CivitaiPrivateScraper

def main():
    image_id = 117165031

    print(f"=== Full getGenerationData Response - ID {image_id} ===\n")

    scraper = CivitaiPrivateScraper(auto_authenticate=True)

    print("Calling fetch_image_details()...")
    details = scraper.fetch_image_details(image_id)

    if details:
        print(f"\n✅ Got data! Type: {type(details)}")
        print(f"Is dict: {isinstance(details, dict)}")

        if isinstance(details, dict):
            print(f"\nAll top-level keys: {list(details.keys())}")

            # Check for fields we need
            needed_fields = ["url", "user", "nsfw", "createdAt", "username", "account"]
            print("\n" + "="*60)
            print("Checking for basic info fields:")
            print("="*60)

            for field in needed_fields:
                if field in details:
                    print(f"✅ Found '{field}': {details[field]}")
                else:
                    print(f"❌ Missing '{field}'")

            print("\n" + "="*60)
            print("FULL RESPONSE:")
            print("="*60)
            print(json.dumps(details, indent=2))
    else:
        print("❌ No data returned!")

if __name__ == "__main__":
    main()
