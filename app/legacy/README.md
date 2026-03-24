# Legacy CivitAI Modules

This folder contains archived CivitAI integrations kept for reference and debugging.
These files are not part of the current production import path, but may still be useful
for historical behavior comparisons.

## Files

### `civitai.py`
- Purpose: Older scraper-oriented integration wrapper around CivitAI API calls.
- Notes: Earlier high-level implementation before later refactors.

### `civitai_refactored.py`
- Purpose: Refactored legacy variant of `civitai.py` with cleaner structure.
- Notes: Transitional step between older scraper logic and current `src/atelierai/civitai` modules.

### `civitai_paginated.py`
- Purpose: Legacy focused implementation for paginated `image.getInfinite` collection fetching.
- Notes: Specialized around pagination behavior and older payload patterns.

### `civitai_trpc.py`
- Purpose: Base tRPC client variant (`v1.1` marker in file).
- Notable differences:
  - Simpler request/response helper layout.
  - Supports browsing presets and one-page/limited `image.getInfinite` usage.
  - Earlier behavior compared to `v2`/`v3` pagination refactors.

### `civitai_trpc_v2.py`
- Purpose: Structured refactor of the base tRPC client (`v1.2` marker).
- Notable differences:
  - Introduces more helper decomposition for request/pagination flow.
  - Adds explicit multi-page accumulation helpers.
  - Transitional version before `v3` cleanup/debug enhancements.

### `civitai_trpc_v3.py`
- Purpose: Newest archived tRPC variant (`v1.3` marker).
- Notable differences:
  - Adds `very_verbose` mode and richer pagination diagnostics.
  - More robust pagination helper flow and limit modes.
  - Supersedes `v2` in capability among legacy tRPC variants.

### `civitai_trpc_pagination_fix.py`
- Purpose: Patch/snippet-style pagination method extraction.
- Notable differences:
  - Not a full module/class implementation by itself.
  - Captures an intermediate `get_infinite_images` fix iteration.

## Guidance

- Prefer active modules under `src/atelierai/civitai/` for production behavior.
- Use these files for:
  - behavior regression checks,
  - endpoint payload history,
  - debugging older pagination outcomes.
