#!/usr/bin/env python3
"""
Test if the collection has images by fetching without filters.
"""

from civitai_trpc_v3 import CivitaiTrpcClient
from config import CIVITAI_SESSION_COOKIE

FINGERPRINT = "48cc7067da9614a09cdfa515bb51ec3d8d362efa293d0d8f9d15f7c9919bac80cbdaff9cd1e91cd02902e40dd02b38d8"

client = CivitaiTrpcClient(
    session_token=CIVITAI_SESSION_COOKIE,
    x_fingerprint=FINGERPRINT,
    verbose=True,
    auto_load_settings=True,
)

print("=" * 70)
print("Testing different collection presets...")
print()

# Test 1: Don't load any presets (use defaults)
print("Test 1: No presets loaded (using client defaults)")
print("-" * 70)
collection_data_1 = client.get_collection_by_id(10842247)
print(f"Collection Name: {collection_data_1.get('name')}")
print(f"NSFW Level: {collection_data_1.get('nsfwLevel')}")

images_1 = client.get_infinite_images(collection_id=10842247, limit=5)
items_1 = images_1.get('items', [])
print(f"Items found: {len(items_1)}")
print()

# Test 2: Load "none" preset
print("Test 2: Loading 'none' preset")
client.load_browsing_settings("none")
print("-" * 70)
collection_data_2 = client.get_collection_by_id(10842247)
print(f"Collection Name: {collection_data_2.get('name')}")
print(f"NSFW Level: {collection_data_2.get('nsfwLevel')}")

images_2 = client.get_infinite_images(collection_id=10842247, limit=5)
items_2 = images_2.get('items', [])
print(f"Items found: {len(items_2)}")
print()

# Test 3: Load "some" preset
print("Test 3: Loading 'some' preset")
client.load_browsing_settings("some")
print("-" * 70)
collection_data_3 = client.get_collection_by_id(10842247)
print(f"Collection Name: {collection_data_3.get('name')}")
print(f"NSFW Level: {collection_data_3.get('nsfwLevel')}")
print(f"Browsing preferences: {client.get_browsing_prefs()}")

images_3 = client.get_infinite_images(collection_id=10842247, limit=5)
items_3 = images_3.get('items', [])
print(f"Items found: {len(items_3)}")
print()

print("=" * 70)
print("Summary:")
print(f"Test 1 (no presets): {len(items_1)} items")
print(f"Test 2 (none preset): {len(items_2)} items")
print(f"Test 3 (some preset): {len(items_3)} items")
print()
print("💡 If all tests return 0 items, the collection might be empty or inaccessible.")
print("💡 If only Test 3 (some preset) returns images but others don't,")
print("   the 'some' preset might be too restrictive (browsingLevel=31, has excluded tags).")
