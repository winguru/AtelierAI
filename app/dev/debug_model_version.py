#!/usr/bin/env python3
"""Debug script to see raw API response for model version"""

from src.civitai import CivitaiPrivateScraper
import json

print("=" * 70)
print("Debug: Model Version Extraction")
print("=" * 70)
print()

# Initialize scraper
scraper = CivitaiPrivateScraper(auto_authenticate=True)

# Fetch details for the specific image (ID 77468734)
image_id = 77468734
print(f"Fetching generation data for image ID: {image_id}")
print()

# Get raw data (not merged)
raw_details = scraper.fetch_image_details(image_id)

if not raw_details:
    print("❌ No data returned!")
    exit(1)

print("RAW API RESPONSE:")
print("-" * 70)
print(json.dumps(raw_details, indent=2))
print()

# Extract key sections
print("=" * 70)
print("ANALYSIS:")
print("=" * 70)

# Check meta
meta = raw_details.get("meta", {})
print(f"\n📦 Meta keys: {list(meta.keys())}")

# Check resources
resources = raw_details.get("resources", [])
print(f"\n📦 Resources count: {len(resources)}")

print("\n--- RESOURCES DETAIL ---")
for idx, res in enumerate(resources):
    print(f"\n[Resource {idx + 1}]")
    print(f"  All keys: {list(res.keys())}")
    print(f"  modelType: {res.get('modelType')}")
    print(f"  modelName: {res.get('modelName')}")
    print(f"  modelVersion: {res.get('modelVersion')}")
    print(f"  version: {res.get('version')}")
    print(f"  modelId: {res.get('modelId')}")
    print(f"  name: {res.get('name')}")
    print(f"  id: {res.get('id')}")

    # Try to find nested version
    if 'model' in res:
        model_obj = res['model']
        print(f"  Nested model object: {list(model_obj.keys()) if isinstance(model_obj, dict) else type(model_obj)}")

print()
print("=" * 70)
