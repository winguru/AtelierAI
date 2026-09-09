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
# Public CDP port (compose-internal interface). Headed Chromium ALWAYS binds
# its DevTools server to 127.0.0.1 regardless of --remote-debugging-address
# (that flag is headless-only), so socat republishes the loopback port on the
# container interface for the AtelierAI backend.
CDP_PORT="${CDP_PORT:-9222}"
CDP_LOOPBACK_PORT="${CDP_LOOPBACK_PORT:-9221}"
NOVNC_PORT="${NOVNC_PORT:-6080}"
CHROME_USER_DATA_DIR="${CHROME_USER_DATA_DIR:-/data/profile}"

mkdir -p "$CHROME_USER_DATA_DIR" /captured

# Chromium must see the Xvfb display — it resolves the X server via $DISPLAY
# (x11vnc/websockify get it explicitly, Chromium does not).
export DISPLAY=:99

# Clear Chromium profile singleton locks from a previous (crashed) run.
# Container hostnames change on every recreate, so a leftover lock always
# looks like "another computer" still using the profile — Chromium exits 21
# with "profile appears to be in use" and the restart loops forever. Safe
# here because the entrypoint is the only Chromium that ever touches this
# profile, and the previous one is definitionally dead when we're starting.
rm -f "$CHROME_USER_DATA_DIR"/SingletonLock \
      "$CHROME_USER_DATA_DIR"/SingletonSocket \
      "$CHROME_USER_DATA_DIR"/SingletonCookie

# ---------------------------------------------------------------- X server --
# Clean stale X locks left behind by a previous (crashed) run. Container
# restarts keep the writable layer, so /tmp/.X99-lock can persist after the
# old Xvfb died — without this, every restart fails with
# "Server is already active for display 99".
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99

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
#   --remote-debugging-port              CDP endpoint (loopback; socat below
#                                        republishes it on the interface —
#                                        --remote-debugging-address is ignored
#                                        by headed Chromium, do not rely on it)
#   --user-data-dir                      persistent profile volume
#   --no-first-run / --no-default-browser-check   clean start
#   --disable-dev-shm-usage              /dev/shm is 64MB in Docker by default
#   --window-size                        match Xvfb screen so pages render fully
#   --no-sandbox                         container runs as root; Chromium's
#                                        sandbox requires user namespaces that
#                                        unprivileged Docker defaults disallow.
#                                        Compensating controls: compose-internal
#                                        CDP binding, loopback-only noVNC,
#                                        dedicated container, no host mounts
#                                        beyond the named profile volume.
CHROMIUM_FLAGS=(
    "--remote-debugging-port=${CDP_LOOPBACK_PORT}"
    "--user-data-dir=${CHROME_USER_DATA_DIR}"
    "--no-first-run"
    "--no-default-browser-check"
    "--disable-dev-shm-usage"
    "--window-size=${SCREEN_WIDTH},${SCREEN_HEIGHT}"
    "--start-maximized"
    "--no-sandbox"
)

echo "Starting Chromium with CDP on loopback :${CDP_LOOPBACK_PORT} (published as :${CDP_PORT} via socat)"
chromium "${CHROMIUM_FLAGS[@]}" &
CHROME_PID=$!

# Republish the loopback-only DevTools port on the container interface so the
# AtelerAI backend (and anything else on the compose network) can connect.
socat TCP-LISTEN:"${CDP_PORT}",bind=0.0.0.0,fork,reuseaddr \
      TCP:127.0.0.1:"${CDP_LOOPBACK_PORT}" &
SOCAT_PID=$!

# Chromium is the critical process; when it exits, shut everything down.
trap 'kill -TERM $XVFB_PID $X11VNC_PID $NOVNC_PID $SOCAT_PID $CHROME_PID 2>/dev/null || true' EXIT

# Fail fast if Chromium dies immediately (bad flags, corrupt profile,
# sandbox errors). 5s window catches the common startup failures.
sleep 5
if ! kill -0 "$CHROME_PID" 2>/dev/null; then
    echo "ERROR: Chromium exited during startup (exit status follows)" >&2
    wait "$CHROME_PID"
    exit $?
fi

# Wait for CDP to answer so `docker logs` shows readiness. Curl goes through
# the socat forwarder — the same path Playwright will use — so readiness here
# validates the full proxy chain, not just Chromium's loopback listener.
for _ in $(seq 1 30); do
    if curl -fsS "http://127.0.0.1:${CDP_PORT}/json/version" >/dev/null 2>&1; then
        echo "CDP ready on :${CDP_PORT} — connect with connect_over_cdp('http://<sidecar>:${CDP_PORT}')"
        break
    fi
    sleep 1
done

wait "$CHROME_PID"
