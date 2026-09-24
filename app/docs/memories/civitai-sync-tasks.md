# CivitAI Sync Tasks — Cooperative Cancellation & Throughput

Covers: `_run_civitai_collection_sync_job`, `_run_civitai_collection_import_pipeline`,
`_run_civitai_post_collection_import_pipeline`, `_run_civitai_post_import_pipeline`,
`_process_civitai_image_ids`, `_prepare_civitai_download`,
`CivitaiPrivateScraper.fetch_collection_items`.

## Architecture (why sync is slow, why cancel used to hang)

- **Single FIFO consumer thread** in `app/src/atelierai/civitai/http_client.py` serializes
  ALL CivitAI HTTP traffic. Nothing overlaps: probe, metadata, and download requests queue
  behind each other.
- **Global backoff** (min 30s ratcheting; 429→Retry-After; 403 CF→90s) stalls every queued
  request, not just the offending one.
- **CDN pacing** ≥1s/download ⇒ thousands of downloads ⇒ ≥1s × N minimum runtime.
- **Retries**: inner request loop retries ×4 with backoff + Retry-After per URL;
  `download_to_temp` no longer multiplies this for upstream 5xx (see 503 soft-skip below).
- **503 soft-skip policy** (speedup): retryable 5xx from a candidate URL → try next
  candidate; all candidates exhausted with 5xx → validation raises **HTTP 503** (temporary)
  vs 502 (permanent). The sync loop (`_process_civitai_image_ids` generic `except`)
  classifies via `_is_civitai_temporary_upstream_error` and soft-skips with
  `_build_skipped_civitai_import_result(image_id, "upstream_temporarily_unavailable")` —
  the image is retried naturally on the next sync instead of hard-failing. Non-retryable
  errors (403 etc.) still propagate the original `CivitaiRequestError` immediately
  (fail-fast; callers map 404/401 semantics themselves). `download_to_temp` accepts
  `max_attempts=` to override the outer attempt cap; outer loop never re-retries 5xx.
- **Throughput/ETA heartbeat**: `_update_civitai_pipeline_heartbeat` accepts
  `started_at` + `baseline_completed`; suffix `f" ({rate:.1f}/min, ETA MM:SS)"` computed by
  `_format_civitai_pipeline_progress_suffix`. `baseline_completed` is set to `len(results)`
  at executor start so Phase-1 DB-only completions don't inflate the network rate; the
  suffix is hidden until the first network completion (elapsed>0, network_completed>0).
- **Upstream-bound, not client-bound**: measured 37.8 RPM vs tRPC bucket 225 TPM — the
  client limit is not the bottleneck; CivitAI-side throttling is.
- **Cache hit rate ~0%** on image-endpoint lookups (94 / ~14,906) during the 6h19m task
  `39302df18fe4` (3,703 added / 1,854 skipped / 71 failed / 430 cancelled).
- **Cancellation was flag-only**: the flag was honored at coarse boundaries only, so a
  cancel could sit "Cancelling…" for minutes (live probe `eadf121154c8` took ~4 min,
  queued behind the FIFO backlog during the probe phase).

## Cancellation model (post-fix)

The task manager (`app/src/atelierai/task_manager.py`) sets a cancel flag;
`TaskContext.cancel_requested` is a **property** (pass `lambda: ctx.cancel_requested` where
a callable is required). `check_cancelled()` raises `TaskCancelledError`, which the task
manager `_run` wrapper maps to a proper `cancelled` status.

Cooperative checks must exist at **every phase boundary**:

1. **Phase-1 loop** (`_process_civitai_image_ids`): on cancel, append cancelled results for
   all remaining ids, `mark_item(..., "cancelled")`, `break`; no network/DB work.
2. **Post-Phase-1 early return**: if cancel after Phase-1, drain `download_candidates`
   into cancelled results (never enters the executor).
3. **`_prepare_civitai_download`**: 4× `check_cancelled()` before network/DB work.
4. **Executor drain loop**: stop submitting on cancel; map `TaskCancelledError` futures to
   `_build_cancelled_civitai_import_result` (never let them fall into `except Exception`).
5. **Scraper pagination** (`fetch_collection_items(should_stop=...)`): stops issuing new
   pages; checked before the first page too.
6. **Sync-job per-collection loop**: `except TaskCancelledError` → finalize
   `cp_entry["status"]="cancelled"` + zero-summary + `break` — the summary **tail must
   still run** (orphan detection, retry metrics) before `task_context.cancel(summary,
   "Cancelled")`.
7. **Post pipelines**: `check_cancelled()` before `fetch_post` / `fetch_post_images`.
8. **Post-collection loop**: `except TaskCancelledError: raise` BEFORE the generic
   `except Exception` — otherwise the raise is swallowed and recorded as an error.

## Gotchas

- `cancel_requested` is a property, not a method.
- Per-collection `except Exception` blocks are swallow hazards for `TaskCancelledError`;
  always add a passthrough `raise` or finalize+`break` above them.
- A cancelled sync must still run the summary tail; the task manager wrapper converts the
  return value via `task_context.cancel(result, "Cancelled")`.
- Heartbeat during cancel drain: "Cancelling — waiting on N in-flight CivitAI request(s)…"
  comes from `_update_civitai_pipeline_heartbeat`.
- Empty-collection / no-change fast paths (`probe.image_ids` empty, `needs_full_verify`
  false) bypass the import pipeline — tests must mock `_probe_civitai_collection_head`,
  `_get_or_create_collection`, `_inspect_local_civitai_collection_health`,
  `_civitai_collection_requires_full_verify`, and `CivitaiPrivateScraper` to steer past
  them.

## Tests

`app/tests/test_civitai_cancel_cooperative.py` — 4 tests:
scraper `should_stop` halts pagination (and before first page); Phase-1 marks all remaining
ids cancelled without network/DB; sync job still builds the summary tail after
`TaskCancelledError` and finalizes via `task_context.cancel(summary, "Cancelled")`.

`app/tests/test_civitai_cdn_503_softskip.py` — 7 tests:
candidate-continue on retryable 503; all-5xx → HTTP 503; permanent error (403) propagates
the original `CivitaiRequestError` (fail-fast, no 502 wrap); classifier truth table
(`_is_civitai_temporary_upstream_error`: retryable 5xx / HTTPException 503 → True; 404,
non-retryable, ValueError → False); remote-not-found classifier unchanged; progress
suffix formatting (rate/ETA/baseline/invalid inputs).

## Sync Lab download resume + preview-variant fail-open (2026-09-08)

Incident: ingest interrupted by app restart → download step re-downloaded all
88 images → during ingest of video assets, `_preserve_civitai_source_variant`
fetched the original webm (via `preview_image_url`, built with
`use_video_transcode=False`) as a best-effort enrichment; that CDN GET hit the
sticky 503 flag (3×503 trip), the exception propagated up through
`_ingest_prepared_civitai_import`, and the whole per-image ingest rolled back
(DB record gone, file+JSON orphaned on disk).

Fixes:
- `_preserve_civitai_source_variant()` now wraps the preview fetch in
  try/except `CivitaiRequestError` → warn + return (fail-open enrichment;
  matches the project rule that enrichment must never block imports).
- `_restore_sync_lab_prepared_from_session()` helper (main.py, near
  `_sync_lab_prepared`) restores persisted `SyncSession.prepared_imports`
  into memory, SKIPPING entries whose temp file no longer exists. Called at
  the top of BOTH `sync_lab_download` (step 6) and `sync_lab_ingest` (step 7).
  The download worker then has a resume fast-path: prepared entry with intact
  temp file → status "downloaded" with `resumed: true` (no CDN call).
- Frontend `sync-lab.js` shows "N reused from previous run" in the step-6
  summary and a ↻ tag on resumed item chips.

Gotchas:
- Temp files are `image_library/temp_civitai_{image_id}_{random}` — random
  suffix means a fresh download NEVER overwrites/reuses an old temp; reuse
  only happens via the session-restored `temp_path` reference.
- `prepared_imports` persists only when the download step COMPLETES (the
  checkpoint write happens after the loop); a crash mid-download loses the
  in-memory entries but files remain on disk (178 orphans at incident time).
- The ingest-rollback path leaves the renamed library file + sidecar JSON on
  disk with NO DB record — repair requires the scan/reconcile pass or manual
  cleanup. (Root cause now fixed by the fail-open change.)

Tests: `app/tests/test_sync_lab_download_resume.py` — 4 tests (restore with
intact/missing temp; variant fetch fail-open; variant success path writes
sidecar metadata).

## Sync Lab download+ingest pipeline (added 2026-09-17)

Motivation: 5,322-image collection at ~1-2s/download meant hours of stage-6
waiting before ANY image reached the library under the strict 6-then-7
staging.

Design:
- `sync_lab_download(auto_ingest=True, collection_id=...)` (step 6) feeds
  each completed download (fresh OR resumed-with-intact-temp) straight to a
  single background ingest consumer thread via a queue; downloads keep
  their CDN pacing untouched (downloads remain the bottleneck — ingest is
  local CPU/DB/file work that fully overlaps). One consumer only: more would
  contend on SQLite write locks without speeding anything up.
- **Collection must be created BEFORE the first ingest** (fixed 2026-09-18):
  `_ensure_image_in_collection` resolves the CivitAI collection id via the
  junction table and SILENTLY SKIPS the membership when no local collection
  exists (SQLite does not enforce FKs; it logs a warning). Standalone step 7
  creates the collection upfront; the pipeline originally didn't — the
  2026-09-17 5322-image MLP run ingested everything into the library with
  ZERO collection attachments, making the images invisible in the collection
  view (looked like "not imported"; step 4 correctly showed them existing).
  The pipeline now calls `_get_or_create_collection` before starting the
  download loop. Backfilled via `_ensure_image_in_collection` for the
  affected window; same-named CivitAI collections share one local collection
  via junction mapping by design (MLP 11284204 + 16393805 → collection 8).
- Ingest runs `_ingest_prepared_for_pipeline` (extracted shared helper —
  same reconciliation order as step 7: source-URL match → hash-duplicate →
  normal ingest). On success the prepared entry is popped (ingest MOVES the
  temp file into the library via `save_to_library`; a stale reference would
  point at a dead path).
- **No duplicated work**: `_sync_lab_auto_ingested` (image_id → result dict)
  is persisted into `SyncSession.step_7_data` after every ingest
  (`_persist_sync_lab_auto_ingested`, fail-open) and restored on pipeline
  start (`_restore_sync_lab_auto_ingested`). An ID present in that set skips
  BOTH download and ingest on resume/re-run.
- SSE: ingest events ride the download stream as `type: "ingested"` /
  `type: "ingest_failed"` events; the completion payload gains
  `auto_ingest/ingested/ingest_failed/already_ingested` counts. Frontend
  shows live dual-stage status and finalizes steps 6+7 together (checkbox
  "Auto-ingest on download" in step 6 controls; requires a selected
  collection).
- Completion: sentinel `None` → consumer exits; drain via thread join
  BEFORE checkpointing, then steps 6 AND 7 are checkpointed complete.
- Ingest failures never break the pipeline (recorded + streamed; retried by
  re-running — failed IDs stay out of the skip set unless already ingested).
- Standalone step 7 (no checkbox) behaves exactly as before.

Gotchas:
- Tests must monkeypatch the SAME module object they call — `import main`
  (not `backend.main`) when both app/ and app/backend are on PYTHONPATH, or
  patches land on a stale duplicate module.
- `sync_lab_download`'s StreamingResponse generator is async; SSE draining
  in tests needs an async-capable iterator helper.

Tests: `app/tests/test_sync_lab_auto_ingest_pipeline.py` — 6 tests (400
without collection_id; immediate pipelined ingest; ingest failure
non-fatal; already-ingested skips both stages; persist/restore; merge
semantics).
