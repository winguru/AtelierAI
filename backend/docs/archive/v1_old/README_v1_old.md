# Civitai Private Scraper & Analyzer

A comprehensive Python toolkit for Civitai that automatically authenticates with Google OAuth and extracts full generation metadata including models, versions, prompts, LoRAs, tags, and image URLs.

## ✨ New Architecture (v2.0)

The project has been refactored with a modern, maintainable architecture:

- **`CivitaiAPI` (Singleton)** - Centralized API client for all Civitai API calls
- **`CivitaiImage` (Class)** - Image data model with consistent URL construction and display
- **`analyze_image.py`** - Single image analysis with full metadata and tags
- **`analyze_collection.py`** - Collection-wide analysis with statistics and common patterns

The legacy `CivitaiPrivateScraper` class is still available but deprecated.

## ⚠️ IMPORTANT: Correct Cookie Name

The scraper uses the cookie `__Secure-civitai-token`, **NOT** `__Secure-next-auth.session-token`.

If you're manually extracting the token from your browser's DevTools, make sure you copy the value from the cookie named `__Secure-civitai-token`.

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

1. Copy the configuration file:
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
python civitai_auth.py --headless=false
```

### Force Re-Authentication

If your token expires or you want to sign in with a different account:

```bash
python civitai_auth.py --force
```

This deletes old cache files and prompts you to authenticate again.

### Headless Mode

Once you're authenticated (browser state saved), you can run in headless mode:

```bash
python civitai_auth.py --headless
```

**Note:** Headless mode only works if you have an existing `.civitai_browser_state` file.

## Usage Examples

### Basic Scraping

```python
from civitai import CivitaiPrivateScraper

# Initialize scraper with auto-authentication
scraper = CivitaiPrivateScraper(auto_authenticate=True)

# Scrape a collection by ID
data = scraper.scrape(11035255)

# Access data
for item in data:
    print(f"Image ID: {item['image_id']}")
    print(f"Author: {item['author']}")
    print(f"Model: {item['model']} - {item['model_version']}")
    print(f"URL: {item['image_url']}")
```

### Download Images

```python
import requests
import os
from civitai import CivitaiPrivateScraper

# Initialize scraper
scraper = CivitaiPrivateScraper(auto_authenticate=True)
data = scraper.scrape(11035255)

# Create output directory
os.makedirs('downloaded_images', exist_ok=True)

# Download all images
for item in data:
    response = requests.get(item['image_url'], stream=True)
    
    # Get file extension from URL
    ext = item['image_url'].split('.')[-1].split('?')[0]
    filename = f"downloaded_images/{item['image_id']}.{ext}"
    
    # Save image
    with open(filename, 'wb') as f:
        for chunk in response.iter_content(chunk_size=8192):
            f.write(chunk)
    
    print(f"✅ Downloaded: {filename}")
```

### Export to JSON

```python
import json
from civitai import CivitaiPrivateScraper

scraper = CivitaiPrivateScraper(auto_authenticate=True)
data = scraper.scrape(11035255)

# Save to JSON
with open('collection_data.json', 'w') as f:
    json.dump(data, f, indent=2)

print(f"✅ Saved {len(data)} images to collection_data.json")
```

### Export to CSV

```python
import csv
from civitai import CivitaiPrivateScraper

scraper = CivitaiPrivateScraper(auto_authenticate=True)
data = scraper.scrape(11035255)

# Save to CSV
with open('collection_report.csv', 'w', newline='', encoding='utf-8') as f:
    writer = csv.writer(f)
    
    # Header
    writer.writerow([
        'Image ID', 'Author', 'Model', 'Version',
        'Sampler', 'Steps', 'CFG', 'Seed', 'URL'
    ])
    
    # Data rows
    for item in data:
        writer.writerow([
            item['image_id'],
            item['author'],
            item['model'],
            item['model_version'],
            item['sampler'],
            item['steps'],
            item['cfg_scale'],
            item['seed'],
            item['image_url']
        ])

print(f"✅ Saved {len(data)} images to collection_report.csv")
```

### Filter by Model

```python
from civitai import CivitaiPrivateScraper

scraper = CivitaiPrivateScraper(auto_authenticate=True)
data = scraper.scrape(11035255)

# Filter images using specific model
target_model = "Pony Diffusion V6 XL"
pony_images = [item for item in data if target_model.lower() in item['model'].lower()]

print(f"Found {len(pony_images)} images using {target_model}")
for img in pony_images:
    print(f"  - ID {img['image_id']}: {img['image_url']}")
```

### Extract LoRAs

```python
from civitai import CivitaiPrivateScraper

scraper = CivitaiPrivateScraper(auto_authenticate=True)
data = scraper.scrape(11035255)

# Collect all unique LoRAs
all_loras = set()
for item in data:
    for lora in item.get('loras', []):
        all_loras.add((lora['name'], lora['weight']))

print(f"Unique LoRAs found in collection:")
for name, weight in sorted(all_loras):
    print(f"  - {name} (weight: {weight})")
```

## Data Structure

### Returned Fields

Each item in the returned list contains the following fields:

| Field | Type | Description |
|-------|------|-------------|
| `image_id` | int | Unique image identifier |
| `image_url` | str | Direct download URL (full resolution, 90% quality) |
| `author` | str | Username of the uploader (or "[deleted]" if account removed) |
| `tags` | list | List of tag objects (each has `id`, `name`, etc.) |
| `prompt` | str | Full positive prompt used for generation |
| `negative_prompt` | str | Negative prompt used for generation |
| `model` | str | Name of the primary checkpoint model |
| `model_version` | str | Version name of the model (e.g., "v5.0", "v7.0 Cinematic") |
| `loras` | list | List of LoRA objects with `name` and `weight` |
| `sampler` | str | Sampler used (e.g., "DPM++ 2M Karras") |
| `steps` | int | Number of sampling steps |
| `cfg_scale` | float | CFG scale value |
| `seed` | int | Random seed used |
| `raw_meta_json` | dict | Complete raw metadata from API (includes all available fields) |

### LoRA Structure

```python
{
    "name": "Detail Tweaker XL",
    "weight": 1.2
}
```

### Tag Structure

```python
{
    "id": 123456,
    "name": "1girl",
    "type": "Tag"  # Can be Tag, Mod, User, etc.
}
```

### Raw Metadata

The `raw_meta_json` field contains all metadata returned by the Civitai API, including fields not extracted into the main structure. This includes:

- `baseModel` - Base model type (Pony, SDXL 1.0, etc.)
- `Size` - Image dimensions (e.g., "832x1216")
- `nsfw` - Boolean indicating NSFW status
- `width` - Image width in pixels
- `height` - Image height in pixels
- `clipSkip` - CLIP skip setting
- And many more API-specific fields

## Image URL Format

Image URLs follow this pattern:

```
https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA/{image_hash}/original=true/quality=90/{filename}
```

- `{image_hash}` - Unique hash for the image storage
- `{filename}` - Original filename with correct extension
- `original=true` - Full resolution (not thumbnail)
- `quality=90` - JPEG quality setting (0-100)

## Session Token Refresh

Session tokens from Civitai typically expire after ~30 days. When you encounter authentication errors:

### Automatic Refresh

Simply run the authentication script:

```bash
python civitai_auth.py
```

If the cached token is expired, it will automatically:
1. Detect expiration
2. Open browser window
3. Prompt you to sign in
4. Save new token

### Force Refresh

To manually refresh regardless of token validity:

```bash
python civitai_auth.py --force
```

This deletes:
- `.civitai_session` (cached token)
- `.civitai_browser_state` (browser state)

Then prompts you to authenticate fresh.

### In Code

If you want to handle refresh programmatically:

```python
from civitai import CivitaiPrivateScraper

try:
    scraper = CivitaiPrivateScraper(auto_authenticate=True)
    data = scraper.scrape(11035255)
except Exception as e:
    if "authentication" in str(e).lower():
        print("Token expired, refreshing...")
        import subprocess
        subprocess.run(["python", "civitai_auth.py", "--force"])
        # Retry
        scraper = CivitaiPrivateScraper(auto_authenticate=True)
        data = scraper.scrape(11035255)
```

## Advanced Usage

### Multiple Collections

```python
from civitai import CivitaiPrivateScraper

scraper = CivitaiPrivateScraper(auto_authenticate=True)

collections = [11035255, 12345678, 98765432]
all_data = []

for collection_id in collections:
    print(f"Scraping collection {collection_id}...")
    data = scraper.scrape(collection_id)
    all_data.extend(data)
    print(f"  Collected {len(data)} images")

print(f"Total images across all collections: {len(all_data)}")
```

### Rate Limiting

The scraper includes a built-in delay between requests (0.2 seconds). To adjust this:

```python
from civitai import CivitaiPrivateScraper
import time

class CustomScraper(CivitaiPrivateScraper):
    def scrape(self, collection_id):
        collection_items = self.fetch_collection_items(collection_id)
        curated_data = []
        
        for idx, item in enumerate(collection_items):
            details = self.fetch_image_details(item.get("id"))
            if details:
                curated_data.append(self._merge_data(item, details))
            
            # Custom delay
            time.sleep(0.5)  # Slower to be safe
        
        return curated_data

scraper = CustomScraper(auto_authenticate=True)
data = scraper.scrape(11035255)
```

### Pagination for Large Collections

The current implementation fetches one page (typically 50+ items). For larger collections, you may need to handle pagination. The cursor is available in the API response:

```python
def scrape_all_pages(self, collection_id):
    items = []
    cursor = None
    
    while True:
        # ... fetch with cursor ...
        page_items = self.fetch_collection_items_with_cursor(collection_id, cursor)
        
        if not page_items:
            break
            
        items.extend(page_items)
        
        # Extract next cursor from response
        cursor = self._get_next_cursor()
        
        if not cursor:
            break
    
    return items
```

## Troubleshooting

### "Authentication failed" or "401 Unauthorized"

**Cause:** Session token expired

**Solution:**
```bash
python civitai_auth.py --force
```

### "OAuth authentication failed in headless mode"

**Cause:** First-time authentication requires visible browser

**Solution:**
```bash
python civitai_auth.py --headless=false
```

### "No session cache found" repeatedly

**Cause:** Browser state not saved properly

**Solution:**
1. Check if `.civitai_browser_state` exists
2. Delete it and re-authenticate
3. Ensure you complete the full OAuth flow in the browser

### "Author: Unknown" for some images

**Cause:** User deleted their account or has private profile

**Solution:** This is expected behavior. The API returns "Unknown" for deleted accounts.

### Model version is empty

**Cause:** Some models don't have a `versionName` field in the API response

**Solution:** Check the `raw_meta_json` field for all available metadata fields.

### Stealth mode warnings

**Cause:** `playwright-stealth` not installed

**Solution:** This is optional. Install if needed:
```bash
pip install playwright-stealth
```

The scraper works without it, just with slightly higher chance of detection during OAuth.

## File Structure

```
.
├── civitai.py                 # Main scraper class
├── civitai_auth.py            # Authentication module (Playwright)
├── config.py                  # Configuration file
├── .civitai_session           # Cached session token (auto-generated)
├── .civitai_browser_state     # Browser state (auto-generated)
├── test_detailed_scrape.py    # Example/test script
├── debug_model_version.py     # Debug tool for API responses
└── README.md                  # This file
```

## API Endpoints Used

| Endpoint | Purpose |
|----------|---------|
| `image.getInfinite` | Fetch collection items (with pagination) |
| `image.getGenerationData` | Fetch full generation metadata for an image |

## Token Expiration

Civitai session tokens typically expire after:
- **~30 days** of inactivity
- When the user logs out from Civitai
- When Civitai rotates their session keys

The scraper automatically detects expired tokens and prompts for re-authentication.

## Contributing

This scraper was designed for personal use with Civitai's private API. Please respect Civitai's Terms of Service:
- Don't scrape at high rates
- Respect rate limits
- Don't redistribute content without permission
- Attribute creators when using scraped data

## License

This code is provided as-is for educational purposes. Use responsibly.

## Support

For issues or questions:
1. Check this README's troubleshooting section
2. Run `python test_detailed_scrape.py` to verify setup
3. Run `python debug_model_version.py` to inspect API responses

## Changelog

### v1.0.0 (Current)
- ✅ Google OAuth authentication with Playwright
- ✅ Stealth mode support (playwright-stealth)
- ✅ Session token caching
- ✅ Browser state persistence
- ✅ Full metadata extraction (model, version, LoRAs, etc.)
- ✅ Image URL generation
- ✅ Batch collection scraping
