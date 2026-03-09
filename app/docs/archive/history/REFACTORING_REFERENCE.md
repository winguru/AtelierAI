# Quick Reference: Refactored Civitai Classes

## Class Hierarchy

```
CivitaiAPI (Singleton)
    └── CivitaiPrivateScraper
            └── analyze_collection.py (uses scraper.scrape())

CollectionAnalyzer
    └── analyze_collection.py (analyzes scraped data)
```

## Quick API Reference

### CivitaiAPI (Singleton)

```python
from civitai_api import CivitaiAPI

# Get singleton instance
api = CivitaiAPI.get_instance()

# Or initialize explicitly
api = CivitaiAPI(auto_authenticate=True)

# Fetch methods
tags = api.fetch_image_tags(image_id)
basic_info = api.fetch_basic_info(image_id)
gen_data = api.fetch_generation_data(image_id)
combined = api.fetch_image_data(image_id)
```

### CivitaiPrivateScraper

```python
from civitai import CivitaiPrivateScraper

# Initialize
scraper = CivitaiPrivateScraper(auto_authenticate=True)

# Fetch collection items (with pagination)
items = scraper.fetch_collection_items(collection_id, limit=50)

# Scrape with full details (includes tags automatically)
data = scraper.scrape(collection_id, limit=50)
```

### CollectionAnalyzer

```python
from analyze_collection import CollectionAnalyzer

# Initialize with scraped data
analyzer = CollectionAnalyzer(scraped_data)

# Run analysis
analyzer.analyze()

# Get results
top_models = analyzer.get_top_items(analyzer.models, 10)
avg_weights = analyzer.get_average_weights()
```

## Method Mapping

### Removed from CivitaiPrivateScraper → Use in CivitaiAPI

| Removed Method | Replacement |
|---------------|-------------|
| `fetch_image_basic_info()` | `api.fetch_basic_info()` |
| `fetch_image_details()` | `api.fetch_generation_data()` |
| `_get_auto_session_token()` | `api._get_auto_session_token()` |
| `_get_headers()` | `api._get_headers()` |
| `self.session_cookie` | `api.session_cookie` |
| `self.base_url` | `api.base_url` |
| `self.default_params` | `api.default_params` |

### Removed from analyze_collection.py → Built-in to CivitaiPrivateScraper

| Removed Function | Replacement |
|----------------|-------------|
| `fetch_collection_items_paginated()` | `scraper.fetch_collection_items()` |
| `scrape_with_limit()` | `scraper.scrape(limit=50)` |
| Monkey-patching code | Not needed - use built-in methods |

## Data Flow

```
1. Scraper Initialization
   CivitaiPrivateScraper.__init__()
       ↓
   CivitaiAPI.get_instance()
       ↓
   Authenticate & get session token

2. Collection Scraping
   scraper.scrape(collection_id, limit=50)
       ↓
   scraper.fetch_collection_items()  # Paginated fetch
       ↓
   For each image:
       ├── api.fetch_generation_data()  # Get generation info
       ├── api.fetch_image_tags()  # Get tags
       └── scraper._merge_data()  # Merge all data

3. Data Analysis
   CollectionAnalyzer(scraped_data)
       ↓
   analyzer.analyze()
       ↓
   Extract patterns, count occurrences, generate stats

4. Report Generation
   print_analysis_report()  # Display formatted results
```

## Example: Complete Workflow

```python
#!/usr/bin/env python3
from civitai import CivitaiPrivateScraper
from analyze_collection import CollectionAnalyzer
from console_utils import ConsoleFormatter

# 1. Initialize scraper
scraper = CivitaiPrivateScraper(auto_authenticate=True)

# 2. Scrape collection (with limit)
data = scraper.scrape(collection_id=11035255, limit=100)
print(f"Scraped {len(data)} images")

# 3. Analyze data
analyzer = CollectionAnalyzer(data)
analyzer.analyze()

# 4. Print report
fmt = ConsoleFormatter()
fmt.print_header("Analysis Results")
fmt.print_key_value("Top Model", analyzer.get_top_items(analyzer.models, 1)[0][0])
fmt.print_key_value("Total Tags", len(analyzer.tags))
```

## Troubleshooting

### "AttributeError: 'CivitaiPrivateScraper' object has no attribute 'fetch_image_details'"

**Solution**: Use `scraper.api.fetch_generation_data()` instead

### "AttributeError: 'CivitaiPrivateScraper' object has no attribute '_get_headers'"

**Solution**: Use `scraper.api._get_headers()` instead

### "Need to specify limit for collection scraping"

**Solution**: Use `scraper.scrape(collection_id, limit=50)` - limit is built-in now

### "Tags are missing from scraped data"

**Solution**: Ensure you're using `scraper.scrape()` (not `fetch_collection_items()`), which automatically fetches tags

## Testing Changes

After refactoring, verify:

```python
# Test 1: Basic scraper initialization
from civitai import CivitaiPrivateScraper
scraper = CivitaiPrivateScraper(auto_authenticate=True)
assert scraper.api is not None

# Test 2: Scraping with limit
data = scraper.scrape(11035255, limit=5)
assert len(data) <= 5
assert 'tags' in data[0]  # Tags should be included

# Test 3: Analysis
from analyze_collection import CollectionAnalyzer
analyzer = CollectionAnalyzer(data)
analyzer.analyze()
assert len(analyzer.models) > 0
```
