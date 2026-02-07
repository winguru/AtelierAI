#!/usr/bin/env python3
"""Detailed test of Civitai scraper showing all extracted data"""

from src.civitai import CivitaiPrivateScraper
import json

table_header_length = 10+18+27+32

print("=" * table_header_length)
print("Civitai Scraper - Detailed Output Test")
print("=" * table_header_length)
print()

# Initialize scraper with auto-authentication
print("Initializing scraper...")
scraper = CivitaiPrivateScraper(auto_authenticate=True)
print()

# Scrape collection
collection_id = 11035255
print(f"Fetching collection {collection_id}...")
print()

data = scraper.scrape(collection_id)

# Display results
if not data:
    print("❌ No data found!")
    exit(1)

print()
print("=" * table_header_length)
print(f"✅ SUCCESS! Fetched {len(data)} images")
print("=" * table_header_length)
print()

# Show first item in detail
if data:
    print("📋 FIRST IMAGE - Full Details:")
    print("-" * table_header_length)
    first = data[0]

    print(f"Image ID:        {first.get('image_id')}")
    print(f"Image URL:       {first.get('image_url')}")
    print(f"Author:          {first.get('author')}")
    print(f"Model:           {first.get('model')}")
    print(f"Model Version:   {first.get('model_version', 'N/A')}")
    print(f"Sampler:         {first.get('sampler', 'N/A')}")
    print(f"Steps:           {first.get('steps', 'N/A')}")
    print(f"CFG Scale:       {first.get('cfg_scale', 'N/A')}")
    print(f"Seed:            {first.get('seed', 'N/A')}")

    # Show LoRAs if any
    loras = first.get('loras', [])
    if loras:
        print(f"LoRAs:")
        for lora in loras:
            print(f"  - {lora.get('name')} (weight: {lora.get('weight', 'N/A')})")
    else:
        print(f"LoRAs:           None")

    # Show tags (first 5)
    tags = first.get('tags', [])
    if tags:
        tag_names = [t.get('name', t) for t in tags[:5]]
        print(f"Tags (first 5): {', '.join(tag_names)}")

    # Show prompt (truncated)
    prompt = first.get('prompt', '')
    if prompt:
        prompt_preview = prompt[:300] + '...' if len(prompt) > 300 else prompt
        print(f"Prompt:           {prompt_preview}")

    print()
    print("=" * table_header_length)
    print("📋 ALL IMAGES - Summary:")
    print("-" * table_header_length)
    print(f"{'ID':<10} {'Author':<18} {'Model':<27} {'Version':<32}")
    print("-" * table_header_length)

    for item in data:
        img_id = str(item.get('image_id', ''))[:8]
        author = str(item.get('author', 'Unknown'))[:16]
        model = str(item.get('model', 'Unknown'))[:25]
        version = str(item.get('model_version', ''))[:30]
        print(f"{img_id:<10} {author:<18} {model:<27} {version:<32}")

    print()
    print("=" * table_header_length)
    print("📋 ALL IMAGE URLs:")
    print("-" * table_header_length)
    for item in data:
        print(f"{item.get('image_url')}")

    print()
    print("=" * table_header_length)
