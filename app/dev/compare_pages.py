#!/usr/bin/env python3
"""Compare items across pages to see if they're the same"""

import requests
from src.civitai import CivitaiPrivateScraper

scraper = CivitaiPrivateScraper(auto_authenticate=True)

collection_id = 14949699

# Fetch page 1
cursor = None
pages = []

for page_num in range(1, 4):
    default_params = getattr(scraper, "default_params", {})
    payload_data = {**default_params}
    payload_data["collectionId"] = int(collection_id)
    payload_data["cursor"] = cursor

    params = {"input": scraper._build_trpc_payload(payload_data)}

    response = requests.get(
        f"{scraper.api.base_url}/image.getInfinite",
        headers=scraper.api._get_headers(),
        params=params,
    )

    if response.status_code == 200:
        data = response.json()
        items = scraper._find_deep_image_list(data) or []
        next_cursor = (
            data.get("result", {}).get("data", {}).get("json", {}).get("nextCursor")
        )

        item_ids = [item.get("id") for item in items]
        pages.append(
            {
                "page": page_num,
                "cursor": cursor,
                "next_cursor": next_cursor,
                "item_ids": item_ids,
                "first_id": item_ids[0] if item_ids else None,
                "last_id": item_ids[-1] if item_ids else None,
            }
        )

        print(f"Page {page_num}: {len(items)} items")
        print(f"  Cursor: {cursor}")
        print(f"  Next cursor: {next_cursor}")
        print(f"  First ID: {item_ids[0] if item_ids else 'N/A'}")
        print(f"  Last ID: {item_ids[-1] if item_ids else 'N/A'}")
        print()

        if next_cursor and next_cursor != cursor:
            cursor = next_cursor
        else:
            break

print("=" * 80)
print("COMPARISON")
print("=" * 80)

for i, p in enumerate(pages):
    print(f"\nPage {p['page']}:")
    print(f"  Cursor: {p['cursor']}")
    print(f"  Next cursor: {p['next_cursor']}")
    print(f"  Item IDs: {p['item_ids'][:5]}...{p['item_ids'][-5:]}")

print("\n" + "=" * 80)
print("Are page 1 and page 2 the same?")
print("=" * 80)
if len(pages) >= 2:
    print(f"Page 1 IDs: {pages[0]['item_ids']}")
    print(f"Page 2 IDs: {pages[1]['item_ids']}")
    print(f"Same? {pages[0]['item_ids'] == pages[1]['item_ids']}")
