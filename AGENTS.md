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

The root launcher resolves the application under `app/`, starts `uvicorn backend.main:app` on port `8000`, and uses **WatchFiles** to auto-reload on any code change. This process is long-running and self-managing — **do not kill or restart it** during normal development.

### Port 8000 is reserved for `start.sh`

**Never attempt to kill the process on port 8000.** The auto-reload watcher handles picking up code changes automatically. If you need to run the application manually (to debug a runtime error, test a specific startup sequence, or capture stdout directly), use a **different port**:

```bash
cd /Users/winguru/Sources/AtelierAI/app
PYTHONPATH=app/src:app/backend python -m uvicorn backend.main:app --port 8001
```

When you are done testing on the alternate port, stop that server directly. The `start.sh` process on port 8000 remains unaffected.

## Testing In The Shell

When running Python commands in a terminal (tests, one-off scripts, import checks), you **must** set the correct working directory and `PYTHONPATH`. The project structure requires two path roots:

| What you're running | Working directory | PYTHONPATH |
|---|---|---|
| Backend code / API | `app/` | `app/src:app/backend` |
| Tests (`app/tests/`) | `app/` | `app/src:app/backend` |
| Scripts (`app/scripts/`) | repo root or `app/` | `app/src:app/backend` |
| CivitAI package standalone | anywhere | `app/src` |

### Quick reference commands

All commands assume the venv is activated:
```bash
source /Users/winguru/Sources/AtelierAI/.venv/bin/activate
```

**Test a Python import resolves correctly:**
```bash
cd /Users/winguru/Sources/AtelierAI/app
PYTHONPATH=app/src:app/backend python -c "from backend.main import app; print('OK')"
```

**Run a test file:**
```bash
cd /Users/winguru/Sources/AtelierAI/app
PYTHONPATH=app/src:app/backend python -m pytest tests/test_file.py -v
```

**Run ruff lint check:**
```bash
cd /Users/winguru/Sources/AtelierAI
ruff check app/backend app/src app/scripts
```

### Common pitfalls
- **Wrong cwd**: Running from repo root when code expects `app/` (or vice versa). Imports like `from backend.main import ...` only resolve when cwd is `app/`.
- **Missing PYTHONPATH**: VS Code terminals set `PYTHONPATH` via `.vscode/settings.json`, but a raw shell session does not. Always set it explicitly.
- **Forgetting to activate venv**: The project's dependencies (fastapi, sqlalchemy, etc.) live in `.venv/`, not the system Python.

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
- `TODO.md`: informal project-wide todos and notes
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

## MCP (Model Context Protocol) Considerations
- At the start of the session, the agent should run this instruction to load the MCP context:
```text
Activate the current dir as project using serena
```
- This will load the project context, including file contents and memory documents, into the agent's working memory, allowing it to reference design decisions and code history when making edits or suggestions.