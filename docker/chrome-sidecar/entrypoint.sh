#!/bin/bash
# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/civitai-integration.md
# ──────────────────────────────────────────────────────────────────────────────
# Entrypoint for the chrome sidecar.
#
# Boots Xvfb → x11vnc → noVNC (websockify) → headed Chromium with CDP.
# The browser is the last thing to start so a crash of any earlier layer is
# visible in `docker logs` before Chromium exits.
set -euo pipefail

SCREEN_WIDTH="${SCREEN_WIDTH:-1440}"
SCREEN_HEIGHT="${SCREEN_HEIGHT:-900}"
SCREEN_DEPTH="${SCREEN_DEPTH:-24}"
CDP_PORT="${CDP_PORT:-9222}"
NOVNC_PORT="${NOVNC_PORT:-6080}"
CHROME_USER_DATA_DIR="${CHROME_USER_DATA_DIR:-/data/profile}"

mkdir -p "$CHROME_USER_DATA_DIR" /captured

# ---------------------------------------------------------------- X server --
# -ac disables access control (sidecar-internal, never exposed publicly).
Xvfb :99 -screen 0 "${SCREEN_WIDTH}x${SCREEN_HEIGHT}x${SCREEN_DEPTH}" \
      -nolisten tcp +extension RANDR &
XVFB_PID=$!

# Wait for the X server to be ready before starting anything on top of it.
for _ in $(seq 1 30); do
    if xdpyinfo -display :99 >/dev/null 2>&1; then
        break
    fi
    sleep 0.5
done
if ! xdpyinfo -display :99 >/dev/null 2>&1; then
    echo "ERROR: Xvfb failed to start on :99" >&2
    exit 1
fi
echo "Xvfb ready on :99 (${SCREEN_WIDTH}x${SCREEN_HEIGHT}x${SCREEN_DEPTH})"

# -------------------------------------------------------------------- VNC --
# Loopback-only VNC server on the internal display.
x11vnc -display :99 -forever -shared -rfbport 5900 -nopw -quiet &
X11VNC_PID=$!

# ------------------------------------------------------------------ noVNC --
# Serve the noVNC web client; websockify bridges browser WebSocket → VNC.
websockify --web /usr/share/novnc/ "${NOVNC_PORT}" localhost:5900 &
NOVNC_PID=$!
echo "noVNC listening on :${NOVNC_PORT} (VNC on :5900)"

# ---------------------------------------------------------------- Chromium --
# Launch args rationale (kept minimal — parity with civitai_auth.py's
# CDP launch, where excessive hardening flags caused OAuth friction):
#   --remote-debugging-address=0.0.0.0   reachable from the AtelierAI container
#   --remote-debugging-port              CDP endpoint for Playwright
#   --user-data-dir                      persistent profile volume
#   --no-first-run / --no-default-browser-check   clean start
#   --disable-dev-shm-usage              /dev/shm is 64MB in Docker by default
#   --window-size                        match Xvfb screen so pages render fully
CHROMIUM_FLAGS=(
    "--remote-debugging-address=0.0.0.0"
    "--remote-debugging-port=${CDP_PORT}"
    "--user-data-dir=${CHROME_USER_DATA_DIR}"
    "--no-first-run"
    "--no-default-browser-check"
    "--disable-dev-shm-usage"
    "--window-size=${SCREEN_WIDTH},${SCREEN_HEIGHT}"
    "--start-maximized"
)

echo "Starting Chromium with CDP on :${CDP_PORT}"
chromium "${CHROMIUM_FLAGS[@]}" &
CHROME_PID=$!

# Chromium is the critical process; when it exits, shut everything down.
trap 'kill -TERM $XVFB_PID $X11VNC_PID $NOVNC_PID $CHROME_PID 2>/dev/null || true' EXIT

# Fail fast if Chromium dies immediately (bad flags, corrupt profile).
sleep 2
if ! kill -0 "$CHROME_PID" 2>/dev/null; then
    echo "ERROR: Chromium exited during startup" >&2
    exit 1
fi

# Wait for CDP to answer so `docker logs` shows readiness.
for _ in $(seq 1 30); do
    if curl -fsS "http://127.0.0.1:${CDP_PORT}/json/version" >/dev/null 2>&1; then
        echo "CDP ready on :${CDP_PORT} — connect with connect_over_cdp('http://<sidecar>:${CDP_PORT}')"
        break
    fi
    sleep 1
done

wait "$CHROME_PID"
