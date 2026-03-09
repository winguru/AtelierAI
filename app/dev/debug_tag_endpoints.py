#!/usr/bin/env python3
"""Debug script to find how to access tags from Civitai API."""

import json
from src.civitai_api import CivitaiAPI

def main():
    api = CivitaiAPI.get_instance()
    image_id = 117165031

    print("=" * 70)
    print("Testing various approaches to find tags")
    print("=" * 70)
    print()

    # 1. Check image.get response for tags
    print("1. Checking image.get response for tags...")
    basic_info = api.fetch_basic_info(image_id)
    if basic_info:
        print(f"   Keys in basic_info: {list(basic_info.keys())[:20]}")
        if "tags" in basic_info:
            print(f"   Found tags in basic_info: {basic_info['tags']}")
        else:
            print("   No 'tags' key in basic_info")
        if "meta" in basic_info:
            print(f"   Has 'meta' in basic_info: {list(basic_info['meta'].keys()) if isinstance(basic_info['meta'], dict) else type(basic_info['meta'])}")
    print()

    # 2. Check generation data for tags
    print("2. Checking image.getGenerationData response for tags...")
    gen_data = api.fetch_generation_data(image_id)
    if gen_data:
        print(f"   Keys in gen_data: {list(gen_data.keys())[:20]}")
        if "meta" in gen_data:
            meta = gen_data["meta"]
            print(f"   Keys in meta: {list(meta.keys())[:20]}")
            if "tags" in meta:
                print(f"   Found tags in meta: {meta['tags']}")
            if "hashtags" in meta:
                print(f"   Found hashtags in meta: {meta['hashtags']}")
        if "tags" in gen_data:
            print(f"   Found tags directly in gen_data: {gen_data['tags']}")
    print()

    # 3. Try different endpoint patterns
    print("3. Trying different endpoint patterns...")
    endpoints_to_try = [
        "image.getTags",
        "tag.get", 
        "tag.getByImage",
        "image.tags",
        "tags.getByImage",
        "tag.image",
    ]

    for endpoint in endpoints_to_try:
        print(f"   Trying: {endpoint}")
        response = api._make_request(
            endpoint=endpoint,
            payload_data={"id": int(image_id), "authed": True}
        )
        if response:
            print(f"      Success! Response type: {type(response)}")
            if isinstance(response, dict):
                print(f"      Keys: {list(response.keys())[:10]}")
            break
        else:
            print(f"      Failed (404 or error)")
    print()

    # 4. Save full responses for manual inspection
    print("4. Saving full responses to files for inspection...")
    with open("debug_basic_info.json", "w") as f:
        json.dump(basic_info, f, indent=2)
    print("   Saved to: debug_basic_info.json")

    with open("debug_gen_data.json", "w") as f:
        json.dump(gen_data, f, indent=2)
    print("   Saved to: debug_gen_data.json")
    print()

if __name__ == "__main__":
    main()
