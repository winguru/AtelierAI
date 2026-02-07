# Pagination and Limit Feature - Collection Analyzer

## Overview

Added pagination support to the Collection Analyzer, allowing you to:
- Fetch all images from large collections (beyond 50)
- Limit scraping to specific number of images
- Get informed feedback about collection size

## Command Line Arguments

### `--limit` Parameter

Controls how many images to fetch from the collection.

```
python analyze_collection.py <collection_id> --limit N
```

**Options:**
- `--limit 10` - Fetch first 10 images
- `--limit 50` - Fetch first 50 images (default)
- `--limit 100` - Fetch first 100 images
- `--limit -1` - Fetch ALL images from collection

## Usage Examples

### Quick Test (First 10 Images)
```bash
python analyze_collection.py 12176069 --limit 10
```
Great for quickly testing or previewing collection patterns.

### Default (First 50 Images)
```bash
python analyze_collection.py 12176069
# OR
python analyze_collection.py 12176069 --limit 50
```
Recommended for initial analysis. Default behavior.

### Large Collection (First 200 Images)
```bash
python analyze_collection.py 12176069 --limit 200
```
For larger collections when you want more data but not all.

### Complete Analysis (All Images)
```bash
python analyze_collection.py 12176069 --limit -1 --save
```
Fetches and analyzes ALL images in the collection.

## Output Feedback

The analyzer now provides feedback about collection size:

### When Limit is Reached
```
======================================================================
Civitai Collection Analyzer
======================================================================

Scraping collection 12345...
✅ Using cached session token from .civitai_session
Fetching first 50 images...
Fetching collection items for ID: 12345
  Page 1: Fetched 50 items (total: 50)
  Reached limit of 50 items.
Found 50 total items.
Fetching details for 50 images...
...
✅ Successfully scraped 50 images!

ℹ️  Fetched 50 images (limit reached).
   Use '--limit -1' to fetch all images from this collection.
```

### When Collection is Smaller Than Limit
```
...
✅ Successfully scraped 21 images!

ℹ️  Collection has 21 images (less than limit 50).
   This is all available images in the collection.
```

### When Fetching All Images
```
...
Fetching all images...
Fetching collection items for ID: 12345
  Page 1: Fetched 50 items (total: 50)
  Page 2: Fetched 47 items (total: 97)
Found 97 total items.
...
✅ Successfully scraped 97 images!

ℹ️  Fetched all 97 images from collection.
```

## Pagination Details

The scraper now:
1. **Fetches items in pages** (typically ~50 items per page)
2. **Shows progress** per page: `Page N: Fetched X items (total: Y)`
3. **Respects limits** - stops at specified limit
4. **Handles cursors** - uses Civitai's `nextCursor` for pagination
5. **Stops gracefully** - when collection ends or API returns no more pages

## Technical Implementation

### Patched Methods

The `CivitaiPrivateScraper` class is patched with:

#### `fetch_collection_items(self, collection_id, limit=None)`
- Fetches collection items with pagination support
- Respects the `limit` parameter
- Uses cursor-based pagination from Civitai API
- Returns when limit is reached or no more items

#### `scrape(self, collection_id, limit=None)`
- Main scrape method with limit support
- Calls `fetch_collection_items` with limit
- Fetches detailed generation data for each item
- Returns curated list of image data

## JSON Output

When using `--save`, the JSON file includes:

```json
{
  "collection_id": 12176069,
  "limit_applied": 50,
  "total_images_scraped": 50,
  "top_models": [...],
  "top_loras": [...],
  "top_positive_concepts": [...],
  ...
}
```

## Recommendations

### For Quick Preview
```bash
python analyze_collection.py 12345 --limit 10
```
- Fast (seconds)
- Get a sense of collection themes
- Decide if full analysis is needed

### For Detailed Analysis
```bash
python analyze_collection.py 12345 --limit 100 --save
```
- 100 images provides good statistical significance
- Balance between speed and data depth
- Export to JSON for further analysis

### For Complete Data
```bash
python analyze_collection.py 12345 --limit -1 --save
```
- All images in collection
- Full picture of patterns
- Best for model/LoRA optimization

## Use Cases

### 1. Model Selection
```bash
# Quick test to see which models work best
python analyze_collection.py 12345 --limit 50

# Then analyze all if interested
python analyze_collection.py 12345 --limit -1 --save
```

### 2. LoRA Discovery
```bash
# Find most-used LoRAs across collection
python analyze_collection.py 12345 --limit 200 --save
```

### 3. Prompt Pattern Mining
```bash
# Extract successful prompt structures
python analyze_collection.py 12345 --limit -1 --save
# Review top_positive_concepts and top_positive_phrases in JSON
```

### 4. Collection Comparison
```bash
# Compare two collections
python analyze_collection.py collection_a --limit 50 > analysis_a.txt
python analyze_collection.py collection_b --limit 50 > analysis_b.txt

# Compare manually or use diff tools
```

## Performance Notes

| Limit | Estimated Time* | Notes |
|--------|------------------|--------|
| 10 | ~30 seconds | Quick preview |
| 50 | ~2.5 minutes | Default, good balance |
| 100 | ~5 minutes | Detailed analysis |
| 200 | ~10 minutes | Large collections |
| -1 (all) | Varies | Depends on collection size |

*Time includes scraping (0.2s per image) + analysis

## Files Modified

1. **`analyze_collection.py`** - Added limit parameter and pagination
2. **`analyze_collection_limit.py`** - Pagination functions (module)

## Backward Compatibility

The `--limit` parameter defaults to 50, so existing scripts:
```bash
python analyze_collection.py 12345
```
Will work exactly as before (first 50 images).

## Troubleshooting

### Issue: "Reached limit of X items" but want more

**Solution:** Use `--limit -1` to fetch all images
```bash
python analyze_collection.py 12345 --limit -1
```

### Issue: Pagination stops early

**Cause:** Civitai API may not return `nextCursor` or collection has ended.

**Check:** Look for "No more pages" message or "Found X total items" message.

### Issue: Very large collection taking too long

**Solution:** Use intermediate limits
```bash
# First 200 to get sense
python analyze_collection.py 12345 --limit 200

# Then decide if you need all
```

## Summary

- **Default**: `--limit 50` - Quick, representative sample
- **Flexible**: `--limit N` - Any number of images
- **Complete**: `--limit -1` - All images in collection
- **Informed**: Clear feedback on what was fetched vs. available
- **Efficient**: Pagination support for large collections

The analyzer now handles collections of any size efficiently!
