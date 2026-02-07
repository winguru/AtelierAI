#!/usr/bin/env python3
"""Debug script to find tag name resolution API."""

import json
from src.civitai_api import CivitaiAPI

def main():
    api = CivitaiAPI.get_instance()

    print("=" * 70)
    print("Looking for tag name resolution API")
    print("=" * 70)
    print()

    # From post infinite, we saw tagIds like: 4, 81, 292, 3629
    test_tag_ids = [4, 81, 292, 3629]

    # 1. Try to get tag names by ID
    print("1. Trying tag.getById...")
    for tag_id in test_tag_ids[:2]:  # Just try first 2
        print(f"   Tag ID {tag_id}:")
        response = api._make_request(
            endpoint="tag.getById",
            payload_data={"id": tag_id}
        )
        if response:
            print(f"      Success! {json.dumps(response, indent=2)[:200]}")
        else:
            print("      Failed")
    print()

    # 2. Try tag.getByIds (plural)
    print("2. Trying tag.getByIds with multiple IDs...")
    response = api._make_request(
        endpoint="tag.getByIds",
        payload_data={"ids": test_tag_ids}
    )
    if response:
        print(f"   Success! Type: {type(response)}")
        if isinstance(response, dict):
            print(f"   Keys: {list(response.keys())[:10]}")
        elif isinstance(response, list):
            print(f"   Got {len(response)} tags")
            if len(response) > 0:
                print(f"   First tag: {json.dumps(response[0], indent=2)[:200]}")

        # Save to file
        with open("debug_tag_names.json", "w") as f:
            json.dump(response, f, indent=2)
        print("   Saved to: debug_tag_names.json")
    else:
        print("   Failed")
    print()

    # 3. Try tag.getInfinite to see what's available
    print("3. Trying tag.getInfinite...")
    response = api._make_request(
        endpoint="tag.getInfinite",
        payload_data={"limit": 10}
    )
    if response:
        print(f"   Success! Type: {type(response)}")
        if isinstance(response, dict):
            print(f"   Keys: {list(response.keys())}")

        # Save to file
        with open("debug_tag_infinite.json", "w") as f:
            json.dump(response, f, indent=2)
        print("   Saved to: debug_tag_infinite.json")
    else:
        print("   Failed")
    print()

    print("Conclusion:")
    print("- Tag IDs need to be resolved to tag names using tag.getById or tag.getByIds")
    print("- Our specific image (117165031) has no tags assigned")

if __name__ == "__main__":
    main()
