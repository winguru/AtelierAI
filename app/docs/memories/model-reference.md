# Model Reference & ComfyUI Integration

## Design Decisions

### Resource type subtypes are mapped to parent types
ComfyUI's LoRA Manager API returns subtypes like `diffusion_model` and `dora`. These are mapped to parent types via `ModelReferenceService.parent_resource_type()`:
- `diffusion_model` → `checkpoint`
- `dora` → `lora`

This mapping is used in the model maintenance endpoints so that `diffusion_model` entries appear under "Checkpoints" and `dora` entries appear under "LoRAs".

### Pagination: always send page_size
The ComfyUI LoRA Manager API defaults `page_size` to 20 if not specified. The `_fetch_paginated_catalog_payload()` method always sends `page_size=100` on the first request and does not shrink `total_pages` with `min()`.

### Deduplication
Same-type duplicates exist in the local catalog (e.g., same hash under different paths). These are not removed — the count reflects actual files on disk.

## Key Files
- `app/backend/services/model_reference_service.py` — `parent_resource_type()`, `_fetch_paginated_catalog_payload()`, `_RESOURCE_TYPE_PARENTS`
- `app/backend/routers/civitai/models.py` — model maintenance endpoints, `_normalize_model_type()`

## Gotchas
- The LoRA Manager API is at `http://192.168.50.10:8188`
- `_normalize_model_type()` maps user input to "Checkpoint" or "LORA" — other values raise HTTPException
- The model maintenance table shows `row.type` from CivitAI API data, not the local catalog resource_type
