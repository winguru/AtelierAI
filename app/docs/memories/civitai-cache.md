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
