# Civitai API Reference

## Overview

This document documents all known Civitai API endpoints, request formats, and response structures based on reverse-engineering and testing with the Civitai Private Scraper project.

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

---

## 📨 Request Format (tRPC)

Civitai uses **tRPC** protocol which requires a specific JSON structure.

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
    """Wrap the input JSON into tRPC structure."""
    return json.dumps({
        "json": input_json,
        "meta": {"values": {"cursor": ["undefined"]}}
    })
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
| `civitaiResources` | list | Civitai resource references |

**Resources Array:**
| Field | Type | Description |
|-------|------|-------------|
| `modelType` | str | Type: "lora", "checkpoint", "textualinversion" |
| `modelName` | str | Display name |
| `strength` | float | Weight for LoRAs/textualinversions |
| `modelVersionId` | int | Civitai model version ID |
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
- **"Deleted"** - Model/version has been removed from Civitai
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
| 1     | Mild          |
| 2     | Moderate      |
| 4     | Mature        |
| 8     | Explicit      |
| 16    | Very Explicit |

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
        """Make a request to Civitai API."""
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

This API reference is based on reverse-engineering and testing. Civitai may change endpoints or parameters without notice. Use responsibly and respect their Terms of Service.

---

## 📝 Version History

| Version | Date       | Changes                                  |
|---------|------------|------------------------------------------|
| 1.0     | 2026-01-30 | Initial documentation of known endpoints |
