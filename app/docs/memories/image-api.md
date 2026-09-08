# Image API & Data Pipeline

## Design Decisions

### ImageData DTO does not include all DB columns
`ImageData.from_db_record()` does NOT include `user_tags`, `user_nsfw_rating`, `user_nsfw_safety_class`, etc. These are injected separately in `_load_display_image_items()`. When adding new DB columns that need to appear in the image list API, both `from_db_record()` (for the DTO) and `_load_display_image_items()` (for the merge) may need updates.

### user_tags injection
The `ImageModel.user_tags` JSON column is NOT in the ImageData DTO. It is injected in `_load_display_image_items` only when sidecar JSON does not provide it. Any code that builds image display items must explicitly handle `user_tags`.

### Tag counting uses DB observations, not json_metadata
Gallery tag counting queries `authority_terms` joined with `image_concept_observations` and `tag_authorities`. The old JSON-parsing functions are deprecated. Tag filtering still uses `Concept`/`ConceptAlias` + `ImageConceptObservation`. User tags not yet in `authority_terms` are supplemented by scanning `ImageModel.user_tags`.

### Frontend tag data flows through two pipelines
- **Gallery/All scope**: Backend endpoints (`/taxonomy/tree/state`, `/taxonomy/tree/tags/{source}`)
- **Selected scope**: Selected image's `user_tags` field passed via `window.postMessage` from gallery iframe (`main.js`) to tree iframe (`tree.js`)

### Gallery search and tag filters honor user negative overrides (2026-06)
Negative overrides are additive user-authority observations (`is_present=False`); the upstream civitai/danbooru observation stays `is_present=True`. Filtering by `is_present` alone is therefore NOT enough — the image also matches via its positive upstream observation. Both must be applied:

1. Observation queries require `is_present.is_(True)` (concept and authority-term branches in `ImageQueryService._search_image_ids_phased` / `_tag_branch_search` and `_image_ids_for_tag_name`).
2. Subtract user-negative overrides: `ImageQueryService._user_negative_override_map(session)` returns `{normalized_name -> set(image_ids)}` from user-authority terms joined to `is_present=False` observations; `_image_ids_negated_by_overrides(session, matched_tag_names)` unions IDs whose override name exactly matches a matched tag name (an override on "furry" does NOT suppress "furry ears" matches).

In search (`_search_image_ids_phased`) the subtraction is tag-branch-only (`matched -= negated & tag_branch_matched`), so Phase-2 column matches (file name, URL, …) keep an image discoverable even when its tag match is overridden. In `filter_image_ids_by_tag_names`, the subtraction applies to both include and exclude semantics — an overridden image "effectively lacks" the tag. Callers (`main.py` wrapper, `gallery_filter_service._apply_tag_filters`) are fixed transitively.

### Frontend effective-tag list: overrides suppress any source; single global sort (2026-06)
`buildEffectiveImageTagEntries` (main.js) builds the fullscreen-preview effective tag list. Two rules:
1. **A user negative override hides the tag regardless of which authority reported it.** The skip is NOT civitai-only — an image may carry the tag from danbooru/prompt observations only (no civitai obs at all), and the override means "this tag is wrong for this image" (matches backend `ImageQueryService` semantics).
2. **The combined list is sorted globally alphabetically** (`entries.sort` by name after the pass over `TAG_SOURCE_ORDER`). `TAG_SOURCE_ORDER` only decides attribution/dedupe priority when the same tag name arrives from multiple sources — the first source in `['civitai','danbooru','prompt','user']` containing the name wins.

Cache gotcha: `index.html` loads `main.js` with a `?v=` cache-buster — bump it when shipping frontend fixes, or browsers serve stale JS and verification falsely fails.

## Key Files
- `app/backend/image_data.py` — `ImageData.from_db_record()`
- `app/backend/main.py` — `_load_display_image_items()`, gallery tag counting functions
- `app/backend/services/image_query_service.py` — `filter_image_ids_by_tag_names()`
- `app/backend/services/image_service.py` — image CRUD operations
- `app/backend/routers/images.py` — image API endpoints
- `app/frontend/js/main.js` — gallery, selected-image tag pipeline
- `app/frontend/js/tree.js` — concept hierarchy tree UI

## Gotchas
- Adding a column to `ImageModel` does NOT make it appear in `/images/` responses — must update both DTO and merge logic
- Sidecar JSON can override DB values in the display pipeline
