# Civitai Private Scraper & Analyzer

A comprehensive Python toolkit for Civitai that automatically authenticates with Google OAuth and extracts full generation metadata including models, versions, prompts, LoRAs, tags, and image URLs.

## ⚠️ IMPORTANT: Correct Cookie Name

The scraper uses `__Secure-civitai-token` cookie, **NOT** `__Secure-next-auth.session-token`.

If you're manually extracting the token from your browser's DevTools, make sure you copy the value from the cookie named `__Secure-civitai-token`.

## ✨ New Architecture (v2.0)

The project has been refactored with a modern, maintainable architecture:

- **`CivitaiAPI` (Singleton)** - Centralized API client for all Civitai API calls
- **`CivitaiImage` (Class)** - Image data model with consistent URL construction and display
- **`analyze_image.py`** - Single image analysis with full metadata and tags
- **`analyze_collection.py`** - Collection-wide analysis with statistics and common patterns

The legacy `CivitaiPrivateScraper` class is still available but deprecated.

## Features

- ✅ **Automatic Authentication** - Google OAuth with Playwright
- ✅ **Stealth Mode** - Avoids bot detection during OAuth flow
- ✅ **Token Caching** - Session tokens cached for ~30 days
- ✅ **Browser State Persistence** - Stay logged in across runs
- ✅ **Full Metadata Extraction**:
  - Image URLs (direct download links with correct extensions)
  - Model names and versions
  - Generation parameters (sampler, steps, CFG, seed)
  - LoRAs with weights
  - Full prompts and negative prompts
  - Author information with profile URLs
  - **Tags** (votable tags from Civitai API)
- ✅ **Single Image Analysis** - Detailed breakdown of individual images
- ✅ **Collection Analysis** - Find common patterns across collections
  - Top models, LoRAs, samplers, tags
  - Common prompt concepts and phrases
  - Statistical analysis
- ✅ **Model Availability Detection** - Automatically detect deleted/removed LoRAs
  - Identifies models that have been deleted from Civitai
  - Provides links to Civitai Archive (civitaiarchive.com) for deleted models
  - Shows model status and usage count
  - **Model availability detection** - Identifies deleted/removed models with archive links

## Installation

### Prerequisites

- Python 3.11+
- Playwright browsers

```bash
# Install dependencies
pip install playwright requests

# Install Playwright browsers
playwright install chromium

# Optional: Install playwright-stealth for anti-detection
pip install playwright-stealth
```

### First-Time Setup

1. Copy configuration file:
```bash
cp config.example.py config.py
```

2. Edit `config.py` if needed (optional - auto-authentication handles everything)

3. Run authentication:
```bash
python setup_session_token.py
```

This will:
- Open a browser window
- Prompt you to sign in with Google (or your preferred OAuth provider)
- Save browser state to `.civitai_browser_state`
- Cache session token to `.civitai_session`

## Authentication

### First-Time Authentication

```bash
python setup_session_token.py
```

A browser window will open. Click "Sign In with Google" and complete authentication. The script will automatically detect when you're logged in and save your session.

### Using Visible Browser

```bash
python setup_session_token.py --headless=false
```

### Force Re-Authentication

If your token expires or you want to sign in with a different account:

```bash
python setup_session_token.py --force
```

This deletes old cache files and prompts you to authenticate again.

## Usage

### Analyze a Single Image

```bash
python analyze_image.py 117165031
```

Output includes:
- Basic information (ID, URL, author, NSFW status)
- Model information (model, version, sampler, steps, CFG, seed)
- LoRAs used (with weights)
- **Tags** (sorted by relevance)
- Full prompts (positive and negative)
- Additional parameters (CLIP skip, workflow, etc.)
- Raw scraped data

Save analysis to JSON:
```bash
python analyze_image.py 117165031 --save
```

### Analyze a Collection

```bash
# Analyze first 50 images
python analyze_collection.py 11035255 --limit 50

# Analyze all images
python analyze_collection.py 11035255 --limit -1

# Save results to JSON
python analyze_collection.py 11035255 --save
```

Output includes:
- Overview statistics (total images, unique models, samplers)
- Top models and versions
- Sampler, steps, and CFG distributions
- Top LoRAs with average weights
- **Deleted/Unavailable Models** - Models removed from Civitai with archive links
- **Top Tags** (most common across collection)
- Common prompt concepts and phrases
- Sample prompts

### Programmatic Usage with New API

```python
from civitai_api import CivitaiAPI
from civitai_image import CivitaiImage

# Get API singleton instance
api = CivitaiAPI.get_instance()

# Fetch basic info
basic_info = api.fetch_basic_info(117165031)

# Fetch generation data
generation_data = api.fetch_generation_data(117165031)

# Fetch tags
tags = api.fetch_image_tags(117165031)

# Create image instance
image = CivitaiImage.from_single_image(basic_info, generation_data, api=api)

# Print details
CivitaiImage.print_details(image)

# Get URL (auto-constructed with correct extension)
print(f"Image URL: {image.image_url}")

# Get display URL (shortened)
print(f"Display URL: {image.display_url}")
```

## Data Structure

### CivitaiImage Class

The `CivitaiImage` class provides consistent access to image data:

#### Properties

| Property | Type | Description |
|-----------|------|-------------|
| `image_id` | int | Unique image identifier |
| `image_url` | str | Full direct download URL (auto-constructed) |
| `display_url` | str | Shortened URL for display |
| `author` | str | Username of uploader |
| `tags` | list | List of tag strings (sorted by relevance) |
| `model` | str | Primary checkpoint model name |
| `model_version` | str | Model version |
| `loras` | list | List of LoRA objects |
| `models` | list | List of model objects |
| `embeddings` | list | List of embedding objects |

#### Methods

| Method | Description |
|---------|-------------|
| `from_single_image(basic_info, generation_data, api)` | Factory method from API responses |
| `from_collection_item(item, generation_data)` | Factory method from collection data |
| `print_details(image, fmt)` | Print formatted analysis |
| `to_dict(include_full_url)` | Export to dictionary |

### LoRA Structure

```python
{
    "name": "Detail Tweaker XL",
    "weight": 1.2,
    "modelId": "Unknown",
    "modelVersionId": 123456,
    "versionName": "v1.0",
    "baseModel": "Pony"
}
```

### Tag Structure

Tags are simple strings, sorted by relevance score from Civitai API:

```python
[
    "breasts",
    "woman",
    "solo focus",
    "nudity",
    "looking at viewer"
]
```

Tags are fetched using the `tag.getVotableTags` API endpoint.

## CivitaiAPI Singleton

The `CivitaiAPI` class provides a singleton instance for all API operations:

### Methods

| Method | Description |
|---------|-------------|
| `get_instance()` | Get singleton instance |
| `fetch_basic_info(image_id)` | Fetch basic image info |
| `fetch_generation_data(image_id)` | Fetch generation parameters |
| `fetch_image_tags(image_id)` | Fetch tags (sorted by relevance) |
| `fetch_image_data(image_id)` | Fetch both basic info and generation data |
| `fetch_collection_items(collection_id)` | Fetch collection items list |
| `fetch_collection_with_details(collection_id, limit)` | Fetch items with generation data |
| `check_model_availability(model_id, model_version_id)` | Check if model/version is available or deleted |

## File Structure

```
.
├── src/                           # Core source code
│   ├── civitai_api.py          # API singleton
│   ├── civitai_auth.py         # Authentication module
│   ├── civitai_image.py       # Image data model
│   └── console_utils.py         # Console formatting utilities
│
├── scripts/                       # Main executable scripts
│   ├── analyze_image.py         # Single image analyzer
│   ├── analyze_collection.py     # Collection analyzer
│   └── setup_session_token.py # Authentication script
│
├── docs/                          # Documentation
│   ├── api/                    # API reference
│   ├── guides/                 # User guides
│   ├── features/               # Feature documentation
│   ├── auth/                   # Authentication docs
│   └── archive/                # Historical docs
│
├── tests/                         # Test files
├── legacy/                       # Deprecated code
├── dev/                          # Development/debug scripts
├── examples/                      # Example code (to be added)
├── data/                         # Generated data (gitignored)
├── config.example.py               # Configuration template
├── requirements.txt               # Python dependencies
├── entrypoint.sh, start.sh        # Docker entrypoints
└── README.md                     # This file
```

## API Endpoints Used

| Endpoint | Purpose |
|----------|---------|
| `image.get` | Fetch basic image info (URL, author, NSFW, etc.) |
| `image.getGenerationData` | Fetch generation parameters (prompts, models, LoRAs) |
| `image.getInfinite` | Fetch collection items (with pagination) |
| `tag.getVotableTags` | Fetch votable tags for an image |
| `tag.getById` | Fetch tag details by ID |
| `modelVersion.getById` | Fetch model version details (including status) |

## Troubleshooting

### "Authentication failed" or "401 Unauthorized"

**Cause:** Session token expired

**Solution:**
```bash
python setup_session_token.py --force
```

### "No tags found for this image"

**Cause:** Image has no tags assigned on Civitai (common for newer images)

**Solution:** This is expected behavior. The API returns empty tags for images without tags.

### "OAuth authentication failed in headless mode"

**Cause:** First-time authentication requires visible browser

**Solution:**
```bash
python setup_session_token.py --headless=false
```

## Changelog

### v2.1.0 (Latest)
- ✅ Added model availability detection for LoRAs
- ✅ Automatically checks if models have been deleted from Civitai
- ✅ Provides links to Civitai Archive (civitaiarchive.com) for deleted models
- ✅ Shows model status and usage count for deleted models
- ✅ Added `check_model_availability()` method to CivitaiAPI

### v2.0.0
- ✅ Refactored to use `CivitaiAPI` singleton pattern
- ✅ Added `CivitaiImage` class for consistent data handling
- ✅ Added `analyze_image.py` for single image analysis
- ✅ Added `analyze_collection.py` for collection-wide analysis
- ✅ Added tag fetching via `tag.getVotableTags` API
- ✅ Added `ConsoleFormatter` for consistent output
- ✅ Improved URL construction with automatic extension detection
- ✅ Added author URL generation
- ✅ Enhanced display formatting (full URLs, simplified LoRA tables)

### v1.0.0
- Google OAuth authentication with Playwright
- Stealth mode support (playwright-stealth)
- Session token caching
- Browser state persistence
- Full metadata extraction (model, version, LoRAs, etc.)
- Image URL generation
- Batch collection scraping
