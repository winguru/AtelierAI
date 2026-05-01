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

## Key Files
- `app/src/atelierai/civitai/civitai_auth.py` — `_launch_chrome_cdp()`, `_launch_context()`, `_terminate_chrome()`
- `app/backend/services/civitai_service.py` — CivitAI API client
- `app/backend/civitai_enrichment.py` — enrichment pipeline
- `app/backend/routers/civitai/` — CivitAI-related API endpoints

## Gotchas
- Chrome CDP port must be available; if already in use, auth fails
- CivitAI API rate limits apply — batch operations should include delays
