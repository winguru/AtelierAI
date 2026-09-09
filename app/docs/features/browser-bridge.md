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

# Open the browser UI (noVNC) in your host browser:
#   http://localhost:6080/vnc.html
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

Verify captures on disk:

```bash
ls app/image_resources/civitai_api_responses/latest/ | head
```

## Security notes

- **CDP port 9222 is full profile control** (cookies included). It is bound
  to the compose network only — never publish it with `ports:`, and never
  enable `--remote-debugging-address=0.0.0.0` on a host network.
- noVNC on 6080 is published to `127.0.0.1` only. It currently has **no
  password** — fine on a single-user dev box, but put it behind auth or an
  SSH tunnel before any shared-host deployment.
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
