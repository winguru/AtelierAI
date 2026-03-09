#!/usr/bin/env python3
"""Debug script to check if tags are in the post data."""

import json
from src.civitai_api import CivitaiAPI

def main():
    api = CivitaiAPI.get_instance()
    image_id = 117165031

    print("=" * 70)
    print("Checking if tags are in post data")
    print("=" * 70)
    print()

    # 1. Get basic info to find postId
    print("1. Getting basic info to find postId...")
    basic_info = api.fetch_basic_info(image_id)
    post_id = basic_info.get("postId")
    print(f"   Post ID: {post_id}")
    print()

    # 2. Try to fetch post data
    if post_id:
        print("2. Trying to fetch post data...")
        print(f"   Attempting: post.get with id={post_id}")

        post_data = api._make_request(
            endpoint="post.get",
            payload_data={"id": post_id, "authed": True}
        )

        if post_data:
            print(f"   Success! Type: {type(post_data)}")
            if isinstance(post_data, dict):
                print(f"   Keys in post_data: {list(post_data.keys())[:20]}")
                if "tags" in post_data:
                    print(f"   Found tags: {post_data['tags']}")
                elif "image" in post_data and isinstance(post_data["image"], dict):
                    print(f"   Has 'image' key with keys: {list(post_data['image'].keys())[:20]}")
                    if "tags" in post_data["image"]:
                        print(f"   Found tags in image: {post_data['image']['tags']}")

            # Save to file
            with open("debug_post_data.json", "w") as f:
                json.dump(post_data, f, indent=2)
            print(f"   Saved to: debug_post_data.json")
        else:
            print("   Failed to fetch post data")
        print()

    # 3. Try post.getInfinite to see structure
    print("3. Trying post.getInfinite to see what's available...")
    post_infinite = api._make_request(
        endpoint="post.getInfinite",
        payload_data={"authed": True, "limit": 1}
    )

    if post_infinite:
        print(f"   Success! Type: {type(post_infinite)}")
        if isinstance(post_infinite, dict):
            print(f"   Keys: {list(post_infinite.keys())[:10]}")

        # Save to file
        with open("debug_post_infinite.json", "w") as f:
            json.dump(post_infinite, f, indent=2)
        print(f"   Saved to: debug_post_infinite.json")
    else:
        print("   Failed")
    print()

    print("Conclusion: Check the saved JSON files for any 'tags' fields.")

if __name__ == "__main__":
    main()
