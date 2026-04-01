# AtelierAI Generation Lab API

This document describes AtelierAI backend endpoints used by Generation Lab and integrations for generation inspection and Comfy exports.

## Scope

These endpoints are internal AtelierAI backend routes (not CivitAI public APIs).

## Query parameters (shared)

Many Comfy endpoints accept the same optional query parameters:

- `catalog_url`: ComfyUI LoRA Manager base URL.
- `checkpoints_url`: explicit checkpoint list endpoint override.
- `loras_url`: explicit LoRA list endpoint override.
- `include_full_catalog_raw`: when `true`, includes un-compacted catalog raw payloads where supported.

## Inspection endpoints (wrapped payload)

### CivitAI target inspection

- `GET /generation-prototype/civitai/{image_id}`

Returns normalized inspection payload for a CivitAI image.

### Local target inspection

- `GET /images/{file_hash}/generation-prototype`

Returns normalized inspection payload for a local library item.

## Comfy wrapped export endpoints

### CivitAI wrapped Comfy export

- `GET /generation-prototype/civitai/{image_id}/comfy-workspace`

### Local wrapped Comfy export

- `GET /images/{file_hash}/generation-prototype/comfy-workspace`

Returns a diagnostic envelope including:

- `workspace_bundle.comfy_prompt_api`
- `workspace_bundle.comfy_workflow_ui`
- `model_validation`
- `validation`

Use these endpoints when you need both Comfy payloads plus validation context.

## Comfy raw endpoints (integration-friendly)

These endpoints return only the requested raw Comfy object.

### CivitAI raw workflow JSON

- `GET /generation-prototype/civitai/{image_id}/comfy-workflow-raw`

Returns Comfy UI workflow JSON object (for UI import).

### CivitAI raw API prompt JSON

- `GET /generation-prototype/civitai/{image_id}/comfy-prompt-raw`

Returns Comfy API prompt graph object (for `/prompt` submissions).

## Generation template endpoints (Phase 1 + Phase 2)

These endpoints allow importing a Comfy workspace JSON as a reusable template, then resolving it against generation metadata from either a local image or a CivitAI image.

### Import template from workspace JSON

- `POST /generation-templates/import-workspace`

Request body:

- `name`: template name (unique)
- `description`: optional text
- `workflow_json`: Comfy workspace JSON object (`nodes` array required)
- `mappings`: optional array of token->path mappings
- `default_tokens`: optional default token values

Mapping object fields:

- `token`: token key such as `prompt.positive`
- `target_path`: workspace property path (for example `nodes[id=6].widgets_values[0]`)
- `required`: when true, missing token value is an error
- `value_type`: `auto|string|integer|number|boolean|json`
- `default_value`: optional per-mapping fallback

### List templates

- `GET /generation-templates`

Query parameters:

- `include_workflow` (default `false`)
- `limit` (default `50`, max `200`)
- `offset` (default `0`)

### Get one template

- `GET /generation-templates/{template_id}`

Query parameters:

- `include_workflow` (default `true`)

### Update template

- `PUT /generation-templates/{template_id}`

Request body fields are optional, but at least one should be provided:

- `name`: updated unique name
- `description`: optional text or `null`
- `workflow_json`: updated Comfy workspace JSON object (`nodes` array required)
- `mappings`: updated full mapping array
- `default_tokens`: updated default token object

### Delete template

- `DELETE /generation-templates/{template_id}`

Deletes a saved template by id.

### Discover derived tokens for a source target

- `GET /generation-templates/token-preview`

Query parameters:

- `source_mode`: `local` or `civitai` (required)
- `file_hash`: required when `source_mode=local`
- `image_id`: required when `source_mode=civitai`
- `catalog_url`, `checkpoints_url`, `loras_url`, `include_full_catalog_raw`: optional catalog enrichment inputs

Returns token candidates generated from normalized payload analysis before any template mapping is applied.

Token preview includes LoRA strength token families for template mapping:

- `model.lora_model_strength`, `model.lora_model_strengths`
- `model.lora_clip_strength`, `model.lora_clip_strengths`
- Per-step/per-lora variants in `step_groups[*].tokens` such as `step.0.model.lora.0.model_strength` and `step.0.model.lora.0.clip_strength`.

Template Studio token picker also exposes indexed helper tokens for rapid mapping (default index range 0-7), including:

- `model.lora.<index>.name`
- `model.lora.<index>.path`
- `model.lora.<index>.model_strength`
- `model.lora.<index>.clip_strength`

Template Studio consumes this response with a deterministic node-type/widget-index helper to rank likely mapping targets (for example KSampler seed/steps/cfg and known loader node slots) so users can map standard tokens without manually scanning full workflow JSON.

### Resolve/export template

- `POST /generation-templates/{template_id}/resolve`

Request body:

- `source_mode`: `local` or `civitai`
- `file_hash`: required when `source_mode=local`
- `image_id`: required when `source_mode=civitai`
- `token_overrides`: optional explicit token overrides
- `include_generation_payload`: optional debug include

Optional query parameters for local model-path enrichment during resolution:

- `catalog_url`
- `checkpoints_url`
- `loras_url`
- `include_full_catalog_raw`

Resolution behavior:

- Applies explicit path mappings from `mappings`.
- Applies inline string placeholders in workflow JSON (for example `{{prompt.positive}}`).
- Returns `resolved_workflow_json` ready for Comfy import.
- Returns `validation` warnings/errors for unresolved or invalid mappings.

### Local raw workflow JSON

- `GET /images/{file_hash}/generation-prototype/comfy-workflow-raw`

Returns Comfy UI workflow JSON object (for UI import).

### Local raw API prompt JSON

- `GET /images/{file_hash}/generation-prototype/comfy-prompt-raw`

Returns Comfy API prompt graph object (for `/prompt` submissions).

## Status and error behavior

- `200`: requested raw payload is available and returned.
- `404`: local file hash not found (local routes).
- `422`: requested raw payload is unavailable for the target (for example, missing workflow graph).

422 responses include a detail message that may contain validation warnings/errors from the wrapped pipeline.

## Example requests

### Fetch raw workflow JSON (CivitAI)

```bash
curl "http://localhost:8000/generation-prototype/civitai/117165031/comfy-workflow-raw"
```

### Fetch raw API prompt JSON (local)

```bash
curl "http://localhost:8000/images/<file_hash>/generation-prototype/comfy-prompt-raw"
```

### Fetch wrapped Comfy export with catalog overrides

```bash
curl "http://localhost:8000/images/<file_hash>/generation-prototype/comfy-workspace?catalog_url=http://localhost:8188&include_full_catalog_raw=true"
```

### Import a workspace template

```bash
curl -X POST "http://localhost:8000/generation-templates/import-workspace" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "simple-txt2img",
    "description": "KSampler text-to-image template",
    "workflow_json": {"nodes": []},
    "mappings": [
      {"token": "prompt.positive", "target_path": "nodes[id=6].widgets_values[0]", "required": true, "value_type": "string"},
      {"token": "model.checkpoint_path", "target_path": "nodes[id=4].widgets_values[0]", "required": true, "value_type": "string"}
    ],
    "default_tokens": {}
  }'
```

### Resolve template for a local image

```bash
curl -X POST "http://localhost:8000/generation-templates/1/resolve" \
  -H "Content-Type: application/json" \
  -d '{
    "source_mode": "local",
    "file_hash": "<file_hash>",
    "token_overrides": {}
  }'
```

### Preview derived tokens for a local image

```bash
curl "http://localhost:8000/generation-templates/token-preview?source_mode=local&file_hash=<file_hash>"
```

## Integration guidance

Use wrapped endpoints when:

- You need validation summaries and reference matching diagnostics.
- You are building operator-facing debug tooling.

Use raw endpoints when:

- You need direct Comfy import payloads with no wrapper parsing.
- You are wiring automation that posts to Comfy `/prompt`.

## Related docs

- End-user export workflow:
  - `app/docs/features/GENERATION_LAB_COMFY_EXPORTS.md`
- CivitAI external endpoint reverse-engineering:
  - `app/docs/api/CIVITAI_API_REFERENCE.md`
