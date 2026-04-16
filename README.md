# AtelierAI
AI Assisted curation of multimodal datasets for analysis and training

AtelierAI is a platform designed to assist in the curation of multimodal datasets using AI-powered tools. It provides features for taxonomic backfilling, concept normalization, and authoritative term management to help build structured, high-quality datasets for analysis and training.

The goals of this project are to:
- Collect raw multimodal data from diverse sources
- Collate and integrate heterogeneous datasets
- Conserve and preserve data integrity and provenance
- Curate with authoritative taxonomy and standardized concepts

## Running the app

From the repo root:

```bash
./start.sh
```

The root launcher always targets the application under `app/`, so the same command works locally, in a VS Code dev container, and in the runtime Docker image.

On local machines, the preferred workflow is still to activate a virtual environment first. In Docker and VS Code dev containers, a separate venv is not required; the launcher prefers the container's system Python.

`./start.sh` can also bootstrap Python dependencies when needed:

- It checks for core runtime packages from `app/requirements.txt`.
- If they are missing and `ATELIER_AUTO_INSTALL_DEPS=1` (default), it runs `pip install -r app/requirements.txt`.
- If the `atelierai` package is not installed in editable mode, and `ATELIER_ENSURE_EDITABLE_SRC=1` (default), it runs `pip install -e app/src`.

That makes fresh local clones, dev containers, and runtime containers more forgiving without requiring a manually prepared Python environment first.

The development server startup script supports environment variables for HTTP access-log control:

- `ATELIER_SUPPRESS_STATUS_GET_LOGS=1`
	Suppresses noisy polling access logs such as `GET /tasks/?limit=40` while keeping other access logs enabled.
- `ATELIER_DISABLE_ACCESS_LOG=1`
	Disables all Uvicorn HTTP access logs.

Examples:

```bash
ATELIER_SUPPRESS_STATUS_GET_LOGS=1 ./start.sh
```

```bash
ATELIER_DISABLE_ACCESS_LOG=1 ./start.sh
```

Useful startup variables:

- `ATELIER_HOST`
	Host interface to bind. Default: `0.0.0.0`.
- `ATELIER_PORT`
	Port to bind. Default: `8000`.
- `ATELIER_RELOAD`
	Enables autoreload when truthy. Default: `1` for local/dev usage.
- `ATELIER_APP_ROOT`
	Overrides the application root path if needed. Default: `<repo>/app`.
- `ATELIER_AUTO_INSTALL_DEPS`
	When truthy, installs `app/requirements.txt` automatically if core runtime dependencies are missing. Default: `1`.
- `ATELIER_ENSURE_EDITABLE_SRC`
	When truthy, installs `app/src` in editable mode if the `atelierai` package is not already available from there. Default: `1`.

## Docker and dev containers

This repository supports both a development container workflow and a complete runtime container workflow through one Compose file.

### VS Code dev container

- Open the repo in VS Code.
- Reopen in container.
- The dev container uses the `atelier-dev` Compose service and mounts the full repo at `/workspace`.
- Inside the container, start the app from repo root:

```bash
./start.sh
```

### Docker development service

Use the dev profile if you want an interactive container with bind mounts and live reload:

```bash
docker compose --profile dev up -d atelier-dev
docker compose exec atelier-dev bash
```

Then run from inside the container:

```bash
cd /workspace
./start.sh
```

### Complete runtime container

Use the runtime profile if you want a self-contained Docker environment with persistent named-volume storage:

```bash
docker compose --profile runtime up --build atelier-runtime
```

The runtime service stores the SQLite database, image library, and generated resources under a persistent Docker volume mounted at `/var/lib/atelierai`.

These can also be provided through your `.env` if that is how you manage local runtime settings.

## Makefile and justfile

This repository now includes both a [Makefile](/Users/winguru/Sources/AtelierAI/Makefile) and a [justfile](/Users/winguru/Sources/AtelierAI/justfile) with the same core commands.

Why both:

- `make` is the more universal, established choice on Linux and macOS.
- `just` usually has a cleaner UX and more predictable command behavior.
- Windows support is usually better with `just` than with `make`, because `make` is often not installed by default on Windows machines.

Practical recommendation:

- On macOS and Linux, `make` is the safest default if you already have it.
- On Windows, `just` is usually the better choice.
- In docs and automation, either is fine here because both expose the same command set.

Examples:

```bash
make help
make run
make bootstrap-local
make smoke
make lint
make typecheck-civitai
make doctor
make build-images
make test-all
make compose-dev-up
```

```bash
just
just run
just bootstrap-local
just smoke
just lint
just typecheck-civitai
just doctor
just build-images
just test-all
just compose-runtime-build
```

Additional convenience tasks:

- `bootstrap-local`
	Host-only setup helper. Creates `.venv`, upgrades `pip`, installs `app/requirements.txt`, and installs `app/src` in editable mode.
- `smoke`
	Runs lightweight launcher and Compose validation checks without starting the full app server.
- `lint`
	Runs available Python quality checks against core production code under `app/backend` and `app/src`, preferring tools from `.venv` when present and skipping tools that are not installed.
- `typecheck-civitai`
	Runs stricter mypy checks (`--check-untyped-defs`) for `app/src/atelierai/civitai`.
- `doctor`
	Prints Python, pip, Docker, Compose, and optional media-tool availability for quick environment diagnosis.
- `build-images`
	Builds both the dev and runtime Docker images.
- `test-all`
	Runs the full pytest suite under `app/tests`.

## Healthcheck

The API now exposes a lightweight health endpoint at `/healthz`.

- It verifies the FastAPI process is alive.
- It verifies the configured database connection can execute `SELECT 1`.
- The runtime Docker image and runtime Compose service both use this endpoint for container healthchecks.

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
	--url https://civitai.red/videos \
	--period week \
	--order "Most Reactions"

# User gallery (images)
python scripts/crawl_getinfinite.py \
	--url https://civitai.red/user/TheDreadMerchant/images

# Collection gallery
python scripts/crawl_getinfinite.py \
	--url https://civitai.red/collections/12176069

# Model gallery with modelVersion filter
python scripts/crawl_getinfinite.py \
	--url "https://civitai.red/models/871004?modelVersionId=1498821"

# Resume an interrupted crawl in an existing output directory
python scripts/crawl_getinfinite.py \
	--url https://civitai.red/videos \
	--period week \
	--order "Most Reactions" \
	--output-dir /tmp/civitai_videos_week \
	--resume
```

CLI flags:
- `--url`: starting CivitAI gallery URL. Default: `https://civitai.red/images`
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
	--url https://civitai.red/videos \
	--period week \
	--order "Most Reactions" \
	--max-pages 10 \
	--delay-ms 150 \
	--output-dir /tmp/civitai_videos_week
```

Supported URL patterns:
- `https://civitai.red/images`
- `https://civitai.red/videos`
- `https://civitai.red/user/<username>/images`
- `https://civitai.red/user/<username>/videos`
- `https://civitai.red/collections/<collection_id>`
- `https://civitai.red/models/<model_id>?modelVersionId=<model_version_id>`

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