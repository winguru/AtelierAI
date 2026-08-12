# Backend Startup & Environment

## Design Decisions

### Server requires cd app/ and specific PYTHONPATH
The backend must be started with `cwd=app/` because:
1. `main.py` uses top-level imports like `from database import ...` which requires `app/backend/` on PYTHONPATH
2. `StaticFiles` mounts use relative `directory='frontend'` which resolves from cwd

Full incantation: `cd app/ && PYTHONPATH='app/:app/backend/:app/src/:app/dev' python3 -m uvicorn backend.main:app --reload --reload-dir app/`

Or from repo root: `./start.sh`

### PYTHONPATH is project-scoped
This repo uses project-scoped VS Code settings for Python paths:
- `.vscode/settings.json` configures analysis paths and local terminal PYTHONPATH
- `.vscode/.env` defines project-local PYTHONPATH for debug/test tooling
- `.devcontainer/devcontainer.json` sets container PYTHONPATH

Do not rely on global shell/profile PYTHONPATH for this project.

### Database configuration
Runtime DB configuration comes from `atelierai.config` (backed by `app/backend/config.py`). `IMAGE_LIBRARY_PATH` and DB directories must be writable. Sidecar metadata is merged into `/images` responses.

## Key Files
- `start.sh` — root launcher
- `app/backend/config.py` — runtime configuration
- `app/backend/database.py` — DB connection setup
- `app/backend/main.py` — FastAPI application entry point
- `.vscode/settings.json` — analysis paths and terminal PYTHONPATH

## Gotchas
- Starting without `cd app/` causes `ModuleNotFoundError` or `RuntimeError: Directory 'frontend' does not exist`
- `main.py` is very large (~12000 lines) — contains both legacy inline routes and newer router-based routes
- Older SQLite DBs can miss `image_concept_observations.is_present`; startup migrations now add `is_present`/`is_curated` additively before ingestion paths write observations.
- **Import convention: always `from database import ...` / `from models import ...`, never `from backend.database import ...` / `from backend.models import ...`.** The app is launched with `app/backend` on PYTHONPATH and `cwd=app/`, so `database` and `backend.database` resolve to **two different module objects** in `sys.modules`. Each creates its own `Base = declarative_base()` and `MetaData`. If a lazy import inside a module (e.g. `civitai_enrichment._try_resolve_deleted_username`) uses `backend.database` while `models.py` uses `database`, SQLAlchemy sees two conflicting table definitions and raises `"Table already defined for this MetaData instance"` or `"Multiple classes found for path 'ImageModel'"` at query time. The error is swallowed by fail-open exception handlers, so the symptom is silently returning `None`.
