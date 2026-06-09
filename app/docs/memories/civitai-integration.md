# CivitAI Integration

## Design Decisions

### Enrichment fails open
CivitAI enrichment should fail open (warn and continue) so uploads/scans are not blocked by API errors or missing data.

### Defensive null handling
Handle partial/null API payloads defensively — always use `None` checks before nested `.get()` calls on CivitAI API responses.

### Auth uses CDP-connected real Chrome
CivitAI authentication launches the system Chrome binary via subprocess with `--remote-debugging-port` and connects Playwright via `connect_over_cdp()`. This produces zero automation markers, allowing Google OAuth to succeed.

The Playwright-managed launch is kept only as a fallback when no Chrome binary is found. Chrome process is tracked and terminated via `os.killpg(SIGTERM)` in cleanup.

**Do not revert to Playwright-managed launch with stealth flags as the primary path** — Google detects and blocks it.

### Modules location
CivitAI modules live under `app/src/atelierai/civitai`.

## Key Files
- `app/src/atelierai/civitai/civitai_auth.py` — `_launch_chrome_cdp()`, `_launch_context()`, `_terminate_chrome()`
- `app/backend/services/civitai_service.py` — CivitAI API client
- `app/backend/civitai_enrichment.py` — enrichment pipeline
- `app/backend/routers/civitai/` — CivitAI-related API endpoints

## Gotchas
- Chrome CDP port must be available; if already in use, auth fails
- CivitAI API rate limits apply — batch operations should include delays
- Sync Lab collection listing (`/api/sync-lab/collections`) is cache-first (2-minute max age) to keep troubleshooting responsive; use `?force_refresh=true` to force a live CivitAI pull.

### Collection ID mapping (CivitAI → local DB)
`_ensure_image_in_collection()` resolves CivitAI collection IDs to local `collections.id` automatically. The `image_collections.collection_id` FK references `collections.id` (local PK), but callers throughout the codebase may pass either the CivitAI ID or the local ID. The resolution logic handles both transparently. Do NOT assume callers pass the local ID — always use the resolution function.

**Critical:** If no local `CollectionModel` exists for the given CivitAI ID, `_ensure_image_in_collection` now logs a warning and returns without creating a membership (instead of silently inserting an orphaned row — SQLite does not enforce FK constraints by default). Callers must ensure a local collection exists first (e.g. via `_get_or_create_collection`). The Sync Lab ingest worker now does this at the top of its loop.

- Sync Lab ingest now ensures a local `CollectionModel` exists **before** processing images: the worker calls `_get_or_create_collection()` with `source="civitai"` and the collection name from the `SyncSession` record.
- Sync Lab ingest resolves existing records by CivitAI source URL/ID before hash-collision duplicate logic; duplicate asset records are now reserved for distinct-source hash collisions.
- Sync Lab ingest now auto-refreshes collection sync metadata (`civitai_head_item_count`, `civitai_last_synced_at`; full snapshot on all-success runs). Manual refresh is available via `POST /api/sync-lab/collection-status/{collection_id}/refresh`.
- Sync Lab Step 4 (`analyze-local`) auto-finalizes sync metadata when `new=0` and there are no tombstoned/placeholders; retry runs can opt out via `is_retry_run=true`.
- Sync Lab Steps 5–7 support stage-level subset execution with candidate selection + optional per-stage `limit`; empty selections still run as no-op completions so sessions can finish through Step 7.
- Stage 6 download now retries alternate image URLs on 404 (raw page-render URL and `image-b2 ... /original` UUID endpoint) because some CivitAI image pages remain visible while a direct CDN filename URL returns `File with such name does not exist`.

### Broken `original=true` CDN route (May 2025)
Some CivitAI images have a UUID for which the `original=true` CDN route returns HTTP 404 (`"File with such name does not exist"`), while the image is still perfectly visible on civitai.com via width-transformed routes. This appears to be a CivitAI CDN storage issue where the original file wasn't properly stored but derived transforms were generated.

**Known working patterns when `original=true` fails:**
- `https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA/{uuid}/width={N}/{uuid}.jpeg` — works for widths 450–2048
- `https://image-b2.civitai.com/file/civitai-media-cache/{uuid}/800x%3Cauto%3E_du` — the `_du` suffix variant works; without it returns 404

**Fix:** `_build_civitai_image_candidate_urls()` in `app/backend/main.py` now appends UUID+width fallback candidates (2048, native width, native height, 1600, 1536, 1200, 1024, 800, 768, 450 — deduplicated) when `civitai_uuid` is available. The `image-b2` transform pattern is not yet implemented as a fallback tier (the width-based fallbacks on the primary CDN are sufficient for current cases).

**CDN URL tracking:** The `civitai_cdn_url` column on `ImageModel` stores the actual CDN URL used to download each image (e.g. `https://image.civitai.com/xG1nkqKTMzGDvpLrqFT7WA/{uuid}/width=1200/{uuid}.jpeg`). This differs from `source_url` which stores the CivitAI page URL (e.g. `https://civitai.com/images/101374502`). Set during `_ingest_prepared_civitai_import()` from `prepared.effective_image_url`. Column is added via `_ensure_civitai_cdn_url_column()` migration in `db_migrations.py`. Only populated for new downloads — existing images retain `NULL` until re-downloaded.
### Unpublished Post Discovery (March 2026)

**Problem:** `post.getInfinite` with `collectionId` does NOT return unpublished posts (`publishedAt: null`). Post-type collections containing only draft/unpublished posts appear empty via the standard collection query.

**Discovery method:** Inspected browser network requests on `https://civitai.red/user/<username>/posts?section=draft` while authenticated. Found that the same `post.getInfinite` endpoint accepts draft-specific parameters.

**Draft endpoint parameters:**
- `section: "draft"`, `draftOnly: true`, `pending: true`
- `username: <owner_username>` (required)
- `browsingLevel: 31` (captures all ratings — 28 would miss some)

**Three-tier fallback pattern** (implemented in both import pipeline and Sync Lab):
1. **Tier 1** — `fetch_collection_posts(collection_id)`: Standard `post.getInfinite` with `collectionId`. Works for published posts.
2. **Tier 2a** — `post.getInfinite` with `collectionId` + `section=draft, draftOnly=true, pending=true, username`: Returns unpublished posts **scoped to the collection**. Verified working 2026-05-15.
3. **Tier 2b** — `fetch_user_draft_posts(username)` without `collectionId`: Broader fallback returning all user drafts (not collection-scoped). Catches edge cases where 2a misses.
4. **Manual override** — Explicit `post_ids` list passed through sync request schema for known post IDs.

**Key files:**
- `app/src/atelierai/civitai/civitai_api.py` — `fetch_user_draft_posts()` method
- `app/backend/main.py` — `_run_civitai_post_collection_import_pipeline()` fallback logic
- `app/backend/routers/collections.py` — `_fetch_collection_posts_with_draft_fallback()` module-level helper used by Sync Lab Step 3 (`_fetch_post_collection_items`)
- `app/docs/api/CIVITAI_API_REFERENCE.md` — Full API documentation for `post.getInfinite`

**Gotcha:** The Sync Lab Step 3 (`sync_lab_fetch_collection_items` in `collections.py`) has its **own** `_fetch_post_collection_items()` inner function that is separate from the import pipeline in `main.py`. Both now use the shared `_fetch_collection_posts_with_draft_fallback()` helper for the three-tier draft fallback.

**Gotcha:** `browsingLevel` must be 31 (not lower) to capture all possible ratings. Lower values are what the browser captures when the user has filtered ratings in the UI.