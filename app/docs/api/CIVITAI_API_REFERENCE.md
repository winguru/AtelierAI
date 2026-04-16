# CivitAI API Reference

> **Domain note (2025):** CivitAI split into two domains: `civitai.com` (sanitized/PG-13) and `civitai.red` (all existing content, PG through XXX). Both domains expose the same tRPC/REST API surface. AtelierAI defaults to `civitai.red` via the `CIVITAI_BASE_DOMAIN` env var; the CDN domains (`image.civitai.com`, `image-b2.civitai.com`) and search service (`search-new.civitai.com`) are unchanged.

## Overview

This document documents all known CivitAI API endpoints, request formats, and response structures based on reverse-engineering and testing with the CivitAI Private Scraper project.

## Table of Contents

### Core Sections

- [Overview](#overview)
- [Authentication](#-authentication)
- [Session Cookie](#session-cookie)
- [Headers Required](#headers-required)
- [Search Bearer Token](#search-bearer-token)
- [Request Format (tRPC)](#-request-format-trpc)
- [URL Structure](#url-structure)
- [JSON Payload Structure](#json-payload-structure)
- [Encoding](#encoding)
- [Helper Function](#helper-function)
- [Response Structure](#-response-structure)
- [tRPC Response Format](#trpc-response-format)
- [Navigation](#navigation)
- [Known API Endpoints](#-known-api-endpoints)

### API Endpoint Groups

- [Search Endpoints](#search-endpoints)
- [Image Endpoints](#image-endpoints)
- [Tag Endpoints](#tag-endpoints)
- [Model Endpoints](#model-endpoints)
- [Post Endpoints](#post-endpoints)
- [System and Preferences Endpoints](#system-and-preferences-endpoints)

### API Endpoints

- [POST /multi-search](#post-multi-search)
- [signals.getToken](#signalsgettoken)
- [image.get](#imageget)
- [image.getGenerationData](#imagegetgenerationdata)
- [image.getInfinite](#imagegetinfinite)
- [tag.getVotableTags](#taggetvotabletags)
- [tag.getById](#taggetbyid)
- [modelVersion.getById](#modelversiongetbyid)
- [post.get](#postget)
- [system.getBrowsingSettingAddons](#systemgetbrowsingsettingaddons)
- [hiddenPreferences.getHidden](#hiddenpreferencesgethidden)
- [collection.getAllUser](#collectiongetalluser)

### Examples and Reference

- [Complete Data Flow Example](#-complete-data-flow-example)
- [Fetch Single Image with All Data](#fetch-single-image-with-all-data)
- [Common Patterns](#-common-patterns)
- [NSFW Levels](#nsfw-levels)
- [Image MIME Types](#image-mime-types)
- [Model Types](#model-types)
- [Base Models](#base-models)
- [Error Responses](#-error-responses)
- [Common Errors](#common-errors)
- ["401 Unauthorized"](#401-unauthorized)
- ["404 Not Found"](#404-not-found)
- [Helper Code](#-helper-code)
- [Complete Request Function](#complete-request-function)
- [Related Documentation](#-related-documentation)
- [Disclaimer](#-disclaimer)
- [Version History](#-version-history)

---

## 🔑 Authentication

### Session Cookie

**Cookie Name:** `__Secure-civitai-token` ⚠️ IMPORTANT

**Incorrect:** `__Secure-next-auth.session-token` (don't use this!)

**Format:** Long JWT-like string starting with `eyJ...`

**Duration:** ~30 days of inactivity

**Example:**
```http
Cookie: __Secure-civitai-token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ...<very long string>...3g.5HqX8FQyS6hYw
```

### Headers Required

```python
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Cookie": f"__Secure-civitai-token={your_token}",
    "Referer": "https://civitai.com/"
}
```

### Search Bearer Token

The hosted search service at `search-new.civitai.com` uses a separate bearer token rather than the raw `__Secure-civitai-token` cookie.

**Observed token bridge endpoint:** `signals.getToken`

**Observed behavior:**
- A logged-in browser session can call `https://civitai.com/api/trpc/signals.getToken` with the normal `__Secure-civitai-token` cookie.
- The request payload observed so far is `{"json":{"authed":true}}`.
- This endpoint appears to return the bearer token later sent as `Authorization: Bearer <token>` to `https://search-new.civitai.com/multi-search`.
- This relationship is inferred from captured request flow and should be treated as reverse-engineered, not officially documented.

**Observed minimal request shape:**
```http
GET /api/trpc/signals.getToken?input=%7B%22json%22%3A%7B%22authed%22%3Atrue%7D%7D HTTP/1.1
Host: civitai.com
Cookie: __Secure-civitai-token=<redacted>
Referer: https://civitai.com/
X-Client: web
```

**Practical note:** most browser fingerprint and client-hint headers appear incidental. For documentation purposes, the normal authenticated cookie is the important requirement; CGI-style request metadata does not appear essential.

---

## 📨 Request Format (tRPC)

CivitAI uses **tRPC** protocol which requires a specific JSON structure.

**Note:** Not every CivitAI-adjacent endpoint uses tRPC. In particular, the hosted search service at `search-new.civitai.com` exposes separate JSON POST APIs.

### URL Structure

```
https://civitai.com/api/trpc/{endpoint}?input={encoded_payload}
```

### JSON Payload Structure

```json
{
  "json": {
    // Your endpoint-specific data goes here
    "id": 123456,
    "authed": true,
    "type": "image"
  },
  "meta": {
    "values": {
      "cursor": ["undefined"]
    }
  }
}
```

**Important:** `meta` is optional for most endpoints and is often ignored when provided.

**Observed usage rule (2026-03-11):**
- Non-infinite endpoints (`image.get`, `collection.getAllUser`, `hiddenPreferences.getHidden`, `system.getBrowsingSettingAddons`) returned identical data with and without `meta`.
- `image.getInfinite`:
  - First page (`cursor: null`): include `meta.values.cursor=["undefined"]`.
  - Next pages (`cursor` is a string): omit `meta`; including `meta` can cause first-page repetition.

**Validation Notes (2026-03-11):**
- Non-infinite endpoint comparisons (with vs without `meta`):
  - `system.getBrowsingSettingAddons`: `200/200`, payload equality `True`
  - `hiddenPreferences.getHidden`: `200/200`, payload equality `True`
  - `collection.getAllUser`: `200/200`, payload equality `True`
  - `image.get`: `200/200`, payload equality `True`
- `image.getInfinite` first page (`cursor: null`):
  - With `meta`: `200`
  - Without `meta`: `400` (for tested payload shape)
- `image.getInfinite` second page (using a returned `nextCursor`):
  - With `meta`: `200`, but returned a page fully overlapping first-page IDs (`50` overlap)
  - Without `meta`: `200`, returned non-overlapping next-page IDs (`0` overlap)

Test scripts used:
- `app/dev/check_meta_requirement.py`
- `/tmp/check_infinite_cursor_meta.py`

### Encoding

The entire JSON structure must be URL-encoded and passed as the `input` query parameter:

```python
import json
from urllib.parse import quote

payload_data = {
    "id": 117165031,
    "type": "image",
    "authed": True
}

trpc_structure = {
    "json": payload_data,
    "meta": {"values": {"cursor": ["undefined"]}}
}

encoded_input = quote(json.dumps(trpc_structure))

url = f"https://civitai.com/api/trpc/image.get?input={encoded_input}"
```

### Helper Function

```python
def _build_trpc_payload(input_json: dict) -> str:
  """Wrap input JSON for tRPC.

  For image.getInfinite:
  - include meta only when cursor is None (first page)
  - omit meta when cursor is present (next pages)
  """
  payload = {"json": input_json}
  if input_json.get("cursor") is None:
    payload["meta"] = {"values": {"cursor": ["undefined"]}}
  return json.dumps(payload)
```

---

## 📨 Response Structure

### tRPC Response Format

```json
{
  "result": {
    "data": {
      "json": {
        // Your actual data is here
      }
    }
  }
}
```

### Navigation

To access your data:

```python
# Method 1: Deep navigation (recommended)
def _find_deep_image_list(obj):
    # Recursively find image list in nested structure
    ...

# Method 2: Direct access
response = requests.get(url)
data = response.json()
result = data.get("result", {}).get("data", {}).get("json", {})
```

---

## 📡 Known API Endpoints

### Search Endpoints

The dedicated search host uses bearer auth instead of the session cookie directly. The current evidence suggests the bearer token is obtained from the main-site tRPC procedure `signals.getToken` described in the authentication section above.

#### `POST /multi-search`

Submit one or more Meilisearch-style search queries in a single request. This endpoint is hosted on the dedicated CivitAI search service rather than the main tRPC host.

**URL:** `https://search-new.civitai.com/multi-search`

**Method:** `POST`

**Authentication:**
- Requires `Authorization: Bearer <token>`
- This is distinct from the `__Secure-civitai-token` cookie used by the tRPC endpoints
- The bearer token appears to be minted or returned by `https://civitai.com/api/trpc/signals.getToken` for an already-authenticated browser session

#### `signals.getToken`

Return or mint the bearer token used by the hosted search service.

**URL:** `https://civitai.com/api/trpc/signals.getToken`

**Method:** `GET`

**Authentication:**
- Requires the normal CivitAI session cookie: `__Secure-civitai-token=<token>`
- No bearer token is sent to this endpoint in the observed request

**Observed query string:**
```text
input={"json":{"authed":true}}
```

**Observed request notes:**
- Called against the main `civitai.com/api/trpc` host, not `search-new.civitai.com`
- Appears to be part of the browser flow before authenticated search requests
- Likely usable with a minimal header set so long as the session cookie is valid

**Observed role in auth flow:**
1. Browser sends authenticated tRPC request to `signals.getToken`
2. Response appears to contain a search-scoped token
3. Browser sends that token as `Authorization: Bearer <token>` to `/multi-search`

**Response shape:**
- Not fully captured yet
- Expected to be wrapped in the standard tRPC envelope under `result.data.json`

**Observed headers:**
```http
Authorization: Bearer <redacted>
Content-Type: application/json
Origin: https://civitai.com
Referer: https://civitai.com/
X-Meilisearch-Client: Meilisearch instant-meilisearch (v0.13.5) ; Meilisearch JavaScript (v0.34.0)
```

**Request body:**
```json
{
  "queries": [
    {
      "q": "",
      "indexUid": "images_v6",
      "facets": [
        "aspectRatio",
        "baseModel",
        "createdAtUnix",
        "tagNames",
        "techniqueNames",
        "toolNames",
        "type",
        "user.username"
      ],
      "attributesToHighlight": [],
      "highlightPreTag": "__ais-highlight__",
      "highlightPostTag": "__/ais-highlight__",
      "limit": 51,
      "offset": 0,
      "filter": [
        "\"tagNames\"=\"bikini\"",
        "createdAtUnix>=1773633600000",
        "(poi != true OR user.username = winguru) AND (minor != true) AND (NOT (nsfwLevel IN ['4', '8', '16', '32'] AND baseModel IN ['SD 3', 'SD 3.5', 'SD 3.5 Medium', 'SD 3.5 Large', 'SD 3.5 Large Turbo', 'SDXL Turbo', 'SVD', 'SVD XT', 'Stable Cascade'])) AND (nsfwLevel=1 OR nsfwLevel=2 OR nsfwLevel=4 OR nsfwLevel=8 OR nsfwLevel=16)"
      ],
      "sort": [
        "stats.reactionCountAllTime:desc"
      ]
    },
    {
      "q": "",
      "indexUid": "images_v6",
      "facets": ["createdAtUnix"],
      "attributesToHighlight": [],
      "highlightPreTag": "__ais-highlight__",
      "highlightPostTag": "__/ais-highlight__",
      "limit": 1,
      "offset": 0,
      "filter": [
        "\"tagNames\"=\"bikini\"",
        "(poi != true OR user.username = winguru) AND (minor != true) AND (NOT (nsfwLevel IN ['4', '8', '16', '32'] AND baseModel IN ['SD 3', 'SD 3.5', 'SD 3.5 Medium', 'SD 3.5 Large', 'SD 3.5 Large Turbo', 'SDXL Turbo', 'SVD', 'SVD XT', 'Stable Cascade'])) AND (nsfwLevel=1 OR nsfwLevel=2 OR nsfwLevel=4 OR nsfwLevel=8 OR nsfwLevel=16)"
      ],
      "sort": [
        "stats.reactionCountAllTime:desc"
      ]
    }
  ]
}
```

**Top-level fields:**
| Field | Type | Description |
|-------|------|-------------|
| `queries` | list | Array of independent search requests executed in one POST |

**Per-query fields:**
| Field | Type | Description |
|-------|------|-------------|
| `q` | str | Search text; empty string is valid |
| `indexUid` | str | Search index name, e.g. `images_v6` |
| `facets` | list[str] | Facet fields to aggregate |
| `attributesToHighlight` | list[str] | Fields to highlight in text results |
| `highlightPreTag` | str | Prefix marker for search highlights |
| `highlightPostTag` | str | Suffix marker for search highlights |
| `limit` | int | Maximum number of hits to return |
| `offset` | int | Offset for paginated search |
| `filter` | list[str] | Meilisearch-compatible filter expressions |
| `sort` | list[str] | Sort expressions such as `stats.reactionCountAllTime:desc` |

**Filter behavior notes:**
- Filter expressions are passed as strings and can combine equality, inequality, range checks, `IN`, `NOT`, `AND`, and `OR`.
- Nested dotted fields are supported in both `facets` and filters, e.g. `user.username`.
- This endpoint appears to follow Meilisearch filter syntax rather than the tRPC input envelope conventions.

**Observed usage pattern:**
- One query can request a page of hits and full facet buckets.
- A second query in the same request can be used to compute a narrower aggregate, such as a single-facet time histogram or alternate sort probe.

**Response shape (Meilisearch-style envelope):**
```json
{
  "results": [
    {
      "hits": [
        {
          "id": 124133297,
          "type": "video",
          "user": {
            "username": "fatberg_slim"
          },
          "stats": {
            "reactionCountAllTime": 783
          }
        }
      ],
      "query": "",
      "limit": 51,
      "offset": 0,
      "estimatedTotalHits": 0,
      "processingTimeMs": 0,
      "facetDistribution": {},
      "facetStats": {}
    }
  ]
}
```

**Response notes:**
- The exact hit object depends on the selected `indexUid`.
- For `images_v6`, hit rows are expected to resemble the image/video objects returned elsewhere in CivitAI search-driven UIs.
- `facetDistribution` and `facetStats` are only populated when relevant facets are requested.

---

### Image Endpoints

#### `image.get`

Fetch basic image information.

**URL:** `https://civitai.com/api/trpc/image.get`

**Payload:**
```json
{
  "json": {
    "id": <image_id>,
    "authed": true
  },
  "meta": {
    "values": {
      "cursor": ["undefined"]
    }
  }
}
```

**Response:**
```json
{
  "result": {
    "data": {
      "json": {
        "id": 117165031,
        "name": "SSG1WJ9110B8W7X7T3G69VSK00.jpg",
        "url": "95f49958-6194-495d-a49c-6344453df726",
        "height": 1536,
        "width": 1536,
        "hash": "U9G[4%0e0KoeKjE2wJNa00$*~CnhI=NukC^k",
        "createdAt": "2026-01-11T23:49:44.272Z",
        "mimeType": "image/jpeg",
        "nsfwLevel": 16,
        "postId": 25815673,
        "user": {
          "id": 3319660,
          "username": "Buzzington",
          "image": "https://lh3.googleusercontent.com/a/ACg8ocLF78XW6D5j9qzQwF1jIlzZzR8PzaCw47jJNrIjAes=s96-c"
        },
        "stats": {
          "likeCountAllTime": 5,
          "collectedCountAllTime": 3
        },
        "type": "image",
        "onSite": true
      }
    }
  }
}
```

**Fields:**
| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Unique image identifier |
| `name` | str | Filename (may have wrong extension) |
| `url` | str | URL hash (NOT full URL) |
| `height` | int | Image height in pixels |
| `width` | int | Image width in pixels |
| `hash` | str | Perceptual hash for deduplication |
| `createdAt` | str | ISO timestamp of creation |
| `mimeType` | str | MIME type (e.g., "image/jpeg") |
| `nsfwLevel` | int | NSFW level (0=Safe, 16=Explicit, etc.) |
| `postId` | int | Associated post ID (for tag fetching) |
| `user` | dict | User object with `username`, `image`, etc. |
| `stats` | dict | Like, collect, view counts |

---

#### `image.getGenerationData`

Fetch detailed generation parameters.

**URL:** `https://civitai.com/api/trpc/image.getGenerationData`

**Payload:**
```json
{
  "json": {
    "id": <image_id>,
    "authed": true
  },
  "meta": {
    "values": {
      "cursor": ["undefined"]
    }
  }
}
```

**Response:**
```json
{
  "result": {
    "data": {
      "json": {
        "type": "image",
        "onSite": true,
        "process": "txt2img",
        "engine": "comfyui",
        "meta": {
          "baseModel": "Illustrious",
          "prompt": "dutch angle, back view, perfect round ass...",
          "negativePrompt": "worst quality, bad quality, simple background...",
          "cfgScale": 3,
          "steps": 34,
          "sampler": "Euler a",
          "seed": 1339988861,
          "engine": "comfyui",
          "workflow": "img2img-hires",
          "clipSkip": 2,
          "width": 1024,
          "height": 1024,
          "Model": "DarkMix Mimosa Illustrious - 2.5D Anime",
          "extra": {},
          "civitaiResources": [...],
          "models": [...],
          "images": [...]
        },
        "resources": [
          {
            "modelType": "lora",
            "modelName": "Slave auction | Sex slave  | Concept version...",
            "strength": 0.7,
            "modelVersionId": 1725302,
            "versionName": "Illustrious/NoobAI/Pony",
            "baseModel": "Illustrious"
          },
          ...
        ]
      }
    }
  }
}
```

**Fields:**
| Field | Type | Description |
|-------|------|-------------|
| `type` | str | Resource type (always "image") |
| `process` | str | Generation process (txt2img, img2img, etc.) |
| `engine` | str | Engine used (comfyui, a1111, etc.) |
| `meta` | dict | **Main generation metadata** |
| `resources` | list | **Models, LoRAs, embeddings** |

**Meta Object:**
| Field | Type | Description |
|-------|------|-------------|
| `baseModel` | str | Base model (Pony, SDXL 1.0, Illustrious, etc.) |
| `prompt` | str | Positive prompt |
| `negativePrompt` | str | Negative prompt |
| `cfgScale` | float | CFG scale value |
| `steps` | int | Number of steps |
| `sampler` | str | Sampler name |
| `seed` | int | Random seed |
| `clipSkip` | int | CLIP skip value |
| `width` | int | Image width |
| `height` | int | Image height |
| `workflow` | str | Workflow type |
| `Model` | str | Model name (from civitaiResources) |
| `civitaiResources` | list | CivitAI resource references |

**Resources Array:**
| Field | Type | Description |
|-------|------|-------------|
| `modelType` | str | Type: "lora", "checkpoint", "textualinversion" |
| `modelName` | str | Display name |
| `strength` | float | Weight for LoRAs/textualinversions |
| `modelVersionId` | int | CivitAI model version ID |
| `versionName` | str | Version name |
| `baseModel` | str | Base model type |

---

#### `image.getInfinite`

Fetch collection items with pagination support.

**URL:** `https://civitai.com/api/trpc/image.getInfinite`

**Payload:**
```json
{
  "json": {
    "collectionId": <collection_id>,
    "authed": true,
    "period": "AllTime",
    "sort": "Newest",
    "browsingLevel": 31,
    "include": ["cosmetics"],
    "excludedTagIds": [...],
    "disablePoi": true,
    "disableMinor": true,
    "cursor": null  // or cursor string for next page
  },
  "meta": {
    "values": {
      "cursor": ["undefined"]
    }
  }
}
```

**Response:**
```json
{
  "result": {
    "data": {
      "json": {
        "nextCursor": "62702|16646466",
        "items": [
          {
            "id": 88474892,
            "name": "SSG1WJ9110B8W7X7T3G69VSK00.jpg",
            "url": "95f49958-6194-495d-a49c-6344453df726",
            "type": "image",
            "postId": 25815673,
            "tagIds": [...],  // Tag IDs if available
            ...
          }
        ]
      }
    }
  }
}
```

**Parameters:**
| Parameter | Type | Description |
|-----------|------|-------------|
| `collectionId` | int | Collection ID to fetch |
| `period` | str | Time period ("AllTime", "Day", "Week", "Month", "Year") |
| `sort` | str | Sort order ("Newest", "Oldest", "MostCollected", etc.) |
| `browsingLevel` | int | NSFW filter (0-31, 31=everything) |
| `include` | list | Extra data to include (e.g., ["cosmetics"]) |
| `excludedTagIds` | list | Tag IDs to exclude |
| `disablePoi` | bool | Exclude persons of interest |
| `disableMinor` | bool | Exclude potentially minor content |
| `cursor` | str/null | Pagination cursor |

**Pagination:**
- Use `nextCursor` from response to fetch next page
- Pass cursor value in subsequent requests
- Omit `meta` for cursor-based follow-up requests (only use it for first-page `cursor: null`)
- Stop when `nextCursor` is null or empty

---

### Tag Endpoints

#### `tag.getVotableTags`

Fetch votable tags for an image (sorted by relevance).

**URL:** `https://civitai.com/api/trpc/tag.getVotableTags`

**Payload:**
```json
{
  "json": {
    "id": <image_id>,
    "type": "image",
    "authed": true
  },
  "meta": {
    "values": {
      "cursor": ["undefined"]
    }
  }
}
```

**Response:**
```json
{
  "result": {
    "data": {
      "json": [
        {
          "score": 9,
          "upVotes": 0,
          "downVotes": 0,
          "automated": true,
          "needsReview": false,
          "concrete": true,
          "lastUpvote": null,
          "id": 1465,
          "type": "UserGenerated",
          "nsfwLevel": 1,
          "name": "butt"
        },
        {
          "score": 8,
          "upVotes": 0,
          "downVotes": 0,
          "automated": true,
          "needsReview": false,
          "concrete": true,
          "lastUpvote": null,
          "id": 5228,
          "type": "UserGenerated",
          "nsfwLevel": 1,
          "name": "long hair"
        },
        ...
      ]
    }
  }
}
```

**Tag Fields:**
| Field | Type | Description |
|-------|------|-------------|
| `score` | int | Relevance score (higher = more accurate) |
| `upVotes` | int | Number of upvotes |
| `downVotes` | int | Number of downvotes |
| `automated` | bool | Whether tag was auto-generated (AI detection) |
| `needsReview` | bool | Whether tag needs review |
| `concrete` | bool | Whether tag is a concrete concept |
| `lastUpvote` | str/null | Timestamp of last upvote |
| `id` | int | Unique tag ID |
| `type` | str | Tag type ("UserGenerated", "Label", "Moderation") |
| `nsfwLevel` | int | NSFW level for tag |
| `name` | str | **Tag display name** |

**Tag Types:**
- **UserGenerated** - Community-created tags
- **Label** - Pre-defined classification tags
- **Moderation** - Content moderation tags

---

#### `tag.getById`

Fetch details for a specific tag by ID.

**URL:** `https://civitai.com/api/trpc/tag.getById`

**Payload:**
```json
{
  "json": {
    "id": <tag_id>
  },
  "meta": {
    "values": {
      "cursor": ["undefined"]
    }
  }
}
```

**Response:**
```json
{
  "result": {
    "data": {
      "json": {
        "id": 4,
        "name": "anime",
        "type": "Label"
      }
    }
  }
}
```

---

### Model Endpoints

#### `modelVersion.getById`

Fetch details for a specific model version, including availability status.

**URL:** `https://civitai.com/api/trpc/modelVersion.getById`

**Payload:**
```json
{
  "json": {
    "id": <model_version_id>,
    "authed": true
  },
  "meta": {
    "values": {
      "cursor": ["undefined"]
    }
  }
}
```

**Response:**
```json
{
  "result": {
    "data": {
      "json": {
        "id": 1498821,
        "name": "IXL",
        "description": null,
        "baseModel": "Illustrious",
        "baseModelType": "Standard",
        "status": "Published",
        "createdAt": "2025-03-06T10:39:08.487Z",
        "model": {
          "id": 871004,
          "name": "Deepthroat slider Pony/IllustriousXL",
          "type": "LORA",
          "status": "Deleted",
          "publishedAt": "2024-10-20T03:38:50.436Z",
          "nsfw": true,
          "uploadType": "Created",
          "user": {
            "id": 827028
          },
          "availability": "Public"
        },
        "files": [...],
        "settings": {
          "strength": 0.5,
          "maxStrength": 10,
          "minStrength": -10
        }
      }
    }
  }
}
```

**Model Version Fields:**
| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Model version ID |
| `name` | str | Version name |
| `description` | str/null | Version description |
| `baseModel` | str | Base model (Pony, SDXL, etc.) |
| `baseModelType` | str | Base model type |
| `status` | str | **Version status** ("Published", "Deleted", etc.) |
| `createdAt` | str | ISO timestamp of creation |
| `model` | dict | Parent model information |
| `files` | array | Available download files |
| `settings` | dict | Model settings (strength range, etc.) |

**Model Object (within version):**
| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Parent model ID |
| `name` | str | Model name |
| `type` | str | Model type ("LORA", "Checkpoint", etc.) |
| `status` | str | **Model status** ("Published", "Deleted", etc.) |
| `availability` | str | Availability setting ("Public", "Private", etc.) |

**Status Values:**
- **"Published"** - Model/version is available
- **"Deleted"** - Model/version has been removed from CivitAI
- Other statuses may include "Processing", "Unpublished", etc.

**Use Case - Model Availability Checking:**
This endpoint is primarily used to check if a model has been deleted:

```python
def check_model_availability(model_id, model_version_id):
    response = api._make_request(
        endpoint="modelVersion.getById",
        payload_data={"id": model_version_id, "authed": True}
    )

    if response:
        model_info = response.get("model", {})
        model_status = model_info.get("status", "Unknown")

        if model_status == "Deleted":
            return {
                "available": False,
                "civitai_url": f"https://civitai.com/models/{model_id}",
                "archive_url": f"https://civitaiarchive.com/models/{model_id}"
            }
        else:
            return {"available": True, "status": model_status}

    return {"available": False, "error": "Not found"}
```

---

### Post Endpoints

#### `post.get`

Fetch post details (includes tags array).

**URL:** `https://civitai.com/api/trpc/post.get`

**Payload:**
```json
{
  "json": {
    "id": <post_id>,
    "authed": true
  },
  "meta": {
    "values": {
      "cursor": ["undefined"]
    }
  }
}
```

**Response:**
```json
{
  "result": {
    "data": {
      "json": {
        "id": 25815673,
        "nsfwLevel": 16,
        "title": null,
        "detail": null,
        "modelVersionId": null,
        "modelVersion": null,
        "user": {
          "id": 3319660,
          "username": "Buzzington"
        },
        "publishedAt": "2026-01-11T23:50:24.059Z",
        "availability": "Public",
        "tags": [],  // May be empty
        "collectionId": null
      }
    }
  }
}
```

**Note:** The `tags` field here may be empty. Use `tag.getVotableTags` with the `postId` for actual tag data.

---

### System and Preferences Endpoints

#### `system.getBrowsingSettingAddons`

Fetch browsing setting addon definitions used by the site filtering UI.

**URL:** `https://civitai.com/api/trpc/system.getBrowsingSettingAddons`

**Tested Payload (2026-03-11):**
```json
{
  "json": {
    "authed": true
  },
  "meta": {
    "values": {
      "cursor": ["undefined"]
    }
  }
}
```

**Observed Response Shape:**
- Top-level `json` is a list.
- List items include keys such as:
  - `type`
  - `nsfwLevels`
  - `excludedFooterLinks`

**Field Notes (interpretation):**
- `disablePoi`
  - Disables Point of Interest (PoI) detection or related detailer/refiner behavior
    in browsing and/or generation contexts.
- `disableMinor`
  - Disables minor (child) detection or related detailer/refiner behavior
    in browsing and/or generation contexts.

**`nsfwLevels` Mapping:**
| Value | Label |
|-------|-------|
| `1`   | PG |
| `2`   | PG13 |
| `4`   | R |
| `8`   | X |
| `16`  | XXX |
| `32`  | Blocked |

**Saved sample output:** `data/debug_system_getBrowsingSettingAddons_response.json`

---

#### `hiddenPreferences.getHidden`

Fetch hidden/blocked preferences for the authenticated user.

**URL:** `https://civitai.com/api/trpc/hiddenPreferences.getHidden`

**Tested Payload (2026-03-11):**
```json
{
  "json": {
    "authed": true
  },
  "meta": {
    "values": {
      "cursor": ["undefined"]
    }
  }
}
```

**Observed Response Shape:**
- Top-level `json` is an object.
- Keys observed:
  - `hiddenImages`
  - `hiddenModels`
  - `hiddenUsers`
  - `hiddenTags`
  - `blockedUsers`
  - `blockedByUsers`

**Saved sample output:** `data/debug_hiddenPreferences_getHidden_response.json`

---

#### `collection.getAllUser`

Fetch collections visible to the authenticated user.

**URL:** `https://civitai.com/api/trpc/collection.getAllUser`

**Tested Payload (2026-03-11):**
```json
{
  "json": {
    "authed": true
  },
  "meta": {
    "values": {
      "cursor": ["undefined"]
    }
  }
}
```

**Observed Response Shape:**
- Top-level `json` is a list.
- Observed list item keys include:
  - `id`
  - `name`
  - `description`
  - `read`
  - `userId`
  - `write`
  - `imageId`
  - `type`
  - `isOwner`
  - `image`
  - `tags`

**Observed Collection `type` Values (2026-03-11):**
- `Image`
- `Model`
- `Article`
- `Post`

**Important Filtering Rule:**
- For AtelierAI image ingestion (`POST /import_civitai/` with `import_type="collection"`), only use collections where `type == "Image"`.
- For tag bootstrap/export workflows that depend on `image.getInfinite` image items, only use collections where `type == "Image"`.
- Skip `Model`, `Article`, and `Post` collections for image-based import/tag extraction.

**Saved sample output:** `data/debug_collection_getAllUser_response.json`

---

## 📊 Complete Data Flow Example

### Fetch Single Image with All Data

```python
from civitai_api import CivitaiAPI

# Get API singleton
api = CivitaiAPI.get_instance()
image_id = 117165031

# 1. Fetch basic info
basic_info = api.fetch_basic_info(image_id)
# Contains: id, url, user, createdAt, mimeType, postId

# 2. Fetch generation data
generation_data = api.fetch_generation_data(image_id)
# Contains: meta (prompts, params), resources (loras, models)

# 3. Fetch tags
tags = api.fetch_image_tags(image_id)
# Contains: List of tag names sorted by relevance

# Create image instance
from civitai_image import CivitaiImage
image = CivitaiImage.from_single_image(basic_info, generation_data, api=api)

# Access all data
print(f"Author: {image.author}")
print(f"URL: {image.image_url}")
print(f"Tags: {', '.join(image.tags)}")
print(f"LoRAs: {[l['name'] for l in image.loras]}")
```

---

## 🔍 Common Patterns

### NSFW Levels

| Level | Description   |
|-------|---------------|
| 0     | Safe          |
| 1     | PG            |
| 2     | PG13          |
| 4     | R             |
| 8     | X             |
| 16    | XXX           |
| 32    | Blocked       |

### Image MIME Types

| MIME Type    | Extension |
|--------------|-----------|
| `image/jpeg` | `.jpeg`   |
| `image/png`  | `.png`    |
| `image/webp` | `.webp`   |
| `image/tiff` | `.tif`    |
| `video/mp4`  | `.mp4`    |

### Model Types

| Type | Description |
|--------------------|-------------------------------|
| `checkpoint`       | Main model (SDXL, Pony, etc.) |
| `lora`             | Style/character model         |
| `textualinversion` | Textual embedding             |
| `embedding`        | Text embedding (alternative)  |
| `LoCon`            | LoRA for SD 1.5               |
| `lycoris`          | LoRA for SDXL                 |
| `controlnet`       | Control model                 |

### Base Models

- `Pony` - Pony Diffusion family
- `SDXL 1.0` - Stable Diffusion XL 1.0
- `SD 1.5` - Stable Diffusion 1.5
- `Illustrious` - Illustrious Diffusion
- `Pony` (in resources) - May appear as base model name too

---

## ⚠️ Error Responses

| Status Code | Meaning                                  |
|-------------|------------------------------------------|
| 200         | Success                                  |
| 400         | Bad request (invalid parameters)         |
| 401         | Unauthorized (invalid/expired token)     |
| 404         | Endpoint not found or invalid parameters |
| 429         | Rate limited                             |
| 500         | Server error                             |

### Common Errors

#### "401 Unauthorized"

**Cause:** Session token expired

**Solution:**
```bash
python setup_session_token.py --force
```

#### "404 Not Found"

**Causes:**
- Invalid endpoint name
- Invalid image ID
- Image/post deleted
- No access to collection

**Solution:** Verify the ID and your access permissions.

---

## 🔧 Helper Code

### Complete Request Function

```python
import json
import requests
from urllib.parse import quote

class CivitaiAPI:
    """Example API client based on documented endpoints."""

    def __init__(self, session_token):
        self.session_token = session_token
        self.base_url = "https://civitai.com/api/trpc"
        self.session = requests.Session()

    def _build_trpc_payload(self, input_json):
        """Wrap input JSON in tRPC structure."""
        return json.dumps({
            "json": input_json,
            "meta": {"values": {"cursor": ["undefined"]}}
        })

    def _make_request(self, endpoint, payload_data):
        """Make a request to CivitAI API."""
        url = f"{self.base_url}/{endpoint}"
        params = {"input": quote(self._build_trpc_payload(payload_data))}

        headers = {
            "User-Agent": "Mozilla/5.0...",
            "Cookie": f"__Secure-civitai-token={self.session_token}",
            "Referer": "https://civitai.com/"
        }

        response = self.session.get(url, headers=headers, params=params, timeout=30)

        if response.status_code == 200:
            data = response.json()
            # Navigate tRPC structure
            return data.get("result", {}).get("data", {}).get("json")
        else:
            print(f"Error: {response.status_code}")
            return None
```

---

## 📚 Related Documentation

- `README.md` - Main project documentation (v2.0)
- `PROJECT_UPDATE_SUMMARY_v2.md` - v2.0 changelog
- `METADATA_REFERENCE.md` - Detailed metadata field reference
- `SETUP_GUIDE.md` - Setup instructions

---

## ⚠️ Disclaimer

This API reference is based on reverse-engineering and testing. CivitAI may change endpoints or parameters without notice. Use responsibly and respect their Terms of Service.

---

## 📝 Version History

| Version | Date       | Changes                                  |
|---------|------------|------------------------------------------|
| 1.0     | 2026-01-30 | Initial documentation of known endpoints |
