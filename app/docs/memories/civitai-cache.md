# CivitAI API Response Cache — Design Memory

## Purpose
DB-backed cache for every CivitAI tRPC response so that repeated enrichment,
rescan, and offline/dev workflows avoid redundant API calls and preserve a
change history for each resource.

## Table
`civitai_api_cache` (see `app/backend/models.py::CivitaiApiCacheEntry`)
- Append-on-change: a new row is inserted only when `response_hash` changes.
- Identical re-fetches update `fetched_at` in place (no new row).
- `is_latest` + `prev_id` chain for history traversal.
- Table created automatically via `Base.metadata.create_all()` in `main.py` startup.

## Key Concepts
- **`request_key`**: human-readable `field=value&field=value` string derived
  from a per-endpoint field whitelist. Trivially queryable in SQL:
  `WHERE endpoint='image.get' AND request_key='id=12345'`.
- **`canonical_hash`**: SHA-256 over sorted-keys JSON of the response.
  `None` responses hash to `{}` so tombstones (404s) are stable.
- **`response_json`**: the *parsed* tRPC result (what `_make_request` returns),
  not the raw envelope.

## Write Path (Phase 2)
`CivitaiAPI._make_request()` calls `_record_to_db_cache()` on every success.
`_make_request()` also writes a tombstone on terminal HTTP errors (404 etc.)
when `exc.status_code is not None`. These writes are fire-and-forget:
they never raise and never affect the caller's transaction.

## Read Path (Phase 3)
`CivitaiAPI.get_cached_or_fetch(endpoint, payload, *, max_age, cache_only)`
checks the cache before making a live call. Convenience wrappers:
- `fetch_basic_info_cached(image_id, *, max_age, cache_only)` → `image.get`
- `fetch_generation_data_cached(image_id, *, max_age, cache_only)` → `image.getGenerationData`
- `fetch_image_tag_records_cached(image_id, *, max_age, cache_only)` → `tag.getVotableTags` (normalised)
- `fetch_model_detail_cached(model_id, *, max_age, cache_only)` → `model.getById`

`max_age=None` means "any cached row is acceptable" (stale-ok).
`max_age=timedelta(days=7)` means "only use cache if fetched within 7 days".
`cache_only=True` skips the live call entirely (returns None on miss).

## Excluded Endpoints
`signals.getToken` and `multi-search` are never stored — both are transient
and carry no historical value.

## Uncached call-site audit (2026-09-16)
Live validation recipe: call an endpoint twice, watch
`/api/civitai/auth/rate-limit-status` → `tpm_breakdown.endpoints[*].cached`
and the transport-log line count. Cached reads add ZERO transport lines.

Findings & fixes:
- `/api/civitai-search/image/{id}` used UNCACHED fetch_basic_info /
  fetch_generation_data / fetch_image_tag_records on every view — tags
  had 29k stored responses never read. Fixed to `*_cached` with 7-day
  TTL (bcc17b4): repeat view 4.05s → 0.105s, fully cache-served.
- `metrics session_cached` is a process-lifetime classvar — resets on
  every uvicorn --reload; a zero there does NOT mean the cache is idle.
- Cache hits still pay the global rate-limiter queue when a request
  mixes cached + live endpoints — 3-4s waits are queue latency, not
  CivitAI. Keep hot paths fully cache-first to avoid the queue entirely.

## HTTP-404 Tombstones + id Plausibility Guards (added 2026-09-18)

**Incident**: an invalid CivitAI image id (1) entered a stale Sync Lab step-6
plan and was submitted live — 404ing TWICE, 30s apart, because HTTP-level
404s from the transport never wrote a tombstone row (only payload-level
tRPC error envelopes in `_make_request` did). Cached callers had nothing to
hit, so every retry went live.

**Fixes**:
- `CivitaiAPI._make_raw_request` now tombstones HTTP 404s via
  `_record_to_db_cache(endpoint, payload, None, 404)` — dead ids are served
  from cache thereafter. 5xx stays un-tombstoned (transient).
- `_parse_sync_lab_image_ids` drops ids `< 1000`: real CivitAI image ids are
  well into the millions; tiny ints are UI/state artifacts (array indices,
counters). Applies to all sync-lab stage inputs (steps 5-7).
- Tests: `app/tests/test_civitai_404_tombstone.py` (8). Pipeline test
  fixtures now use ids ≥1000 (100001/100002) — tiny-id fixtures get silently
  dropped by the guard.

## Collection Page Cache — image.getInfinite pagination (added 2026-09-18)

**Problem**: step 3 (Fetch Collection Items) fetched 919 pages live, and a
page refresh re-fetched ALL 919 again — zero caching. Root cause:
`CivitaiPrivateScraper._make_collection_request` used
`api._make_raw_request` (never caches), not `api._make_request`/cached
helpers. DB had zero `collectionId=…` cache rows despite ~2k live requests
(the `postId=…` rows came from the post pipeline, which used the caching
path).

**Design** (in `atelierai.civitai.civitai`):
- Each page is keyed by its full request (collectionId + cursor + sort/
  period/browsingLevel — see `_KEY_FIELDS["image.getInfinite"]`), so every
  pagination page is a distinct cache row.
- Cache probe uses `get_cached_or_fetch(..., cache_only=True)` — a plain
call would itself live-fetch via `_make_request` and the raw fetch here
  would then fetch a SECOND time (double-fetch trap).
- Live pages are written through via `_record_to_db_cache` on BOTH shapes:
  flat-array (current CivitAI format) AND legacy dict responses (early
  version cached only the flat-array path).
- Freshness window: 15 min default (`CIVITAI_COLLECTION_PAGE_CACHE_TTL_MINUTES`),
  so new collection additions surface on a later fetch. `use_cache=False`
  (Sync Lab "Force refresh" checkbox, `?force_refresh=true`) bypasses reads
  but still writes through — a forced refresh also refreshes the cache.
- `_normalize_collection_page` coerces cached payloads back to page shape
  (top-level `items`/`nextCursor` or the legacy `result.data.json`
  wrapper); unrecognised shapes fall through to a live fetch.

**Tests**: `app/tests/test_civitai_collection_page_cache.py` — write-through
on first fetch (both pages keyed), second fetch = zero live requests,
force-refresh re-fetches live, no double-fetch on miss, page normalization
shapes, TTL default/env-override/garbage-fallback. NOTE: test mocks for
`_make_collection_request` must accept the `use_cache` kwarg
(`test_civitai_cancel_cooperative.py` needed updating).

## CDN Media Cache (read/write-through, added 2026-09-17)

**Problem**: CDN (image.civitai.com) downloads were NEVER cached — tRPC
metadata had the DB cache, but image bytes went to the CDN every time.
An asset preserved in Search Lab and later ingested in Sync Lab was
downloaded twice; re-ingests re-downloaded again. At 1-2s+ per download
(and 503-flag pressure), this was pure waste for identical bytes.

**Design**: the Search Lab preserve cache
(`image_resources/civitai_search_media/<image_id>/original.*` +
`media.json` sidecar, in `services/civitai_search_media.py`) is now the
shared CDN media cache:

- **Read-through**: `_download_civitai_image_with_validation` checks
  `get_cached_media_path(image_id)` BEFORE building candidate URLs. A hit
  (with a detectable media signature, and category-compatible with the
  declared type — video targets never accept a cached static image)
  returns a temp-file COPY (`selected_url="cache://…"`, `from_cache=True`
  in sync-lab results) — copy because ingest MOVES its temp file into the
  library and must not consume the cache's stored bytes.
- **Write-through**: after a verified CDN download (post PNG-repack,
  category validation), `record_ingested_media()` stores a copy under the
  per-image cache dir. Fail-open: a cache write error never fails the
  download/ingest. Idempotent: an existing record always wins.
- Benefits compound: Search Lab preserve → ingest, ingest → re-view,
  duplicate-collection ingest, and post-crash re-runs all hit the cache —
  zero CDN requests, zero rate-limit pressure.
- UI: sync-lab summary shows ", N from local cache"; item chips show ⚡.

**Tests**: `app/tests/test_civitai_cdn_media_cache.py` (7) — read-through
skips CDN, temp is a copy, video/static mismatch guard, write-through
records, second-call cache hit, write failure non-fatal, idempotency.

**Gotcha (dual-module trap, again)**: `backend/main.py` imports the service
as `services.civitai_search_media`; tests that isolate the cache root MUST
patch that module instance (`from services import civitai_search_media`),
not `backend.services.civitai_search_media` — with both app/ and
app/backend on PYTHONPATH these are two distinct module objects. The
un-isolated instance writes to the REAL cache root and cross-contaminates
suites (observed: PNG-repair suite's write-through made the media-cache
suite read a stale hit). `test_civitai_png_download_repair.py` carries the
same isolation fixture for this reason. Both `_cache_root` AND the module's
`app_config` reference must be redirected (PreservedSearchMedia.absolute_path
derives from `app_config.IMAGE_RESOURCES_PATH`).

## Call-Site Migration (Phase 4)
Scripts using `_make_raw_request + _extract_trpc_result` have been migrated
to `api.get_cached_or_fetch(endpoint, payload)`.  The tRPC envelope extraction
helper `_extract_trpc_result` was removed from those scripts as dead code.

`civitai_enrichment.fetch_civitai_image_data` gained a `max_age` keyword-only
argument (default `None` = always live, preserving prior behaviour).  Callers
that want cache-first enrichment pass e.g. `max_age=timedelta(days=7)`.

## Disk Archive
`_archive_metadata_response()` still runs in parallel for the 3 endpoints
it previously covered. Deprecation is deferred to Phase 7.

## Per-Endpoint Key Fields
```
image.get / image.getGenerationData   : id
tag.getVotableTags                    : id, type
tag.getById / model.getById / modelVersion.getById / post.get : id
model.getAll                          : username, cursor, sort, period, limit
image.getInfinite                     : collectionId, postId, modelId, modelVersionId,
                                        username, sort, period, browsingLevel, types, cursor
post.getInfinite                      : cursor, collectionId
collection.getAllUser                  : userId
```
Unknown endpoints fall back to sorted full-payload serialisation.
