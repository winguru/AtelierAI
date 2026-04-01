# AtelierAI
AI Assisted curation of multimodal datasets for analysis and training

AtelierAI is a platform designed to assist in the curation of multimodal datasets using AI-powered tools. It provides features for taxonomic backfilling, concept normalization, and authoritative term management to help build structured, high-quality datasets for analysis and training.

The goals of this project are to:
- Collect raw multimodal data from diverse sources
- Collate and integrate heterogeneous datasets
- Conserve and preserve data integrity and provenance
- Curate with authoritative taxonomy and standardized concepts

## Running the app

From the app directory:

```bash
cd app
pip install -e app/src # If running for the first time in a dev environment
./start.sh
```

The development server startup script supports environment variables for HTTP access-log control:

- `ATELIER_SUPPRESS_STATUS_GET_LOGS=1`
	Suppresses noisy polling access logs such as `GET /tasks/?limit=40` while keeping other access logs enabled.
- `ATELIER_DISABLE_ACCESS_LOG=1`
	Disables all Uvicorn HTTP access logs.

Examples:

```bash
cd app
ATELIER_SUPPRESS_STATUS_GET_LOGS=1 ./start.sh
```

```bash
cd app
ATELIER_DISABLE_ACCESS_LOG=1 ./start.sh
```

These can also be provided through your `.env` if that is how you manage local runtime settings.

## Running tests

From the app directory:

```bash
cd app
../.venv/bin/python -m pytest -q tests/test_a1111_bridge_regression.py
```

This regression suite covers the A1111 Bridge fallback Comfy workflow synthesis path, including the non-numeric link-source case that previously raised `UnboundLocalError`.

## Generation Lab Comfy Exports

AtelierAI supports Comfy-compatible generation exports from Generation Lab in both UI-friendly and integration-friendly forms.

- End-user guide (Generation Lab workflow, button behavior, compare-mode caveats):
	- `app/docs/features/GENERATION_LAB_COMFY_EXPORTS.md`
- Developer API guide (raw + wrapped endpoint contracts and query params):
	- `app/docs/api/ATELIER_GENERATION_LAB_API.md`

Highlights:
- Generation Lab can export import-ready Comfy workflow JSON and Comfy API prompt JSON.
- Backend supports wrapped inspection exports and dedicated raw JSON endpoints for external integrations.
- Local model availability validation can be sourced from ComfyUI LoRA Manager endpoints.

## CivitAI getInfinite crawler

Use `scripts/crawl_getinfinite.py` to iterate `image.getInfinite` pages and save raw page responses as formatted JSON.

Current behavior:
- Saves one `page_XXXXXX.json` file per response page.
- Saves `summary.json` with crawl metadata and payload template.
- Saves `crawl_state.json` after each written page so interrupted runs can resume.
- Does not fetch per-image detail endpoints.

From the app directory:

```bash
cd app

# Main gallery (images), defaults: Day + Newest
python scripts/crawl_getinfinite.py

# Main gallery videos, weekly, top reactions
python scripts/crawl_getinfinite.py \
	--url https://civitai.com/videos \
	--period week \
	--order "Most Reactions"

# User gallery (images)
python scripts/crawl_getinfinite.py \
	--url https://civitai.com/user/TheDreadMerchant/images

# Collection gallery
python scripts/crawl_getinfinite.py \
	--url https://civitai.com/collections/12176069

# Model gallery with modelVersion filter
python scripts/crawl_getinfinite.py \
	--url "https://civitai.com/models/871004?modelVersionId=1498821"

# Resume an interrupted crawl in an existing output directory
python scripts/crawl_getinfinite.py \
	--url https://civitai.com/videos \
	--period week \
	--order "Most Reactions" \
	--output-dir /tmp/civitai_videos_week \
	--resume
```

CLI flags:
- `--url`: starting CivitAI gallery URL. Default: `https://civitai.com/images`
- `--period`: `day`, `week`, `month`, `year`, or `alltime`
- `--order`: one of the validated `image.getInfinite` sort values
- `--max-pages`: stop after `N` pages instead of crawling to exhaustion
- `--delay-ms`: sleep between page requests
- `--output-dir`: custom destination for `page_*.json` and `summary.json`
- `--resume`: continue an interrupted crawl from an existing `--output-dir`
- `--no-empty-results`: when a crawl returns zero total records, skip creating output directory/files

Reliability behavior:
- The crawler now retries empty first-page responses (up to a small fixed retry budget) before treating them as real empty output.
- This helps smooth over intermittent API/index windows where `items=[]` and `nextCursor=null` can be returned transiently.
- When `--resume` is used, the crawler restores its next cursor and page counters from `crawl_state.json`, or reconstructs them from existing `page_*.json` files if needed.
- Reusing an output directory without `--resume` is rejected to avoid silently overwriting prior crawl pages.

Example with explicit flags:

```bash
python scripts/crawl_getinfinite.py \
	--url https://civitai.com/videos \
	--period week \
	--order "Most Reactions" \
	--max-pages 10 \
	--delay-ms 150 \
	--output-dir /tmp/civitai_videos_week
```

Supported URL patterns:
- `https://civitai.com/images`
- `https://civitai.com/videos`
- `https://civitai.com/user/<username>/images`
- `https://civitai.com/user/<username>/videos`
- `https://civitai.com/collections/<collection_id>`
- `https://civitai.com/models/<model_id>?modelVersionId=<model_version_id>`

Supported order values (currently validated by live API responses):
- `Newest`
- `Oldest`
- `Most Reactions`
- `Most Comments`
- `Most Collected`
- `Random`

### Planned enhancements

This crawler is intentionally scoped to raw page capture first. It is expected to evolve to support:
- Optional extraction pipelines (for selected fields and derived analytics).
- Normalized tag exports (for example: `tags` + `image_tags` CSV/JSONL outputs) for easy downstream analytics loads.
- Hydration of `modelVersionId` when omitted in page payloads (for example via `modelVersionIds` heuristics and optional lookup enrichment).
- Optional related endpoint enrichment (post/image/model lookups) behind explicit flags.
- Schema-versioned output metadata for backward-compatible downstream parsing.