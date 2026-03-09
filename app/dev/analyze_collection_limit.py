"""
This file contains the pagination functions to be added to analyze_collection.py
Place these functions BEFORE the class definition and BEFORE main()
"""

import requests
import time


def fetch_collection_items_paginated(self, collection_id, limit=None, debug=False):
    """Paginated version of fetch_collection_items.

    Args:
        collection_id: The Civitai collection ID
        limit: Maximum number of items to fetch (None = all)
        debug: Enable debug output

    Returns:
        List of collection items
    """
    endpoint = "image.getInfinite"
    items = []
    cursor = None
    page_count = 0

    print(f"Fetching collection items for ID: {collection_id}")

    while True:
        # Check if we've hit the limit
        if limit is not None and len(items) >= limit:
            print(f"  Reached limit of {limit} items.")
            break

        # 1. Prepare Payload
        payload_data = {**self.default_params}
        payload_data["collectionId"] = int(collection_id)
        if cursor:
            payload_data["cursor"] = cursor
        else:
            payload_data["cursor"] = None

        params = {"input": self._build_trpc_payload(payload_data)}

        # 2. Make Request
        response = requests.get(
            f"{self.base_url}/{endpoint}",
            headers=self._get_headers(),
            params=params,
        )

        if response.status_code != 200:
            print(f"Error fetching collection page: {response.status_code}")
            break

        # 3. Parse Data
        data = response.json()
        page_items = self._find_deep_image_list(data)

        if not page_items:
            break  # No more items found

        # 4. Add items (respect limit)
        remaining = limit - len(items) if limit else None
        if remaining is not None and len(page_items) > remaining:
            page_items = page_items[:remaining]
            items.extend(page_items)
            print(f"  Reached limit of {limit} items.")
            break

        items.extend(page_items)
        page_count += 1
        print(f"  Page {page_count}: Fetched {len(page_items)} items (total: {len(items)})")

        # 5. Check for next cursor
        try:
            next_cursor = data.get("result", {}).get("data", {}).get("json", {}).get("nextCursor")
            if next_cursor and next_cursor != cursor:
                cursor = next_cursor
            else:
                break  # No more pages
        except Exception:
            break  # Can't find cursor, stop for stability

    print(f"Found {len(items)} total items.")
    return items


def scrape_with_limit(self, collection_id, limit=None, debug=False):
    """Scrape with limit support.

    Args:
        collection_id: The Civitai collection ID
        limit: Maximum number of items to fetch (None = all)
        debug: Enable debug output

    Returns:
        List of curated image data
    """
    collection_items = self.fetch_collection_items(collection_id, limit)
    if not collection_items:
        return []

    curated_data = []
    print(f"Fetching details for {len(collection_items)} images...")

    for idx, item in enumerate(collection_items):
        img_id = item.get("id")
        print(f"  [{idx+1}/{len(collection_items)}] Processing ID {img_id}...")

        details = self.fetch_image_details(img_id)

        if details:
            merged = self._merge_data(item, details)
            curated_data.append(merged)

        time.sleep(0.2)

    return curated_data


# Patch the scraper class when this file is imported
def patch_scraper():
    """Patch CivitaiPrivateScraper with pagination support."""
    from src.civitai import CivitaiPrivateScraper
    CivitaiPrivateScraper.fetch_collection_items = fetch_collection_items_paginated
    CivitaiPrivateScraper.scrape = scrape_with_limit


if __name__ == "__main__":
    # Just show help for this module
    print("Use: python analyze_collection.py <collection_id> [--limit N] [--save]")
    print("\n--limit options:")
    print("  50 (default) : Fetch first 50 images")
    print("  10            : Fetch first 10 images")
    print("  -1            : Fetch ALL images in collection")
    print("\nExample:")
    print("  python analyze_collection.py 12345 --limit 10")
    print("  python analyze_collection.py 12345 --limit -1 --save")
