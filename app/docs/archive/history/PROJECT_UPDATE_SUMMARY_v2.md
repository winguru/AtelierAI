# Project Update Summary - v2.0

## Overview

The Civitai scraper has been significantly refactored from v1.0 to v2.0 with a modern, maintainable architecture and new features.

## Major Changes

### 1. New Architecture

#### Files Created (NEW):
- **`civitai_api.py`** - Singleton API client for all Civitai API operations
- **`civitai_image.py`** - Image data model class
- **`analyze_image.py`** - Single image analyzer script
- **`analyze_collection.py`** - Collection-wide analyzer script (updated with tag support)
- **`console_utils.py`** - Console formatting utilities
- **`setup_session_token.py`** - Renamed authentication script (was `civitai_auth.py`)

#### Files Updated:
- **`README.md`** - Updated to reflect v2.0 architecture
- **`SETUP_GUIDE.md`** - Updated usage examples for new API
- **`COLLECTION_ANALYZER_GUIDE.md`** - Added tag support documentation

#### Files Deprecated:
- **`CivitaiPrivateScraper`** class in `civitai.py` - Still available but marked as legacy

---

## 2. New Features

### Tag Support
- **Tag fetching** via `tag.getVotableTags` API endpoint
- **Tags sorted by relevance score** (highest first)
- **Tags displayed** in both single image and collection analysis
- **Tag statistics** included in collection reports

### Single Image Analysis
```bash
python analyze_image.py <image_id>
```
Shows:
- Basic information (ID, URL, author, NSFW)
- Model information (model, version, sampler, steps, CFG)
- LoRAs used (with weights)
- **Tags** (sorted by relevance)
- Full prompts (positive and negative)
- Additional parameters (CLIP skip, workflow)
- Raw scraped data

### Collection Analysis (Enhanced)
```bash
python analyze_collection.py <collection_id> [--limit N]
```
Shows:
- Overview statistics
- Top models and versions
- Sampler, steps, CFG distributions
- Top LoRAs with average weights
- **Top Tags** (NEW - most common across collection)
- Common prompt concepts and phrases
- Sample prompts

---

## 3. CivitaiAPI Singleton

### Purpose
Centralized API client for all Civitai operations using singleton pattern.

### Methods

| Method | Description |
|---------|-------------|
| `get_instance()` | Get singleton instance |
| `fetch_basic_info(image_id)` | Fetch basic image info (URL, author, NSFW, etc.) |
| `fetch_generation_data(image_id)` | Fetch generation parameters (prompts, models, LoRAs) |
| `fetch_image_tags(image_id)` | Fetch tags (sorted by relevance) |
| `fetch_image_data(image_id)` | Fetch both basic info and generation data |
| `fetch_collection_items(collection_id)` | Fetch collection items list |
| `fetch_collection_with_details(collection_id, limit)` | Fetch items with generation data |

### Usage

```python
from civitai_api import CivitaiAPI

api = CivitaiAPI.get_instance()
tags = api.fetch_image_tags(117165031)
```

---

## 4. CivitaiImage Class

### Purpose
Data model for consistent image data handling and URL construction.

### Properties

| Property | Type | Description |
|-----------|------|-------------|
| `image_id` | int | Unique image identifier |
| `image_url` | str | Full direct download URL (auto-constructed) |
| `display_url` | str | Shortened URL for display |
| `author` | str | Username of uploader |
| `tags` | list | List of tag strings (sorted by relevance) |
| `model` | str | Primary checkpoint model name |
| `loras` | list | List of LoRA objects |
| `models` | list | List of model objects |
| `embeddings` | list | List of embedding objects |

### Key Features

#### Automatic URL Construction
```python
image = CivitaiImage(
    image_id=12345,
    url_hash="abc123def456",
    image_name="test.jpg",
    mime_type="image/jpeg"
)

print(image.image_url)
# https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA/abc123def456/original=true/quality=90/test.jpeg
```

**Automatic extension detection:**
- MIME type mapped to correct extension (`.jpeg`, `.png`, `.webp`, `.tif`, `.mp4`)
- Fixes filename extension mismatches
- Handles various image formats

#### Factory Methods
```python
# From single image (analyze_image.py)
image = CivitaiImage.from_single_image(basic_info, generation_data, api=api)

# From collection item (legacy workflow)
image = CivitaiImage.from_collection_item(item, generation_data)
```

#### Display Formatting
```python
CivitaiImage.print_details(image)
```
Shows:
- Full image URL (not truncated)
- Author URL: `https://civitai.com/user/{author}`
- Simplified LoRA table (Name, Model Weight columns only)
- **Tags section** (if available)
- Raw data with indented lists

---

## 5. ConsoleFormatter

### Purpose
Provides consistent, formatted console output across all scripts.

### Methods

| Method | Description |
|---------|-------------|
| `print_header(text)` | Print section header with lines |
| `print_subheader(text)` | Print subsection header with dashes |
| `print_key_value(key, value, indent)` | Print key-value pair |
| `print_table(headers, rows)` | Print formatted table |
| `print_wrapped_text(text, indent)` | Print text with word wrapping |
| `print_info(text)` | Print informational message |
| `print_success(text)` | Print success message |
| `print_error(text)` | Print error message |

---

## 6. Tag Implementation Details

### API Endpoint
Uses `tag.getVotableTags` with payload:
```json
{
  "id": <image_id>,
  "type": "image",
  "authed": true
}
```

### Response Structure
```python
[
  {
    "score": 9,
    "upVotes": 0,
    "downVotes": 0,
    "automated": true,
    "name": "breasts",
    "type": "UserGenerated",
    "nsfwLevel": 1
  },
  ...
]
```

### Tag Processing
- Tags sorted by `score` (highest relevance first)
- Returns tag names as simple strings
- Tags fetched per image during scraping
- Aggregated in collection analyzer

---

## 7. File Structure (Updated)

```
.
├── civitai_api.py              # API singleton (NEW)
├── civitai_image.py            # Image data model (NEW)
├── analyze_image.py             # Single image analyzer (NEW)
├── analyze_collection.py         # Collection analyzer (UPDATED - with tags)
├── console_utils.py             # Console formatting utilities (NEW)
├── setup_session_token.py       # Authentication script (NEW - renamed)
├── civitai_auth.py             # Original auth module (still available)
├── civitai.py                  # Legacy scraper class (deprecated)
├── config.py                   # Configuration file
├── README.md                    # Main documentation (UPDATED to v2.0)
├── SETUP_GUIDE.md              # Setup guide (UPDATED)
├── COLLECTION_ANALYZER_GUIDE.md # Collection analyzer guide (UPDATED to v2)
├── QUICK_REFERENCE.md            # Cookie reference (no changes needed)
├── CIVITAI_AUTH_README.md       # Auth readme (no changes needed)
├── PROJECT_FILES.md              # Project files list (needs update)
└── PROJECT_UPDATE_SUMMARY_v2.md  # This file (NEW)
```

---

## 8. Migration Guide

### From v1.0 to v2.0

#### Old Usage (Deprecated):
```python
from civitai import CivitaiPrivateScraper

scraper = CivitaiPrivateScraper(auto_authenticate=True)
data = scraper.scrape(11035255)
```

#### New Usage (Recommended):
```python
from civitai_api import CivitaiAPI
from civitai_image import CivitaiImage

api = CivitaiAPI.get_instance()
items = api.fetch_collection_with_details(11035255, limit=50)
```

### Breaking Changes

None! The legacy `CivitaiPrivateScraper` still works, but:
- New scripts use `CivitaiAPI` and `CivitaiImage`
- Tags are now fetched via `tag.getVotableTags` endpoint
- URL construction is now handled by `CivitaiImage` class

---

## 9. Testing

### Single Image Analysis
```bash
# Test single image
python analyze_image.py 117165031

# Test with JSON save
python analyze_image.py 117165031 --save
```

### Collection Analysis
```bash
# Test with small collection
python analyze_collection.py 11035255 --limit 3

# Test with full collection
python analyze_collection.py 11035255 --limit -1 --save
```

---

## 10. Documentation Updates

### README.md (Updated to v2.0)
Added sections:
- ✨ New Architecture overview
- CivitaiAPI singleton documentation
- CivitaiImage class documentation
- Tag fetching and structure
- Updated API endpoints list
- Updated file structure
- New usage examples

### SETUP_GUIDE.md (Updated)
Added sections:
- Single Image Analysis usage
- Collection Analysis usage
- New API usage examples
- Updated file structure table

### COLLECTION_ANALYZER_GUIDE.md (Updated to v2)
Added sections:
- Tag feature documentation
- Tag fetching details
- "Top Tags" output example
- Updated technical details
- Tag integration explanation

---

## 11. Key Improvements

### Maintainability
- **Singleton pattern** - Single API instance across all scripts
- **Class-based architecture** - Consistent data handling
- **Separation of concerns** - API, data model, analysis, display

### Functionality
- **Tag support** - Fetch and display tags from Civitai API
- **Better URLs** - Automatic extension detection and construction
- **Enhanced display** - Full URLs, author URLs, simplified LoRA tables
- **Consistent formatting** - ConsoleFormatter for all output

### Performance
- **Connection reuse** - Requests.Session in singleton
- **Reduced API calls** - Tags fetched during initial scrape
- **Efficient tag sorting** - Done once during fetch, not during display

---

## 12. Future Enhancements

Potential additions for v2.1:
- [ ] Migrate `analyze_collection.py` to use `CivitaiAPI` fully (currently uses both)
- [ ] Add tag cloud visualization in collection analysis
- [ ] Support for tag filtering in analysis
- [ ] Export tags to separate file/CSV
- [ ] Add tag type filtering (Label, UserGenerated, Moderation)
- [ ] Visual tag trends over time
- [ ] Prompt similarity scoring using tags

---

## 13. Support & Troubleshooting

### Tag Issues
- **"No tags found for this image"** - Normal for images without tags assigned
- **"404 API request failed"** - Check image ID and authentication
- **Empty tags list** - Image may not have tags assigned on Civitai

### Migration Issues
- **Import errors** - Ensure `from civitai_api import CivitaiAPI` is used
- **Attribute errors** - Check script uses `api.fetch_image_tags()` if needed
- **URL construction** - Use `image.image_url` property instead of manual construction

---

## 14. Version History

### v2.0.0 (Current - January 30, 2026)
- ✅ Refactored to `CivitaiAPI` singleton pattern
- ✅ Added `CivitaiImage` class for consistent data handling
- ✅ Added `analyze_image.py` for single image analysis
- ✅ Added `analyze_collection.py` for collection-wide analysis
- ✅ Added tag fetching via `tag.getVotableTags` API
- ✅ Added `ConsoleFormatter` for consistent output
- ✅ Improved URL construction with automatic extension detection
- ✅ Added author URL generation
- ✅ Enhanced display formatting (full URLs, simplified LoRA tables)
- ✅ Updated all documentation to v2.0

### v1.0.0 (Original)
- Google OAuth authentication with Playwright
- Stealth mode support (playwright-stealth)
- Session token caching
- Browser state persistence
- Full metadata extraction (model, version, LoRAs, etc.)
- Image URL generation
- Batch collection scraping

---

## Conclusion

The v2.0 update significantly improves the codebase with:
- **Better architecture** - Singleton and class-based design
- **New features** - Tag support and enhanced analysis
- **Improved UX** - Better formatting and display
- **Maintainability** - Clear separation of concerns
- **Documentation** - Updated guides and examples

All legacy code remains functional, but new features require the v2.0 architecture.
