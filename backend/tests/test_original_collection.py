#!/usr/bin/env python3
"""Test the scraper on the original collection 11035255"""

from src.civitai import CivitaiPrivateScraper
from src.console_utils import ConsoleFormatter

# Initialize formatter
fmt = ConsoleFormatter()

collection_id = 11035255

fmt.print_header(f"Testing Scraper on Collection {collection_id}")
fmt.print_blank()

# Initialize scraper
fmt.print_info("Initializing scraper...")
scraper = CivitaiPrivateScraper(auto_authenticate=True)

# Scrape the collection
fmt.print_info(f"Scraping collection {collection_id}...")
data = scraper.scrape(collection_id)

fmt.print_blank()
fmt.print_header("RESULTS")
fmt.print_blank()

if data:
    fmt.print_success(f"SUCCESS: Scraped {len(data)} images!")
    fmt.print_blank()

    # Show sample data
    fmt.print_info("Sample data:")
    for i, item in enumerate(data[:5]):
        fmt.print_blank()
        fmt.print_info(f"[{i+1}] Image ID: {item['image_id']}", indent=1)
        fmt.print_key_value("Author", item['author'], indent=5)
        fmt.print_key_value("Model", item['model'], indent=5)
        fmt.print_key_value("Version", item['model_version'] or 'N/A', indent=5)

        if item.get("loras"):
            fmt.print_key_value("LoRAs", len(item['loras']), indent=5)
            for lora in item["loras"][:3]:
                fmt.print_info(f"- {lora['name']} (weight: {lora['weight']})", indent=9)

        fmt.print_key_value("Sampler", item['sampler'] or 'N/A', indent=5)
        fmt.print_key_value("Steps", item['steps'] or 'N/A', indent=5)
        fmt.print_key_value("CFG", item['cfg_scale'] or 'N/A', indent=5)
        fmt.print_key_value("Seed", item['seed'] or 'N/A', indent=5)
        fmt.print_key_value("URL", item['image_url'], indent=5)

    fmt.print_blank()
    fmt.print_info(f"Full data saved to: collection_{collection_id}_scraped.json")

    # Save to JSON
    import json

    with open(f"collection_{collection_id}_scraped.json", "w") as f:
        json.dump(data, f, indent=2)

else:
    fmt.print_error("No data found")
