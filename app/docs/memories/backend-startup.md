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

### Dual lifespan gotcha
`app/backend/core/lifespan.py` is a **dead duplicate** — the *active* lifespan is defined inline in `app/backend/main.py` (~line 15483) and passed directly to `FastAPI(lifespan=...)`. New startup migration calls must be wired into the inline lifespan in `main.py`, not into `core/lifespan.py` (leave that file untouched).

### civitai_search_image_links.search_id nullable rebuild
`civitai_search_image_links.search_id` was `NOT NULL` without a default, causing HTTP 500s on `/artist-discard` when callers omitted `search_id`. Fixed by a table-rebuild migration (`_ensure_search_link_search_id_nullable()`): SQLite cannot `ALTER COLUMN`, so the migration recreates the table with `search_id NULL`, copies data, and re-creates indexes. Table rebuilds are the only way to relax a column constraint in SQLite.

### Artist-preference counter rebuild (dedupe + recompute)
`rebuild_artist_preference_counters()` fixes inflated `civitai_artist_preferences` counters caused by legacy double-counting (multiple links per image/artist each incrementing counters). Strategy:
1. Python-side dedupe of link rows (latest link wins).
2. Recompute counters with UPDATE subqueries that pin to the latest link: `csil.id = (SELECT csil2.id FROM ... ORDER BY csil2.created_at DESC, csil2.id DESC LIMIT 1)`.
3. Delete zero-counter rows for unblocked artists.
Idempotent — safe on every startup.

### autoflush=False requires explicit flush
`backend/database.py` sessions use `autoflush=False`. After `db.add(obj)`, later statements in the same transaction (e.g. subqueries over the same table) will **not** see the new row until `db.flush()` is called. Always `db.flush()` after `db.add()` before running dependent queries.

### NULL keys evade UNIQUE constraints in SQLite
In SQLite, `NULL != NULL`, so a `UNIQUE(artist_id)` style constraint (`sqlite_autoindex_...`) does **not** dedupe rows where the key is NULL. Duplicate `artist_id=NULL` preference rows can accumulate silently. The counter-rebuild migration handles this with explicit Python-side dedupe before insert.

### Transition-only counter increments
Artist preference counters (`skips`/`discards`) are incremented **only on rating transitions** (null→skip, skip→discard, etc.), not on every POST. Both sides enforce this:
- Backend: `_update_artist_preference(..., previous_rating)` in `app/backend/routers/civitai/search.py` adjusts counters only when `previous_rating != new_rating`.
- Frontend: `blockArtistFromFullscreen` in `search-lab.js` computes `newlyDiscarded = imageIds.filter(id => (prevRatings.get(id) || null) !== 'discard')` and sends only the transition count.
When rolling back a failed artist-block, restore previous ratings and re-apply hide filters; always call `checkAutoLoadIfAllHidden()` **after** backend confirmation, never speculatively.

### Test-suite pitfalls
- `tests/test_stealth.py` calls `exit(1)` at import → pytest INTERNALERROR. Exclude with `--ignore=tests/test_stealth.py --ignore=tests/test_stealth_fixed.py --ignore=tests/test_stealth_manual.py`.
- 16 test files fail collection with AttributeErrors (`CivitaiPrivateScraper`, `legacy.civitai_trpc_v3`) — **pre-existing**, verified identical via git-stash baseline diff.
- `tests/test_image_get.py` errors on a missing `endpoint_name` fixture — pre-existing, untouched.
