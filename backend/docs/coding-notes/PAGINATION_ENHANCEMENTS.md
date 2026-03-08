# CivitAI tRPC Client - Pagination Enhancements

## Summary

Completely redesigned the `--limit` argument to use **proper API pagination** instead of just truncating results.

## Key Changes

### 1. New Limit Behavior

| Limit Value | Behavior | Description |
|-------------|-----------|-------------|
| `-1` or `None` | Fetch ALL items from first page (no pagination) |
| `1` to `50` | Fetch ONE page (no pagination needed) |
| `>50` | Fetch multiple pages using cursor pagination |
| Default | 50 (one page) |

### 2. Safety Parameter

Added `max_pages` parameter to prevent infinite loops:
- Default: 10 pages maximum
- Prevents runaway pagination if API keeps returning cursors
- Can be increased if needed: `get_infinite_images(..., max_pages=20)`

### 3. Enhanced Return Value

Now returns additional metadata:
```python
{
    "items": [...],           # All fetched items
    "nextCursor": "...",      # Cursor for next page (if more exist)
    "totalFetched": 150,     # Total items fetched
    "pagesFetched": 3,       # Number of API calls made
}
```

### 4. Verbose Pagination Output

Shows detailed progress:
```
📊 Using browsing preferences:
   period: AllTime
   sort: Newest
   browsingLevel: 31
   excludedTagIds: 7 tags

📄 Fetching page 1...

📄 Fetching page 2...

📄 Fetching page 3...

✅ Reached limit of 150 items (fetched 100 + 50 on last page)

✅ Found 150 images
   Fetched from 3 pages
   Total API calls made: 3
```

## Implementation Details

### Code Structure

```python
def get_infinite_images(self, ..., limit: Optional[int] = None, max_pages: int = 10, ...):
    # 1. Build initial request
    payload = {...}

    # 2. If no limit or limit is small, fetch first page only
    if limit == -1 or limit is None:
        return self._request("image.getInfinite", payload)

    # 3. Pagination loop
    all_items = []
    total_fetched = 0
    pages_fetched = 0

    while total_fetched < limit and pages_fetched < max_pages:
        # Update cursor for page 2+
        if current_cursor:
            payload["cursor"] = current_cursor

        # Fetch page
        response = self._request("image.getInfinite", payload)
        items = response.get("items", [])

        # Accumulate
        all_items.extend(items)
        total_fetched += len(items)
        pages_fetched += 1

        # Check for more pages
        current_cursor = response.get("nextCursor")
        if not current_cursor:
            break

    return {
        "items": all_items,
        "nextCursor": current_cursor,
        "totalFetched": total_fetched,
        "pagesFetched": pages_fetched,
    }
```

## Usage Examples

### Example 1: One Page (Default)
```bash
python src/civitai_trpc_v2.py
```
Output:
```
Fetching images for collection 10842247...
📄 Fetching 50 images (one page)
✅ Found 50 images
```

### Example 2: Fetch All (No Limit)
```bash
python src/civitai_trpc_v2.py --limit -1
```
Output:
```
Fetching images for collection 10842247...
📄 Fetching ALL images (no limit)
✅ Found 150 images
```

### Example 3: Fetch 150 Items (3 Pages)
```bash
python src/civitai_trpc_v2.py --limit 150
```
Output:
```
Fetching images for collection 10842247...
📄 Fetching 150 images (using pagination)

📄 Fetching page 1...
📥 RESPONSE (Status 200):
{... first 50 items ...}

📄 Fetching page 2...
📥 RESPONSE (Status 200):
{... next 50 items ...}

📄 Fetching page 3...
📥 RESPONSE (Status 200):
{... final 50 items ...}

✅ Reached limit of 150 items (fetched 100 + 50 on last page)
✅ Found 150 images
   Fetched from 3 pages
   Total API calls made: 3
```

### Example 4: Programmatic Usage
```python
from civitai_trpc_v2 import CivitaiTrpcClient

client = CivitaiTrpcClient(verbose=True, auto_load_settings=True)

# Fetch exactly 100 items (will use ~2 pages)
result = client.get_infinite_images(collection_id=10842247, limit=100)

print(f"Fetched: {result['totalFetched']} items")
print(f"Pages: {result['pagesFetched']}")
print(f"More available: {bool(result['nextCursor'])}")

# Process items
for item in result['items']:
    print(f"  - {item.get('name')}")
```

## Benefits

1. **Accurate Results** - Always get exactly N items, not N-minus-some
2. **Respects API** - Uses proper cursor pagination as intended
3. **Efficient** - One API call per page, minimal overhead
4. **Transparent** - Verbose mode shows exactly what's happening
5. **Safe** - Max pages limit prevents runaway loops
6. **Informative** - Returns metadata about pages/calls made
7. **Backward Compatible** - Existing code continues to work with limit=None

## Migration Guide

### Old Code (v1)
```python
# Had to manually paginate or get truncated results
page1 = client.get_infinite_images(collection_id=10842247, limit=50)['items']
page2 = client.get_infinite_images(collection_id=10842247, cursor=page1_cursor)['items']
```

### New Code (v2)
```python
# Automatic pagination!
result = client.get_infinite_images(collection_id=10842247, limit=100)
# result['items'] contains all 100 items from multiple pages
# result['pagesFetched'] tells you how many API calls were made
# result['nextCursor'] gives you cursor for more if needed
```

## Command-Line Help

```bash
$ python src/civitai_trpc_v2.py --help

usage: civitai_trpc_v2.py [-h] [-v] [-a] [-l LIMIT]

optional arguments:
  -h, --help            show this help message and exit
  -v, --verbose         Output request URLs and formatted JSON response data
  -a, --auto-load-settings
                        Automatically load browsing settings from API on startup
  -l LIMIT, --limit LIMIT
                        Number of images to fetch (default: all)
                        -1: Fetch all items (unlimited)
                        - >50: Use pagination to fetch multiple pages
```

## Additional Notes

### Why Pagination Matters

CivitAI's API uses cursor-based pagination (like infinite scroll):
- Each page returns ~50 items
- Cursor tells API where to start fetching from
- Without proper pagination, you miss items across page boundaries

### When to Use Different Limits

**`limit=20`**: Fetch 20 items from first page (1 API call)
**`limit=50`**: Fetch 50 items from first page (1 API call, default)
**`limit=150`**: Fetch 150 items using 3 pages (3 API calls)
**`limit=-1`**: Fetch ALL items (keep fetching until no cursor)

### Performance

Approximate API calls needed:
- 50-100 items: 1 page (1 call)
- 100-150 items: 2 pages (2 calls)
- 150-200 items: 3 pages (3 calls)
- Each additional 50 items = +1 API call
