"""
Add these functions to CivitaiPrivateScraper class to support pagination with limits.

Replace the existing fetch_collection_items and scrape methods.
"""


def fetch_collection_items_paginated(self, collection_id, limit=None):
    """
    Fetches list of all items in a collection using image.getInfinite.
    Handles pagination automatically.

    Args:
        collection_id: The Civitai collection ID
        limit: Maximum number of items to fetch (None = all items)

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
        # Merge defaults with current cursor and collection ID
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
        # tRPC infinite scroll usually returns nextCursor in result.data.json
        try:
            next_cursor = data.get("result", {}).get("data", {}).get("json", {}).get("nextCursor")
            if next_cursor and next_cursor != cursor:
                cursor = next_cursor
            else:
                # No more pages
                break
        except Exception:
            # If we can't find cursor, stop for stability
            break

    print(f"Found {len(items)} total items.")
    return items


def scrape_with_limit(self, collection_id, limit=None):
    """
    Main entry point: Orchestrates fetching collection and merging details.

    Args:
        collection_id: The Civitai collection ID
        limit: Maximum number of items to process (None = all)

    Returns:
        List of curated image data
    """
    # 1. Get list of items
    collection_items = self.fetch_collection_items(collection_id, limit)
    if not collection_items:
        return []

    # 2. Get details for each item
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


# Instructions to integrate into civitai.py:
"""
1. Add these methods to the CivitaiPrivateScraper class in civitai.py
2. Or replace the existing methods with these new versions

Example usage in analyze_collection.py:

scraper = CivitaiPrivateScraper(auto_authenticate=True)

# First fetch to see total available
print("Checking collection size...")
test_items = scraper.fetch_collection_items(collection_id, limit=1)
print(f"Collection has at least 1 page of items available")

# Then scrape with actual limit
data = scraper.scrape(collection_id, limit=50)  # Default: first 50
# OR
data = scraper.scrape(collection_id, limit=-1)  # All images
"""
