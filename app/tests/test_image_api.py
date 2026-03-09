#!/usr/bin/env python3
"""Test script to debug image API response structure."""

import json
from src.civitai import CivitaiPrivateScraper
from src.console_utils import ConsoleFormatter

def main():
    image_id = 117165031

    fmt = ConsoleFormatter()
    fmt.print_header(f"Debugging Image API - ID {image_id}")
    fmt.print_blank()

    scraper = CivitaiPrivateScraper(auto_authenticate=True)

    # Try fetching details
    fmt.print_info("Attempting to fetch image generation data...")
    details = scraper.fetch_image_details(image_id)

    fmt.print_info(f"Response type: {type(details)}")
    fmt.print_info(f"Response is None: {details is None}")
    fmt.print_info(f"Response is dict: {isinstance(details, dict)}")

    if details:
        fmt.print_info(f"Keys in response: {list(details.keys()) if isinstance(details, dict) else 'N/A'}")

        if isinstance(details, dict):
            fmt.print_blank()
            fmt.print_subheader("Full API Response")
            fmt.print_blank()
            print(json.dumps(details, indent=2))
    else:
        fmt.print_error("No data returned from API!")

    # Also try to search for the image
    fmt.print_blank()
    fmt.print_subheader("Alternative: Search for Image")
    fmt.print_blank()
    fmt.print_info("Trying to search for image in collections...")

    # This might require a collection ID, but let's try searching
    # Note: The scraper doesn't have a search_by_image_id method

    fmt.print_info("Note: Scraper doesn't have direct search-by-ID method.")
    fmt.print_info("We need to know which collection contains this image.")


if __name__ == "__main__":
    main()
