# AGENTS.md

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
./start.sh
```

The root launcher resolves the application under `app/` and starts `uvicorn backend.main:app` on port `8000`.

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

## Code Review
- See `.github/instructions/code-review.instructions.md` for the full review checklist (clarity, consistency, naming, performance, security, testing, error handling, UI, documentation).

## Documentation
- `app/docs/api`: AtelierAI and 3rd-party API documentation
- `app/docs/archive`: historical/obsolete documentation
- `app/docs/auth`: 3rd-party authentication reference and documenation
- `app/docs/coding-notes`: coding notes, fixes, todo's, updates, and refactoring
- `app/docs/features`: current functionality
- `app/docs/guides`: application and web interface usage guides
- `app/docs/memories`: coding agents' memories, learnings, and notes from working on the project
- `app/docs/plans`: planned features and/or code revisions
- `app/docs/research`: research notes, references, and findings related to the project
- `app/docs/roadmap`: high-level project roadmap and milestones

## Agent Memory System

Source files reference memory documents that record design decisions, constraints, gotchas, and change history. When editing a file, read the referenced memory documents first to understand context.

### Memory Documents
Memory documents live in `app/docs/memories/` and are organized by domain (not by file). Multiple files can share the same memory doc, and a single file can reference multiple memory docs.

### File-Level Memory References
Each source file that has associated design decisions or coding history should include a memory header comment near the top of the file:

```python
# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/<domain>.md
# ──────────────────────────────────────────────────────────────────────────────
```

For files that reference multiple memory docs:

```python
# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/<domain-a>.md
# 📄 docs: app/docs/memories/<domain-b>.md
# ──────────────────────────────────────────────────────────────────────────────
```

### Rules
- **Always read** the referenced memory docs before editing a file.
- **Update** the relevant memory doc when making design-level changes or fixing non-obvious bugs.
- **Root concepts must never be created automatically** — this is a hard constraint.
- **Tag imports never create concepts** — they create `authority_terms` with `concept_id=None` when no matching concept exists.
- Memory docs should be concise: decisions, constraints, gotchas — not step-by-step change logs.
- When a memory doc grows too large, split it by sub-domain.

## Editing Guidelines
- Keep changes localized and avoid unrelated refactors.
- Preserve backward-compatible behavior unless intentionally changing it.
- Prefer clear, explicit imports and avoid dynamic path manipulation.
- Update this file when project structure or conventions change.
- Always run ruff and/or pylint to check for warnings and errors after any code changes
