# Concept-Association Studio

Design decisions, constraints, and gotchas for the concept-association studio and visual lookup system.

## Overview

The concept-association studio enables iterative image curation by focusing on **concepts** (deeper ideas with multiple surface forms) rather than flat tags. It supports ground-zero images (no existing tags) via CLIP visual similarity.

## Key Architecture Decisions

### Bootstrap = Text + Exemplar
Text-seeded prototypes break the chicken-and-egg problem where images with no tags have no observations. CLIP maps text and images to the same 512-D space, so text-seeded concept prototypes can match ground-zero images visually.

### Embedding Storage = DB Column
CLIP embeddings stored as BLOB in `ImageModel.clip_embedding` (512 float32 packed via `struct.pack`). Concept prototypes stored in `Concept.prototype_embedding` using the same scheme.

### VISUAL_CLIP Observation Source
New `ObservationSource` enum member (value 5) tracks concepts derived from kNN visual similarity lookups. IntEnum stored as Integer — adding members is pure Python, no DB migration needed.

## Data Flow

1. **Seed**: `seed_style_taxonomy.py` creates concepts + builds text-seeded prototypes
2. **Embed**: `backfill_clip_embeddings.py` computes CLIP vectors for all images
3. **Lookup**: `GET /api/visual-lookup/{file_hash}` finds kNN neighbours + aggregated concepts
4. **Hydrate**: `POST /api/visual-lookup/{file_hash}/hydrate` persists VISUAL_CLIP observations
5. **Score**: `POST /api/visual-lookup/{file_hash}/score-concept` scores image vs concept prototype

## Schema Additions

### ImageModel columns (added via migration)
- `clip_embedding` (BLOB) — 512 float32 L2-normalized CLIP vector
- `clip_embedding_model` (VARCHAR) — model identifier (e.g. `ViT-B-32::openai`)
- `clip_embedding_at` (DATETIME) — timestamp of embedding computation

### ObservationSource enum
- IMPORT=1, RESCAN=2, USER=3, ANALYSIS=4, VISUAL_CLIP=5
- **Never renumber existing values** — they are persisted as integers.

## Constraints

- CLIP provider may be unavailable (graceful degradation — return `has_embedding=False`).
- kNN loads all embeddings into memory — fine for thousands of images, would need ANN index for larger scale.
- Minimum similarity threshold `_MIN_SIMILARITY = 0.75` filters noise neighbours.
- Default `_DEFAULT_K = 20` neighbours returned.

## Files

| File | Purpose |
|---|---|
| `app/backend/routers/visual_lookup.py` | kNN lookup, hydrate, score-concept endpoints |
| `app/backend/services/concept_prototype_service.py` | `build_prototype_from_text()`, `backfill_image_embedding()` |
| `app/scripts/seed_style_taxonomy.py` | Seed art-style spectrum + text prototypes |
| `app/scripts/backfill_clip_embeddings.py` | Batch backfill image embeddings |
| `app/backend/services/db_migrations.py` | `_ensure_clip_embedding_columns()` |
| `app/frontend/concept-studio.html` | Three-pane studio layout (exemplar/association/confirmed) |
| `app/frontend/css/concept-studio.css` | Dark-theme styling for concept-studio panes |
| `app/frontend/js/concept-studio.js` | Interactive concept-association loop (~500 lines) |

## Phase 2: Frontend Studio (Complete)

Three-pane layout for iterative concept curation:
- **Left pane** — exemplar image viewer with metadata
- **Center pane** — concept association (visual neighbours, AI suggestions, concept search/create)
- **Right pane** — confirmed concepts list

### Frontend → Backend API Contracts
- Image queue: `GET /api/images/?limit=50&sort_by=last_added&group_variants=false`
- Visual lookup: `GET /api/visual-lookup/{file_hash}?k=20` → `{neighbours, aggregated_concepts}`
- Confirm concept: `POST /api/visual-lookup/{file_hash}/hydrate` body `{concept_id, is_curated:true}`
- Score concept: `POST /api/visual-lookup/{file_hash}/score-concept` body `{concept_id}`
- Create concept: `POST /api/taxonomy/concepts` body `{canonical_name, description}`
- Build prototype: `POST /api/taxonomy/concepts/{id}/build-prototype-from-text` body `{prompts}`
- Similar terms search: `GET /api/taxonomy/concept-search/similar-terms?q=...&limit=5`
  → **Gotcha**: response uses `.results[]` / `.normalized_name` (NOT `.terms` / `.canonical_name`)

### Navigation
Studio accessible at `/concept-studio` (explicit route in health.py) and linked from
`index.html`, `prototype-lab.html`, `concept-search-lab.html`, and `review-lab.html`.

## Phase 2.1: Infinite Image Queue (Complete)

The original studio hard-capped the exemplar queue at 50 images (`limit=50` with no
paging). Replaced with lazy offset pagination so curators can walk the entire library.

### How it works
- `state` tracks `nextOffset`, `hasMore`, `filteredCount`, `searchTerm`, `isLoadingPage`
- `fetchImagesPage({ searchTerm, offset })` hits `GET /api/images/?limit=50&skip=N&sort_by=last_added&group_variants=false`
  and reads `X-Filtered-Count` header for the total
- `nextImage()` auto-fetches the next page when at the end of the loaded queue
- `loadExemplar()` fire-and-forget prefetches the next page when within `PAGE_SIZE/2`
  of the end (silent — does not touch the status bar)
- `updateQueuePos()` shows position within total catalog (e.g. `53 / 3133`)
- Nav buttons disabled (`setNavDisabled`) during page fetch to prevent races
- De-dup by `file_hash` guards against offset overlaps

### Why offset (skip) not cursor
`/api/images` cursor pagination is gated on `group_variants=true` (`use_overfetch = group_variants
and limit is not None`, main.py:12094/12461). The concept studio uses `group_variants=false`, so
`X-Next-Cursor` is never emitted. Offset pagination always works and always emits
`X-Filtered-Count`. `hasMore` is derived from `items.length >= PAGE_SIZE`.

### Future enhancement (not started)
Make the main gallery's search filters (generation_software, source_site, nsfw_rating,
include_tag, etc.) into a reusable module so any page that searches for images can include
the same filter UI. Currently the filter logic lives inline in `app/frontend/js/main.js`
(`buildImagesRequestUrl`, `_buildFilterBody`) and is not modular.
