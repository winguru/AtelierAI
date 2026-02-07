#!/usr/bin/env python3
"""Debug script to see actual API response structure for an image."""

import json
from src.civitai import CivitaiPrivateScraper

def main():
    image_id = 117165031

    print(f"=== Debugging Image API - ID {image_id} ===\n")

    scraper = CivitaiPrivateScraper(auto_authenticate=True)

    print("Fetching image generation data...")
    details = scraper.fetch_image_details(image_id)

    if details:
        print(f"\nResponse type: {type(details)}")
        print(f"Response is dict: {isinstance(details, dict)}")

        if isinstance(details, dict):
            print(f"\nTop-level keys: {list(details.keys())}")

            print("\n" + "="*60)
            print("FULL API RESPONSE (pretty-printed):")
            print("="*60)
            print(json.dumps(details, indent=2))

            print("\n" + "="*60)
            print("CHECKING FOR META SECTION:")
            print("="*60)

            if "meta" in details:
                print("\nFound 'meta' section:")
                print(json.dumps(details["meta"], indent=2))
            else:
                print("\nNo 'meta' section found!")

            print("\n" + "="*60)
            print("CHECKING FOR RESOURCES SECTION:")
            print("="*60)

            if "resources" in details:
                print(f"\nFound 'resources' section with {len(details['resources'])} items:")
                print(json.dumps(details["resources"], indent=2))
            else:
                print("\nNo 'resources' section found!")
        else:
            print(f"\nResponse is not a dict! Type: {type(details)}")
            if details:
                print(f"Content: {details}")
    else:
        print("No data returned from API!")

if __name__ == "__main__":
    main()
