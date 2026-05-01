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
