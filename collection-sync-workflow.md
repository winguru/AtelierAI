Sync My CivitAI Collections Workflow (Canonical)

This document defines the implementation-aligned workflow for syncing CivitAI collections into AtelierAI.

## Scope

- Sync runs as an asynchronous background task.
- Collection sync is optimized with a probe-first strategy and only performs full verification when needed.
- Existing healthy local assets are fast-pathed to avoid unnecessary re-downloads.
- Remote-unavailable CivitAI items create/update persistent `placeholder` lifecycle records and are also reported in task output.
- Offsite-only resources are metadata references only and are not represented as standalone gallery items.

## Phase 1: Queue and task lifecycle

1. Queue sync using `POST /collections/sync/civitai`.
2. Track progress through `GET /tasks/`, `GET /tasks/{task_id}`, and `POST /tasks/{task_id}/cancel`.
3. Treat task summary as the source of truth for `errors`, `warnings`, `unavailable_items`, `collections`, and retry metrics.

## Phase 2: Discover remote collections

1. Resolve user image collections through `collection.getAllUser` (authed).
2. Keep only collection rows where type is `image`.
3. Create or reuse a local CivitAI-backed collection record.

## Phase 3: Per-collection decision: probe vs full verify

1. Probe the first page head (fingerprint and page-shape signal).
2. Inspect local collection health (membership count and local media usability).
3. Run full verify when any trigger is true:
	- `limit` was requested.
	- Local media is incomplete or non-active rows are present in membership.
	- Missing full snapshot metadata.
	- Local membership count mismatch versus last full item count.
	- Remote head fingerprint changed.
	- Remote page-shape (`has_more`) changed.
	- Last full verify is older than max age threshold.
4. Skip full verify when head and local state are still valid; update sync metadata only.

## Phase 4: Full verify import pipeline

1. Fetch collection items (`image.getInfinite` path via scraper), normalize, and dedupe IDs.
2. Archive collection payload stubs for diagnostics.
3. For each image ID, run existing-image fast path before any remote fetch:
	- Match by CivitAI source URL (DB first, sidecar fallback).
	- If status is `active` and media is usable, attach membership and skip download.
	- If status is `deleted`, reactivate and attach membership.
	- If status is `tombstoned`, skip with explicit skip reason.
	- If stale/unusable local media is detected, remove stale record and continue to remote fetch.
4. For download candidates, stage remote work:
	- Resolve target metadata from `image.get` and `image.getGenerationData`.
	- Save API responses early for auditability.
	- Download with media validation and fallback URL strategy.
5. Ingest prepared media into library:
	- Run library ingest and hash dedupe.
	- Ensure CivitAI source attribution and metadata persistence.
	- Preserve source variants (video source, archived static/source mismatch variants).
	- Preserve variant provenance filenames in metadata (for example `original_variant_file_name`) while keeping on-disk `variant_file_path` canonical and hash-based.
	- Write/refresh sidecar metadata.
6. Handle remote unavailable items:
	- Record structured unavailable detail entries.
	- Create or update a persistent `placeholder` image record keyed by source URL.
	- Keep placeholder records hidden from default gallery queries (`active` filter only).

## Phase 5: Membership reconciliation

1. Build desired membership set from successful and valid local matches.
2. Remove collection memberships not present in the current desired set.
3. Persist probe/full-scan sync metadata (`last_synced_at`, `last_full_scan_at`, `last_full_item_count`, head fingerprint fields).

## Phase 6: Final outputs and operations

1. Return per-collection and aggregate summaries including:
	- `requested`, `images_added`, `images_skipped`, `images_recovered`, `images_cancelled`.
	- `memberships_removed`, `errors`, `warnings`, `unavailable_items`.
	- `sync_state` indicating full verify, skip reason, empty verified, or failed.
2. Use retry endpoint for failed imports when needed: `POST /tasks/{task_id}/retry_failed`.
3. Use repair/backfill flows for data remediation outside normal sync:
	- `POST /images/{file_hash}/repair`
	- `POST /civitai/backfill/nsfw-levels`
4. Use placeholder review endpoint for unavailable-item investigation:
	- `GET /utilities/placeholders` (supports optional `classification` and `collection_id` filters)
	- `GET /utilities/placeholders/summary` (aggregates counts by classification, endpoint, and status code)

## Integrity expectations after sync

1. Active image rows should reference usable on-disk media and consistent sidecar metadata.
2. Tombstoned/deleted lifecycle states are preserved and not treated as active import targets.
3. Placeholder lifecycle states are preserved for future review/investigation and are excluded from default gallery views.
4. Offsite-only resource references (for example remote source-video URLs) must not become standalone gallery items.
5. Declared-size mismatches from CivitAI metadata are warning-tolerant when transport/media validation succeeds.
6. Variant payloads should represent distinct assets and preserve provenance (local library asset vs source variants).
7. For archived/static source variants, keep canonical hash-based paths on disk, but preserve original variant filenames in metadata so UI can display the source-facing filename independently of storage naming.

## Decision table: Existing local item status

| Local match status | Media usable | Action | Expected outcome |
| --- | --- | --- | --- |
| active | yes | Fast-path skip download, attach collection membership | `images_skipped` increments; item remains active |
| active | no | Remove stale local record and continue to remote fetch | Item becomes download candidate |
| deleted | n/a | Reactivate row, clear replacement pointers, attach membership | `images_added` increments for recovery path |
| tombstoned | n/a | Skip import for source URL | Skip reason indicates tombstoned source |
| no local match | n/a | Continue to remote metadata+download stage | Normal import candidate |

## Decision table: Remote unavailable handling

| Remote condition | Detection stage | Action | Reporting |
| --- | --- | --- | --- |
| `image.get` returns 404 | Target resolution | Build unavailable result and persist/update placeholder row | Added to `unavailable_items` with endpoint/status context |
| `image.getGenerationData` returns 404 | Target resolution | Build unavailable result and persist/update placeholder row | Added to `unavailable_items` with endpoint/status context |
| Both payloads unavailable | Target resolution | Build unavailable result and persist/update placeholder row | Added to `unavailable_items`; placeholder lifecycle state retained for future review |
| Non-404 remote failure | Any fetch stage | Mark failed import result | Added to task `errors` |

## Decision table: Media/variant mismatch behavior

| Condition | Validation signal | Action | Variant expectation |
| --- | --- | --- | --- |
| Declared video, served video | MIME/magic bytes confirm video | Keep downloaded video as library asset | Local asset is video; optional remote source variant when distinct |
| Declared video, served image | MIME/magic bytes detect static content | Preserve static artifact as archived static source, continue validated video strategy when possible | Local asset plus archived static/source variant metadata, with hash-based variant path and preserved original variant filename metadata |
| Existing local video plus remote video source | Variant builder dedupe path | Do not duplicate equivalent remote video variant metadata | Single canonical video representation for duplicated source |
| Remote-only source URL reference with no local media artifact | Variant payload evaluation | Keep reference in metadata only, not as standalone item | No offsite-only gallery item is created |
| Declared-size mismatch only | Declared vs actual size differs, transport/media valid | Warn but do not hard-fail import | Import can succeed with warning semantics |

## Variant filename policy (recent)

1. Canonical storage name: archived/static source variants under `image_resources/civitai_source_variants` are stored by hash-based filename for dedupe/stability.
2. Display/origin name: preserve the source-facing variant filename in metadata (for example `original_variant_file_name`, surfaced as `original_file_name` in API variant payloads).
3. UI expectation: details/captions should prefer the active variant display/origin filename while keeping hash path and hash value visible for diagnostics.
4. Legacy safety: when historic metadata incorrectly carries a primary video filename for an image variant, normalize the displayed original name from variant context (for example CivitAI image id + image extension).

## Appendix: Variant filename fields

| Layer | Field | Role | Notes |
| --- | --- | --- | --- |
| Variant metadata (sidecar/JSON) | `variant_file_path` | Canonical storage-relative path | Hash-based filename under variant storage roots.
| Variant metadata (sidecar/JSON) | `variant_file_hash` | Canonical content identity | Stable identifier used for dedupe/diagnostics.
| Variant metadata (sidecar/JSON) | `original_variant_file_name` | Preserved source-facing filename | Kept independent from canonical storage naming.
| Variant metadata (sidecar/JSON) | `declared_filename` | Upstream-declared filename hint | May differ from downloaded artifact and preserved original.
| API variant payload | `file_path` | Exposed canonical variant path | Mirrors canonical `variant_file_path`.
| API variant payload | `file_hash` | Exposed canonical variant hash | Mirrors canonical `variant_file_hash`.
| API variant payload | `original_file_name` | Exposed display/origin filename | UI should prefer this for human-readable variant naming.

### Field mapping rules

1. Never derive canonical storage paths from source-facing names; canonical path/hash remain content-addressed.
2. Preserve source-facing filenames in `original_variant_file_name` whenever source data provides one.
3. Surface source-facing names to clients as `original_file_name` in each variant payload.
4. Treat `declared_filename` as an upstream hint only; do not let it overwrite canonical path/hash identity.
5. When legacy metadata is inconsistent, keep canonical path/hash unchanged and normalize only display/origin filename fields.

## Legacy 22-step mapping

- Steps 1-4: Covered by Phase 1 and Phase 2 (session/auth and collection discovery).
- Steps 5-7: Covered by Phase 3 and Phase 4.1-4.3.
- Step 8: Replaced. Unavailable remote items now persist as `placeholder` lifecycle records.
- Step 9: Covered by Phase 4.3 and Phase 4.5 (conditional metadata refresh/backfill).
- Step 10: Covered by Phase 4.3 (existing checks) and Phase 4.4 (download candidate staging).
- Step 11: Partially covered by Phase 4.5; broader enrichment/backfill may run in dedicated jobs.
- Steps 12-20: Covered by Phase 4 and Phase 6 integrity outputs.
- Step 13: Covered by Phase 4.3 and Phase 4.6 (`tombstoned` local handling and remote unavailable diagnostics).
- Step 21: Replaced. Remote unavailable entries are tracked in `unavailable_items` and persisted as `placeholder` records for future review.
- Step 22: Covered by Integrity expectations and ongoing repair workflows.