"""URL helpers for normalizing CivitAI source URLs.

Stores *relative* paths in the database (e.g. ``/images/12345``) so that the
configurable base URL (civitai.red / civitai.com) can be changed at runtime
without a data migration.  Full URLs are reconstructed at read / display time.
"""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

# ── Known CivitAI hostname patterns ──────────────────────────────────────────
# Matches https?://(www.)?(civitai.com|civitai.red) with optional trailing path
_CIVITAI_HOST_RE = re.compile(
    r"^https?://(?:www\.)?(?:civitai\.com|civitai\.red)(/.*)?$", re.IGNORECASE
)

# Same patterns as a set for fast membership checks
_CIVITAI_HOSTS = {
    "civitai.com",
    "www.civitai.com",
    "civitai.red",
    "www.civitai.red",
}

# Also match konachan and other known art-source hosts that follow the same
# pattern — currently only CivitAI URLs are normalised, but the helper is
# generic enough to extend.
_KONACHAN_HOST_RE = re.compile(
    r"^https?://(?:www\.)?konachan\.com(/.*)?$", re.IGNORECASE
)


def normalize_civitai_url(url: Optional[str]) -> Optional[str]:
    """Strip CivitAI hostname, returning the relative path.

    * ``https://civitai.red/images/12345`` → ``/images/12345``
    * ``https://civitai.com/images/12345`` → ``/images/12345``
    * ``/images/12345`` → ``/images/12345`` (already relative)
    * ``None`` → ``None``
    * Non-CivitAI URLs are returned unchanged.

    The function is idempotent — calling it on an already-normalised value
    returns the same result.
    """
    if url is None:
        return None
    url = url.strip()
    if not url:
        return None

    m = _CIVITAI_HOST_RE.match(url)
    if m:
        path = m.group(1)
        return path if path else "/"

    # Already a relative path or non-CivitAI URL
    return url.strip()


def build_civitai_url(
    relative_path: Optional[str], base_url: str = "https://civitai.red"
) -> Optional[str]:
    """Reconstruct a full CivitAI URL from a relative path.

    * ``/images/12345`` + base → ``https://civitai.red/images/12345``
    * If *relative_path* is already absolute (starts with ``http``), return it
      unchanged — this provides backward compatibility during the transition.
    * ``None`` → ``None``
    """
    if not relative_path:
        return relative_path

    path = relative_path.strip()

    # Already absolute — return as-is (backward compat during migration)
    if path.startswith(("http://", "https://")):
        return path

    # Ensure single slash between base and path
    base = base_url.rstrip("/")
    if not path.startswith("/"):
        path = "/" + path

    return f"{base}{path}"


def is_civitai_url(url: Optional[str]) -> bool:
    """Return True if *url* points to a known CivitAI hostname."""
    if not url:
        return False
    parsed = urlparse(url.strip())
    return (parsed.hostname or "").lower() in _CIVITAI_HOSTS
