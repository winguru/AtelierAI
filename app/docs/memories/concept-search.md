# Concept Search — Architecture & Key Facts

## Pipeline (3 stages in `concept_search_service.py`)

1. **`decompose_query()`** — Matches query text against concept surface forms
   (canonical names + aliases stored in `ConceptAlias`). Returns matched
   `concept_id`s and leftover `context_text`.

2. **`resolve_candidate_images()`** — Finds images linked to matched concepts
   via `ImageConceptObservation` (where `is_present=True`). Returns
   `ScoredCandidate` list with `file_hash`, `thumbnail_url`, etc.

3. **`visual_score_candidates()`** — Batch CLIP encode + score:
   - Loads prototype vectors from `Concept.prototype_vector` (BLOB).
   - **Encodes candidate images via local file paths** (not URLs).
   - Encodes `context_text` once.
   - Scores: `identity_score` (max cos vs prototypes),
     `context_score` (cos vs context text), `composite_score` (identity × context).

## Critical: Local Files vs URLs

All images in this lab are stored locally at:
```
{IMAGE_LIBRARY_PATH}/{file_hash}.{ext}   # ext: png|jpg|jpeg|webp
```

- `civitai_cdn_url` is **NULL** for all images in this lab.
- `source_url` / `thumbnail_url` contain CivitAI **page URLs** (e.g.
  `https://civitai.red/images/2955178`) — these are HTML pages, NOT direct
  image files. CLIP's `encode_image_urls` cannot decode them.

**Always use `encode_image_paths` with resolved local file paths.**
The helper `_resolve_local_image_path(file_hash)` checks all extensions
and returns the first match.

URL-based encoding (`encode_image_urls`) is kept as a fallback only for
candidates with real CDN URLs (`http://`/`https://` pointing to image files)
and no local file.

## Prototype Building

Prototypes are built by `concept_prototype_service.py` using
`encode_image_paths` — this is why prototypes build successfully even
when search scoring previously failed (it was using `encode_image_urls`).

## Server Startup

```bash
cd /workspace/app
export PYTHONPATH="/workspace/app:/workspace/app/src:/workspace/app/dev"
python -m uvicorn main:app --port 8001 --app-dir backend
```
Or simply: `./start.sh` (uses port 8000 by default).

## API Endpoint

```
POST /api/taxonomy/concept-search
{"query": "shion on a beach at sunset", "limit": 10}
```
