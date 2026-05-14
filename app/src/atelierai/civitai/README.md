# CivitAI Integration Package (`atelierai.civitai`)

A shared Python package providing authenticated access to the CivitAI tRPC API, dual-backend image search (Meilisearch + REST), model catalog sync, Playwright-based OAuth authentication, and rate-limited HTTP transport.

## Installation

```bash
# From repo root (editable install)
pip install -e app/src
```

After installation, import with:

```python
from atelierai.civitai import CivitaiAPI, CivitaiImage, CivitaiSearchClient, CivitaiPrivateScraper
```

## Architecture Overview

```
atelierai.civitai/
├── __init__.py                 # Public exports
├── civitai_api.py              # CivitaiAPI — singleton tRPC client (~1300 lines)
├── http_client.py              # CivitaiHttpClient — rate-limited HTTP transport
├── civitai_search.py           # CivitaiSearchClient — dual-backend image search
├── civitai_models.py           # Model catalog sync (model.getAll / model.getById)
├── civitai_auth.py             # Playwright OAuth authentication
├── civitai_image.py            # CivitaiImage — image data model + URL construction
├── civitai.py                  # CivitaiPrivateScraper — legacy collection scraper
├── console_utils.py            # ConsoleFormatter — terminal output with Unicode width
├── rate_limiter.py             # Generic rate limiter (3 RPM / 10K TPM — VoyageAI)
├── civitai_trpc_spec.yaml      # Reverse-engineered tRPC API spec
└── civitai_search_spec.yaml    # Reverse-engineered Meilisearch search spec
```

## Core Components

### CivitaiAPI (Singleton)

**File:** `civitai_api.py`

Central singleton for all CivitAI tRPC operations. Wraps `CivitaiHttpClient` with retry logic, response caching (DB-backed), and automatic response archiving to JSON files.

```python
api = CivitaiAPI.get_instance()

# Image metadata
basic_info = api.fetch_basic_info(117165031)           # image.get
gen_data = api.fetch_generation_data(117165031)         # image.getGenerationData
tags = api.fetch_image_tags(117165031)                  # tag.getVotableTags
both = api.fetch_image_data(117165031)                  # basic + generation combined

# Model catalog
page = api.fetch_model_list(username="artist", cursor=None)  # model.getAll
detail = api.fetch_model_detail(model_id=12345)               # model.getById
avail = api.check_model_availability(model_id=1, model_version_id=2)  # modelVersion.getById

# Posts
post = api.fetch_post(post_id=999)                      # post.get
posts = api.fetch_user_posts(user_id=42, cursor=None)    # post.getInfinite
images = api.fetch_post_images(post_id=999)              # image.getInfinite filtered by postId

# Collections
items = api.fetch_collection_items(collection_id=11035255)        # image.getInfinite
enriched = api.fetch_collection_with_details(collection_id=11035255)  # items + generation data
posts = api.fetch_collection_posts(collection_id=11035255)        # post.getInfinite filtered by collectionId

# Cache-first variants (DB cache → live fetch)
api.fetch_basic_info_cached(image_id, max_age=timedelta(hours=24))
api.fetch_generation_data_cached(image_id, max_age=timedelta(hours=24))
api.fetch_image_tag_records_cached(image_id, max_age=timedelta(hours=24))
api.fetch_model_detail_cached(model_id, max_age=timedelta(hours=24))
```

### CivitaiHttpClient

**File:** `http_client.py`

Low-level HTTP transport with:

- **FIFO request queue** — daemon consumer thread serializes all CivitAI requests
- **Sliding-window rate limiting** — 25 RPM target, 60-second window, 3-request headroom
- **Per-endpoint TPM tracking** — per-endpoint sliding-window timestamps for telemetry
- **Global backoff** — activated on 429/503/403 Cloudflare responses; pauses all requests
- **CDN download pacing** — minimum interval between `image.civitai.com` downloads
- **DNS fallback** — UDP-based resolver for macOS stale-cache issues (`_DnsFallbackAdapter`)
- **Request classification** — `_classify_request()` categorizes URLs as `TRPC`, `CDN_DOWNLOAD`, or `UNKNOWN`

```python
client = CivitaiHttpClient(headers_factory=lambda: {"Cookie": f"__Secure-civitai-token={token}"})

# Blocking request (enqueued to FIFO, processed by daemon thread)
response = client.request("GET", url, headers=headers)

# JSON convenience
data = client.request_json("GET", url, headers=headers)

# Download to temp file
path = client.download_to_temp(url, headers=headers)

# Metrics
metrics = client.get_request_metrics()  # includes tpm_breakdown, session stats
```

**Request rate categories:**

| Category | Rate-limited | Transport |
|---|---|---|
| tRPC (`/api/trpc/*`) | Yes — 25 RPM sliding window | `CivitaiHttpClient` FIFO queue |
| CDN (`image.civitai.com`) | Yes — shared limiter + CDN pacing | `CivitaiHttpClient` FIFO queue |
| REST search (`/api/v1/images`) | No | Direct `requests.get` |
| Meilisearch (`/multi-search`) | No | Direct `requests.post` |

### CivitaiSearchClient

**File:** `civitai_search.py`

Dual-backend image search with automatic fallback:

1. **Meilisearch** (preferred) — full-text search at `search-new.civitai.com` with tag/NSFW filtering, facets, sort. Requires `CIVITAI_MEILISEARCH_KEY` (auto-scraped from CivitAI frontend JS bundle).
2. **REST API** (fallback) — `GET /api/v1/images` — no auth required, cursor pagination only.

```python
client = CivitaiSearchClient()

results = client.search_images(
    query="landscape",
    tags=["scenery", "nature"],
    nsfw_level=1,
    limit=20,
    offset=0,
)
```

### CivitaiModelSync (`civitai_models.py`)

Functions for model catalog scraping and database sync:

- `fetch_model_list(api, username, cursor, limit)` — cursor-paginated `model.getAll`
- `fetch_model_detail(api, model_id)` — full detail via `model.getById`
- `sync_models_for_user(db, api, username)` — full catalog sync to DB
- `sync_model_detail(db, api, model_id)` — single model detail sync

Dynamically loads DB model classes (`CivitaiModel`, `CivitaiModelVersion`, etc.) to avoid circular imports.

### CivitaiImage

**File:** `civitai_image.py`

Image data model providing:

- Consistent URL construction from URL hashes with correct file extensions
- Factory methods: `from_single_image()`, `from_collection_item()`
- Properties: `image_id`, `image_url`, `display_url`, `author`, `tags`, `model`, `loras`, `embeddings`
- `ConsoleFormatter`-based display via `print_details()`

### CivitaiPrivateScraper

**File:** `civitai.py`

Legacy high-level collection scraper. Delegates to `CivitaiAPI` for all API calls. Primary use: `scrape(collection_id, limit)`.

### CivitaiAuth

**File:** `civitai_auth.py`

Playwright-based OAuth authentication with:

- **Stealth mode** — `playwright-stealth` with macOS platform override (`MacIntel`)
- **Token caching** — session token persisted to file (~30-day validity)
- **Browser state persistence** — Chrome user data directory survives across runs
- **Auto-refresh** — `get_cached_or_refresh_session_token()` checks cache then refreshes

```python
from atelierai.civitai.civitai_auth import get_cached_or_refresh_session_token

token = get_cached_or_refresh_session_token(cache_file=".civitai_session", headless=True)
```

### ConsoleFormatter

**File:** `console_utils.py`

Terminal output utilities with Unicode display-width support (CJK, emoji) via `wcwidth`. Provides:

- `get_display_width(text)` — terminal columns occupied
- `truncate_to_width(text, max_width)` — ellipsis-aware truncation
- `ConsoleFormatter` class — styled tables, headers, separators

### RateLimiter

**File:** `rate_limiter.py`

Generic sliding-window rate limiter (3 RPM / 10K TPM). **Note:** This is for VoyageAI integration, NOT used by the CivitAI HTTP client (which has its own built-in rate limiting).

## tRPC API Endpoints

All tRPC procedures are accessed via GET requests with JSON-encoded `input=` query parameter. Responses use the `result.data.json` envelope.

| Procedure | CivitaiAPI Method | Description |
|---|---|---|
| `image.get` | `fetch_basic_info()` | Single image metadata (URL, author, NSFW) |
| `image.getGenerationData` | `fetch_generation_data()` | Generation params (prompts, models, LoRAs) |
| `image.getInfinite` | `fetch_collection_items()`, `fetch_post_images()`, `fetch_collection_with_details()` | Paginated image listing (collection/post filters) |
| `tag.getVotableTags` | `fetch_image_tag_records()` | Votable tags for an image/model/post |
| `model.getAll` | `fetch_model_list()` | Cursor-paginated model listing by username |
| `model.getById` | `fetch_model_detail()` | Full model detail by ID |
| `modelVersion.getById` | `check_model_availability()` | Model version detail + status check |
| `post.get` | `fetch_post()` | Post metadata by ID |
| `post.getInfinite` | `fetch_user_posts()`, `fetch_collection_posts()` | Paginated post listing |
| `collection.getById` | *(via CivitaiAPI)* | Collection metadata |

**Not implemented in Python but documented in spec:**
- `signals.getToken` — bearer token for search service
- `system.getBrowsingSettingAddons` — account content-filter presets
- `hiddenPreferences.getHidden` — user hidden/blocked entities
- `collection.getAllUser` — user's collections list

## Configuration

All config values are loaded from `atelierai.config` (or fallback `backend.config`). Key values:

| Variable | Default | Purpose |
|---|---|---|
| `CIVITAI_WEB_BASE_URL` | `https://civitai.red` | Frontend URL for link generation |
| `CIVITAI_TRPC_BASE_URL` | `https://civitai.red/api/trpc` | tRPC API base URL |
| `CIVITAI_CDN_BASE_URL` | `https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA` | CDN base for image URLs |
| `CIVITAI_BASE_DOMAIN` | `civitai.red` | Domain for DNS/cookie resolution |
| `CIVITAI_SEARCH_BASE_URL` | `https://search-new.civitai.com` | Meilisearch host |
| `CIVITAI_SESSION_CACHE` | *(project-specific)* | Token cache file path |
| `CIVITAI_MEILISEARCH_KEY` | *(auto-scraped)* | Meilisearch API key |

Environment variable overrides:

| Variable | Default | Purpose |
|---|---|---|
| `CIVITAI_MIN_REQUEST_INTERVAL` | `0.25` | Min seconds between queued requests |
| `CIVITAI_CDN_MIN_INTERVAL` | `1.0` | Min seconds between CDN downloads |

## Response Archiving

`CivitaiAPI` automatically archives raw tRPC responses to `image_resources/civitai_api_responses/`:

- `image.get` → `civitai_image_get_{uuid}.json`
- `image.getGenerationData` → `civitai_image_getGenerationData_{uuid}.json`
- `image.getInfinite` → `civitai_image_getInfinite_{uuid}.json` (per-item)

Archival uses a UUID-to-image-ID index for cross-referencing.

## DB Cache

Live API responses are persisted to a database cache table via `_record_to_db_cache()`. Cache-first methods (`fetch_*_cached()`) check the DB before making network calls, with optional `max_age` and `cache_only` modes.

## Rate Limiting Details

The `CivitaiHttpClient` implements a sliding-window rate limiter:

- **Target:** 25 RPM (requests per minute)
- **Window:** 60 seconds
- **Headroom:** stops 3 requests before the ceiling (effective limit: 22 RPM)
- **Global backoff:** 30-second cooldown on 429 responses, triggered by Cloudflare 403 challenges and 503 responses
- **Per-endpoint TPM:** sliding-window timestamps tracked per tRPC procedure name
- **FIFO queue:** all tRPC and CDN requests serialized through a daemon consumer thread
- **CDN pacing:** separate minimum interval for `image.civitai.com` downloads

## Full Spec Files

- **tRPC API:** `civitai_trpc_spec.yaml` — all reverse-engineered tRPC procedures with input/output schemas
- **Search API:** `civitai_search_spec.yaml` — Meilisearch multi-search endpoint at `search-new.civitai.com`
