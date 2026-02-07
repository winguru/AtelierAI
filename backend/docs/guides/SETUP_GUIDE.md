# Civitai Private Collection Scraper - Setup Guide

## 🎯 Overview

This scraper allows you to fetch images from your Civitai collections, including private collections.

**New Architecture (v2.0):** The project now uses `CivitaiAPI` singleton and `CivitaiImage` class for improved maintainability.

## ⚠️ IMPORTANT: Cookie Name

The scraper uses the cookie `__Secure-civitai-token`, NOT `__Secure-next-auth.session-token`.

## 🔑 Setup Instructions

### Step 1: Get Your Session Token

1. **Open Civitai.com** in your browser (Chrome, Firefox, Edge)
2. **Sign in** with your Civitai account
3. **Open Developer Tools** (F12)
4. **Go to Application tab** > **Cookies** > **https://civitai.com`
5. **Find the cookie** named `__Secure-civitai-token`
6. **Copy the Value** (it's a long string starting with `eyJ...`)

### Step 2: Save the Token

Run the setup script:

```bash
python setup_session_token.py
```

Paste your token when prompted. It will save it to `.civitai_session`.

### Step 3: Test Access

```bash
python test_private_access.py
```

You should see:
```
✅ read: True
✅ isOwner: True
```

## 🚀 Usage

### Single Image Analysis (NEW)

```bash
# Analyze a single image with full details and tags
python analyze_image.py 117165031

# Save analysis to JSON
python analyze_image.py 117165031 --save
```

### Collection Analysis (NEW)

```bash
# Analyze first 50 images from a collection
python analyze_collection.py 11035255 --limit 50

# Analyze all images and save results (includes deleted model detection)
python analyze_collection.py 11035255 --limit -1 --save
```

### Model Availability Checking (NEW v2.1)

The collection analyzer automatically checks if LoRAs have been deleted:

```bash
python analyze_collection.py 11035255 --limit 50
```

If any models are deleted, you'll see:
```
Deleted/Unavailable Models
--------------------------------------------------------------------------------
⚠️  Found 1 model(s) that have been removed from Civitai:

  🗑️  Deepthroat slider Pony/IllustriousXL
    Status: Deleted
    Civitai URL: https://civitai.com/models/871004?modelVersionId=1498821
    📦 Archive URL: https://civitaiarchive.com/models/871004?modelVersionId=1498821
```

### Basic Usage (Legacy)

```python
from civitai import CivitaiPrivateScraper

# Initialize with auto-authentication (uses cached token)
scraper = CivitaiPrivateScraper(auto_authenticate=True)

# Scrape a collection
data = scraper.scrape(12176069)

# Output results
for item in data:
    print(f"Image URL: {item['image_url']}")
    print(f"Model: {item['model']}")
    print(f"Prompt: {item['prompt']}")
    print()
```

### New API Usage (Recommended)

```python
from civitai_api import CivitaiAPI
from civitai_image import CivitaiImage

# Get API singleton
api = CivitaiAPI.get_instance()

# Fetch image data
basic_info = api.fetch_basic_info(117165031)
generation_data = api.fetch_generation_data(117165031)
tags = api.fetch_image_tags(117165031)

# Create image instance
image = CivitaiImage.from_single_image(basic_info, generation_data, api=api)

# Print details
CivitaiImage.print_details(image)
```

## 📊 Returned Data

Each scraped image includes:

| Field | Description |
|-------|-------------|
| `image_id` | Unique image ID |
| `image_url` | Full URL to download the image |
| `author` | Username of the creator |
| `model` | Primary model name (checkpoint) |
| `model_version` | Model version string |
| `loras` | List of LoRAs used (name + weight) |
| `prompt` | Positive prompt |
| `negative_prompt` | Negative prompt |
| `sampler` | Sampler name (e.g., DPM++ 2M Karras) |
| `steps` | Number of steps |
| `cfg_scale` | CFG scale value |
| `seed` | Generation seed |
| `raw_meta_json` | Full metadata as JSON |

## 🔍 Troubleshooting

### "read: false" / "isOwner: false"

**Problem:** Your token doesn't have access to this collection.

**Solution:**
1. Make sure you got the token from the **correct Civitai account**
2. Sign in with the Google account linked to that Civitai account
3. Get a fresh token from that session
4. Run `python setup_session_token.py` again

### "0 items found"

**Possible causes:**
- Wrong cookie name (must be `__Secure-civitai-token`)
- Collection is empty
- Token expired (session tokens last ~30 days)

**Solution:**
- Get a fresh token from your browser
- Verify the cookie name in your browser's DevTools

### Token expired

Session tokens expire after ~30 days. Simply get a fresh token:

```bash
python setup_session_token.py
```

## 📋 Files

| File | Purpose |
|------|---------|
| `civitai_api.py` | API singleton (NEW - recommended) |
| `civitai_image.py` | Image data model (NEW) |
| `analyze_image.py` | Single image analyzer (NEW) |
| `analyze_collection.py` | Collection analyzer (NEW) |
| `console_utils.py` | Console formatting utilities (NEW) |
| `civitai.py` | Legacy scraper class (deprecated) |
| `config.py` | Configuration settings |
| `setup_session_token.py` | Interactive token setup |
| `test_private_access.py` | Test collection access |

## 🔐 Security Notes

- **Keep your token secure** - it gives full access to your Civitai account
- **Don't commit tokens** to version control
- Add `.civitai_session` to your `.gitignore`
- Tokens expire after ~30 days for security

## 📞 Support

If you encounter issues:

1. Check your token is from the correct account
2. Verify the cookie name is `__Secure-civitai-token`
3. Run `test_private_access.py` to diagnose issues
4. Ensure the collection exists and you have access to it
