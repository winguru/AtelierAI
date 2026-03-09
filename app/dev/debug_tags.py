#!/usr/bin/env python3
"""Debug tags issue - check what data is available."""

import json
from src.civitai_api import CivitaiAPI

def main():
    image_id = 117165031

    print(f"=== Checking tags for Image ID {image_id} ===\n")

    api = CivitaiAPI.get_instance()

    print("1. Fetching basic info from image.get endpoint...")
    basic_info = api.fetch_basic_info(image_id)

    if basic_info:
        print(f"\n✅ Basic info keys: {list(basic_info.keys())}")

        # Check for tags in different locations
        if "tags" in basic_info:
            print(f"\nFound 'tags' field:")
            print(f"  Type: {type(basic_info['tags'])}")
            print(f"  Value: {basic_info['tags']}")
        else:
            print(f"\n❌ 'tags' field NOT found in basic info")

        # Check for tag-related fields
        tag_fields = ["tags", "tag", "hashtags", "keywords"]
        for field in tag_fields:
            if field in basic_info:
                print(f"\n✅ Found '{field}': {basic_info[field]}")

        # Show full basic_info response (truncated)
        print(f"\n2. Full basic_info response (first 500 chars):")
        print(json.dumps(basic_info, indent=2)[:500] + "...")

    else:
        print("❌ No basic info returned")

    print("\n" + "="*60)
    print("3. Checking generation data...")
    generation_data = api.fetch_generation_data(image_id)

    if generation_data:
        print(f"\nGeneration data keys: {list(generation_data.keys())}")

        if "meta" in generation_data:
            meta = generation_data["meta"]
            print(f"\nMeta keys: {list(meta.keys())}")

            # Check for tags in meta
            if "tags" in meta:
                print(f"  ✅ Found tags in meta: {meta['tags']}")
            else:
                print(f"  ❌ 'tags' NOT in meta")

            # Check for resources
            if "resources" in generation_data:
                print(f"\n  Resources count: {len(generation_data['resources'])}")
                for res in generation_data["resources"]:
                    print(f"    - {res.get('modelType', 'unknown')}: {res.get('modelName', 'unknown')}")

if __name__ == "__main__":
    main()
