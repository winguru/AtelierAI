# Scan Missing CivitAI Tags

Scope: `app/backend/services/scan_missing_service.py` (+ SSE consumer in
`app/frontend/js/tag-maint.js`). 3-tier resolution for images that have a
`civitai_image_id` but no CivitAI tag observations.

## Archive Layout Note (2026-09-06 sharding migration)

Tag archives now live under `civitai_api_responses/tag.getVotableTags/{s1}/{s2}/`
(2-level hex sharding; see `civitai-integration.md`). `_build_tag_archive_index`
must glob BOTH the legacy flat layout AND the sharded layout until the flat
files are confirmed gone:
`archive_dir.glob(pattern)` + `archive_dir.glob(f"tag.getVotableTags/*/*/{pattern}")`.
After migration only the sharded glob matches, but the dual glob is kept as a
defensive legacy fallback.

## Design Decisions

### Empty archives are NOT tier resolutions
CivitAI's `tag.getVotableTags` returns `[]` with HTTP 200 for images that have
no votable tags — typically because the image was **deleted from CivitAI**
(verified: `image.get` → 404 NOT_FOUND for sampled empty-archive images).
An archived empty list is therefore a definitive "no tags available" answer,
not a fetch failure and not an importable source.

- Tier-2 categorization checks `tier2_index.get(cid)` for truthiness first;
  images whose archive exists but is empty go to a separate
  `tier2_empty_images` bucket.
- The same rule applies to Tier 3: a successful live fetch returning zero tags
  increments `no_tags_available`, not `tier3_resolved`.

### `no_tags_available` bucket
Counted in `stats`, surfaced in `_progress`, both `tier_complete` events, the
tier-2 `tier_start` event (`empty_archives`), and the final `complete` event.
Frontend shows it as a "No Tags" metric tile (`sm-metric-no-tags`) and appends
an explanatory note when > 0 ("likely deleted from Civitai").

## Tombstoning: `civitai_deleted` (404 detection)

When a no-tag image's CivitAI `image.get` lookup returns **404 NOT_FOUND**,
`_mark_image_deleted_from_civitai` records a tombstone: sets
`civitai_deleted_at` (UTC now) + `date_modified`, committed per-row.

- **Cached-404 short-circuit**: a 404 already recorded in the local truth
  store is permanent truth — skip the live call entirely.
- **Strict-raise**: live 404s raise; the per-image handler catches and records
  the tombstone (fail-open for the scan, fail-closed for the flag).
- **Apply-mode-only**: tombstones are written only when `dry_run=0`.
- **Transient errors never tombstone**: 429/5xx/network → `error_event`,
  image stays untouched (no false deletions).

### Gotcha: tombstones keep `image_status='placeholder'`
`_mark_image_deleted_from_civitai` does NOT change `image_status`. Tombstoned
rows remain `placeholder` and are invisible to the standard active-image base
filter (`image_status IS NULL OR 'active'`). Consequence: a naive
`status:civitai_deleted` unified filter returns **0 rows** because the base
query excludes the tombstones before the status term is applied.

**Fix (gallery_filter_service.py + gallery_query.py + main.py):**
- Helpers in `gallery_filter_service.py` (after `_STATUS_FILTERS`):
  `_INACTIVE_STATUS_VALUES = frozenset({"civitai_deleted"})`,
  `relaxed_active_image_filter()` (admits `'placeholder'` rows),
  `status_terms_include_inactive(parsed)`.
- Pattern at every unified query site — parse first, then choose base:

  ```python
  parsed = parse_gallery_filter(...)
  base_clause = (
      relaxed_active_image_filter()
      if status_terms_include_inactive(parsed)
      else _active_image_filter()
  )
  images_query = db.query(ImageModel).filter(base_clause)
  ```

- Call sites: `gallery_query.py` `_resolve_filter` (decision must run BEFORE
  the cache-check early return; result stored in `self._last_base_image_filter`,
  initialized in `__init__` to `active_image_filter()` so direct
  `_fetch_image_page` callers stay safe — base + backfill queries both use it)
  and `main.py` `_load_filtered_image_keys_unified`,
  `_load_display_image_items_unified`, `read_images_state`.
- **Excluded** status terms never widen the base (excluding a status from the
  active set must not pull placeholders in) — only included terms relax.
- `_STATUS_FILTERS["civitai_deleted"]` = `civitai_deleted_at IS NOT NULL`.

## Gotcha: "Rescan Library" purges placeholder rows

`POST /api/scan_library/` → `ImageCollection.scan()` step 3 calls
`_cleanup_orphaned_records()` (app/backend/image_collection.py), which deletes
image rows whose backing file is missing from disk. Tombstone rows are
**virtual placeholders** (`file_path = "placeholders/<xx>/<hash>.placeholder"`,
never written to disk by design) — so a rescan purged all 101 tombstones (plus
3 other placeholders) and the "CivitAI Deleted" gallery filter / tag-maint
scan went empty.

**Fix (image_collection.py `_cleanup_orphaned_records`):** inside the orphan
loop, skip rows whose `file_path` starts with `placeholders/` (count them in
`placeholder_rows_preserved`, print when > 0). Placeholder rows are records,
not files — orphan cleanup must never treat them as missing files.

**Restoration (app/scripts/restore_civitai_tombstones.py):** the `civitai_api_cache`
table (`endpoint='image.get' AND http_status=404`, `request_key='id=<cid>'`) is
the permanent local 404 truth store — re-derive tombstone rows from it when
they've been purged. The script excludes ids already present in
`images.civitai_image_id`, rebuilds each row exactly like
`_build_civitai_unavailable_result` (main.py), stamps `civitai_deleted_at`
from the cache row's `fetched_at`, and supports `--dry-run`.

Caveats:
- Restore does **not** recreate collection memberships — collection-scoped
  gallery views may miss restored tombstones (status filtering unaffected).
- Timestamp column in `civitai_api_cache` is `fetched_at` (NOT `created_at`);
  values are raw string timestamps that need parsing.

## Gotchas

- **"Resolved" tier counts ≠ importable data.** An empty-but-successful data
  source (HTTP 200 + `[]`) silently satisfies `cid in index` membership checks.
  Always check truthiness of the payload, not just presence.
- Deleting the images on CivitAI's side leaves local archives permanently
  empty — re-running the scan or re-importing can never recover these tags;
  they should be reported, not retried.
- `api_limit=0` means UNLIMITED tier-3 live calls (UI default), not zero.
- `_upsert_authority_terms` here (and its `_upsert_civitai_authority_terms` twins in `taxonomy.py`/`main.py`) must never null an existing `external_tag_id`: guard with `if external_tag_id is not None and ...` in the update branch. Tier-1 sidecar payloads are mostly id-less and previously erased IDs resolved from richer sources (see `taxonomy-import.md` → CivitAI Ext ID Backfill).
- Dry-run is the default for the endpoint; the UI "Import Missing Tags" button
  calls the same SSE flow with `dry_run=0`.
- Gallery UI: "CivitAI Deleted" is the first Status pill
  (`STATUS_PILL_ORDER` in `app/frontend/js/main.js`); pill clicks drive
  POST `/api/query` with `included.status=["civitai_deleted"]`. Verified:
  101 tombstones render as `civitai-unavailable-*.placeholder` tiles; the
  placeholder thumbnails 404 by design (no local file).
