# ── Memory ───────────────────────────────────────────────────────────────────
# 📄 docs: app/docs/memories/civitai-integration.md
# ──────────────────────────────────────────────────────────────────────────────
# Chrome sidecar for the AtelierAI browser bridge.
#
# Runs a real, headed Chromium (Debian package, works on amd64 + arm64) under
# Xvfb with noVNC/websockify so the user can view and drive the browser from a
# host browser tab. Chromium listens for CDP on port 9222 (compose-internal);
# AtelierAI connects with Playwright's connect_over_cdp.
#
# Why not google-chrome-stable? No Linux ARM64 builds exist. Debian's chromium
# package is multi-arch and is the same engine the user's Chrome runs, so the
# automation story is identical on both host architectures.
FROM debian:trixie-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV SCREEN_WIDTH=1440 \
    SCREEN_HEIGHT=900 \
    SCREEN_DEPTH=24 \
    CDP_PORT=9222 \
    NOVNC_PORT=6080 \
    CHROME_USER_DATA_DIR=/data/profile \
    TZ=UTC

# chromium + codecs for media playback, X stack, novnc + websockify from
# Debian (avoids pip/npM in the image), plus procps for tini-style PID 1 via
# tini package. ca-certificates and fonts improve rendering fidelity.
RUN apt-get update && apt-get install -y --no-install-recommends \
        chromium \
        fonts-liberation \
        fonts-noto-color-emoji \
        xvfb \
        x11vnc \
        novnc \
        websockify \
        socat \
        tini \
        procps \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Persistent profile state (cookies, extensions, OAuth grants) lives on a
# named volume so it survives container restarts.
RUN mkdir -p /data/profile /captured

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 9222 6080

ENTRYPOINT ["/usr/bin/tini", "--", "/entrypoint.sh"]
