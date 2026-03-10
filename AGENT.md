# AGENT.md

## Project Overview
AtelierAI is a FastAPI-based application for curating image datasets and enriching metadata (including CivitAI lookups) for analysis and training workflows.

Primary code areas:
- `app/backend`: API, DB models, metadata processing, image collection workflows
- `app/src/atelierai`: shared Python package (CivitAI integration and reusable modules)
- `app/frontend`: static web UI
- `app/tests`, `app/dev`, `app/scripts`: tests, debugging tools, and utility scripts

## Python Package And Imports
Use `atelierai` as the canonical import namespace.
- Preferred: `from atelierai.civitai import ...`
- Avoid legacy imports like `from src...`, `from civitai...`, or direct path hacks.

Editable install (recommended for development):
```bash
source .venv/bin/activate
pip install -e app/src
```

With editable mode, source changes under `app/src/atelierai` are immediately available without reinstalling.

## Environment And Paths
This repository uses project-scoped VS Code settings for Python paths.
- `.vscode/settings.json` configures analysis paths and local terminal `PYTHONPATH`
- `.vscode/.env` defines project-local `PYTHONPATH` for debug/test tooling
- `.devcontainer/devcontainer.json` sets container `PYTHONPATH`

Do not rely on global shell/profile `PYTHONPATH` for this project.

## Running The App
From repo root:
```bash
cd app
./start.sh
```

The server is started by `uvicorn backend.main:app` on port `8000`.

## Database And Storage Notes
- Runtime DB configuration comes from `atelierai.config` (backed by `app/backend/config.py`).
- `IMAGE_LIBRARY_PATH` and DB directories are expected to be writable.
- Sidecar metadata is used and merged for `/images` responses.

## CivitAI Integration Notes
- CivitAI modules live under `app/src/atelierai/civitai`.
- Enrichment should fail open (warn and continue) so uploads/scans are not blocked.
- Handle partial/null API payloads defensively (`None` checks before nested `.get`).

## Testing And Validation
Common checks after edits:
- Run targeted tests under `app/tests`
- Confirm backend starts with `./start.sh`
- Validate key imports resolve in VS Code/Pylance

## Editing Guidelines
- Keep changes localized and avoid unrelated refactors.
- Preserve backward-compatible behavior unless intentionally changing it.
- Prefer clear, explicit imports and avoid dynamic path manipulation.
- Update this file when project structure or conventions change.
