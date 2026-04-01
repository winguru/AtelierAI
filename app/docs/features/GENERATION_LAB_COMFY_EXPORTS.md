# Generation Lab Comfy Exports

This guide explains how to export Comfy-compatible payloads from Generation Lab.

## Who this is for

- End-users who want to regenerate an image in ComfyUI.
- Power users who want to validate model availability against local LoRA Manager endpoints.

## Where to use it

- Open `Generation Lab` from the app UI.
- Load one target:
  - CivitAI image id
  - local file hash
- Optional: load both targets for side-by-side inspection.

## Export types

Generation Lab exposes four export buttons:

- `Copy JSON` / `Download JSON`
  - Exports AtelierAI inspection payloads (diagnostic envelope).
- `Copy Comfy Workspace JSON` / `Download Comfy Workspace JSON`
  - Exports raw Comfy workflow JSON (Comfy UI import shape).
- `Copy Comfy API Prompt JSON` / `Download Comfy API Prompt JSON`
  - Exports raw Comfy API prompt graph (for `/prompt` API).

## Compare tab behavior

Comfy exports are intentionally single-target raw files.

- In `Compare` tab (when both CivitAI + local are loaded), Comfy export buttons are disabled.
- Switch to `CivitAI` or `Local` tab to export raw Comfy payloads.

This avoids producing mixed payloads that are not valid Comfy imports.

## Catalog validation settings

Use `Comfy Export Validation` in Generation Lab to configure model catalog validation:

- `ComfyUI LoRA Manager BASE URL`
  - Example: `http://localhost:8188`
- Optional advanced overrides:
  - `Checkpoint list URL`
  - `LoRA list URL`
- `Include full catalog raw payload in exports`
  - Helpful for troubleshooting catalog shape mismatches.

## What "raw" means here

Raw Comfy export buttons output only the Comfy object itself:

- Workspace JSON: object with keys like `nodes`, `links`, `last_node_id`.
- API prompt JSON: object keyed by node id, each containing `class_type` and `inputs`.

No AtelierAI wrapper fields are included in these raw exports.

## Common troubleshooting

- Workspace export unavailable:
  - The selected image may not include a Comfy UI workflow graph.
  - Try API prompt export instead.
- API prompt export unavailable:
  - The image may lack embedded graph data and fallback synthesis may have failed.
  - Review inspection validation warnings/errors.
- Model references marked missing:
  - Confirm BASE URL or endpoint overrides are reachable.
  - Verify the desired checkpoint/LoRA exists in local catalog endpoints.

## Related docs

- Developer endpoint contract:
  - `app/docs/api/ATELIER_GENERATION_LAB_API.md`
- CivitAI reverse-engineered endpoint reference:
  - `app/docs/api/CIVITAI_API_REFERENCE.md`

## A1111 Bridge (Comfy-focused)

Generation Lab now includes an `A1111 Bridge` tab for discovery and comparison.

- Input:
  - local `file hash`
  - optional pasted `Comfy workflow JSON` (exported from Comfy UI)
- Output:
  - parsed A1111 metadata from `user_comment`/`parameters`
  - inferred Comfy candidate payload extracted from local generation data
  - optional comparison report against the provided Comfy workflow JSON:
    - scalar-path structural diff summary
    - parsed A1111 field alignment (`seed`, `steps`, `cfg_scale`, `size`, etc.)

Bridge export actions:

- `Download Comparison JSON`
  - Saves the current bridge analysis payload to your browser download location.
- `Save to AtelierAI Folder`
  - Persists the current bridge analysis payload on the server under:
    - `app/data/a1111_bridge_exports/`
  - Useful for building a multi-image comparative corpus over time.

Backend endpoint:

- `POST /generation-prototype/a1111-bridge/analyze`
- `POST /generation-prototype/a1111-bridge/save-analysis`

Request body:

```json
{
  "file_hash": "<local file hash>",
  "comfy_workflow_json": {"nodes": []},
  "include_generation_payload": false
}
```

Save-analysis request body:

```json
{
  "analysis_payload": {"ok": true, "comparison": {}},
  "file_name": "my-bridge-sample.json"
}
```

## One-Off Collection Script

Use the discovery script to batch collect local candidates with A1111-style metadata and call the bridge endpoint:

- Script: `app/scripts/collect_a1111_bridge_samples.py`

Example:

```bash
cd app
python3 scripts/collect_a1111_bridge_samples.py \
  --db-path image_db.sqlite \
  --api-base http://localhost:8000 \
  --workflow-dir data/comfy_exports \
  --limit 200
```

Workflow file naming convention for auto-attachment:

- `data/comfy_exports/<file_hash>.json`

The script writes JSONL output under `app/data/` by default for iterative analysis and diff review.
