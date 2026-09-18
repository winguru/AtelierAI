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

### Response archive layout (2026-09-06: 2-level hex sharding)
`civitai_api_responses/` uses deterministic 2-level shard dirs so writers and
readers compute file locations without an index (`shard_parts()` /
`shard_root()` in `atelierai.civitai.response_archive`):

- Writer-1 (`CivitaiAPI._archive_json_file`, key = image uuid or
  `imageid_<n>` fallback):
  `<endpoint>/<s1>/<s2>/civitai_image_<kind>_<key>.json`
- Writer-2 (`CivitaiResponseArchive.record`, key = sha256 of the redacted
  request, first 16 hex chars `req16`):
  `latest/<endpoint-slug>/<req16[:2]>/<req16[2:4]>/<kind>_<endpoint>_<req16>.json`
  — overwrite-in-place, replay via `read_latest(kind=…, endpoint=…, request=…)`.
- Shard rule: hex-ish key → chars `[0:2]` + `[2:4]`; `imageid_<n>` → zero-padded
  last 2 + previous 2 digits. Endpoint dirs are `_slug()`-ed (dots preserved:
  `image.get`, `tag.getVotableTags`).
- **`history/` is retired** (was per-request versioned copies, ~80k files):
  renamed to `image_resources/civitai_api_responses_history_retired_20260906/`
  by `app/scripts/migrate_civitai_api_responses_sharded.py` (idempotent, dry-run
  default, `--apply` executes). Delete the retired dir manually once satisfied —
  nothing references it.
- Readers that glob the tree (`scan_missing_service._build_tag_archive_index`)
  must dual-glob flat + sharded layouts; new writes go sharded only.

### Transport logging (request/response/timing)
Every dispatched CivitAI HTTP request emits one JSONL record to
`<IMAGE_RESOURCES_PATH>/civitai_transport_logs/civitai_transport_YYYY-MM-DD.jsonl`
via `atelierai.civitai.transport_log` (buffered daemon-thread writer; flush at
≥128 entries or 1s; daily files pruned to last 14 by default).

- **Env toggles:** `CIVITAI_TRANSPORT_LOG` (default `1`; `0` disables),
  `CIVITAI_TRANSPORT_LOG_PATH` (override root), `CIVITAI_TRANSPORT_LOG_MAX_FILES`
  (retention, default 14).
- **Schema:** one record per request — `request_id`, `timestamp`, `request_type`,
  `fqdn`, `endpoint`, `url` (query strings stripped), `method`,
  `total_duration_seconds`, `max_attempts`, `queue_wait_seconds`,
  `pacing_wait_seconds`, `rpm_at_dispatch`, `queue_depth`, backoff snapshot at
  dequeue (`backoff_active_at_dequeue`, `backoff_reason`, `backoff_wait_seconds`),
  and aggregated per-attempt log (`attempts[]` with outcome/status/elapsed —
  outcomes: `transport_error`, `http_429`, `http_503`, `http_403_cloudflare`,
  `http_error`, `success`), `http_elapsed_seconds`, `attempts_used`.
- **Fail-open constraint:** logging must never break or slow requests —
  `record()`/`record_transport_event()` never raise; `_emit_transport_log()`
  wraps everything in try/except.
- **Response archive timing:** `CivitaiResponseArchive.record()` accepts optional
  `queue_wait_seconds`/`elapsed_seconds` kwargs (kwarg-only, non-breaking);
  `CivitaiAPI._record_response_archive` sources them from
  `CivitaiHttpClient.get_last_request_info()`.
- **Analyzer:** `python scripts/analyze_civitai_logs.py [--by-type] [--timeline]
  [--json] [--date YYYY-MM-DD] [--last N] [--log-dir PATH]` — latency
  percentiles, outcome counts, rpm-at-dispatch correlation, rate-limit events.
- Log dirs are gitignored via `image_resources/` rules.

### 503 analysis findings (2026-09-05/06 transport logs)
- `rpm_at_dispatch` is a **GLOBAL** rpm (all types combined), not CDN-local —
  do not use it to infer per-CDN rate causes. Derive CDN-local rates from the
  timeline instead.
- Two distinct 503 modes observed:
  1. **Morning rate-cap bursts** — CDN-local 22-37 req/min correlates with 503
     bursts; global rpm looks innocuous. Mitigation: `CIVITAI_CDN_MIN_INTERVAL`
     default raised 1.0 → 2.0s (~30/min, below the danger zone).
  2. **Evening "sticky flag"** — instant (p50 ~0.198s) 503 edge rejections at
     near-zero global rpm, persisting 20-40 min. Retry probes during the flag
     window keep re-tripping it. Mitigation: consecutive-CDN-503 flag cooldown
     (below) — stop probing, wait it out.
- All calls in the analyzed window were live (no cached hits).

### CDN 503 flag cooldown (added 2026-09-06)
- **Detection:** `_CDN_503_CONSECUTIVE` counts consecutive CDN 503s (any CDN
  endpoint); threshold `CIVITAI_CDN_FLAG_THRESHOLD` (default 3); tRPC 503s are
  ignored. A CDN success resets the streak.
- **Cooldown:** CDN-scoped only — tRPC continues normally (unlike the global
  429/403-CF backoff). Escalating schedule 300 → 600 → 1200s (capped) via
  `activate_cdn_flag_cooldown()`. Strikes reset only after cooldown expiry AND
  a successful CDN request.
- **Retry abort:** while the flag is active, the inner retry loop aborts
  immediately on a CDN 503 (raises retryable error → `main.py` soft-skips →
  retried next sync) instead of burning attempts as flag-feeding probes.
- **Observability:** `get_request_metrics()` exposes `endpoint_503_counts`,
  `cdn_503_consecutive`, `cdn_flag_active`, `cdn_flag_remaining_seconds`,
  `cdn_flag_strikes`; the TPM stats table gained a "503s" column
  (`session_503s` in `_build_tpm_breakdown`). Envelope timing gains
  `cdn_flag_wait_seconds` / `cdn_flag_tripped` / `cdn_flag_abort`, and
  `backoff_reason="cdn_503_flag"` on dequeues during cooldown.
- **Tests:** `app/tests/test_civitai_cdn_503_flag.py` (calls
  `_execute_envelope_request` directly — no queue/consumer-thread timing
  involved); regression pair `app/tests/test_civitai_cdn_503_softskip.py`.
- **Browser bridge (CDP sidecar lane):** `CivitaiBrowserBridge` in
  `app/src/atelierai/civitai/browser_bridge.py` owns a Playwright
  `connect_over_cdp` connection to the optional `chrome-sidecar` container
  (compose profile `browser`). `fetch()` evaluates `fetch()` inside a live
  civitai tab so requests carry Chromium's TLS fingerprint + profile cookies
  (auth once by hand via noVNC `:6080`; profile volume persists it). Fail-open
  contract everywhere: never raises, returns `{"ok": false, "bridge": ...}`.
  Tests: `app/tests/test_browser_bridge.py` (offline, mocked browser).

### Browsed-id re-staging must be quarantined (fixed 2026-09-16)
- Open `/images/{id}` tabs are re-staged on EVERY 30s drain tick; any staging
  fetch keyed on "row lacks uuid" re-fetches dead (404) or rate-limited ids
  forever — measured 616 wire requests/day for one deleted image, which
  self-inflicted the 429s we blamed on CivitAI. Before diagnosing CivitAI
  rate errors, grep transport logs for repeated ids: the caller is often us.
- Fix shape: `_fetch_bare_metadata` = `fetch_basic_info_cached` (7-day TTL —
  metadata basics are immutable per id; tombstoned 404s count as cache hits)
  + `_backfill_fails` quarantine shared with the janitor backfill.
- The DB cache (`civitai_api_cache`) DOES absorb repeats when callers use the
  `*_cached` variants; uncached call sites bypass it entirely. Prefer
  `fetch_basic_info_cached` for anything reachable from a periodic loop.

### Janitor settings persist in app_settings (added 2026-09-16)
- `harvester.auto_scrape_posts` / `harvester.auto_close_seconds` live in the
  `app_settings` KV table (`AppSetting` model) — loaded fail-open in the
  harvester `__init__`, upserted on every setter. Missing table/row or
  unimportable backend ⇒ defaults, never an error (standalone-safe).
- Any new user-facing toggle reachable from a periodic loop should use the
  same table — per-process settings reset on every uvicorn --reload and
  silently stall whatever depended on them.

### Error-tab recovery must be self-driving (fixed 2026-09-16)
- An idle error page makes NO API calls, so its health can never flip back to
  ok on its own — recovery requires an action (reload or navigation).
- Never gate recovery behind a per-process setting (auto_scrape_posts resets
  on every uvicorn --reload). Erroring /images/ tabs now navigate to their
  owning post page as their retry action (`_recover_image_page`) — recovery
  and scraping in one step; plain-reload fallback when no post_id yet.
- Janitor duties must ride BOTH probe() and the auto-drain tick
  (`_run_janitor_duties`); anything only on the dashboard poll stalls when
  the Bridge Lab is closed.
- Cap recoveries per cycle (`_RETRY_MAX_PER_CYCLE=3`) — a fleet of erroring
  tabs firing one synchronized tRPC burst re-trips the rate limit.

### API-health-aware retry + auto-close suspension (added 2026-09-07)
- The in-page wrapper tracks `apiOkAt`/`apiErrAt`/`apiLastStatus`; `_page_health()` classifies `pending`/`ok`/`error`.
- Retryable statuses: `409`, `429`, `5xx`, network-fail (`0`). Other 4xx (404) count as **ok** — dead resources must not be retried/held forever (mirrors the backfill-quarantine lesson).
- Auto-close only reaps **healthy, drained** tabs (`_should_close_page`); erroring tabs get `_page_last_active` restamped (suspension), never closed.
- `_retry_error_pages` rides the probe cycle: `/posts/` tabs reload with backoff `2^attempt × 5s` cap 60s (`_retry_backoff`/`_retry_next`, cleared on recovery). `/images/` tabs are scrape-owned — protected from close but not reloaded here.
- Ruff complexity cap 15 keeps biting janitor loops — per-page decisions are extracted (`_should_close_page`, `_retry_one_page`, `_lookup_post_pages`).

## Key Files
- `app/src/atelierai/civitai/civitai_auth.py` — `_launch_chrome_cdp()`, `_launch_context()`, `_terminate_chrome()`
- `app/src/atelierai/civitai/browser_bridge.py` — CDP browser bridge singleton (`get_browser_bridge()`), fail-open `fetch()`/`navigate()`/`status()`
- `app/backend/routers/browser_bridge.py` — `/api/browser-bridge/*` control endpoints
- `docker/chrome-sidecar/` — headed Chromium + Xvfb + noVNC sidecar (compose profile `browser`, CDP `:9222`, noVNC `:6080` loopback-only)
- `app/frontend/browser-bridge-lab.html` — bridge status/fetch/navigate UI
- `app/docs/features/browser-bridge.md` — full operational runbook
- `app/src/atelierai/civitai/transport_log.py` — JSONL transport logger (`get_transport_log()`, `record_transport_event()`)
- `app/src/atelierai/civitai/http_client.py` — consumer loop timing capture, `_emit_transport_log()`, `get_last_request_info()`
- `app/src/atelierai/civitai/response_archive.py` — durable redacted archives with timing fields
- `app/scripts/analyze_civitai_logs.py` — transport-log analysis CLI
- `app/backend/services/civitai_service.py` — CivitAI API client
- `app/backend/civitai_enrichment.py` — enrichment pipeline
- `app/backend/routers/civitai/` — CivitAI-related API endpoints

## Gotchas
- **Expired server token looks like a content filter**: CivitAI serves anonymous traffic an SFW-only view — `image.getInfinite?username=X` returns just the artist's PG images with NO cursor, indistinguishable from "artist has 21 images" unless you check `auth/status`. Symptom signature: small all-nsfwLevel-1 result set, `nextCursor: null`. Recovery: `POST /api/browser-bridge/refresh-session` pulls the live cookie from the sidecar browser (Playwright reads httpOnly cookies) into the API singleton + cache. REST v1 API needs a real API key — a session cookie gets 403, so it can't be used as a fallback probe.
- Chrome CDP port must be available; if already in use, auth fails
- CivitAI API rate limits apply — batch operations should include delays
- Artist avatars: `image.civitai.com` 301-redirects newer avatars to
  `blobs-b2.civitai.com` (B2 blob storage; no `.red` mirror exists). The
  `_ARTIST_AVATAR_HOSTS` allowlist must include it or every artist-summary
  request re-attempts a failing download (allowlist failure = nothing cached).
- Single-image 404s (`❌ API request error (HTTP 404)`) on the backend console
  are usually remotely-deleted CivitAI images; tombstones are recorded to avoid
  re-fetching and the frontend handles the miss gracefully — not a bug.
- **Download retry semantics** (`download_to_temp`): the inner request loop
  (`_send_with_retries`) already retries 5xx ×4 with backoff + Retry-After; the
  outer loop must NOT re-retry 5xx (previously 4×4=16 attempts/URL on persistent
  503). Outer retries only for non-5xx retryable errors; `max_attempts=` kwarg
  overrides the outer cap per call. Callers in `backend/main.py` treat retryable
  5xx as "try next candidate URL, then soft-skip + retry next sync" (see
  `civitai-sync-tasks.md`).
- Sync Lab collection listing (`/api/sync-lab/collections`) is cache-first (2-minute max age) to keep troubleshooting responsive; use `?force_refresh=true` to force a live CivitAI pull.
- **Browser bridge is additive, not a replacement** — nothing in the sync/enrichment pipeline routes through it by default; integration is per-caller opt-in. The bridge is fail-open (sidecar down → `ok:false, bridge:"unavailable"`, callers fall back to direct). CDP `:9222` is full profile control — compose-internal only, never publish it. Sidecar uses Debian `chromium` because google-chrome-stable has no Linux ARM64 builds (this repo targets aarch64).
- **Page harvester** (`app/src/atelierai/civitai/page_harvester.py`): wraps page fetch/XHR via `add_init_script` (survives SPA navigation); captures ONLY `civitai.red`/`civitai.com` hosts on `/api/trpc/`|`/api/v1/` — early version substring-matched `/api/v1/` anywhere and archived `advertising.civitai.com` ad beacons; filter now checks hostname first. Drains into `CivitaiResponseArchive` as `kind="harvested"` with the same request-hash keying as the direct lane (same shards). `page.evaluate` takes exactly ONE arg — pass packed objects, and keep test mocks signature-enforcing (AsyncMock hides arity bugs). **Event-driven wrapping**: `watch()` arms `context.on('page')` (new tabs wrapped via retry-on-document-ready) + `page.on('framenavigated')` (re-wrap after full loads; SPA pushState keeps the wrapper — probe before reinstalling); `harvest_once` re-arms watchers each tick so sidecar restarts (new context objects) self-recover. `GET /harvest/status` probes per-page wrapped/queued state for the Bridge Lab display.
- **Browsed staging** (`app/backend/services/browsed_stager.py`): harvested `image.getInfinite` captures → `CivitaiSearchImage` upserts (fill-if-absent, never clobber ratings/search metadata) + standalone links (`search_id=NULL, rating=NULL`) → review via `/rated?rating=unrated` in the search lab. State file `browsed_staging_state.json` tracks staged archive paths (canonicalized via `_norm` — early versions mixed absolute/relative paths and re-decoded everything every pass). **Staging caps + alphabetical ordering starve new files**: the 200-files-per-pass cap consumed the alphabetically-first untracked shards every pass, so fresh captures buried at sorted-index 354+ never staged — files are now pre-filtered to untracked and sorted newest-first by mtime (fresh browsing stages on the next pass; backlog drains newest-end first). `/rated` + `/rated/artists` needed NULL-rating base-filter handling for `unrated` (two parallel validation blocks in search.py — update BOTH when adding rating values). Flat-array ints inside templates are REFERENCES into the array (strings pass inline) — fixture-building must store ints as refs or the deserializer resolves them wrongly. **Never call `CivitaiAPI.__new__(CivitaiAPI)` for "an instance without auth"** — the custom `__new__` registers the singleton immediately without running `__init__`, and the partially-initialized object poisoned `get_instance()` process-wide (gallery `AttributeError: default_params`, imports misclassified as unavailable). `_deserialize_trpc_flat_array` is now a `@staticmethod` for auth-free use, and `get_instance()` self-heals un-initialized singletons.
- **Sidecar gotchas (learned from first host run):** Chromium as container-root requires `--no-sandbox` or it exits at startup; the entrypoint must clear `/tmp/.X99-lock`/`/tmp/.X11-unix/X99` before starting Xvfb or `restart: unless-stopped` wedges on "Server is already active for display 99"; `DISPLAY=:99` must be exported for Chromium (empty `$DISPLAY` → "Can't open display:"); and the profile's `SingletonLock` must be removed at boot — container hostnames change every recreate, so a stale lock from a crashed run permanently blocks Chromium (exit 21, restart loop). Do NOT set compose `init: true` alongside the tini ENTRYPOINT (double-init demotes tini, kills zombie reaping). **Headed Chromium ignores `--remote-debugging-address`** (headless-only flag; anti-DNS-rebinding hardening) — DevTools always binds loopback, so the sidecar runs a `socat` forwarder (`CDP_LOOPBACK_PORT` 9221 → published `CDP_PORT` 9222). Google OAuth through the sidecar's real Chromium + persistent profile worked on the first attempt — the approach that `playwright-stealth` never achieved. **noVNC access model:** published on all interfaces (`6080:6080`) so the Bridge Lab link works from LAN browsers; auth is optional via `VNC_PASSWORD_DATA` (contents of an `x11vnc -storepasswd` file, passed through compose env) — empty = no password, and noVNC accepts `?password=` in the URL for transparent bookmarked auth.
- `_fetch_civitai_user_image_collections()` DB enrichment resolves collections
  via the junction table (`CollectionCivitaiMapping`) join first, then a legacy
  `CollectionModel.civitai_collection_id` column fallback for ids with no
  junction row. Do NOT add a NOT IN / subquery exclusion to the fallback —
  the unmigrated id set is provably disjoint from junction rows by
  construction (any junction hit would already be in the join result), so
  such a clause is dead logic and only emits SAWarning "Coercing Subquery
  object into a select() for use in IN()" (removed 2026-09-07).

### Concept observations: UNIQUE(image, concept, authority) ignores the term
`image_concept_observations` has `uq_obs_image_concept_authority` on
`(image_id, concept_id, authority_id)` — `authority_term_id` is NOT part of it.
CivitAI tags routinely alias one concept (`forest`/`full moon`/`indoors` →
concept "indoors"), so a tag batch containing ≥2 aliases produces a duplicate
INSERT, a failed flush, and a full-transaction rollback that takes every other
row in the sync batch with it (symptom: `PendingRollbackError` wrapping
`sqlite3.IntegrityError` on the next session use).

Rules for every observation-insert path (4 helpers: `_insert_tag_observations_for_image`
and `_hydrate_observations_from_payload` in `main.py`;
`_hydrate_missing_observations_if_needed` and `_hydrate_observations_from_tags`
in `image_collection.py`):

- **Dedupe on the concept key** `(image_id, concept_id, authority_id)` before
  inserting — one observation per concept per image per authority; first alias
  term in the batch wins.
- **Batch pre-load** existing `(authority_term_id, concept_id)` pairs per
  (image, authority) — sessions run with `autoflush=False`, so pending `db.add()`
  objects are invisible to queries and per-row existence checks are both wrong
  and N+1.
- **`IntegrityError` → `db.rollback()` → warn and continue.** A failed flush
  poisons the session even inside `with db.begin_nested():` (empirically
  verified — savepoints do NOT unpoison; the pending object is auto-expunged).
  Rollback is mandatory before any session reuse; observations are enrichment,
  so fail open per AGENTS.md.
- `concept_id = NULL` rows evade the unique constraint entirely — never rely on
  the DB alone to dedupe; the pre-load sets are the source of truth.

Regression tests: `app/tests/test_civitai_tag_observations.py`.

### Search Lab pagination & filtering (no post-fetch image filtering)
**Never filter per-image states (discard/seen/saved/keep/skip) on the backend search proxy.** Doing so shifts the Meilisearch offset on every page and causes the same images to reappear across pages (duplicate tiles).

Architecture:
- **Artist-level exclusions** (blocked artists) are pushed down to Meilisearch via `extra_filters` (`NOT (user.username = "a" OR ...)`) so they apply *before* pagination — offsets stay stable. `toggle_artist_block` calls `_invalidate_search_cache(reason="artist_block")` so the change is reflected immediately.
- **Per-image states** (discard/seen/saved/keep/skip) are returned to the frontend and hidden client-side via the hide-filter bar (`isHiddenByFilter`). This avoids re-running the entire search when a hide-filter is toggled.
- `_get_excluded_civitai_image_ids` is retained for the `/excluded` endpoint but is **not** used by the search proxy.

The frontend already handles this: `fetchImageRatings()` fetches the rating for returned hits, then `applyHideFilters()` + `checkAutoLoadIfAllHidden()` hide matching tiles and auto-load more pages if a whole page is hidden.

### Search Lab frozen-grid race (gallery/preview desync)

**Never mutate search state (offset=0, hits=[]) and then call a guarded `executeSearch()` that can silently drop the call.** If a fresh (non-append) search is submitted while an append (load-more / infinite scroll / fullscreen advance) is in flight, the old code's `if (state.loading) return;` swallowed the fresh search entirely. When the in-flight append then completed (`state.hits = state.hits.concat(page)` + `renderResults(append=true)`), the grid kept stale tiles from the previous results generation while `state.hits` held the new generation — tile `data-index` no longer matched `hits[i]`, so the preview pane (`showDetails(state.hits[index])`) showed a *different image* than the clicked tile. Symptom: gallery and detail pane "from completely different sets of images", with the desync boundary exactly at the frozen grid's tile count.

Fix (two layers, both in `search-lab.js`):
1. **Queue swallowed fresh searches** — `executeSearch` sets `_pendingFreshSearch = true` instead of returning silently; `_drainPendingFreshSearch()` runs it from the `finally` blocks of `executeReviewSearch` / `executeGallerySearch` / the search path, after `state.loading = false`. Handlers already reset `offset`/`hits` before the swallowed call, so the queued re-run picks up the handler's intent.
2. **Defensive realignment in `renderResults(append=true)`** — if `grid.children.length > state.hits.length` (impossible in a healthy generation), fall back to a full re-render so tile indices always match hits. Heals any already-diverged session on the next append.

Gotchas:
- The load-more button handler checks `if (state.loading) return;` *before* incrementing `state.offset` — correct order. The shared InfiniteScroll module also guards via its `isLoading()` callback before firing `onLoadMore`. Preserve both when touching pagination code.
- `renderResults` append uses `startIdx = grid.children.length`, which assumes tile count == prior hits length. Any code path that lets the grid and `state.hits` diverge breaks that invariant — this is the fragile assumption to protect.
- Repro technique: monkeypatch `window.fetch` to delay `/api/civitai-search` ~2500ms (or fully mock it), submit a fresh search during an in-flight append, then compare `tile[data-index=N] img.src` with the detail-pane image after clicking.

### Gallery mode (image.getInfinite + username)

Gallery mode is a third Search Lab mode (`state.mode === 'gallery'`) that browses a CivitAI user's complete image gallery using the `image.getInfinite` tRPC endpoint with a `username` parameter.

**API flow:**

- `CivitaiAPI.fetch_user_gallery_images(username, cursor)` → `image.getInfinite` with `username` param
- Cursor format is a composite string (`"<offset>|<unix_timestamp_ms>"`), passed verbatim on subsequent pages
- `image.getInfinite` returns a double-encoded column-oriented format; `_make_request()` + `_deserialize_trpc_flat_array()` handle this transparently
- `collectionId` is popped from `default_params` before the request (gallery is not collection-scoped)

**Backend endpoint:** `POST /api/civitai-search/gallery`

- Schema: `CivitaiGalleryRequest(username, cursor=None, limit=51)`
- Response shape mirrors the search proxy but with cursor-based pagination: `{hits, nextCursor, hasMore, ...}`
- `_normalize_gallery_item()` converts raw tRPC items to the standard hit shape (CDN URLs, video detection, stats normalization stripping "AllTime", user extraction, hash→blurhash)
- DB enrichment (`_enrich_hits_from_db`) and lazy metadata fetch are shared with search mode

**Frontend pagination differences:**

- Gallery mode uses **cursor-based pagination** — `state.galleryCursor` advances via API response `nextCursor`, NOT `state.offset += state.limit`
- Every `state.offset += state.limit` in the IIFE is guarded with `if (state.mode !== 'gallery')` (infinite scroll `onLoadMore`, `load_more_btn` handler, `advanceToNext`, `navigateFullscreen`, `checkAutoLoadIfAllHidden`)
- `_hasMorePages()` returns `state.galleryHasMore && state.galleryCursor !== null` in gallery mode
- `switchMode()` resets `galleryCursor`, `galleryUsername`, `galleryHasMore` when leaving gallery mode

**Key files:**

- `app/src/atelierai/civitai/civitai_api.py` — `fetch_user_gallery_images()`
- `app/backend/routers/civitai/search.py` — `civitai_user_gallery()` endpoint, `_normalize_gallery_item()`
- `app/backend/schemas.py` — `CivitaiGalleryRequest`
- `app/frontend/js/search-lab.js` — `executeGallerySearch()`, gallery state fields, mode switching
- `app/frontend/search-lab.html` — Gallery mode button in mode-bar

### Search Lab batch import reconciliation
A batch task reaching `completed` only means every requested ID finished
processing. It does not mean every ID was added to the gallery. The batch task
result must expose authoritative `imported_ids`, `existing_ids`, and
`failed_ids`; Search Lab marks only imported/existing IDs as saved.

Placeholder, tombstoned, remote-not-found, cancelled, and errored outcomes stay
visible and retryable. The `/civitai-search/library-status` endpoint reports
only active images, never placeholder rows. Frontend library-status refreshes
must merge task-confirmed IDs before applying Hide Saved so an older concurrent
status response cannot erase a successful import.

`existing_file_hash` skips (cross-post duplicates: same file already in the
library under a different CivitAI id) count as `existing`, not failed. The
ingest path backfills civitai metadata on the existing image and attaches it to
the target collection, so the image genuinely is in the library. Hash-dedup is
why Search Lab's "Saved" filter (which matches by `civitai_image_id`) can miss
these: the UI looks up by the requested id while the library already holds the
identical file under another id. `tombstoned_file_hash` / `tombstoned_source_url`
remain failed and retryable by design. Search Lab derives its failure message
from `result.failures[].reason` (`summarizeImportFailures`); never reintroduce a
hardcoded "unavailable on CivitAI" reason string.

### Search Lab artist avatar cache
Fullscreen artist avatars are cached once per CivitAI artist in the
`civitai_artist_profiles` table. Store a 96x96 WebP (maximum 16 KiB) as a
SQLite `BLOB` with MIME type, source URL, and fetch timestamp; do not duplicate
bytes on each `civitai_search_images` row.

Search responses include cached avatars in a top-level `artist_avatars` map,
keyed by the `artistAvatarKey` added to each hit. Values are base64 data URIs,
so the browser does not make a separate avatar request. A cache miss is resolved
through the normal `GET /api/civitai-search/image/{image_id}` metadata request,
which downloads, compacts, stores, and returns the avatar inline. Never add a
dedicated avatar endpoint.

Do not hold a SQLAlchemy session while fetching CivitAI profile metadata or
downloading an avatar. Read the cache in a short session, perform network work
after it closes, then persist in another short session. This avoids exhausting
the API database connection pool during concurrent imports and Search Lab use.

Avatar downloads are size-limited and only follow redirects between approved
CivitAI image hosts. Preparation failures serve an existing cached avatar when
available.

Cache-miss metadata merges must be field-scoped. `fetchInlineArtistAvatar`
fires for fullscreen neighbors while browsing; if it merges the whole
`GET /api/civitai-search/image/{id}` hit onto the in-memory hit (as it did
before 2026-08), detail-only fields — notably numeric `nsfwLevel` — land on
review hits that never carried one. `isHiddenByFilter` only applies the NSFW
pill filter when `nsfwLevel` is a number, so every visited/prefetched image
silently gained an NSFW verdict. With the NSFW dropdown on "Safe" (levels
{1,2}), Explicit neighbors became permanently hidden mid-session and
fullscreen navigation skipped them until reload; the skips clustered wherever
the user had browsed. Fix: merge only `artistAvatarKey`, `user`, and
`username`. The user-invoked `reloadCurrentImage` keeps its intentional
full merge (it preserves local UI state explicitly).

CivitAI `image.get.user.image` may be null even when the artist has a profile
picture. In that case, call `user.getById` and build the CDN URL from its
`profilePicture.url` UUID and `profilePicture.name` filename.

Review mode (`GET /rated`) is local-only: it attaches the same cached
`artist_avatars` map via `_attach_cached_artist_avatars`, and
`_build_hit_from_search_image` includes `user.id` so hit keys resolve to the
stable `id:{artist_id}` profile rows. Browsing reviews must not trigger live
tRPC calls to civitai.red for avatars — if it does, the `user.id` pass-through
is broken.

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

### Deleted-user username resolution (June 2026)

**Problem:** When a CivitAI user deletes their account, `image.get` returns `username: null` and `deletedAt: <date>`. The enrichment pipeline was producing synthetic `[deleted:USERID]` artist names, losing the historical username entirely.

**API limitation:** `user.getById` returns the real `username` only for **banned** accounts (`deletedAt: null`). For **truly deleted** accounts (`deletedAt` present), it returns `username: null`. There is no CivitAI API endpoint that resolves a user ID back to the historical username for deleted accounts.

**Resolution strategy** (`_try_resolve_deleted_username()` in `civitai_enrichment.py`):
1. **Live API** (`CivitaiAPI.fetch_user_by_id`) — works for banned and active users; fails for truly deleted.
2. **Local Search Lab data** (`CivitaiSearchImage.artist_name` where `artist_id == user_id`) — captures the username at scrape time, even for users later fully deleted. This is the **only** source for truly deleted accounts. Covers ~15/77 cases in the current DB.
3. **Synthetic fallback** — `[deleted:USERID]` when no source yields a name.

The enrichment pipeline sets `author_deleted=True`, `author_original_name` (preserving the resolved name), and `author_profile` when resolution succeeds.

**Retroactive repair:** `app/scripts/repair_deleted_artists.py` scans the `artists` table for `[deleted:UID]` names, applies the same resolution strategy, and merges resolved artists into existing real-name artists (reassigning images, deleting the synthetic artist first to avoid UNIQUE constraint on `civitai_user_id`). Run with `--dry-run` first. 62/77 remain unresolvable (no API data, no local Search Lab data).

### Deleted image tag fallback via Search Lab (August 2026)

**Problem:** When a CivitAI image is deleted, the API returns HTTP 404 (`"No image with id X"`) for both `fetch_basic_info()` and `fetch_generation_data()`. The enrichment pipeline (`fetch_civitai_image_data()`) returned `None`, losing all metadata (tags, prompt, models, author) for the image. Tags from deleted images never appeared in the gallery.

**Resolution strategy** (`_build_fallback_data_from_search_lab()` in `civitai_enrichment.py`):
When both API calls fail, fall back to the `CivitaiSearchImage` table (populated at scrape time). Build the enrichment dict from:
- `tags`: string list → `[{"name": str, "id": None}]` (id unavailable for Search Lab data)
- `prompt`: from `generation_prompt`
- `models`: from `generation_models`
- `author_name`, `author_profile`, `author_id`: from artist fields (skips `[deleted:` prefixed names for profile URL)
- `author_deleted = True`, `civitai_uuid`, `blurhash`

Returns `None` only when no Search Lab record exists.

**Taxonomy sync gap (also fixed):** The gallery `civitai_tags` field is built from `ImageConceptObservation` JOIN `AuthorityTerm` JOIN `TagAuthority(name='civitai')`, NOT from `json_metadata.civitai.tags` directly. During rescan, `_sync_image_tags_to_authority_terms()` extracted civitai tags but only upserted prompt and danbooru authorities — civitai tags were silently dropped. Fixed by adding a civitai authority upsert loop after the danbooru loop (around line 430 of `image_collection.py`). Observations are then created by `_hydrate_observations_from_tags()` which already iterates all sources including civitai.

**Key files:**
- `app/backend/civitai_enrichment.py` — `_build_fallback_data_from_search_lab()`, early return in `fetch_civitai_image_data()`
- `app/backend/image_collection.py` — civitai authority upsert in `_sync_image_tags_to_authority_terms()`
- `app/backend/main.py` — gallery `civitai_tags` query (lines ~12420-12438, ~19316)

**Gotcha:** Import convention inside `_try_resolve_deleted_username` must use `from database import SessionLocal` / `from models import CivitaiSearchImage` (not `backend.database` / `backend.models`). See `backend-startup.md` gotcha for details.

### Field-level merge in enrichment & banned vs deleted distinction (August 2026)

**Problem:** `_enrich_from_civitai_if_needed()` in `image_collection.py` did a wholesale replacement of `json_metadata.civitai`:
```python
merged_json_metadata["civitai"] = civitai_data  # overwrote everything
```
When fresh enrichment returned a sparser payload (e.g. Search Lab fallback for deleted images), it overwrote richer existing data — tags, prompts, models were lost. Re-enrichment could never *improve* a record, only *replace* it.

**Resolution — field-level merge (only fill missing fields):**
Non-user fields (tags, prompt, models, etc.) are filled only when MISSING in the existing record. User/author identity fields are ALWAYS overwritten from fresh data so deleted/banned status changes propagate.

Author fields that always update: `author_deleted`, `author_banned`, `author_name`, `author_id`, `author_profile`, `author_original_name`.

The sidecar JSON write (`processor.save_json_metadata`) was also updated to persist the merged dict, not the raw `civitai_data`.

**Banned vs deleted CivitAI accounts:**
The CivitAI `image.get` user object has no explicit "banned" field. The distinction is:
- **Deleted accounts**: `deletedAt` timestamp is set, `username` is null/None.
- **Banned accounts**: `deletedAt` is null, `username` is still present (full user data still served by `user.getById`).

**Database schema changes:**
- `Artist` model: added `civitai_user_banned` (Boolean, nullable) alongside existing `civitai_user_deleted`.
- `CivitaiUser` model: added `banned_at` (DateTime, nullable) alongside existing `deleted_at`.
- `civitai_enrichment.py`: `fetch_civitai_image_data()` now emits `author_banned` alongside `author_deleted` for both live API and Search Lab fallback paths.

**Artist record propagation:**
`_enrich_from_civitai_if_needed()` now calls `ImageProcessor.find_or_update_civitai_artist()` after the merge, passing `is_deleted` and `is_banned` flags. This updates the linked `Artist` record so the UI can filter/display banned and deleted accounts. The call is wrapped in a try/except to fail open.

`find_or_update_civitai_artist()` in `image_processor.py` accepts a new `is_banned: bool = False` parameter and sets `civitai_user_banned = True` on the Artist record.

**Key files:**
- `app/backend/image_collection.py` — `_enrich_from_civitai_if_needed()` field-level merge + artist update call
- `app/backend/civitai_enrichment.py` — `fetch_civitai_image_data()` emits `author_banned`; fallback also sets it
- `app/backend/image_processor.py` — `find_or_update_civitai_artist()` accepts `is_banned`
- `app/backend/models.py` — `Artist.civitai_user_banned`, `CivitaiUser.banned_at`

### CivitAI tRPC flat-array serialization for `image.getInfinite` (June 2026)

**Problem:** CivitAI's `image.getInfinite` tRPC endpoint now returns responses in a **column-oriented flat-array** format that is **double-encoded** — the entire flat array is stringified and placed inside `{"result":{"data":"<stringified-JSON>"}}`. Previous code expected the standard tRPC format `{"result":{"data":{"json":{"items":[...],"nextCursor":...}}}}` and could not parse the new format, causing "Post X has no images or could not be fetched" errors for post imports and empty results for collection item fetches.

**Scope:** Only `image.getInfinite` uses this format (post images and collection items). `post.getInfinite` still returns the standard dict format.

**Flat-array format structure:**
1. The raw response is `{"result":{"data":"<stringified JSON flat array>"}}`.
2. After `json.loads()`, you get a flat list where:
   - `[0]` = metadata dict whose `nextCursor` and `items` values may be **absolute positions** in the flat array
   - In the current format, `flat_array[metadata["items"]]` is the row-offset array and `flat_array[metadata["nextCursor"]]` is the cursor value
   - Older responses place the row-offset array directly at `[1]`; the decoder retains this fallback
   - Each column template (e.g. at position `[2]`) maps field names to **absolute positions** in the flat array. Example: `{"id": 3, "name": 4, "url": 5, ...}` — so `flat_array[3]` is the first item's `id` value
   - Nested dicts and lists within templates follow the same positional scheme recursively (nested dicts map keys → absolute positions; nested lists contain absolute positions)
3. **Deserialization algorithm:** For each row offset in `[1]`, get the template dict at `flat_array[row_offset]`, then recursively resolve each field's value by looking up `flat_array[position]`. Lists of positions are resolved element-by-element.

**Solution — `_deserialize_trpc_flat_array()` method:**
Added to `CivitaiAPI` in `civitai_api.py` (~line 1706). Accepts the raw response dict, extracts and `json.loads()` the stringified data, extracts `nextCursor` from `[0]`, and resolves all rows. Returns `{"items": [...], "nextCursor": <int|None>}` or `None` if the format doesn't match (graceful fallback for old-format responses).

Contains a nested `_resolve()` recursive function that handles:
- `int` → `flat_array[int]` (positional lookup, with depth limit of 20)
- `list` → recursively resolve each element
- `dict` → recursively resolve each value
- Everything else → return as-is (scalar values)

**Integration points:**
1. **`_make_request()`** (~line 467): Centralized fix. After extracting `result.data`, checks `isinstance(result_data, str)`. If so, calls `_deserialize_trpc_flat_array()` and uses the deserialized dict as `result_json`. This covers all `CivitaiAPI` methods (`fetch_post_images`, `fetch_collection_items`, `fetch_collection_posts`).
2. **`civitai.py._make_collection_request()`** (~line 107): `CivitaiPrivateScraper` calls `_make_raw_request()` directly (for strict error propagation), bypassing `_make_request()`. Added the same deserialization call here: after getting the raw response, calls `self.api._deserialize_trpc_flat_array(data)`. If it returns non-None, returns `(deserialized, deserialized.get("nextCursor"))`. Otherwise falls through to legacy `result.data.json.nextCursor` extraction.

### Superjson Date tuples in flat-array payloads (September 2026)

Timestamp fields (`createdAt`, `publishedAt`, `sortAt`) inside flat-array
responses arrive as superjson-tagged **two-element lists**:
`["Date", "2026-03-17T00:00:00.000Z"]` — not plain strings. `_resolve()` in
`_deserialize_trpc_flat_array()` unwraps them to the plain ISO string
(verified across all ~1600 archived `image.getInfinite` responses, `Date` is
the only tag in use). Without unwrapping, every consumer downstream breaks
silently: JS `new Date([...])` → "Invalid Date" in the Sync Lab items table,
and `civitai_models.py` `.replace("Z", ...)` on a list would raise
`AttributeError`. Frontend `fmtTimestamp()` also normalizes the tuple shape
defensively (for archived/cached payloads predating the fix) and returns `—`
instead of "Invalid Date" for unparseable values.

### Private collection empty responses (August 2026)

CivitAI can return HTTP 200 with an empty `image.getInfinite` item list when a
collection is private and the configured session belongs to an account without
access. Do not treat every empty list as a genuinely empty collection.

The import pipeline diagnoses empty responses with `collection.getById` and the
protected session validation endpoint. Token validity and collection-level
authorization are separate states: a valid token with `permissions.read ==
false` must report a wrong-account/private-access error, while an invalid token
must request session refresh. Post collections still redirect to the post
pipeline, and readable image collections with a positive reported item count
are classified as response/parser mismatches.

**Key files:**
- `app/src/atelierai/civitai/civitai_api.py` — `_deserialize_trpc_flat_array()`, `_make_request()` string branch
- `app/src/atelierai/civitai/civitai.py` — `_make_collection_request()` flat array deserialization path

### Search Lab NSFW visibility modes (September 2026)

The Search Lab toolbar has a dedicated NSFW visibility control (separate from
the advanced hide-filter bar), matching the main gallery. It is a **client-side
post-filter** — the NSFW mode is never sent in the search POST body so CivitAI
facet counts stay truthful, and tiles are hidden via the existing
`isHiddenByFilter` / `applyHideFilters` (`.tile-hidden` class) pipeline.

- **Modes:** Safe = levels {1,2}; Mature = {1,2,4}; Explicit =
  {1,2,4,8,16,32} (all). Per-level pills remain in the hide-filter bar for
  fine-grained control on top of the mode.
- **Persistence:** cookie `atelier_nsfw_visibility` (mode string) + URL param
  `?nsfw=safe|mature|explicit`. URL restores on load; Explicit omits the param
  (default). Legacy comma-separated level lists in the URL still parse.
- **CivitAI URL builder:** `browsingLevel` was removed from generated CivitAI
  URLs — CivitAI does not support that parameter; filtering stays local.
- **ui-kit gotcha:** `mountHoverChoiceControl` calls `setValue(nextValue)`
  BEFORE `onChange(nextValue)`. If `setValue` writes state directly, the
  subsequent `setNsfwVisibility` call early-returns (state already matches) and
  cookie/URL are never written. Delegate the full change flow inside
  `setValue` and omit `onChange`.

### NSFW level ingest, backfill & missing-data probe (September 2026)

Imports were silently dropping `nsfwLevel`. Fixed in two places:

1. `_ingest_prepared_civitai_import` sets `image.civitai_nsfw_level` from
   `extract_civitai_nsfw_level({"civitai": prepared.raw_basic_info})` when the
   column is still `None`.
2. The same ingest writes `nsfwLevel` (via `setdefault`) into the merged
   `json_metadata["civitai"]` payload so `_payload_has_nsfw_level` treats the
   image as complete and the backfill job skips it without re-querying CivitAI.

**Backfill UI:** Search Lab shows a "Backfill NSFW metadata" button only when
`GET /api/images?missing_data=civitai_nsfw_level&limit=1` returns rows (plain
JSON array — not `{images: [...]}`). Clicking POSTs to
`/api/civitai/backfill/nsfw-levels` (202 + task id) and polls
`GET /api/tasks/{id}` every 2s. On completion it re-probes and hides itself
when nothing remains missing; images with no remote NSFW data keep the button
visible (that is correct — some images genuinely lack the field remotely).

**Key files:**
- `app/backend/main.py` — `_build_missing_data_condition`
  (`civitai_nsfw_level` keys), `_ingest_prepared_civitai_import`,
  `_payload_has_nsfw_level`
- `app/frontend/js/search-lab.js` — `setNsfwVisibility`,
  `mountNsfwVisibilityControl`, `runNsfwMetadataBackfill`,
  `checkNsfwBackfillNeed`

### Search Lab keyboard rating + artist-block rollback (September 2026)

**z/x/c multi-select rating.** `rateSelectedImages(rating)` in
`search-lab.js` applies a rating to every selected image when
`state.selectedIndices.size > 1` (calling `rateImageAt(i, rating)` per
selected index), then clears the selection and advances. Single-selection
falls through to `rateImage(rating)` + advance. `rateImage` is now a thin
wrapper over `rateImageAt(state.selectedHitIndex, rating)`; keep the
optimistic-update/POST/rollback body in `rateImageAt` only.

**Artist-block rollback.** `blockArtistFromFullscreen` uses a `.then/.catch`
pair: `.then` counts only rating *transitions*
(`(prevRatings.get(id) || null) !== 'discard'`), updates the summary, calls
`renderStatsIfStillCurrentArtist()`, then `checkAutoLoadIfAllHidden()` (only
after backend confirmation), then `fetchArtistSummary()`. `.catch` restores
all previous ratings (set-or-delete per entry), re-runs `applyHideFilters()`,
and surfaces an error status. Never call `checkAutoLoadIfAllHidden()`
speculatively — that caused the auto-load race.

**Grid selection model.** Multi-select state is `state.selectedIndices: Set`;
tiles are `button.tile[data-index]` inside `#gallery-grid`. A delegated click
handler (~line 1512) implements shift-click range (`selectRange(idx,
{additive})`) and ctrl/cmd-click toggle (`toggleSelection(idx)`).

### CDN 503 root-cause: webm origin timeouts, NOT rate flagging (2026-09-08)

Definitive correlation from transport logs (1,114 CDN requests, one day):
- images: 1/965 failed (0.1%) · .mp4: 0/92 (0.0%) · **.webm: 24/54 (44.4%)**
- Two 503 modes on the SAME assets: **slow** (~35.1s elapsed = origin-side
  on-demand transcode timeout) and **instant** (~0.2s = edge-cached failure
  for the same asset/variant). 8 of 11 failing UUIDs later succeeded —
  per-asset, not per-client.
- Browser sessions succeed on the same assets because the web player uses
  `transcode=true,original=true,quality=90` (pre-warmed edge variant);
  our URLs omitted `quality=90`, landing on the on-demand transcode path.

Mitigations:
1. `_build_civitai_media_url()` now appends `quality=90` to video transcode
   segments (matches the browser's URL form). Image URLs unchanged.
2. Flag-streak exclusion: 503s with attempt elapsed >
   `_CDN_SLOW_503_ELAPSED_SECONDS` (default 10s, env
   `CIVITAI_CDN_SLOW_503_SECONDS`) no longer increment
   `_CDN_503_CONSECUTIVE` — slow origin timeouts are asset-specific and
   must not trip the client-holding cooldown. Instant edge rejections
   still count (true flag signal).

The earlier "sticky flag" theory (2026-09-06) partially stands — instant
503 clusters at low rpm exist — but the dominant trigger in sync workloads
was webm origin misses masquerading as flag trips.

### Video download hardening additions (2026-09-08, later)

- **Stale edge 503s**: Cloudflare serves CACHED 503s (`cf-cache-status:
  STALE`, `age` >> `max-age=120`) long after origin recovery — retries hit
  the poisoned cache entry, not the origin. Cache-busting (`?nocache=`)
  forces a MISS but the origin may still 503 while transcoding.
- **B2 transcode mirror**: `image-b2.civitai.com/file/civitai-media-cache/
  {uuid}/.mp4_hm` serves the pre-transcoded mp4 (200 HIT) even while the
  main CDN is stuck serving stale 503s. This is what the CivitAI web player
  falls back to. Added to `_build_civitai_video_candidate_urls()` AFTER the
  main-CDN transcode URL, BEFORE the B2 `/original` path (which 404s for
  assets that only exist as transcodes).
- **Retry-After cap for slow 503s**: origin transcode-timeout 503s carry
  `Retry-After: 120` meant for rate-limited clients — honoring it produced
  490s downloads that were ~130s of real work (3×120s sleeps between
  probes while the origin kept transcoding regardless). `_retry_delay()`
  now caps the wait at `_CDN_SLOW_503_RETRY_CAP_SECONDS` (default 15s, env
  `CIVITAI_CDN_SLOW_503_RETRY_CAP`) when the 503 was slow (>10s elapsed).
  Instant edge 503s still honor the full header.
- Gotcha: uvicorn --reload graceful shutdown can wedge when browser tabs
  hold open SSE connections; the old worker ignores SIGTERM. SIGKILL on
  the WORKER pid only (never the supervisor on port 8000) forces the
  supervisor to respawn a fresh worker immediately.

### 503 console diagnostics (2026-09-08)

`CivitaiHttpClient.request()` / `download_to_temp()` accept an optional
`log_label` threaded through the request envelope; the 🚫 503 and 🚩 flag
prints append it when present. `_download_civitai_image()` builds it as
`(civitai.red/images/{id}, {mime}, ~{size})` so failures can be correlated
to the exact asset in a browser. No-op for callers that don't pass it.

### best_effort request semantics (2026-09-08)

`CivitaiHttpClient.request(..., best_effort=True)` marks enrichment fetches
(preview variants, optional lookups). Their 503s still log (with
`log_label`) and count toward per-endpoint stats, but do NOT increment
`_CDN_503_CONSECUTIVE` — best-effort traffic must never trip the CDN flag
cooldown that pauses PRIMARY downloads. The preview-variant fetch in
`_preserve_civitai_source_variant()` sets it and now also builds a
`(preview variant — civitai.red/images/{id}, {mime}, ~{size})` log label.
Incident: a preview fetch's 3× 503 tripped the flag mid-ingest and held
CDN downloads for 300s while the primary import had already succeeded.
