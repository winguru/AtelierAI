# Civitai Metadata Field Reference

This document provides a comprehensive reference for all metadata fields available through the Civitai scraper.

---

## Extracted Fields

These are the primary fields extracted and normalized by the scraper:

### Image Information

| Field       | Type | Example                         | Source                           |
|-------------|------|---------------------------------|----------------------------------|
| `image_id`  | int  | `88474892`                      | Collection API                   |
| `image_url` | str  | `https://image.civitai.com/...` | Constructed from hash + filename |
| `author`    | str  | `QuickForgeAI`                  | Collection API (username)        |

### Model Information

| Field           | Type | Example           | Source                           |
|-----------------|------|-------------------|----------------------------------|
| `model`         | str  | `iLustMix`        | Resources (modelType=Checkpoint) |
| `model_version` | str  | `v7.0 Cinematic`  | Resources (versionName)          |

### Generation Parameters

| Field       | Type  | Example           | Source |
|-------------|-------|-------------------|--------|
| `sampler`   | str   | `DPM++ 2M Karras` | Meta   |
| `steps`     | int   | `30`              | Meta   |
| `cfg_scale` | float | `5.5`             | Meta   |
| `seed`      | int   | `1517289903`      | Meta   |

### Text

| Field             | Type | Example                            | Source |
|-------------------|------|------------------------------------|--------|
| `prompt`          | str  | `1girl, solo, white background...` | Meta   |
| `negative_prompt` | str  | `bad anatomy, bad hands...`        | Meta   |

### Additional Resources

| Field   | Type | Example                            | Description                       |
|---------|------|------------------------------------|-----------------------------------|
| `loras` | list | `[{"name": "...", "weight": 1.2}]` | List of LoRA objects with weights |
| `tags`  | list | `[{"id": 123, "name": "1girl"}]`   | Image tags                        |

---

## Raw Metadata Fields

The `raw_meta_json` field contains the complete API response. Below are the commonly used fields:

### Meta Object (`meta`)

| Field              | Type  | Description |
|--------------------|-------|----------------------------------------------------|
| `baseModel`        | str   | Base model type (Pony, SDXL 1.0, etc.)             |
| `prompt`           | str   | Positive prompt                                    |
| `negativePrompt`   | str   | Negative prompt                                    |
| `cfgScale`         | float | CFG scale value                                    |
| `steps`            | int   | Number of sampling steps                           |
| `sampler`          | str   | Sampler name                                       |
| `seed`             | int   | Random seed                                        |
| `Size`             | str   | Image dimensions (e.g., "832x1216")                |
| `nsfw`             | bool  | NSFW flag                                          |
| `width`            | int   | Width in pixels                                    |
| `height`           | int   | Height in pixels                                   |
| `quantity`         | int   | Number of images generated (for batches)           |
| `clipSkip`         | int   | CLIP skip setting                                  |
| `workflow`         | str   | Workflow type (txt2img, img2img)                   |
| `civitaiResources` | list  | List of resource references (modelVersionId, type) |

### Top-Level Fields

| Field        | Type | Description                          |
|--------------|------|--------------------------------------|
| `type`       | str  | Resource type ("image")              |
| `onSite`     | bool | Whether image is still on site       |
| `process`    | str  | Process type (txt2img, img2img)      |
| `draft`      | bool | Draft status                         |
| `tools`      | list | Tools used (if any)                  |
| `techniques` | list | Techniques applied (if any)          |
| `canRemix`   | bool | Whether image can be remixed         |
| `remixOfId`  | int  | Original image ID if this is a remix |

---

## Resources Detail

Each item in the `resources` array represents a model, LoRA, embedding, or other resource used in generation.

### Resource Object Fields

| Field            | Type  | Example | Description |
|------------------|-------|-------------------|---------------------------------------------------|
| `imageId`        | int   | `77468734`        | ID of the image this resource belongs to          |
| `modelVersionId` | int   | `1199750`         | Version ID of the model                           |
| `modelId`        | int   | `439889`          | Base model ID                                     |
| `modelName`      | str   | `Prefect Pony XL` | Human-readable model name                         |
| `modelType`      | str   | `Checkpoint`      | Type of resource                                  |
| `versionId`      | int   | `1199750`         | Version ID (duplicate of modelVersionId)          |
| `versionName`    | str   | `v5.0`            | Human-readable version name                       |
| `strength`       | float | `0.8`             | Weight/strength for LoRAs, `null` for checkpoints |
| `baseModel`      | str   | `Pony`            | Base model this resource is compatible with       |

### Resource Types

| Type               | Description                     | Example             |
|--------------------|---------------------------------|---------------------|
| `Checkpoint`       | Primary checkpoint model        | `Prefect Pony XL`   |
| `LORA`             | Low-Rank Adaptation             | `Detail Tweaker XL` |
| `TextualInversion` | Text embedding                  | `negativeXL`        |
| `embed`            | Embedding (alternative type)    | `EasyNegative`      |
| `LoCon`            | LoRA for convolutional networks | Various             |
| `ControlNet`       | ControlNet model                | Various             |
| `IPAdapter`        | IP-Adapter model                | Various             |

---

## Tag Object Fields

Each tag in the `tags` array has these fields:

| Field   | Type | Description                        |
|---------|------|------------------------------------|
| `id`    | int  | Tag ID                             |
| `name`  | str  | Tag name (e.g., "1girl", "solo")   |
| `type`  | str  | Tag type (Tag, Mod, User, etc.)    |
| `count` | int  | Number of uses (for popular tags)  |

---

## Field Access Examples

### Accessing Raw Metadata

```python
from civitai import CivitaiPrivateScraper

scraper = CivitaiPrivateScraper(auto_authenticate=True)
data = scraper.scrape(11035255)

for item in data:
    raw = item['raw_meta_json']
    
    # Access base model
    base_model = raw['meta'].get('baseModel')
    print(f"Base model: {base_model}")
    
    # Access dimensions
    width = raw['meta'].get('width')
    height = raw['meta'].get('height')
    print(f"Size: {width}x{height}")
    
    # Access NSFW status
    nsfw = raw['meta'].get('nsfw')
    print(f"NSFW: {nsfw}")
```

### Filtering by Base Model

```python
from civitai import CivitaiPrivateScraper

scraper = CivitaiPrivateScraper(auto_authenticate=True)
data = scraper.scrape(11035255)

# Filter by base model type
pony_images = [
    item for item in data
    if item['raw_meta_json']['meta'].get('baseModel') == 'Pony'
]

print(f"Found {len(pony_images)} Pony-based images")
```

### Filtering by Model Version ID

```python
from civitai import CivitaiPrivateScraper

scraper = CivitaiPrivateScraper(auto_authenticate=True)
data = scraper.scrape(11035255)

# Filter by specific model version ID
target_version_id = 1199750  # Prefect Pony XL v5.0

filtered_images = [
    item for item in data
    if any(
        res.get('modelVersionId') == target_version_id
        for res in item['raw_meta_json'].get('resources', [])
    )
]

print(f"Found {len(filtered_images)} images using version ID {target_version_id}")
```

### Extracting All LoRA Combinations

```python
from civitai import CivitaiPrivateScraper
from collections import Counter

scraper = CivitaiPrivateScraper(auto_authenticate=True)
data = scraper.scrape(11035255)

# Count LoRA usage
lora_counter = Counter()

for item in data:
    for lora in item.get('loras', []):
        lora_counter[lora['name']] += 1

# Print most used LoRAs
print("Top 10 LoRAs in collection:")
for name, count in lora_counter.most_common(10):
    print(f"  {name}: {count} images")
```

### Extracting Common Prompt Patterns

```python
from civitai import CivitaiPrivateScraper
from collections import Counter
import re

scraper = CivitaiPrivateScraper(auto_authenticate=True)
data = scraper.scrape(11035255)

# Extract first 3 tags from each prompt (common pattern)
first_tags = []

for item in data:
    prompt = item.get('prompt', '')
    # Get first 3 comma-separated tags
    tags = [t.strip() for t in prompt.split(',')[:3]]
    first_tags.extend(tags)

# Count common first tags
tag_counter = Counter(first_tags)

print("Most common prompt starters:")
for tag, count in tag_counter.most_common(20):
    print(f"  {tag}: {count} images")
```

---

## Useful Metadata Combinations

### Identifying Similar Generations

Two images with identical parameters are likely variations of the same generation:

```python
def are_similar(item1, item2):
    """Check if two images have identical generation parameters"""
    return (
        item1['sampler'] == item2['sampler'] and
        item1['steps'] == item2['steps'] and
        item1['cfg_scale'] == item2['cfg_scale'] and
        item1['model'] == item2['model'] and
        item1['model_version'] == item2['model_version'] and
        len(item1['loras']) == len(item2['loras'])
    )
```

### Finding High-Quality Generations

Assume high quality = high steps, high CFG, and no negative prompt:

```python
def get_quality_score(item):
    """Calculate a quality score based on parameters"""
    score = 0
    
    # Steps: 20-50 is optimal
    steps = item.get('steps', 0)
    if 20 <= steps <= 50:
        score += 10
    elif 50 < steps <= 80:
        score += 5
    
    # CFG: 5-9 is typical
    cfg = item.get('cfg_scale', 0)
    if 5 <= cfg <= 9:
        score += 10
    elif 3 <= cfg < 5 or 9 < cfg <= 12:
        score += 5
    
    # Negative prompt: shorter is often better
    neg_len = len(item.get('negative_prompt', ''))
    if neg_len < 100:
        score += 10
    elif neg_len < 300:
        score += 5
    
    return score

# Sort by quality
sorted_data = sorted(data, key=get_quality_score, reverse=True)
```

### Finding Unique Model Combinations

```python
from collections import defaultdict

model_combos = defaultdict(list)

for item in data:
    # Create key from model + LoRAs
    loras = sorted([l['name'] for l in item['loras']])
    key = (item['model'], tuple(loras))
    model_combos[key].append(item['image_id'])

print("Unique model+LoRA combinations:")
for (model, loras), image_ids in model_combos.items():
    lora_str = ", ".join(loras) if loras else "None"
    print(f"  {model} + [{lora_str}]: {len(image_ids)} images")
```

---

## API Data Flow

```
┌─────────────────────────────────────────────────────────────┐
│                     image.getInfinite                       │
│  (Fetches collection items with basic metadata)             │
│  Returns: id, url, name, username, tags, mimeType           │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                  image.getGenerationData                    │
│  (Fetches detailed generation metadata for each image)      │
│  Returns: meta, resources, tools, techniques, etc.          │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                     Scraper._merge_data()                   │
│  (Combines both responses into unified structure)           │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                      Final Output                           │
│  {image_id, image_url, author, model, model_version, ...}   │
└─────────────────────────────────────────────────────────────┘
```

---

## Common Queries

### Get all images using a specific model
```python
images = [item for item in data if item['model'] == 'Pony Diffusion V6 XL']
```

### Get all images with LoRAs
```python
images_with_loras = [item for item in data if item['loras']]
```

### Get all NSFW images
```python
nsfw_images = [
    item for item in data
    if item['raw_meta_json']['meta'].get('nsfw')
]
```

### Get images by aspect ratio
```python
portrait = [
    item for item in data
    if item['raw_meta_json']['meta'].get('height', 0) > 
       item['raw_meta_json']['meta'].get('width', 0)
]

landscape = [
    item for item in data
    if item['raw_meta_json']['meta'].get('width', 0) > 
       item['raw_meta_json']['meta'].get('height', 0)
]
```

---

## Additional Resources

For the latest Civitai API changes, check:
- Civitai website developer tools (Network tab)
- Community discussions on Civitai Discord
- tRPC API documentation (if available)

---

## Version History

| Version | Date | Changes                                     |
|---------|------|---------------------------------------------|
|   1.0   | 2025 | Initial documentation with all known fields |
