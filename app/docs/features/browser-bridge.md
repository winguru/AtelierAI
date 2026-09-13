# Browser Bridge (CDP Sidecar)

Optional egress lane that routes CivitAI tRPC requests through a **real,
headed Chromium** instead of the Python HTTP client. The browser carries
authentic TLS/HTTP2 fingerprints and a live profile cookie jar, so traffic
looks like organic page activity.

This is the "Tier B" capture surface discussed in the architecture notes —
implemented as a **sidecar container we own and drive via CDP**, not a MITM
proxy over the user's daily-driver browser. No local CA, no cert forging,
no fingerprint risk to the user's main session.

---

## Architecture

```mermaid
flowchart LR
    U[You, via noVNC tab] -->|drive by hand: OAuth once, browse| CH
    subgraph Sidecar["chrome-sidecar container"]
        CH[Chromium + Xvfb]
        NV[noVNC / websockify :6080]
        CH --- NV
    end
    subgraph AAI["AtelierAI backend"]
        BR[CivitaiBrowserBridge<br/>connect_over_cdp]
        API[/api/browser-bridge/*|]
    end
    API --> BR
    BR -->|CDP :9222| CH
    CH --> C[civitai.red / image.civitai.com]
    CH -.->|page-context fetch| C
```

- **Sidecar** (`docker/chrome-sidecar/`): Debian trixie + `chromium` package
  (multi-arch: works on amd64 and arm64 — google-chrome-stable has no Linux
  ARM64 builds), Xvfb display, x11vnc, noVNC. CDP on `:9222` (compose-internal),
  noVNC UI on `:6080` (host loopback only). Runs Chromium with `--no-sandbox`
  (root-in-container; compensating controls are the internal-only port
  bindings and the isolated container). The entrypoint clears stale
  `/tmp/.X99-lock` files so `restart: unless-stopped` always recovers.
- **Bridge** (`app/src/atelierai/civitai/browser_bridge.py`): process-wide
  singleton owning the CDP connection. `fetch()` runs `fetch()` **inside a
  civitai tab's page context** — same-origin, profile cookies, Chromium's TLS
  stack. Fail-open by design: sidecar down → structured `unavailable`
  response, callers fall back to the direct lane.
- **Router** (`app/backend/routers/browser_bridge.py`): status / connect /
  disconnect / navigate / fetch / config endpoints under `/api/browser-bridge`.
- **UI** (`app/frontend/browser-bridge-lab.html`): status dashboard, fetch
  playground, navigate control.

## Running it

```bash
# From the repo root on the host (docker access required):
docker compose --profile browser up -d chrome-sidecar

# Watch it boot:
docker logs -f chrome-sidecar
# → "CDP ready on :9222 ..."
#
# NOTE: after pulling this change, rebuild so the fixed entrypoint is baked in:
#   docker compose --profile browser up -d --build chrome-sidecar

# Open the browser UI (noVNC) from this machine OR the LAN:
#   http://<host-ip>:6080/vnc.html
# Optional transparent auth: append ?password=<pw> to the URL (noVNC
# supports the query param) or just bookmark the full link.
```

Then in AtelierAI (devcontainer or any backend run):

- Backend on the compose network discovers the sidecar at
  `http://chrome-sidecar:9222` automatically (default).
- Backend on the host (outside compose): set
  `BROWSER_BRIDGE_CDP_URL=http://127.0.0.1:9222` in `app/.env`.
- Check connectivity: open `/frontend/browser-bridge-lab.html` → **Connect**.
  Status should flip to *connected* with the Chromium version.

First-run auth: use the noVNC window to log into CivitAI (and Google, if
using OAuth) **by hand, once**. The profile volume (`chrome-profile`)
persists cookies across container restarts, so this is a one-time step.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `BROWSER_BRIDGE_CDP_URL` | *(empty → sidecar default)* | CDP endpoint override (e.g. host-local Chrome) |
| `BROWSER_BRIDGE_FETCH_TIMEOUT` | `45.0` | Seconds for page-context fetches |
| `BROWSER_BRIDGE_NAVIGATE_TIMEOUT` | `30.0` | Seconds for tab navigation |
| `BROWSER_BRIDGE_CAPTURE_PATH` | *(empty = disabled)* | JSONL log of bridge fetches |
| `CHROME_SCREEN_WIDTH` / `CHROME_SCREEN_HEIGHT` | `1440` / `900` | Xvfb screen size |

## API

| Endpoint | Purpose |
|---|---|
| `GET /api/browser-bridge/status` | Connectivity, browser version, open tabs, counters |
| `POST /api/browser-bridge/connect` | Force a (re)connect attempt |
| `POST /api/browser-bridge/disconnect` | Drop CDP connection (sidecar keeps running) |
| `POST /api/browser-bridge/navigate` | Drive the civitai tab to a URL |
| `POST /api/browser-bridge/fetch` | Page-context fetch through the browser |
| `GET /api/browser-bridge/config` | Effective lane configuration |
| `POST /api/browser-bridge/harvest/install` | Attach the passive tRPC capture wrapper |
| `POST /api/browser-bridge/harvest/drain` | Drain captured responses into the archive |
| `POST /api/browser-bridge/harvest/once` | Install + drain in one call |

## The tRPC Harvester

The harvester makes browsing self-archiving: it wraps `window.fetch` and XHR
in the civitai tab so every tRPC response the page loads — feed pages,
collections, post details — is captured with **zero additional requests** to
CivitAI.

1. **Install** — `POST /api/browser-bridge/harvest/install` injects the wrapper
   (via `add_init_script`, so it survives SPA navigations) into all civitai
   tabs. Browsing proceeds normally; captures queue in the page.
2. **Browse** — scroll the feed, open posts, whatever the session needs.
3. **Drain** — `POST /api/browser-bridge/harvest/drain` pulls queued records
   and writes each into the existing sharded `CivitaiResponseArchive` as
   `kind="harvested"`, keyed by the same request-hash the direct lane uses —
   browser captures and direct-lane captures land in the same shards.

Filtering: only requests to `civitai.red` / `civitai.com` hosts under
`/api/trpc/` or `/api/v1/` are captured. Ad beacons
(`advertising.civitai.com`) and CDN binaries (`image.civitai.com`) are
excluded. Bodies are capped at 256 KB per record.

**Auto-drain** — `POST /api/browser-bridge/harvest/auto/start` runs a
background loop (default 30s, min 5s; env `BROWSER_BRIDGE_HARVEST_AUTO=1` +
`BROWSER_BRIDGE_HARVEST_INTERVAL` for boot-time start). Each tick drains,
archives, and **stages** new feed captures into the search-lab review tables.

## Browsed → Review → Import (two-stage ingestion)

Browsing civitai.red in the sidecar now feeds the same review pipeline the
search lab uses:

1. **Stage** — `POST /api/browser-bridge/stage` (also run automatically after
   each auto-drain tick) reads harvested `image.getInfinite` captures from
   the archive, decodes them with the existing flat-array deserializer, and
   upserts `CivitaiSearchImage` rows + standalone unrated links
   (`search_id=NULL, rating=NULL`). Zero requests to CivitAI. Idempotent;
   images that already have rating links keep their ratings.
2. **Review** — in the Search Lab's review mode, the **Unrated** tab shows
   browsed-but-not-reviewed images. Use the **Preset: Seen** button (hides
   saved/keep/skip/discard/identical) for incoming review, or **Preset:
   Keep** for the curated view. Rate keep/skip/discard exactly as in search
   sessions; discard excludes the image from future searches.
3. **Import** — kept images import via the existing search-lab import flow
   (library-status → `/api/import_civitai/batch`), unchanged.

Live first-run: 3 archived feed pages → 300 images staged → 298 unrated,
2 already rated — visible in the review grid with CDN thumbnails.

## Security notes

- **CDP port 9222 is full profile control** (cookies included). It is bound
  to the compose network only — never publish it with `ports:`, and never
  enable `--remote-debugging-address=0.0.0.0` on a host network.
- noVNC on 6080 is published on **all interfaces** so the Bridge Lab's
  noVNC link works from LAN browsers. It is unauthenticated by default
  (trusted-network convenience) — the sidecar browser holds authenticated
  CivitAI/Google sessions, so set a password on shared networks:

  ```bash
  # Generate the obfuscated password file contents:
  docker compose --profile browser run --rm chrome-sidecar \
      x11vnc -storepasswd <your-password> /dev/stdout   # copy output
  # Put it in .env next to docker-compose.yml:
  #   VNC_PASSWORD_DATA=<pasteed-file-contents>
  docker compose --profile browser up -d chrome-sidecar
  ```

  With a password set, noVNC prompts on connect — or embed it in the URL
  (`/vnc.html?password=<pw>`) for transparent one-click access.
- The bridge module never reads or exports cookies — requests simply ride
  the browser's own jar.

## Relationship to the direct lane

The browser bridge is an **additive** lane, not a replacement:

- Sync Lab / enrichment / model sync continue using `CivitaiHttpClient`
  (queue, pacing, retry, 503 handling — all battle-tested this session).
- The bridge exists for (a) research — observing how browser-context traffic
  is treated vs Python traffic, (b) interactive capture while a human
  browses, and (c) future commanded fetches when we want tRPC calls to look
  like page traffic.
- Nothing in the existing pipeline routes through the bridge by default.
  Integration points (e.g. "use bridge for tRPC when available") come later,
  per-caller, behind explicit opt-in.

## Roadmap (not yet built)

- In-page fetch/XHR harvesting → response archive (the "browse = archive" goal)
- Extension overlay for grid selection → AtelierAI ingest
- Bridge-aware routing in `CivitaiAPI` (opt-in per endpoint)
- Cookie freshness telemetry (compare jar vs server-side cache timing)
